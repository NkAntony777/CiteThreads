/**
 * Agent SSE Stream Service
 * ========================
 *
 * Posts a JSON body to /api/agent/chat/stream and parses the
 * Server-Sent Events response incrementally. Designed to be
 * framework-agnostic: it takes a set of callback handlers and
 * dispatches each frame to the matching handler.
 *
 * The browser-native ``EventSource`` API doesn't support POST
 * bodies, so we use ``fetch`` + ``ReadableStream`` + ``TextDecoder``
 * to parse the chunked ``text/event-stream`` response.
 */

import type {
    AgentChatRequest,
    AgentEvent,
    AgentStreamHandlers,
    Paper,
    AgentToolCall,
    AgentDoneState,
} from '../types';

const STREAM_ENDPOINT = '/api/agent/chat/stream';

export interface StreamHandle {
    /** Cancel the in-flight request and any pending tool dispatches. */
    abort: () => void;
    /** Resolves when the server has sent the ``done`` (or error) frame. */
    done: Promise<AgentDoneState | null>;
}

/**
 * Open a streaming agent turn. Returns a handle with an ``abort()``
 * method and a ``done`` promise that resolves with the final state
 * (or null if the stream errored or was aborted).
 */
export function streamAgentChat(
    request: AgentChatRequest,
    handlers: AgentStreamHandlers = {},
    options: { endpoint?: string; signal?: AbortSignal } = {},
): StreamHandle {
    const endpoint = options.endpoint ?? STREAM_ENDPOINT;
    const controller = new AbortController();
    // Chain caller-provided abort signal
    if (options.signal) {
        if (options.signal.aborted) controller.abort();
        else options.signal.addEventListener('abort', () => controller.abort());
    }

    let resolveDone: (state: AgentDoneState | null) => void = () => undefined;
    const done = new Promise<AgentDoneState | null>((resolve) => {
        resolveDone = resolve;
    });

    void consume(endpoint, request, handlers, controller.signal, resolveDone);

    return {
        abort: () => controller.abort(),
        done,
    };
}

async function consume(
    endpoint: string,
    request: AgentChatRequest,
    handlers: AgentStreamHandlers,
    signal: AbortSignal,
    resolveDone: (state: AgentDoneState | null) => void,
): Promise<void> {
    let response: Response;
    try {
        response = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Accept: 'text/event-stream',
            },
            body: JSON.stringify(request),
            signal,
        });
    } catch (err) {
        if ((err as Error).name === 'AbortError') {
            resolveDone(null);
            return;
        }
        handlers.onError?.((err as Error).message ?? 'network error', 'network');
        resolveDone(null);
        return;
    }

    if (!response.ok || !response.body) {
        const text = await response.text().catch(() => '');
        handlers.onError?.(text || `HTTP ${response.status}`, `http_${response.status}`);
        resolveDone(null);
        return;
    }

    handlers.onOpen?.();

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let finalState: AgentDoneState | null = null;

    try {
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            // SSE frames are separated by a blank line (``\n\n``).
            let sep: number;
            while ((sep = buffer.indexOf('\n\n')) !== -1) {
                const rawFrame = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                const frame = parseFrame(rawFrame);
                if (!frame) continue;
                if (frame.kind === 'ping') {
                    handlers.onPing?.();
                } else if (frame.kind === 'data' && frame.event) {
                    dispatch(frame.event, handlers, (state) => {
                        finalState = state;
                    });
                }
            }
        }
    } catch (err) {
        if ((err as Error).name === 'AbortError') {
            resolveDone(null);
            return;
        }
        handlers.onError?.((err as Error).message ?? 'stream error', 'stream');
    } finally {
        try {
            reader.releaseLock();
        } catch {
            // ignore
        }
        resolveDone(finalState);
    }
}

interface ParsedFrame {
    kind: 'ping' | 'data' | 'empty';
    event?: AgentEvent;
}

function parseFrame(raw: string): ParsedFrame {
    const trimmed = raw.replace(/\r$/, '');
    if (trimmed === '' || trimmed.startsWith(':')) {
        // SSE comment (keepalive) or blank line. Distinguish by content.
        if (trimmed.startsWith(':')) {
            const text = trimmed.slice(1).trim();
            if (text === 'ping' || text === 'stream-open') {
                return { kind: 'ping' };
            }
        }
        return { kind: 'empty' };
    }

    // Only handle ``data:`` lines; ignore id/event/retry for now.
    const dataLines: string[] = [];
    for (const line of trimmed.split('\n')) {
        if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trimStart());
        }
    }
    if (dataLines.length === 0) return { kind: 'empty' };

    const raw2 = dataLines.join('\n');
    try {
        const obj = JSON.parse(raw2) as AgentEvent;
        return { kind: 'data', event: obj };
    } catch {
        return { kind: 'empty' };
    }
}

function dispatch(
    event: AgentEvent,
    handlers: AgentStreamHandlers,
    capture: (state: AgentDoneState) => void,
): void {
    const t = event.type;
    switch (t) {
        case 'text_delta': {
            const delta = typeof event.delta === 'string' ? event.delta : '';
            if (delta) handlers.onTextDelta?.(delta);
            return;
        }
        case 'tool_start': {
            const tool = String(event.tool ?? '');
            const args = (event.arguments as Record<string, unknown>) ?? {};
            const id = (event.tool_call_id as string | undefined) ?? undefined;
            handlers.onToolStart?.(tool, args, id);
            return;
        }
        case 'tool_end': {
            const record: AgentToolCall = {
                tool: String(event.tool ?? ''),
                arguments: (event.arguments as Record<string, unknown>) ?? {},
                result_preview: String(event.result_preview ?? ''),
                result_raw: (event.result_raw as string | undefined) ?? undefined,
                latency_ms: Number(event.latency_ms ?? 0),
                error: (event.error as string | null) ?? null,
                tool_call_id: (event.tool_call_id as string | undefined) ?? undefined,
            };
            handlers.onToolEnd?.(record);
            return;
        }
        case 'paper_suggestions': {
            const papers = (event.papers as Paper[] | undefined) ?? [];
            if (papers.length) handlers.onPaperSuggestions?.(papers);
            return;
        }
        case 'error': {
            const msg = String(event.message ?? 'unknown error');
            const code = (event.code as string | undefined) ?? undefined;
            handlers.onError?.(msg, code);
            return;
        }
        case 'done': {
            const state: AgentDoneState = {
                iterations: Number(event.iterations ?? 0),
                truncated: Boolean(event.truncated),
                content: String(event.content ?? ''),
                action_type: (event.action_type as string | null | undefined) ?? null,
                paper_suggestions: (event.paper_suggestions as Paper[] | undefined) ?? [],
                tool_calls: (event.tool_calls as AgentToolCall[] | undefined) ?? [],
                error: (event.error as string | null | undefined) ?? null,
            };
            capture(state);
            handlers.onDone?.(state);
            return;
        }
        default:
            return;
    }
}
