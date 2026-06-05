/**
 * Draft Pipeline API Service
 * ==========================
 *
 * TypeScript client for the ``/api/draft/projects/{id}/...`` router that
 * powers the long-form draft generator tab. The router contract is
 * described in the CTDP-08 task brief; until the backend lands this
 * client sends requests to those paths so the frontend can be wired
 * up in parallel.
 *
 * Endpoints:
 *   POST /api/draft/projects/{id}/research   → run research phase
 *   POST /api/draft/projects/{id}/structure  → run structure phase
 *   POST /api/draft/projects/{id}/compose    → run compose phase
 *   POST /api/draft/projects/{id}/compile    → run compile phase
 *   POST /api/draft/projects/{id}/sections/{section}/regenerate
 *                                            → re-craft one section
 *   POST /api/draft/projects/{id}/cancel     → request cancellation
 *   GET  /api/draft/projects/{id}/status     → poll per-phase status
 *
 * All endpoints are behind the bearer-auth middleware. If a token is
 * configured (via ``VITE_AUTH_TOKEN`` env var or the
 * ``citethreads-auth-token`` localStorage key), the client sends
 * ``Authorization: Bearer <token>`` on every request.
 */
import axios, { AxiosError, type AxiosInstance } from 'axios';
import type { Paper } from '../types';

// ============================================
// Auth helper
// ============================================

const TOKEN_STORAGE_KEY = 'citethreads-auth-token';

function resolveAuthToken(): string {
    // 1. Build-time env var. Highest priority because deployment pipelines
    //    inject the same value as CITETHREADS_AUTH_TOKEN on the server.
    const envToken = import.meta.env.VITE_AUTH_TOKEN;
    if (typeof envToken === 'string' && envToken.trim().length > 0) {
        return envToken.trim();
    }
    // 2. Runtime override via localStorage. Useful for development when
    //    the operator copies a freshly-rotated server token into DevTools.
    if (typeof window !== 'undefined' && window.localStorage) {
        const stored = window.localStorage.getItem(TOKEN_STORAGE_KEY);
        if (stored && stored.trim().length > 0) {
            return stored.trim();
        }
    }
    return '';
}

let cachedToken: string | null = null;

function getAuthToken(): string {
    if (cachedToken === null) {
        cachedToken = resolveAuthToken();
    }
    return cachedToken;
}

function clearCachedToken(): void {
    cachedToken = null;
}

if (typeof window !== 'undefined' && window.localStorage) {
    window.addEventListener('storage', (event) => {
        if (event.key === TOKEN_STORAGE_KEY) {
            clearCachedToken();
        }
    });
}

// ============================================
// Types — match the router contract in CTDP-08
// ============================================

export type CitationStyle = 'APA' | 'IEEE' | 'CHICAGO' | 'MLA';

export type PhaseStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';

export type PhaseName =
    | 'research'
    | 'structure'
    | 'compose'
    | 'validate'
    | 'compile'
    | 'export';

export interface PhaseResult {
    status: PhaseStatus;
    started_at?: string | null;
    finished_at?: string | null;
    error?: string | null;
    output_summary?: string | null;
    /** Free-form per-phase payload (matches backend's PhaseResult.extra) */
    extra?: Record<string, unknown>;
    /**
     * 2026-06: a checkpoint file exists for this phase. The FE
     * progress card uses this to render a "Resumable" hint and the
     * router to short-circuit via ``runner.resume_from``.
     */
    has_checkpoint?: boolean;
    /**
     * Compose only: names of the 6 sections that have a body
     * written. ``sections_total`` lists the canonical order
     * (intro / lit_review / methodology / results / discussion /
     * conclusion).
     */
    sections_done?: string[];
    sections_total?: string[];
}

export interface ResearchContext {
    candidate_papers: Paper[];
    paper_summaries: Array<{ paper_id: string; summary: string; key_findings: string[] }>;
    research_gaps: string[];
    phase_results: Partial<Record<PhaseName, PhaseResult>>;
}

