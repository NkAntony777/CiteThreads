/**
 * CiteThreads - TypeScript Type Definitions
 */

// Citation intent types/enums
export type CitationIntent = 'SUPPORT' | 'OPPOSE' | 'NEUTRAL' | 'UNKNOWN';
export type CitationFunction = 'BACKGROUND' | 'METHODOLOGY' | 'COMPARISON' | 'CRITIQUE' | 'BASIS' | 'UNKNOWN';
export type CitationSentiment = 'POSITIVE' | 'NEUTRAL' | 'NEGATIVE' | 'UNKNOWN';

// Paper/Publication model
export interface Paper {
    id: string;
    doi?: string;
    arxiv_id?: string;
    title: string;
    authors: string[];
    year?: number;
    venue?: string;
    abstract?: string;
    citation_count: number;
    reference_count: number;
    fields: string[];
    url?: string;
}

// Citation edge between papers
export interface CitationEdge {
    source: string;
    target: string;
    intent: CitationIntent;
    confidence: number;
    context?: string; // Legacy
    citation_contexts?: string[]; // Actual context list from backend
    reasoning?: string;

    // Deep Analysis Fields
    citation_function?: CitationFunction;
    citation_sentiment?: CitationSentiment;
    importance_score?: number;
    key_concept?: string;
}

// Graph data
export interface GraphData {
    nodes: Paper[];
    edges: CitationEdge[];
}

// Graph statistics
export interface GraphStats {
    total_nodes: number;
    total_edges: number;
    year_range?: [number, number];
}

// Project configuration
export interface ProjectConfig {
    seed_paper_id: string;
    depth: number;
    direction: 'forward' | 'backward' | 'both';
}

// Project metadata
export interface ProjectMetadata {
    id: string;
    name: string;
    created_at: string;
    updated_at: string;
    config: ProjectConfig;
    status: 'created' | 'crawling' | 'analyzing' | 'completed' | 'failed';
    stats?: GraphStats;
}

// Full project response
export interface Project {
    metadata: ProjectMetadata;
    graph: GraphData;
    // 2026-06: each project is also a chat conversation. The list
    // of ChatMessage turns the agent runtime writes. Defaults to
    // empty for projects created before the chat feature.
    chat_history?: ChatMessage[];
}

// Crawl progress
export interface CrawlProgress {
    status: string;
    progress: number;
    total: number;
    message: string;
    current_paper?: string;
}

// Paper search request/response
export interface PaperSearchRequest {
    query: string;
    query_type?: 'auto' | 'doi' | 'arxiv' | 'title';
    limit?: number;
}

export interface PaperSearchResponse {
    papers: Paper[];
    total: number;
}

// Create project request
export interface CreateProjectRequest {
    seed_paper_id: string;
    name?: string;
    depth?: number;
    direction?: 'forward' | 'backward' | 'both';
    max_papers?: number;
}

// Node with layout position (for visualization)
export interface GraphNode extends Paper {
    x: number;
    y: number;
    size: number;
}

// Edge with layout (for visualization)
export interface GraphLink extends CitationEdge {
    sourceNode?: GraphNode;
    targetNode?: GraphNode;
}

// Cluster information
export interface ClusterInfo {
    id: number;
    name: string;
    description: string;
    paper_ids: string[];
    keywords: string[];
    color: string;
    key_innovation?: string; // New generative field
}

// Clustering result
export interface ClusteringResult {
    success: boolean;
    message: string;
    clusters: ClusterInfo[];
    paper_clusters: Record<string, number>;  // paper_id -> cluster_id
}

// ============================================
// AI Writing Feature Types
// ============================================

// Reference source
export type ReferenceSource = 'graph' | 'search' | 'upload';

// Reference entry
export interface Reference {
    id: string;
    paper: Paper;
    citationKey: string;
    addedAt: string;
    source: ReferenceSource;
    notes?: string;
}

// Writing context
export interface WritingContext {
    projectId: string;
    literatureReview?: string;
    currentDocument: string;
    references: Reference[];
    topic?: string;
}

// Chat message
export interface ChatMessage {
    role: 'user' | 'assistant' | 'system';
    content: string;
    timestamp: string;
    // Legacy fields kept for back-compat with the writing chat
    // tab. New code reads the snake_case variants.
    paperSuggestions?: Paper[];
    actionType?: string;
    // 2026-06: structured surfaces the agent runtime attaches to
    // each assistant turn.
    tool_calls?: Array<Record<string, unknown>>;
    paper_suggestions?: Paper[];
    section_drafts?: SectionDraft[];
}

/** A single section produced by CTDP compose. */
export interface SectionDraft {
    section: string;
    content: string;
    citations?: string[];
}

/** Lightweight summary used by the conversation sider. */
export interface ConversationSummary {
    id: string;
    name: string;
    created_at: string;
    updated_at: string;
    status: string;
    paper_count: number;
    section_draft_count: number;
    last_message_preview?: string;
}

// Literature review draft
export interface LiteratureReviewDraft {
    content: string;
    style: string;
    generatedAt: string;
    referenceCount: number;
}

// Search result for writing
export interface WritingSearchResult {
    papers: Paper[];
    total: number;
    sourcesSearched: string[];
    errors: Record<string, string>;
}

// ============ Agent Runtime Types ============

/**
 * Discriminator for the SSE events emitted by /api/agent/chat/stream.
 * Mirrors the backend EVT_* constants in
 * ``backend/app/agent_runtime/runtime.py``.
 */
export type AgentEventType =
    | 'text_delta'
    | 'tool_start'
    | 'tool_end'
    | 'paper_suggestions'
    | 'error'
    | 'done';

export interface AgentToolCall {
    tool: string;
    arguments: Record<string, unknown>;
    result_preview: string;
    result_raw?: string;
    latency_ms: number;
    error: string | null;
    tool_call_id?: string | null;
}

export interface AgentEvent {
    type: AgentEventType;
    /** Per-event payload. See the AgentEventType union for details. */
    [key: string]: unknown;
}

export interface AgentChatRequest {
    message: string;
    project_id?: string;
    history?: Array<{ role: string; content: string }>;
    extra_context?: string;
}

/**
 * Handler interface for the agent SSE stream. Each callback is
 * optional; the runtime never throws if a handler is missing.
 */
export interface AgentStreamHandlers {
    onOpen?: () => void;
    onTextDelta?: (delta: string) => void;
    onToolStart?: (tool: string, args: Record<string, unknown>, toolCallId?: string) => void;
    onToolEnd?: (record: AgentToolCall) => void;
    onPaperSuggestions?: (papers: Paper[]) => void;
    onError?: (message: string, code?: string) => void;
    onDone?: (state: AgentDoneState) => void;
    onPing?: () => void;
}

export interface AgentDoneState {
    iterations: number;
    truncated: boolean;
    content: string;
    action_type?: string | null;
    paper_suggestions: Paper[];
    tool_calls: AgentToolCall[];
    error: string | null;
}

