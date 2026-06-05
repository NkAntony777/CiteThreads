/**
 * useAgentStream — React hook wrapping the agent SSE service.
 *
 * Manages:
 *  - the live text buffer (grows as ``text_delta`` events arrive)
 *  - the list of in-flight and completed tool calls
 *  - any paper suggestions surfaced mid-stream
 *  - error state and the final ``done`` state
 *  - an AbortController for cancellation
 *
 * Designed to be drop-in usable from a single chat panel component.
 */

import { useCallback, useRef, useState } from 'react';
import { streamAgentChat, type StreamHandle } from '../services/agentStream';
import type {
    AgentChatRequest,
    AgentDoneState,
    AgentToolCall,
    Paper,
} from '../types';

export interface UseAgentStreamState {
    /** Text accumulated so far (token by token). */
    text: string;
    /** Whether a turn is in flight. */
    running: boolean;
    /** Tool calls observed during the current turn. */
    toolCalls: AgentToolCall[];
    /** Paper suggestions surfaced mid-stream. */
    paperSuggestions: Paper[];
    /** Last error message, or null. */
    error: string | null;
    /** Final state once ``done`` arrives. */
    finalState: AgentDoneState | null;
    /** True if the server reported ``truncated: true``. */
    truncated: boolean;
    /** Number of LLM round-trips used. */
    iterations: number;
    /** Most recent keepalive ping timestamp. */
    lastPingAt: number | null;
}

export interface UseAgentStreamReturn extends UseAgentStreamState {
    start: (request: AgentChatRequest) => Promise<void>;
    cancel: () => void;
    reset: () => void;
}

const INITIAL_STATE: UseAgentStreamState = {
    text: '',
    running: false,
    toolCalls: [],
    paperSuggestions: [],
    error: null,
    finalState: null,
    truncated: false,
    iterations: 0,
    lastPingAt: null,
};

export function useAgentStream(): UseAgentStreamReturn {
    const [state, setState] = useState<UseAgentStreamState>(INITIAL_STATE);
    const handleRef = useRef<StreamHandle | null>(null);

    const reset = useCallback(() => {
        setState(INITIAL_STATE);
    }, []);

    const cancel = useCallback(() => {
        handleRef.current?.abort();
        handleRef.current = null;
        setState((s) => ({ ...s, running: false }));
    }, []);

    const start = useCallback(async (request: AgentChatRequest) => {
        // Cancel any in-flight stream first.
        handleRef.current?.abort();
        setState({ ...INITIAL_STATE, running: true });

        const handle = streamAgentChat(request, {
            onTextDelta: (delta) => {
                setState((s) => ({ ...s, text: s.text + delta }));
            },
            onToolStart: (tool) => {
                setState((s) => ({
                    ...s,
                    toolCalls: [
                        ...s.toolCalls,
                        {
                            tool,
                            arguments: {},
                            result_preview: '',
                            latency_ms: 0,
                            error: null,
                        },
                    ],
                }));
            },
            onToolEnd: (record) => {
                setState((s) => {
                    // Replace the last pending entry with the same
                    // tool_call_id (or the last appended) with the
                    // final record.
                    const idx = s.toolCalls.findIndex(
                        (tc, i) =>
                            (record.tool_call_id && tc.tool_call_id === record.tool_call_id) ||
                            i === s.toolCalls.length - 1,
                    );
                    const next = s.toolCalls.slice();
                    if (idx >= 0) next[idx] = record;
                    else next.push(record);
                    return { ...s, toolCalls: next };
                });
            },
            onPaperSuggestions: (papers) => {
                setState((s) => ({
                    ...s,
                    paperSuggestions: papers.slice(0, 5),
                }));
            },
            onError: (message) => {
                setState((s) => ({ ...s, error: message }));
            },
            onPing: () => {
                setState((s) => ({ ...s, lastPingAt: Date.now() }));
            },
            onDone: (final) => {
                setState((s) => ({
                    ...s,
                    running: false,
                    finalState: final,
                    iterations: final.iterations,
                    truncated: final.truncated,
                    // Done always carries the authoritative text; use it
                    // when the server reports a result that the per-token
                    // stream may have missed (e.g. legacy providers).
                    text: s.text || final.content,
                }));
            },
        });

        handleRef.current = handle;
        // Wait for the server to signal done; this resolves when the
        // SSE stream closes (normally or via abort).
        const final = await handle.done;
        if (final) {
            setState((s) => ({
                ...s,
                running: false,
                finalState: final,
                iterations: final.iterations,
                truncated: final.truncated,
            }));
        } else {
            setState((s) => ({ ...s, running: false }));
        }
    }, []);

    return { ...state, start, cancel, reset };
}