export interface StructureContext {
    outline: string;
    formatted_outline: string;
    phase_results: Partial<Record<PhaseName, PhaseResult>>;
}

export interface SectionDraft {
    section: string;
    content: string;
    citations: string[];
}

export interface ComposeContext {
    section_drafts: SectionDraft[];
    phase_results: Partial<Record<PhaseName, PhaseResult>>;
}

export interface QualityHistoryEntry {
    iteration: number;
    overall: number;
    dimensions: Record<string, number>;
    decision: 'accept' | 'revise' | 'reject';
    notes?: string;
}

export interface CompileContext {
    final_draft: string;
    quality_history: QualityHistoryEntry[];
    phase_results: Partial<Record<PhaseName, PhaseResult>>;
}

export interface StatusContext {
    /** 0-100 progress across the 5 user-facing phases. */
    progress_pct: number;
    /**
     * 2026-06: per-phase results keyed by phase name. The shape
     * matches the new `/api/draft/projects/{id}/status` payload
     * (see backend app/services/draft_pipeline/runner.py
     * ``get_status``).
     */
    phases?: Partial<Record<PhaseName, PhaseResult>>;
    /** Flat list of which phases have a checkpoint on disk. */
    checkpoints?: Partial<Record<PhaseName, boolean>>;
    last_error?: string | null;
    project_id?: string;
    /** Kept for back-compat with the old shape used by DraftGenerator. */
    phase_results?: Partial<Record<PhaseName, PhaseResult>>;
    current_phase?: PhaseName | null;
    message?: string;
}

export interface DraftEnvelope<T> {
    success: boolean;
    ctx?: T;
    error?: string;
}

// ============================================
// Research request
// ============================================

export interface ResearchRequest {
    topic?: string;
    target_word_count?: number;
    citation_style?: CitationStyle;
    /** Optional explicit paper seed list; otherwise the pipeline searches. */
    seed_paper_ids?: string[];
    /** Optional language override. */
    language?: 'en' | 'zh';
}

// ============================================
// Per-section regenerate
// ============================================

export interface RegenerateRequest {
    /** Optional free-form guidance appended to the crafter prompt. */
    custom_instructions?: string;
    /** Optional explicit model override. */
    model?: string;
}

export interface RegenerateResponse {
    success: boolean;
    project_id: string;
    section: string;
    body: string;
    body_chars: number;
    progress_pct: number;
    message: string;
}

export interface CancelResponse {
    cancelled: boolean;
    project_id: string;
    already_running: boolean;
    message: string;
}

// ============================================
// HTTP client
// ============================================

const api: AxiosInstance = axios.create({
    baseURL: '/api',
    timeout: 600000, // 10 minutes — compose and compile can be slow
});

