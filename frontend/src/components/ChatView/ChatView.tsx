/**
 * ChatView — the new home of the application.
 *
 * 2026-06 refactor: replaces SmartSearchPanel + WritingAssistant +
 * AgentChatPanel + PaperSearchPanel with a single ChatGPT-style
 * surface. Left sider is the conversation list; the main area is
 * a scrolling message thread with a bottom input bar.
 *
 * Each assistant message is a composite bubble that may contain:
 *   - streamed text (markdown)
 *   - tool activity (collapsed, expandable)
 *   - paper suggestion chips
 *   - section draft blocks (CTDP output)
 *
 * Backend: the existing ``/api/agent/chat/stream`` endpoint drives
 * the stream. Persisted chat history comes from each project's
 * ``chat_history.json`` (the agent runtime writes it).
 */
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Empty, Spin, Tag, Typography, message } from 'antd';
import {
    DownOutlined,
    LoadingOutlined,
    ReloadOutlined,
    RobotOutlined,
    ThunderboltOutlined,
    UserOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { useChatStream } from '../../hooks/useChatStream';
import { useGraphStore } from '../../stores/graphStore';
import { chatApi } from '../../services/chatApi';
import { draftApi } from '../../services/draftApi';
import { ConversationList } from './ConversationList';
import { ChatInput } from './ChatInput';
import { PhaseProgressPanel, type PhaseProgress } from './PhaseProgressPanel';
import type { ChatMessage } from '../../types';
import './ChatView.css';

const { Text } = Typography;

export interface ChatViewProps {
    /** Called when the user toggles the conversation list (mobile). */
    onOpenSettings?: () => void;
    /** Called when the user wants to export the current conversation. */
    onExportCurrent?: () => void;
}

export const ChatView: React.FC<ChatViewProps> = ({
    onOpenSettings: _onOpenSettings,
    onExportCurrent: _onExportCurrent,
}) => {
    const { t } = useTranslation();
    const {
        currentProject,
        setProject,
        setProjectMetadata,
    } = useGraphStore();

    const {
        text,
        running,
        toolCalls,
        paperSuggestions,
        sectionDrafts,
        iterations,
        error,
        start,
        cancel,
        reset,
    } = useChatStream();

    const [conversations, setConversations] = useState<
        Array<{ id: string; name: string; updated_at: string; paper_count: number; section_draft_count: number; last_message_preview?: string }>
    >([]);
    const [loadingConvos, setLoadingConvos] = useState(false);
    const [creatingNew, setCreatingNew] = useState(false);
    /**
     * 2026-06: live CTDP phase progress, polled after each user
     * message + every 3s while a phase is running. Rendered as a
     * sticky "system" message at the bottom of the thread so the
     * user can see / retry / resume without leaving the chat.
     */
    const [phaseStatus, setPhaseStatus] = useState<PhaseProgress | null>(null);
    const [runningPhase, setRunningPhase] = useState<string | null>(null);
    const [phaseBusy, setPhaseBusy] = useState(false);
    const [lastPhaseError, setLastPhaseError] = useState<
        { phase: string; message: string } | null
    >(null);
    const messagesEndRef = useRef<HTMLDivElement | null>(null);

    // Load conversation list on mount and whenever the active
    // project changes (so the new conversation appears
    // immediately after the user sends the first message).
    useEffect(() => {
        void loadConversations();
    }, [currentProject?.metadata.id]);

    // Poll CTDP status whenever a project is loaded and a phase is
    // running. The poll lives only as long as a phase is in flight
    // so the chat doesn't spam /status in the background.
    useEffect(() => {
        if (!currentProject?.metadata.id) {
            setPhaseStatus(null);
            return;
        }
        const pid = currentProject.metadata.id;
        let stopped = false;
        const tick = async () => {
            if (stopped) return;
            try {
                const res = await draftApi.getStatus(pid);
                if (res.success && res.ctx) {
                    setPhaseStatus(res.ctx);
                }
            } catch {
                /* best-effort */
            }
        };
        void tick();
        if (!runningPhase) return;
        const id = setInterval(tick, 3000);
        return () => {
            stopped = true;
            clearInterval(id);
        };
    }, [currentProject?.metadata.id, runningPhase]);

    const handleRunPhase = async (phase: string) => {
        if (!currentProject?.metadata.id || phaseBusy) return;
        const pid = currentProject.metadata.id;
        setPhaseBusy(true);
        setRunningPhase(phase);
        setLastPhaseError(null);
        try {
            // The router auto-resumes from checkpoint when one
            // exists, so the same endpoint serves both Run and
            // Resume. The PhaseProgressPanel surfaces the
            // distinction via the "resumed" tag.
            if (phase === 'research') {
                await draftApi.runResearch(pid, {});
            } else if (phase === 'structure') {
                await draftApi.runStructure(pid);
            } else if (phase === 'compose') {
                await draftApi.runCompose(pid);
            } else if (phase === 'validate') {
                await draftApi.runValidate(pid);
            } else if (phase === 'compile') {
                await draftApi.runCompile(pid);
            }
            // Re-fetch status immediately so the card shows the
            // post-run snapshot.
            const res = await draftApi.getStatus(pid);
            if (res.success && res.ctx) {
                setPhaseStatus(res.ctx);
            }
        } catch (e: unknown) {
            const err = e as { message?: string };
            setLastPhaseError({ phase, message: err?.message || 'Phase failed' });
        } finally {
            setPhaseBusy(false);
            setRunningPhase(null);
        }
    };

    // Re-hydrate the active conversation's history into the store
    // so the message thread restores verbatim on refresh.
    useEffect(() => {
        const pid = currentProject?.metadata.id;
        if (!pid) return;
        if (currentProject.chat_history && currentProject.chat_history.length > 0) {
            // The store already carries the history; nothing to do.
            return;
        }
        // Fall back to fetching the full project on first mount.
        chatApi
            .getFull(pid)
            .then((full) => setProject(full))
            .catch((e) => console.error('Failed to load conversation:', e));
    }, [currentProject?.metadata.id, currentProject?.chat_history?.length, setProject]);

    // Auto-scroll the message thread as new content streams in.
    useEffect(() => {
        const el = messagesEndRef.current;
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }, [text, toolCalls.length, paperSuggestions.length, sectionDrafts.length]);

    const loadConversations = async () => {
        setLoadingConvos(true);
        try {
            const items = await chatApi.list();
            setConversations(items);
        } catch (e) {
            console.error('Failed to list conversations:', e);
        } finally {
            setLoadingConvos(false);
        }
    };

    const handleNewChat = async () => {
        if (creatingNew) return;
        setCreatingNew(true);
        try {
            const newProj = await chatApi.create();
            setProject(newProj);
            reset();
            await loadConversations();
        } catch (e) {
            message.error(t('chat.createFailed'));
        } finally {
            setCreatingNew(false);
        }
    };

    const handleSelectConversation = async (id: string) => {
        if (id === currentProject?.metadata.id) return;
        try {
            const full = await chatApi.getFull(id);
            setProject(full);
            reset();
        } catch (e) {
            message.error(t('chat.loadFailed'));
        }
    };

    const handleDeleteConversation = async (id: string) => {
        try {
            await chatApi.remove(id);
            if (id === currentProject?.metadata.id) {
                // Switch to the most recent remaining conversation, or
                // create a new one.
                const remaining = (await chatApi.list()).filter(c => c.id !== id);
                if (remaining.length > 0) {
                    const full = await chatApi.getFull(remaining[0].id);
                    setProject(full);
                } else {
                    const newProj = await chatApi.create();
                    setProject(newProj);
                }
            }
            await loadConversations();
        } catch (e) {
            message.error(t('chat.deleteFailed'));
        }
    };

    const handleRenameConversation = async (id: string, name: string) => {
        try {
            await chatApi.rename(id, name);
            if (id === currentProject?.metadata.id) {
                setProjectMetadata({ ...currentProject!.metadata, name });
            }
            await loadConversations();
        } catch (e) {
            message.error(t('chat.renameFailed'));
        }
    };

    const handleSubmit = async (text: string) => {
        if (!currentProject) {
            // Lazy create a project if the user clicked send before
            // hitting "+ New chat" (e.g. they sent from the empty
            // state).
            try {
                const newProj = await chatApi.create();
                setProject(newProj);
                await loadConversations();
                // Submit in the next tick so the project_id is set.
                setTimeout(() => {
                    void start({
                        message: text,
                        project_id: newProj.metadata.id,
                    });
                }, 0);
                return;
            } catch (e) {
                message.error(t('chat.createFailed'));
                return;
            }
        }
        if (!currentProject) {
            // The lazy-create branch above raced; nothing to send to.
            return;
        }
        await start({
            message: text,
            project_id: currentProject.metadata.id,
        });
    };

    // The "live" assistant message being streamed right now.
    const liveMessage: ChatMessage | null =
        running || text || toolCalls.length > 0 || paperSuggestions.length > 0 || sectionDrafts.length > 0
            ? {
                role: 'assistant',
                content: text,
                timestamp: new Date().toISOString(),
                tool_calls: toolCalls as unknown as Array<Record<string, unknown>>,
                paper_suggestions: paperSuggestions,
                section_drafts: sectionDrafts,
            }
            : null;

    // Persisted messages from the project history.
    const persistedMessages: ChatMessage[] = currentProject?.chat_history || [];

    const thread: ChatMessage[] = liveMessage
        ? [...persistedMessages, liveMessage]
        : persistedMessages;

    const projectName = currentProject?.metadata.name || t('chat.newChat');

    return (
        <div className="chat-view">
            <aside className="chat-view__sider">
                <ConversationList
                    conversations={conversations}
                    activeId={currentProject?.metadata.id}
                    loading={loadingConvos}
                    onSelect={handleSelectConversation}
                    onDelete={handleDeleteConversation}
                    onRename={handleRenameConversation}
                    onNew={handleNewChat}
                    creatingNew={creatingNew}
                />
            </aside>

            <main className="chat-view__main">
                <header className="chat-view__header">
                    <Space size={8} align="center">
                        <RobotOutlined style={{ color: '#D4AF37' }} />
                        <Text strong style={{ fontSize: 14 }}>
                            {projectName}
                        </Text>
                        {iterations > 0 && running && (
                            <Tag color="blue" icon={<LoadingOutlined />}>
                                {t('chat.iterTag', { n: iterations })}
                            </Tag>
                        )}
                    </Space>
                </header>

                <div className="chat-view__thread">
                    {thread.length === 0 ? (
                        <div className="chat-view__empty">
                            <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description={
                                    <Space direction="vertical" size={4}>
                                        <Text strong>{t('chat.emptyTitle')}</Text>
                                        <Text type="secondary">{t('chat.emptyHint')}</Text>
                                    </Space>
                                }
                            />
                            <div className="chat-view__suggestions">
                                {((t('chat.suggestions', { returnObjects: true }) as unknown) as string[]).map(
                                    (s: string) => (
                                        <Button
                                            key={s}
                                            size="small"
                                            icon={<ThunderboltOutlined />}
                                            onClick={() => void handleSubmit(s)}
                                        >
                                            {s}
                                        </Button>
                                    ),
                                )}
                            </div>
                        </div>
                    ) : (
                        <div className="chat-view__messages">
                            {thread.map((msg, idx) => (
                                <MessageBubble key={idx} message={msg} />
                            ))}
                            {/* CTDP progress panel — the user can see
                                / retry / resume every phase from
                                here without leaving the chat. */}
                            {currentProject && (phaseStatus || runningPhase) && (
                                <PhaseProgressPanel
                                    projectId={currentProject.metadata.id}
                                    status={phaseStatus}
                                    runningPhase={runningPhase}
                                    lastError={lastPhaseError}
                                    busy={phaseBusy}
                                    onRunPhase={handleRunPhase}
                                />
                            )}
                            {running && (
                                <div className="chat-view__typing">
                                    <Spin size="small" /> {t('chat.thinking')}
                                </div>
                            )}
                            <div ref={messagesEndRef} />
                        </div>
                    )}
                </div>

                {error && (
                    <div className="chat-view__error">
                        <Text type="danger">{error}</Text>
                        <Button
                            size="small"
                            icon={<ReloadOutlined />}
                            onClick={reset}
                        >
                            {t('chat.retry')}
                        </Button>
                    </div>
                )}

                <ChatInput
                    onSubmit={handleSubmit}
                    onCancel={cancel}
                    running={running}
                    disabled={!currentProject}
                />
            </main>
        </div>
    );
};

// Lightweight Space import workaround to avoid pulling all of antd here.
import { Space as _Space } from 'antd';
const Space = _Space;

interface MessageBubbleProps {
    message: ChatMessage;
}

const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
    const { t } = useTranslation();
    const isUser = message.role === 'user';

    return (
        <div className={`chat-bubble chat-bubble--${message.role}`}>
            <div className="chat-bubble__avatar">
                {isUser ? <UserOutlined /> : <RobotOutlined />}
            </div>
            <div className="chat-bubble__body">
                {isUser ? (
                    <div className="chat-bubble__user-text">{message.content}</div>
                ) : (
                    <AssistantBubbleBody message={message} t={t} />
                )}
            </div>
        </div>
    );
};

