/**
 * SmartSearchPanel — natural-language paper recommendation UI.
 *
 * Wraps the same agent SSE runtime used by AgentChatPanel but is
 * dedicated to seed-paper discovery. Users type a one-sentence
 * description; the backend agent runs `search_papers` (and friends)
 * across the registered sources and surfaces 1-5 paper suggestion
 * cards that can be promoted to the graph builder flow.
 *
 * Stream endpoint: `POST /api/agent/search/stream`. The frame shape
 * matches `/api/agent/chat/stream` — see `services/agentStream.ts`.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Alert,
    Button,
    Card,
    Collapse,
    Empty,
    Input,
    List,
    Space,
    Spin,
    Tag,
    Tooltip,
    Typography,
    message,
} from 'antd';
import {
    SendOutlined,
    StopOutlined,
    ReloadOutlined,
    ThunderboltOutlined,
    ToolOutlined,
    PartitionOutlined,
} from '@ant-design/icons';
import { streamAgentChat, type StreamHandle } from '../../services/agentStream';
import type { AgentDoneState, AgentToolCall, Paper } from '../../types';
import './SmartSearchPanel.css';

const { Text } = Typography;
const { TextArea } = Input;

export const SMART_SEARCH_ENDPOINT = '/api/agent/search/stream';

export interface SmartSearchPanelProps {
    /** Fired when the user clicks "Use to build graph" on a suggestion. */
    onSelectForGraph?: (paper: Paper) => void;
    /** Override the placeholder string (otherwise the i18n default is used). */
    placeholder?: string;
}

interface PanelState {
    running: boolean;
    text: string;
    toolCalls: AgentToolCall[];
    paperSuggestions: Paper[];
    error: string | null;
    iterations: number;
    truncated: boolean;
}

const INITIAL_STATE: PanelState = {
    running: false,
    text: '',
    toolCalls: [],
    paperSuggestions: [],
    error: null,
    iterations: 0,
    truncated: false,
};

