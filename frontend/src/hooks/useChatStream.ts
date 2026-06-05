/**
 * useChatStream — React hook for the new chat-driven UI.
 *
 * Mirrors useAgentStream but adds section_draft tracking and a
 * stable identifier per assistant turn so the ChatView can append
 * the live streaming response to the persisted message list when
 * the turn ends.
 */
import { useCallback, useRef, useState } from 'react';
import { streamAgentChat, type StreamHandle } from '../services/agentStream';
import type {
    AgentChatRequest,
    AgentDoneState,
    AgentToolCall,
    Paper,
} from '../types';
import type { SectionDraft } from '../types';

export interface UseChatStreamState {
    text: string;
    running: boolean;
    toolCalls: AgentToolCall[];
    paperSuggestions: Paper[];
    sectionDrafts: SectionDraft[];
    error: string | null;
    finalState: AgentDoneState | null;
    iterations: number;
}

export interface UseChatStreamReturn extends UseChatStreamState {
    start: (request: AgentChatRequest) => Promise<void>;
    cancel: () => void;
    reset: () => void;
}

const INITIAL_STATE: UseChatStreamState = {
    text: '',
    running: false,
    toolCalls: [],
    paperSuggestions: [],
    sectionDrafts: [],
    error: null,
    finalState: null,
    iterations: 0,
};

export function useChatStream(): UseChatStreamReturn {
    const [state, setState] = useState<UseChatStreamState>(INITIAL_STATE);
    const handleRef = useRef<StreamHandle | null>(null);

    const reset = useCallback(() => {
        handleRef.current?.abort();
        handleRef.current = null;
        setState(INITIAL_STATE);
    }, []);

    const cancel = useCallback(() => {
        handleRef.current?.abort();
        handleRef.current = null;
        setState((s) => ({ ...s, running: false }));
    }, []);

    const start = useCallback(async (request: AgentChatRequest) => {
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
                setState((s) => {
                    const seen = new Set<string>();
                    for (const p of s.paperSuggestions) {
                        const key = String(p.id ?? p.doi ?? p.title ?? '');
                        if (key) seen.add(key);
                    }
                    const fresh = papers.filter((p) => {
                        const key = String(p.id ?? p.doi ?? p.title ?? '');
                        return !key || !seen.has(key);
                    });
                    return {
                        ...s,
                        paperSuggestions: [...s.paperSuggestions, ...fresh].slice(0, 10),
                    };
                });
            },
            onError: (messageText) => {
                setState((s) => ({ ...s, error: messageText }));
            },
            onDone: (final) => {
                setState((s) => ({
                    ...s,
                    running: false,
                    finalState: final,
                    iterations: final.iterations,
                    text: s.text || final.content,
                }));
            },
        });

        handleRef.current = handle;
        const final = await handle.done;
        if (final) {
            setState((s) => ({
                ...s,
                running: false,
                finalState: final,
                iterations: final.iterations,
            }));
        } else {
            setState((s) => ({ ...s, running: false }));
        }
    }, []);

    return { ...state, start, cancel, reset };
}