interface AssistantBubbleBodyProps {
    message: ChatMessage;
    t: (k: string, opts?: Record<string, unknown>) => string;
}

const AssistantBubbleBody: React.FC<AssistantBubbleBodyProps> = ({ message, t }) => {
    const hasTools = message.tool_calls && message.tool_calls.length > 0;
    const hasPapers = message.paper_suggestions && message.paper_suggestions.length > 0;
    const hasSections = message.section_drafts && message.section_drafts.length > 0;
    const hasContent = message.content && message.content.trim().length > 0;

    return (
        <div className="chat-bubble__assistant-body">
            {hasTools && (
                <details className="chat-bubble__tools">
                    <summary>
                        <DownOutlined /> {t('chat.toolActivity')} ({message.tool_calls!.length})
                    </summary>
                    <ul>
                        {message.tool_calls!.map((tc, i) => (
                            <li key={i}>
                                <Tag color="purple">{String(tc.tool ?? t('chat.tool'))}</Tag>
                                {tc.error ? (
                                    <Text type="danger" style={{ fontSize: 12 }}>
                                        {String(tc.error)}
                                    </Text>
                                ) : (
                                    <Text type="secondary" style={{ fontSize: 12 }}>
                                        {String(tc.result_preview ?? '')}
                                    </Text>
                                )}
                            </li>
                        ))}
                    </ul>
                </details>
            )}
            {hasContent && (
                <div className="chat-bubble__markdown">
                    <ReactMarkdown>{message.content}</ReactMarkdown>
                </div>
            )}
            {hasPapers && (
                <div className="chat-bubble__papers">
                    <Text type="secondary" style={{ fontSize: 12 }}>
                        {t('chat.suggestedPapers')} ({message.paper_suggestions!.length})
                    </Text>
                    <div className="chat-bubble__paper-chips">
                        {message.paper_suggestions!.map((p) => (
                            <Tag key={p.id ?? p.title}>{p.title}</Tag>
                        ))}
                    </div>
                </div>
            )}
            {hasSections &&
                message.section_drafts!.map((sd) => (
                    <SectionDraftBlock
                        key={sd.section}
                        section={sd.section}
                        content={sd.content}
                    />
                ))}
        </div>
    );
};

interface SectionDraftBlockProps {
    section: string;
    content: string;
}

const SectionDraftBlock: React.FC<SectionDraftBlockProps> = ({ section, content }) => {
    const { t } = useTranslation();
    const display = section.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    return (
        <div className="section-draft-block">
            <header className="section-draft-block__header">
                <Text strong style={{ fontSize: 13 }}>{display}</Text>
                <Space size={4}>
                    <Button
                        size="small"
                        type="text"
                        onClick={() => {
                            navigator.clipboard
                                .writeText(content)
                                .then(() => message.success(t('chat.copied')))
                                .catch(() => message.error(t('chat.copyFailed')));
                        }}
                    >
                        {t('chat.copy')}
                    </Button>
                </Space>
            </header>
            <div className="section-draft-block__body">
                <ReactMarkdown>{content}</ReactMarkdown>
            </div>
        </div>
    );
};

export default ChatView;