export const SmartSearchPanel: React.FC<SmartSearchPanelProps> = ({
    onSelectForGraph,
    placeholder,
}) => {
    const { t } = useTranslation();
    const [input, setInput] = useState('');
    const [state, setState] = useState<PanelState>(INITIAL_STATE);
    const handleRef = useRef<StreamHandle | null>(null);

    const reset = useCallback(() => {
        handleRef.current?.abort();
        handleRef.current = null;
        setState(INITIAL_STATE);
        setInput('');
    }, []);

    const cancel = useCallback(() => {
        handleRef.current?.abort();
        handleRef.current = null;
        setState((s) => ({ ...s, running: false }));
    }, []);

    const submit = useCallback(
        async (rawMessage: string) => {
            const message_ = rawMessage.trim();
            if (!message_ || state.running) return;

            // Cancel any in-flight stream before starting a new one.
            handleRef.current?.abort();
            setState({ ...INITIAL_STATE, running: true });

            const handle = streamAgentChat(
                {
                    message: message_,
                    extra_context: 'seed-paper-recommendation',
                },
                {
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
                                    (record.tool_call_id &&
                                        tc.tool_call_id === record.tool_call_id) ||
                                    i === s.toolCalls.length - 1,
                            );
                            const next = s.toolCalls.slice();
                            if (idx >= 0) next[idx] = record;
                            else next.push(record);
                            return { ...s, toolCalls: next };
                        });
                    },
                    onPaperSuggestions: (papers) => {
                        // The BE already dedupes by id/doi/title, so
                        // this is a pure append. Cap at 10 to match
                        // the runtime's MAX_SUGGESTIONS_PER_TURN.
                        setState((s) => {
                            const seen = new Set<string>();
                            for (const p of s.paperSuggestions) {
                                const key = String(p.id ?? p.doi ?? p.title ?? "");
                                if (key) seen.add(key);
                            }
                            const fresh = papers.filter((p) => {
                                const key = String(p.id ?? p.doi ?? p.title ?? "");
                                return !key || !seen.has(key);
                            });
                            const merged = [...s.paperSuggestions, ...fresh].slice(0, 10);
                            return { ...s, paperSuggestions: merged };
                        });
                    },
                    onError: (messageText) => {
                        setState((s) => ({ ...s, error: messageText }));
                    },
                    onDone: (final) => {
                        setState((s) => ({
                            ...s,
                            running: false,
                            iterations: final.iterations,
                            truncated: final.truncated,
                            text: s.text || final.content,
                        }));
                    },
                },
                { endpoint: SMART_SEARCH_ENDPOINT },
            );

            handleRef.current = handle;
            try {
                const final: AgentDoneState | null = await handle.done;
                if (final) {
                    setState((s) => ({
                        ...s,
                        running: false,
                        iterations: final.iterations,
                        truncated: final.truncated,
                    }));
                } else {
                    setState((s) => ({ ...s, running: false }));
                }
            } catch (err) {
                message.error((err as Error).message ?? t('smartSearch.errorGeneric'));
                setState((s) => ({ ...s, running: false }));
            }
        },
        [state.running, t],
    );

    useEffect(() => {
        return () => {
            handleRef.current?.abort();
            handleRef.current = null;
        };
    }, []);

    const onSend = () => {
        void submit(input);
    };

    const onExampleClick = (example: string) => {
        setInput(example);
    };

    const examples = (t('smartSearch.examples', { returnObjects: true }) as string[]) || [];
    const placeholderText = placeholder ?? t('smartSearch.placeholder');
    const hasResults = state.paperSuggestions.length > 0;
    const hasToolActivity = state.toolCalls.length > 0;
    const showNoResult =
        !state.running && !hasResults && state.toolCalls.length > 0;

    return (
        <Card
            className="smart-search-panel"
            size="small"
            title={
                <Space>
                    <ThunderboltOutlined />
                    <span>{t('smartSearch.title')}</span>
                    {state.running && <Spin size="small" />}
                    {state.iterations > 0 && (
                        <Tag color="blue">iter {state.iterations}</Tag>
                    )}
                </Space>
            }
            extra={
                <Tooltip title={t('smartSearch.reset')}>
                    <Button
                        icon={<ReloadOutlined />}
                        size="small"
                        onClick={reset}
                        disabled={state.running && !hasResults && !hasToolActivity}
                    />
                </Tooltip>
            }
        >
            <div className="smart-search-subtitle">{t('smartSearch.subtitle')}</div>

            <Space.Compact style={{ width: '100%' }}>
                <TextArea
                    className="smart-search-input"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder={placeholderText}
                    autoSize={{ minRows: 1, maxRows: 4 }}
                    disabled={state.running}
                    onPressEnter={(e) => {
                        if (!e.shiftKey) {
                            e.preventDefault();
                            onSend();
                        }
                    }}
                />
                {state.running ? (
                    <Button
                        type="primary"
                        danger
                        icon={<StopOutlined />}
                        onClick={cancel}
                    >
                        {t('smartSearch.stop')}
                    </Button>
                ) : (
                    <Button
                        type="primary"
                        icon={<SendOutlined />}
                        onClick={onSend}
                        disabled={!input.trim()}
                    >
                        {t('smartSearch.send')}
                    </Button>
                )}
            </Space.Compact>

            {examples.length > 0 && (
                <div className="smart-search-examples">
                    {examples.slice(0, 4).map((example) => (
                        <Tag
                            key={example}
                            className="smart-search-example-chip"
                            onClick={() => onExampleClick(example)}
                            data-testid="smart-search-example-chip"
                        >
                            {example}
                        </Tag>
                    ))}
                </div>
            )}

            {state.error && (
                <Alert
                    type="error"
                    showIcon
                    message={state.error}
                    className="smart-search-error"
                />
            )}

            {state.running && !state.text && !hasResults && (
                <div className="smart-search-thinking">
                    <Spin size="small" /> <Text type="secondary">{t('smartSearch.thinking')}</Text>
                </div>
            )}

            {state.text && (
                <pre className="smart-search-text">{state.text}</pre>
            )}

            {hasResults && (
                <div className="smart-search-suggestions">
                    <List
                        size="small"
                        dataSource={state.paperSuggestions}
                        renderItem={(p: Paper) => (
                            <List.Item
                                key={p.id}
                                className="smart-search-suggestion-item"
                                actions={[
                                    <Button
                                        key="use"
                                        type="primary"
                                        size="small"
                                        icon={<PartitionOutlined />}
                                        onClick={() => onSelectForGraph?.(p)}
                                        data-testid="smart-search-use-button"
                                    >
                                        {t('smartSearch.selectToBuild')}
                                    </Button>,
                                ]}
                            >
                                <List.Item.Meta
                                    title={<Text strong>{p.title}</Text>}
                                    description={
                                        <Space wrap size={4}>
                                            <Text type="secondary" className="smart-search-authors">
                                                {(p.authors || []).slice(0, 3).join(', ')}
                                                {(p.authors?.length ?? 0) > 3 ? ' …' : ''}
                                            </Text>
                                            {p.year != null && <Tag color="blue">{p.year}</Tag>}
                                            {p.citation_count != null && (
                                                <Tag color="green">
                                                    {t('paperSearch.cited')} {p.citation_count}
                                                </Tag>
                                            )}
                                        </Space>
                                    }
                                />
                            </List.Item>
                        )}
                    />
                </div>
            )}

            {showNoResult && (
                <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={t('smartSearch.noResult')}
                />
            )}

            {!state.running && !hasResults && !hasToolActivity && !state.text && (
                <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={t('smartSearch.empty')}
                />
            )}

            {hasToolActivity && (
                <Collapse
                    ghost
                    className="smart-search-tools"
                    items={[
                        {
                            key: 'tools',
                            label: (
                                <Space>
                                    <ToolOutlined />
                                    <Text strong>{t('smartSearch.toolActivity')}</Text>
                                    <Tag color="purple">{state.toolCalls.length}</Tag>
                                </Space>
                            ),
                            children: (
                                <List
                                    size="small"
                                    dataSource={state.toolCalls}
                                    renderItem={(tc) => (
                                        <List.Item>
                                            <Space>
                                                <Tag color="purple">{tc.tool}</Tag>
                                                <Text type="secondary">{tc.latency_ms}ms</Text>
                                                {tc.error ? (
                                                    <Text type="danger">{tc.error}</Text>
                                                ) : (
                                                    <Text
                                                        type="secondary"
                                                        ellipsis
                                                        style={{ maxWidth: 360 }}
                                                    >
                                                        {tc.result_preview}
                                                    </Text>
                                                )}
                                            </Space>
                                        </List.Item>
                                    )}
                                />
                            ),
                        },
                    ]}
                />
            )}
        </Card>
    );
};

export default SmartSearchPanel;