api.interceptors.request.use((config) => {
    const token = getAuthToken();
    if (token) {
        config.headers = config.headers ?? {};
        // Axios v1 typed headers allow string values; using a plain
        // object avoids the loose `AxiosHeaders` cast.
        (config.headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
    }
    return config;
});

// ============================================
// Error normalisation
// ============================================

export interface DraftApiError extends Error {
    status?: number;
    code?: string;
    detail?: unknown;
}

function normaliseError(err: unknown): DraftApiError {
    if (err instanceof AxiosError) {
        const status = err.response?.status;
        const data = err.response?.data as { detail?: unknown; error?: string } | undefined;
        const detail = data?.detail ?? data?.error;
        const message =
            (typeof detail === 'string' && detail) ||
            err.message ||
            'Request failed';
        const wrapped: DraftApiError = new Error(message);
        wrapped.status = status;
        wrapped.code = status === 401 ? 'auth_required' : status === 412 ? 'llm_key_missing' : 'http_error';
        wrapped.detail = detail;
        return wrapped;
    }
    if (err instanceof Error) {
        const wrapped: DraftApiError = new Error(err.message);
        wrapped.code = 'network_error';
        return wrapped;
    }
    const wrapped: DraftApiError = new Error('Unknown error');
    wrapped.code = 'unknown';
    return wrapped;
}

// ============================================
// Public API surface
// ============================================

export const draftApi = {
    /** Resolve the bearer token that will be sent (empty string if none). */
    getAuthToken,

    /** Run the research phase: scout + scribe + signal. */
    runResearch: async (
        projectId: string,
        request: ResearchRequest,
    ): Promise<DraftEnvelope<ResearchContext>> => {
        try {
            const response = await api.post<DraftEnvelope<ResearchContext>>(
                `/draft/projects/${encodeURIComponent(projectId)}/research`,
                request,
            );
            return response.data;
        } catch (err) {
            throw normaliseError(err);
        }
    },

    /** Run the structure phase: architect + formatter. */
    runStructure: async (
        projectId: string,
    ): Promise<DraftEnvelope<StructureContext>> => {
        try {
            const response = await api.post<DraftEnvelope<StructureContext>>(
                `/draft/projects/${encodeURIComponent(projectId)}/structure`,
            );
            return response.data;
        } catch (err) {
            throw normaliseError(err);
        }
    },

    /** Run the compose phase: crafter + refiner (long-running). */
    runCompose: async (
        projectId: string,
    ): Promise<DraftEnvelope<ComposeContext>> => {
        try {
            const response = await api.post<DraftEnvelope<ComposeContext>>(
                `/draft/projects/${encodeURIComponent(projectId)}/compose`,
            );
            return response.data;
        } catch (err) {
            throw normaliseError(err);
        }
    },

    /** Run the compile phase: compiler + abstract writer + final QA. */
    runCompile: async (
        projectId: string,
    ): Promise<DraftEnvelope<CompileContext>> => {
        try {
            const response = await api.post<DraftEnvelope<CompileContext>>(
                `/draft/projects/${encodeURIComponent(projectId)}/compile`,
            );
            return response.data;
        } catch (err) {
            throw normaliseError(err);
        }
    },

    /** Run the validate phase: referee + fact-check. */
    runValidate: async (
        projectId: string,
    ): Promise<DraftEnvelope<unknown>> => {
        try {
            const response = await api.post<DraftEnvelope<unknown>>(
                `/draft/projects/${encodeURIComponent(projectId)}/validate`,
            );
            return response.data;
        } catch (err) {
            throw normaliseError(err);
        }
    },

    /** Poll overall status and per-phase results. */
    getStatus: async (
        projectId: string,
    ): Promise<DraftEnvelope<StatusContext>> => {
        try {
            const response = await api.get<DraftEnvelope<StatusContext>>(
                `/draft/projects/${encodeURIComponent(projectId)}/status`,
            );
            return response.data;
        } catch (err) {
            throw normaliseError(err);
        }
    },

    /**
     * Re-craft a single named section in-place. The section's previous
     * draft is overwritten with the new body. Optional
     * ``custom_instructions`` are appended to the crafter prompt so the
     * user can steer the rewrite ("make it more technical", etc.).
     */
    regenerateSection: async (
        projectId: string,
        section: string,
        request: RegenerateRequest = {},
    ): Promise<RegenerateResponse> => {
        try {
            const response = await api.post<RegenerateResponse>(
                `/draft/projects/${encodeURIComponent(projectId)}/sections/${encodeURIComponent(section)}/regenerate`,
                request,
            );
            return response.data;
        } catch (err) {
            throw normaliseError(err);
        }
    },

    /**
     * Request cancellation of any in-flight draft phase. Idempotent:
     * returns 200 with ``cancelled: true`` even if nothing was running.
     * The actual stop happens at the next phase or sub-section
     * boundary in the runner.
     */
    cancelDraft: async (projectId: string): Promise<CancelResponse> => {
        try {
            const response = await api.post<CancelResponse>(
                `/draft/projects/${encodeURIComponent(projectId)}/cancel`,
            );
            return response.data;
        } catch (err) {
            throw normaliseError(err);
        }
    },
};

export default draftApi;
