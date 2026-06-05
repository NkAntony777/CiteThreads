/**
 * Tests for SmartSearchPanel
 *
 * Covers:
 *   - 4 example chips render
 *   - clicking an example chip fills the textarea
 *   - mocked SSE response (text_delta + paper_suggestions + done)
 *     renders paper suggestion cards
 *   - "Use to build graph" button forwards the paper to the
 *     onSelectForGraph callback
 *   - Stop button calls fetch's AbortController
 *   - error from the stream surfaces an inline alert
 *
 * The backend endpoint /api/agent/search/stream is not running
 * during the test. We mock global.fetch to return a Response with
 * a ReadableStream of SSE frames so the service layer is exercised
 * end-to-end without msw.
 */
import { describe, it, expect, beforeEach, afterEach, vi, type Mock } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '../../i18n';
import { SmartSearchPanel, SMART_SEARCH_ENDPOINT } from './SmartSearchPanel';
import type { Paper } from '../../types';

// Helper: build a ReadableStream of SSE-encoded frames from a list
// of JSON event objects. The body is closed with `done: true` so the
// consumer's `for(;;)` loop terminates.
function sseStream(events: Array<Record<string, unknown>>): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    const body = events
        .map((e) => `data: ${JSON.stringify(e)}\n\n`)
        .join('');
    return new ReadableStream<Uint8Array>({
        start(controller) {
            controller.enqueue(encoder.encode(body));
            controller.close();
        },
    });
}

function buildSearchPapers(): Paper[] {
    return [
        {
            id: 'p1',
            doi: '10.1109/foo.2023.001',
            title: 'Graph Neural Network Surveys: A Comprehensive Review',
            authors: ['Alice Liu', 'Bob Wang', 'Carol Zhang', 'Dan Park'],
            year: 2023,
            venue: 'TPAMI',
            citation_count: 142,
            reference_count: 0,
            fields: ['cs.LG'],
        },
        {
            id: 'p2',
            title: 'Attention Is All You Need (Recap)',
            authors: ['Vaswani', 'Shazeer'],
            year: 2022,
            citation_count: 88,
            reference_count: 0,
            fields: ['cs.CL'],
        },
    ];
}

type FetchMock = Mock<Parameters<typeof fetch>, ReturnType<typeof fetch>>;

