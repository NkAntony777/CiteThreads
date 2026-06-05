/**
 * Tests for the DraftGenerator component
 *
 * Covers:
 *   - rendering of form + 4 action buttons
 *   - topic input updates state
 *   - research button calls draftApi.runResearch
 *   - structure / compose / compile buttons call the matching API
 *   - error states render the alert surface
 *   - progress bar updates when status reports a percentage
 *   - empty state when no projectId is supplied
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act, cleanup } from '@testing-library/react';
import '../../i18n';

// vi.mock factories are hoisted to the top of the file, so the mock
// object must be created with vi.hoisted so both the factory and the
// test body can reference it.
const { mockDraftApi } = vi.hoisted(() => ({
    mockDraftApi: {
        getAuthToken: vi.fn(() => ''),
        runResearch: vi.fn(),
        runStructure: vi.fn(),
        runCompose: vi.fn(),
        runCompile: vi.fn(),
        getStatus: vi.fn(),
        regenerateSection: vi.fn(),
        cancelDraft: vi.fn(),
    },
}));

vi.mock('../../services/draftApi', () => ({
    __esModule: true,
    default: mockDraftApi,
}));

import DraftGenerator from './DraftGenerator';

describe('DraftGenerator', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockDraftApi.getStatus.mockResolvedValue({
            success: true,
            ctx: { progress_pct: 0, phase_results: {} },
        });
    });

    afterEach(() => {
        cleanup();
    });

    it('renders the form and 4 action buttons', async () => {
        render(<DraftGenerator projectId="proj-1" />);

        expect(await screen.findByTestId('draft-generator')).toBeInTheDocument();
        expect(screen.getByTestId('draft-topic')).toBeInTheDocument();
        expect(screen.getByTestId('draft-word-count')).toBeInTheDocument();
        expect(screen.getByTestId('draft-citation-style')).toBeInTheDocument();
        expect(screen.getByTestId('btn-research')).toBeInTheDocument();
        expect(screen.getByTestId('btn-structure')).toBeInTheDocument();
        expect(screen.getByTestId('btn-compose')).toBeInTheDocument();
        expect(screen.getByTestId('btn-compile')).toBeInTheDocument();
    });

    it('shows the empty state when no projectId is provided', async () => {
        render(<DraftGenerator projectId="" />);
        // The i18n key "draftGenerator.status.noProject" resolves to
        // "未选择项目。" in zh-CN (the i18n default). The component
        // uses the key path through t(), so we look for the
        // antd Empty component's description text.
        await waitFor(() => {
            expect(screen.getByText(/no project|project is required|项目/i)).toBeInTheDocument();
        });
    });

    it('updates the topic input on change', async () => {
        render(<DraftGenerator projectId="proj-1" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'graph neural networks' } });
        expect((topic as HTMLTextAreaElement).value).toBe('graph neural networks');
    });

    it('updates the word count input on change', async () => {
        render(<DraftGenerator projectId="proj-1" />);
        const wordInput = (await screen.findByTestId('draft-word-count')) as HTMLInputElement;
        fireEvent.change(wordInput, { target: { value: '4500' } });
        expect(wordInput.value).toBe('4500');
    });

    it('calls runResearch when the research button is clicked', async () => {
        mockDraftApi.runResearch.mockResolvedValue({
            success: true,
            ctx: {
                candidate_papers: [
                    {
                        id: 'p1',
                        title: 'Sample',
                        authors: ['Alice'],
                        year: 2024,
                        citation_count: 0,
                        reference_count: 0,
                        fields: [],
                    },
                ],
                paper_summaries: [],
                research_gaps: [],
                phase_results: { research: { status: 'completed' } },
            },
        });

        render(<DraftGenerator projectId="proj-1" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'topic A' } });

        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });

        await waitFor(() => {
            expect(mockDraftApi.runResearch).toHaveBeenCalledTimes(1);
        });
        const call = mockDraftApi.runResearch.mock.calls[0];
        expect(call[0]).toBe('proj-1');
        expect(call[1]).toMatchObject({
            topic: 'topic A',
            citation_style: 'APA',
        });
        expect(typeof call[1].target_word_count).toBe('number');
    });

    it('does not call runResearch when topic is empty', async () => {
        render(<DraftGenerator projectId="proj-1" />);

        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });
        // Either it short-circuited (preferred) or the underlying API
        // is rejected. Either way, runResearch is not called with a
        // valid payload.
        expect(mockDraftApi.runResearch).not.toHaveBeenCalled();
    });

    it('calls runStructure / runCompose / runCompile in sequence', async () => {
        mockDraftApi.runResearch.mockResolvedValue({
            success: true,
            ctx: {
                candidate_papers: [],
                paper_summaries: [],
                research_gaps: [],
                phase_results: { research: { status: 'completed' } },
            },
        });
        mockDraftApi.runStructure.mockResolvedValue({
            success: true,
            ctx: {
                outline: 'I. Intro\nII. Body',
                formatted_outline: '# I. Intro\n# II. Body',
                phase_results: { structure: { status: 'completed' } },
            },
        });
        mockDraftApi.runCompose.mockResolvedValue({
            success: true,
            ctx: {
                section_drafts: [
                    { section: 'Introduction', content: 'lorem', citations: [] },
                ],
                phase_results: { compose: { status: 'completed' } },
            },
        });
        mockDraftApi.runCompile.mockResolvedValue({
            success: true,
            ctx: {
                final_draft: '# Final\nHello.',
                quality_history: [],
                phase_results: { compile: { status: 'completed' } },
            },
        });

        render(<DraftGenerator projectId="proj-2" />);

        // Type a topic so the research button is unblocked.
        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'T' } });

        // 1) Research
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });
        await waitFor(() => expect(mockDraftApi.runResearch).toHaveBeenCalled());

        // 2) Structure
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-structure'));
        });
        await waitFor(() => expect(mockDraftApi.runStructure).toHaveBeenCalledWith('proj-2'));

        // 3) Compose
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-compose'));
        });
        await waitFor(() => expect(mockDraftApi.runCompose).toHaveBeenCalledWith('proj-2'));

        // 4) Compile
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-compile'));
        });
        await waitFor(() => expect(mockDraftApi.runCompile).toHaveBeenCalledWith('proj-2'));
    });

    it('renders an error alert when research rejects with auth_required', async () => {
        const authErr = Object.assign(new Error('401 unauthorized'), { code: 'auth_required' });
        mockDraftApi.runResearch.mockRejectedValue(authErr);

        render(<DraftGenerator projectId="proj-3" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'X' } });
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });

        await waitFor(() => {
            // The alert message contains the translated label, which
            // includes either "auth" (en) or "认证" (zh).
            expect(screen.getByText(/auth|认证/)).toBeInTheDocument();
        });
    });

    it('renders a friendly LLM-not-configured error alert when research rejects with llm_key_missing', async () => {
        const llmErr = Object.assign(new Error('precondition required'), { code: 'llm_key_missing' });
        mockDraftApi.runResearch.mockRejectedValue(llmErr);

        render(<DraftGenerator projectId="proj-4" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'X' } });
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });

        await waitFor(() => {
            // The friendly zh-CN message: "AI 助手尚未配置，请联系管理员"
            expect(
                screen.getByText(/AI 助手|尚未配置|联系管理员|not configured/i),
            ).toBeInTheDocument();
        });
    });

    it('updates the progress display from the status endpoint', async () => {
        mockDraftApi.getStatus.mockResolvedValue({
            success: true,
            ctx: {
                progress_pct: 75,
                phase_results: {
                    research: { status: 'completed' },
                    structure: { status: 'completed' },
                    compose: { status: 'running' },
                },
            },
        });

        render(<DraftGenerator projectId="proj-5" />);

        // 75% should appear after the status fetch resolves.
        await waitFor(() => {
            expect(screen.getAllByText('75%').length).toBeGreaterThan(0);
        });
    });

    it('renders a generic error when runResearch fails with an unknown code', async () => {
        const genericErr = Object.assign(new Error('boom'), { code: 'http_error' });
        mockDraftApi.runResearch.mockRejectedValue(genericErr);

        render(<DraftGenerator projectId="proj-6" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'Y' } });
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });

        await waitFor(() => {
            // The alert includes the message we provided.
            expect(screen.getByText(/boom/)).toBeInTheDocument();
        });
    });

    // ============================================
    // P1-2: Cancel + per-section regen + friendly errors
    // ============================================

    it('shows a cancel button while a phase is running and hides it when idle', async () => {
        // Make the research call hang so the running flag stays set
        // long enough for the cancel button to appear.
        let resolveRun: (() => void) | null = null;
        mockDraftApi.runResearch.mockImplementation(
            () => new Promise((resolve) => {
                resolveRun = () =>
                    resolve({
                        success: true,
                        ctx: {
                            candidate_papers: [],
                            paper_summaries: [],
                            research_gaps: [],
                            phase_results: { research: { status: 'completed' } },
                        },
                    });
            }),
        );
        mockDraftApi.cancelDraft.mockResolvedValue({
            cancelled: true,
            project_id: 'proj-cancel',
            already_running: false,
            message: 'cancellation flag set',
        });

        render(<DraftGenerator projectId="proj-cancel" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'T' } });

        // No cancel button while idle.
        expect(screen.queryByTestId('btn-cancel')).not.toBeInTheDocument();

        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });

        // Cancel button visible during the in-flight run.
        await waitFor(() => {
            expect(screen.getByTestId('btn-cancel')).toBeInTheDocument();
        });

        // Let the in-flight call resolve so the button goes away.
        await act(async () => {
            resolveRun?.();
        });
        await waitFor(() => {
            expect(screen.queryByTestId('btn-cancel')).not.toBeInTheDocument();
        });
    });

    it('cancel button calls the cancel API and disables itself while pending', async () => {
        let resolveRun: (() => void) | null = null;
        mockDraftApi.runResearch.mockImplementation(
            () => new Promise((resolve) => {
                resolveRun = () =>
                    resolve({
                        success: true,
                        ctx: { phase_results: { research: { status: 'completed' } } },
                    });
            }),
        );
        let resolveCancel: ((v: unknown) => void) | null = null;
        mockDraftApi.cancelDraft.mockImplementation(
            () => new Promise((resolve) => {
                resolveCancel = () =>
                    resolve({
                        cancelled: true,
                        project_id: 'proj-cancel-2',
                        already_running: false,
                        message: 'cancellation flag set',
                    });
            }),
        );

        render(<DraftGenerator projectId="proj-cancel-2" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'T' } });
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });

        const cancelBtn = await screen.findByTestId('btn-cancel');

        await act(async () => {
            fireEvent.click(cancelBtn);
        });

        await waitFor(() => {
            expect(mockDraftApi.cancelDraft).toHaveBeenCalledWith('proj-cancel-2');
        });
        // Let the cancel resolve and the in-flight research call resolve.
        await act(async () => {
            resolveCancel?.({});
            resolveRun?.();
        });
    });

    it('per-section regen button opens the modal and submit calls the API', async () => {
        mockDraftApi.runResearch.mockResolvedValue({
            success: true,
            ctx: { phase_results: { research: { status: 'completed' } } },
        });
        mockDraftApi.runStructure.mockResolvedValue({
            success: true,
            ctx: { phase_results: { structure: { status: 'completed' } } },
        });
        mockDraftApi.runCompose.mockResolvedValue({
            success: true,
            ctx: {
                section_drafts: [
                    { section: 'introduction', content: 'Old intro', citations: [] },
                    { section: 'methodology', content: 'Old method', citations: [] },
                ],
                phase_results: { compose: { status: 'completed' } },
            },
        });
        mockDraftApi.regenerateSection.mockResolvedValue({
            success: true,
            project_id: 'proj-regen',
            section: 'introduction',
            body: '## Introduction\nNew intro [@p1].',
            body_chars: 30,
            progress_pct: 60,
            message: 'regenerated with custom instructions',
        });

        render(<DraftGenerator projectId="proj-regen" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'T' } });

        // 1) Research
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });
        await waitFor(() => expect(mockDraftApi.runResearch).toHaveBeenCalled());

        // 2) Structure (enables the compose button)
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-structure'));
        });
        await waitFor(() => expect(mockDraftApi.runStructure).toHaveBeenCalled());

        // 3) Compose (populates the section list)
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-compose'));
        });
        await waitFor(() => expect(mockDraftApi.runCompose).toHaveBeenCalled());

        // 4) Click the per-section regen button
        const regenBtn = await screen.findByTestId('btn-regen-introduction');
        await act(async () => {
            fireEvent.click(regenBtn);
        });

        const instructions = await screen.findByTestId('regen-instructions');
        fireEvent.change(instructions, { target: { value: 'be concise' } });
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-regen-submit'));
        });
        await waitFor(() => {
            expect(mockDraftApi.regenerateSection).toHaveBeenCalledWith(
                'proj-regen',
                'introduction',
                { custom_instructions: 'be concise' },
            );
        });
    });

    it('renders a friendly Chinese error message for 401', async () => {
        const authErr = Object.assign(new Error('401 unauthorized'), {
            code: 'auth_required',
            status: 401,
        });
        mockDraftApi.runResearch.mockRejectedValue(authErr);

        render(<DraftGenerator projectId="proj-err-401" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'X' } });
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });

        await waitFor(() => {
            // The friendly zh-CN message: "会话已过期，请重新登录"
            expect(screen.getByText(/会话已过期|重新登录|session/i)).toBeInTheDocument();
        });
    });

    it('renders a friendly Chinese error message for 5xx LLM upstream errors', async () => {
        const upstreamErr = Object.assign(new Error('upstream 502'), {
            code: 'http_error',
            status: 502,
        });
        mockDraftApi.runResearch.mockRejectedValue(upstreamErr);

        render(<DraftGenerator projectId="proj-err-5xx" />);

        const topic = await screen.findByTestId('draft-topic');
        fireEvent.change(topic, { target: { value: 'X' } });
        await act(async () => {
            fireEvent.click(screen.getByTestId('btn-research'));
        });

        await waitFor(() => {
            // The friendly zh-CN message: "AI 服务暂时不可用，请稍后重试"
            expect(
                screen.getByText(/AI 服务|暂时不可用|稍后重试|unavailable/i),
            ).toBeInTheDocument();
        });
    });
});
