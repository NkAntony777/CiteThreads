/**
 * Tests for the AgentChatPanel component.
 *
 * Covers:
 *   - rendering of title, TextArea, and Send button
 *   - i18n: Chinese title is shown in zh-CN mode
 *   - the Send button triggers streamAgentChat and streams text deltas
 *     into the live output area
 *   - the Stop button aborts the in-flight stream
 *
 * The /api/agent/chat/stream endpoint is mocked via the agentStream
 * service so we don't need a real network or a backend fixture.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup, act } from '@testing-library/react';
import '../../i18n';

// vi.mock factories are hoisted to the top of the file, so the mock
// state must be created with vi.hoisted so both the factory and the
// test body can reference it.
const { mockStreamAgentChat, mockAbort } = vi.hoisted(() => ({
    mockStreamAgentChat: vi.fn(),
    mockAbort: vi.fn(),
}));

vi.mock('../../services/agentStream', () => ({
    __esModule: true,
    streamAgentChat: mockStreamAgentChat,
}));

import { AgentChatPanel } from './AgentChatPanel';

type Handler = (...args: unknown[]) => void;
interface Handlers {
    onTextDelta?: Handler;
    onToolStart?: Handler;
    onToolEnd?: Handler;
    onPaperSuggestions?: Handler;
    onError?: Handler;
    onDone?: Handler;
    onPing?: Handler;
}

/**
 * Build a streamAgentChat fake that lets the test dispatch events
 * imperatively via the returned handle. We keep the call args and the
 * handler bundle so the test can assert that the request body was
 * built correctly and that text deltas are rendered as the user
 * observes them.
 */
function installStream() {
    let saved: { req: unknown; handlers: Handlers } | null = null;
    let resolveDone: (state: unknown) => void = () => undefined;
    const donePromise = new Promise<unknown>((resolve) => {
        resolveDone = resolve;
    });

    mockStreamAgentChat.mockImplementation((req: unknown, handlers: Handlers) => {
        saved = { req, handlers };
        return {
            abort: mockAbort,
            done: donePromise,
        };
    });

    return {
        emitText: (delta: string) => saved?.handlers.onTextDelta?.(delta),
        emitToolStart: (tool: string) => saved?.handlers.onToolStart?.(tool, {}),
        emitToolEnd: (record: unknown) => saved?.handlers.onToolEnd?.(record),
        emitPapers: (papers: unknown[]) => saved?.handlers.onPaperSuggestions?.(papers),
        emitError: (msg: string) => saved?.handlers.onError?.(msg),
        emitDone: (state: unknown) => {
            saved?.handlers.onDone?.(state);
            resolveDone(state);
        },
        getRequest: () => saved?.req,
    };
}

describe('AgentChatPanel', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    afterEach(() => {
        cleanup();
    });

    it('renders the panel title, TextArea, and Send button', async () => {
        render(<AgentChatPanel projectId="proj-1" />);

        // Title — zh-CN is the i18n default so we look for the Chinese label.
        expect(screen.getByText('研究助手')).toBeInTheDocument();
        // TextArea is rendered with the placeholder pulled from agent.placeholder.
        const textarea = await screen.findByPlaceholderText(/图神经网络|graph neural/i);
        expect(textarea).toBeInTheDocument();
        // Send button — zh-CN default. Antd icons set aria-label on the
        // button, so the accessible name is "send 发送" not just "发送".
        // We use a regex match to be resilient to icon order.
        expect(screen.getByRole('button', { name: /发送/ })).toBeInTheDocument();
    });

    it('renders the empty-state copy before any turn is sent', async () => {
        render(<AgentChatPanel projectId="proj-empty" />);
        await waitFor(() => {
            expect(screen.getByText('暂无回复')).toBeInTheDocument();
        });
    });

    it('forwards projectId and extraContext to streamAgentChat on Send', async () => {
        const stream = installStream();
        render(
            <AgentChatPanel
                projectId="proj-xyz"
                extraContext="writing-context-hint"
            />,
        );

        const textarea = await screen.findByPlaceholderText(/图神经网络|graph neural/i);
        await act(async () => {
            fireEvent.change(textarea, { target: { value: 'hello agent' } });
        });
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: /发送/ }));
        });

        await waitFor(() => {
            expect(mockStreamAgentChat).toHaveBeenCalledTimes(1);
        });

        const req = stream.getRequest() as {
            message: string;
            project_id: string;
            extra_context?: string;
        };
        expect(req.message).toBe('hello agent');
        expect(req.project_id).toBe('proj-xyz');
        expect(req.extra_context).toBe('writing-context-hint');
    });

    it('streams text deltas into the live output area', async () => {
        const stream = installStream();
        render(<AgentChatPanel projectId="proj-stream" />);

        const textarea = await screen.findByPlaceholderText(/图神经网络|graph neural/i);
        await act(async () => {
            fireEvent.change(textarea, { target: { value: 'hi' } });
        });
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: /发送/ }));
        });

        await act(async () => {
            stream.emitText('Hello');
            stream.emitText(' world');
        });

        await waitFor(() => {
            expect(screen.getByText('Hello world')).toBeInTheDocument();
        });
    });

    it('shows the Reset button when idle and a Stop button while running', async () => {
        installStream();
        render(<AgentChatPanel projectId="proj-buttons" />);

        // Initial: Reset button (zh-CN). Use regex to absorb the icon's aria-label.
        expect(screen.getByRole('button', { name: /重置/ })).toBeInTheDocument();

        const textarea = await screen.findByPlaceholderText(/图神经网络|graph neural/i);
        await act(async () => {
            fireEvent.change(textarea, { target: { value: 'go' } });
        });
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: /发送/ }));
        });

        // While running: Stop button replaces Reset.
        await waitFor(() => {
            expect(screen.getByRole('button', { name: /停止/ })).toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /重置/ })).not.toBeInTheDocument();
        });

        // Stop button should abort the underlying stream handle.
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: /停止/ }));
        });
        expect(mockAbort).toHaveBeenCalled();
    });

    it('renders paper suggestions and tool activity as they arrive', async () => {
        const stream = installStream();
        render(<AgentChatPanel projectId="proj-tools" />);

        const textarea = await screen.findByPlaceholderText(/图神经网络|graph neural/i);
        await act(async () => {
            fireEvent.change(textarea, { target: { value: 'papers please' } });
        });
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: /发送/ }));
        });

        await act(async () => {
            stream.emitToolStart('search_papers');
            stream.emitToolEnd({
                tool: 'search_papers',
                arguments: { query: 'gnn' },
                result_preview: '3 results',
                latency_ms: 42,
                error: null,
            });
            stream.emitPapers([
                {
                    id: 'p1',
                    title: 'Graph Neural Networks: A Review',
                    authors: ['Alice', 'Bob'],
                    year: 2024,
                    citation_count: 12,
                    reference_count: 0,
                    fields: [],
                },
            ]);
        });

        await waitFor(() => {
            // "Recommended papers" header (zh-CN).
            expect(screen.getByText('推荐论文')).toBeInTheDocument();
            // "Tool activity" header (zh-CN).
            expect(screen.getByText('工具活动')).toBeInTheDocument();
        });
        expect(
            screen.getByText('Graph Neural Networks: A Review'),
        ).toBeInTheDocument();
    });
});
