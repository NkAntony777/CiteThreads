/**
 * AgentChatPanel — minimal UI that consumes the agent SSE stream.
 *
 * Renders:
 *  - a textarea + send button
 *  - a "live" text area where the assistant's answer streams in
 *    token by token
 *  - a small activity log of tool calls (with their preview / latency)
 *  - paper suggestion cards surfaced mid-stream
 *  - a "running" indicator + a stop button
 *
 * This is intentionally a self-contained demo panel so it can be
 * dropped into any route; it does not depend on the existing
 * WritingAssistant or graph store.
 */

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Button,
    Card,
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
    ApiOutlined,
    ToolOutlined,
} from '@ant-design/icons';
import { useAgentStream } from '../../hooks/useAgentStream';
import type { Paper } from '../../types';
import './AgentChatPanel.css';

const { Text } = Typography;
const { TextArea } = Input;

export interface AgentChatPanelProps {
    /** Default project id to scope agent memory to. */
    projectId?: string;
    /** Optional placeholder text in the input. */
    placeholder?: string;
    /**
     * Optional context hint forwarded to the agent as
     * ``extra_context`` in the SSE request body. Use it to tell the
     * agent about the surrounding surface (e.g. "Writing context —
     * ask for clarifications, alternatives, or expansions").
     */
    extraContext?: string;
}

export const AgentChatPanel: React.FC<AgentChatPanelProps> = ({
    projectId = 'demo',
    placeholder,
    extraContext,
}) => {
    const { t } = useTranslation();
    const resolvedPlaceholder =
        placeholder ?? t('agent.placeholder');
    const [input, setInput] = useState('');
    const {
        text,
        running,
        toolCalls,
        paperSuggestions,
        error,
        iterations,
        truncated,
        lastPingAt,
        start,
        cancel,
        reset,
    } = useAgentStream();

    const onSend = async () => {
        const message_ = input.trim();
        if (!message_ || running) return;
        setInput('');
        try {
            await start({
                message: message_,
                project_id: projectId,
                extra_context: extraContext,
            });
        } catch (err) {
            message.error((err as Error).message ?? 'Agent request failed');
        }
    };

    return (
        <Card
            className="agent-chat-panel"
            title={
                <Space>
                    <ApiOutlined />
                    <span>{t('agent.panelTitle')}</span>
                    {running && <Spin size="small" />}
                    {iterations > 0 && (
                        <Tag color="blue">iter {iterations}</Tag>
                    )}
                    {truncated && <Tag color="orange">truncated</Tag>}
                    {lastPingAt && (
                        <Tooltip title="Last keepalive ping from server">
                            <Tag color="green">keepalive</Tag>
                        </Tooltip>
                    )}
                </Space>
            }
            extra={
                <Space>
                    {running ? (
                        <Button
                            icon={<StopOutlined />}
                            onClick={cancel}
                            danger
                            size="small"
                        >
                            {t('agent.stop')}
                        </Button>
                    ) : (
                        <Button
                            icon={<ReloadOutlined />}
                            onClick={reset}
                            size="small"
                        >
                            {t('agent.reset')}
                        </Button>
                    )}
                </Space>
            }
        >
            <Space.Compact style={{ width: '100%' }}>
                <TextArea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder={resolvedPlaceholder}
                    autoSize={{ minRows: 1, maxRows: 4 }}
                    disabled={running}
                    onPressEnter={(e) => {
                        if (!e.shiftKey) {
                            e.preventDefault();
                            void onSend();
                        }
                    }}
                />
                <Button
                    type="primary"
                    icon={<SendOutlined />}
                    onClick={() => void onSend()}
                    loading={running}
                    disabled={!input.trim()}
                >
                    {t('agent.send')}
                </Button>
            </Space.Compact>

            {error && (
                <Text type="danger" className="agent-chat-error">
                    {error}
                </Text>
            )}

            <div className="agent-chat-output">
                {text ? (
                    <pre className="agent-chat-text">{text}</pre>
                ) : running ? (
                    <Text type="secondary">{t('agent.thinking')}</Text>
                ) : (
                    <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description={t('agent.noResponse')}
                    />
                )}
            </div>

            {paperSuggestions.length > 0 && (
                <div className="agent-chat-suggestions">
                    <Text strong>
                        <ToolOutlined /> {t('agent.suggestedPapers')}
                    </Text>
                    <List
                        size="small"
                        dataSource={paperSuggestions}
                        renderItem={(p: Paper) => (
                            <List.Item>
                                <List.Item.Meta
                                    title={p.title}
                                    description={
                                        <Space wrap>
                                            <Tag color="blue">{p.year ?? '—'}</Tag>
                                            <Text type="secondary">
                                                {(p.authors || []).slice(0, 3).join(', ')}
                                                {(p.authors?.length ?? 0) > 3 ? ' …' : ''}
                                            </Text>
                                            {p.citation_count != null && (
                                                <Tag>{p.citation_count} citations</Tag>
                                            )}
                                        </Space>
                                    }
                                />
                            </List.Item>
                        )}
                    />
                </div>
            )}

            {toolCalls.length > 0 && (
                <div className="agent-chat-tools">
                    <Text strong>
                        <ToolOutlined /> {t('agent.toolActivity')}
                    </Text>
                    <List
                        size="small"
                        dataSource={toolCalls}
                        renderItem={(tc) => (
                            <List.Item>
                                <Space>
                                    <Tag color="purple">{tc.tool}</Tag>
                                    <Text type="secondary">
                                        {tc.latency_ms}ms
                                    </Text>
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
                </div>
            )}
        </Card>
    );
};

export default AgentChatPanel;