describe('SmartSearchPanel', () => {
    let fetchSpy: FetchMock;

    beforeEach(() => {
        vi.clearAllMocks();
        fetchSpy = vi.spyOn(globalThis, 'fetch') as unknown as FetchMock;
    });

    afterEach(() => {
        fetchSpy.mockRestore();
        cleanup();
    });

    it('renders the textarea, send button, and 4 example chips', () => {
        render(<SmartSearchPanel />);

        // The textarea is a real AntD <TextArea> with autoSize; it
        // renders as a <textarea> element. We pick it up by its
        // placeholder.
        const textarea = screen.getByPlaceholderText(/gnn survey|图神经网络/i);
        expect(textarea).toBeInTheDocument();

        // The Send button starts disabled because input is empty.
        const sendButton = screen.getByRole('button', { name: /send|发送/i });
        expect(sendButton).toBeInTheDocument();

        // 4 example chips
        const chips = screen.getAllByTestId('smart-search-example-chip');
        expect(chips).toHaveLength(4);
    });

    it('fills the textarea when an example chip is clicked', async () => {
        const user = userEvent.setup();
        render(<SmartSearchPanel />);

        const firstChip = screen.getAllByTestId('smart-search-example-chip')[0];
        await user.click(firstChip);

        const textarea = screen.getByPlaceholderText(/gnn survey|图神经网络/i) as HTMLTextAreaElement;
        expect(textarea.value).toBe(firstChip.textContent);
    });

    it('streams SSE frames, renders paper cards, and forwards selection to the callback', async () => {
        const papers = buildSearchPapers();
        fetchSpy.mockImplementation(async (input, init) => {
            // Verify we are hitting the smart-search endpoint and
            // the request body is JSON.
            const requestInit = init as RequestInit | undefined;
            expect(String(input)).toBe(SMART_SEARCH_ENDPOINT);
            expect(requestInit?.method).toBe('POST');
            const body = JSON.parse(String(requestInit?.body));
            expect(body.message).toBeTruthy();
            expect(body.extra_context).toBe('seed-paper-recommendation');

            return new Response(sseStream([
                { type: 'text_delta', delta: 'Here are some candidates.' },
                {
                    type: 'paper_suggestions',
                    papers,
                },
                {
                    type: 'done',
                    iterations: 2,
                    truncated: false,
                    content: 'Here are some candidates.',
                    action_type: 'recommend_seed',
                    paper_suggestions: papers,
                    tool_calls: [
                        {
                            tool: 'search_papers',
                            arguments: { query: 'graph neural network survey' },
                            result_preview: 'Found 12 results',
                            latency_ms: 320,
                            error: null,
                        },
                    ],
                    error: null,
                },
            ]), {
                status: 200,
                headers: { 'Content-Type': 'text/event-stream' },
            });
        });

        const onSelect = vi.fn();
        const user = userEvent.setup();
        render(<SmartSearchPanel onSelectForGraph={onSelect} />);

        const textarea = screen.getByPlaceholderText(/gnn survey|图神经网络/i);
        await user.type(textarea, 'GNN survey{Enter}');

        // Wait for the cards to render after the stream completes.
        await waitFor(() => {
            expect(
                screen.getByText('Graph Neural Network Surveys: A Comprehensive Review'),
            ).toBeInTheDocument();
        });
        expect(screen.getByText('Attention Is All You Need (Recap)')).toBeInTheDocument();

        // Year and citation count tags should appear. The citation
        // count is wrapped inside a Tag with a "cited" prefix, so
        // we look for the combined string.
        expect(screen.getByText('2023')).toBeInTheDocument();
        expect(screen.getByText(/142/)).toBeInTheDocument();

        // Click "Use to build graph" on the first card.
        const useButtons = screen.getAllByTestId('smart-search-use-button');
        await user.click(useButtons[0]);

        expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'p1' }));
    });

    it('aborts the in-flight request when Stop is clicked', async () => {
        // Build a stream that never closes (no controller.close()).
        // This lets us assert the AbortController is signaled by
        // clicking Stop.
        const encoder = new TextEncoder();
        const infiniteStream = new ReadableStream<Uint8Array>({
            start(controller) {
                controller.enqueue(encoder.encode('data: {"type":"text_delta","delta":"hi"}\n\n'));
            },
            cancel() {
                // noop: stream cancellation is asserted via abortSpy
            },
        });

        const abortSpy = vi.fn();
        fetchSpy.mockImplementation(async (_input, init) => {
            const requestInit = init as RequestInit | undefined;
            const signal = requestInit?.signal as AbortSignal | undefined;
            if (signal) {
                signal.addEventListener('abort', abortSpy);
            }
            return new Response(infiniteStream, {
                status: 200,
                headers: { 'Content-Type': 'text/event-stream' },
            });
        });

        const user = userEvent.setup();
        render(<SmartSearchPanel />);

        const textarea = screen.getByPlaceholderText(/gnn survey|图神经网络/i);
        await user.type(textarea, 'GNN survey{Enter}');

        // The Stop button replaces Send while running.
        const stopButton = await screen.findByRole('button', { name: /stop|停止/i });
        await act(async () => {
            await user.click(stopButton);
        });

        await waitFor(() => {
            expect(abortSpy).toHaveBeenCalled();
        });
    });

    it('surfaces a stream error in an alert', async () => {
        fetchSpy.mockImplementation(async () => {
            return new Response(sseStream([
                { type: 'error', message: 'LLM guard rejected the request', code: 'llm_guard' },
                {
                    type: 'done',
                    iterations: 0,
                    truncated: false,
                    content: '',
                    action_type: null,
                    paper_suggestions: [],
                    tool_calls: [],
                    error: 'LLM guard rejected the request',
                },
            ]), {
                status: 200,
                headers: { 'Content-Type': 'text/event-stream' },
            });
        });

        const user = userEvent.setup();
        render(<SmartSearchPanel />);

        const textarea = screen.getByPlaceholderText(/gnn survey|图神经网络/i);
        // Use fireEvent.submit to bypass the i18n key race; this
        // also works as a Send click path.
        fireEvent.change(textarea, { target: { value: 'foo' } });
        const sendButton = screen.getByRole('button', { name: /send|发送/i });
        await user.click(sendButton);

        await waitFor(() => {
            expect(
                screen.getByText('LLM guard rejected the request'),
            ).toBeInTheDocument();
        });
    });
});
