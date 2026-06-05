/**
 * WritingAssistant - AI Paper Writing Assistant Component
 *
 * 2026-06 refactor: the standalone "Generate Literature Review"
 * tab was removed. The CTDP compose phase (POST /draft/.../compose)
 * owns the literature review step as part of the 6-section
 * long-form pipeline (introduction / literature_review / methodology
 * / results / discussion / conclusion). This component now exposes
 * three tabs in priority order:
 *   - references (left sider, auto-filled from project graph)
 *   - draft (CTDP composer)
 *   - ai chat (per-turn polish + agent chat)
 */
import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Layout,
    Typography,
    Button,
    Space,
    List,
    Input,
    Tabs,
    message,
    Spin,
    Empty,
    Modal,
    Tooltip,
    Popconfirm,
    Switch,
    Drawer,
} from 'antd';
import {
    PlusOutlined,
    DeleteOutlined,
    SendOutlined,
    SearchOutlined,
    DownloadOutlined,
    RobotOutlined,
    BookOutlined,
    ArrowLeftOutlined,
    CopyOutlined,
    RocketOutlined,
    ApiOutlined,
    CloseOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { writingApi } from '../../services/writingApi';
import CanvasEditor, { CanvasEditorHandle } from './CanvasEditor';
import FullscreenCanvas from './FullscreenCanvas';
import { PaperSearchPanel } from '../PaperSearchPanel';
import { DraftGenerator } from '../DraftGenerator';
import { AgentChatPanel } from '../AgentChatPanel';
import { ResizableShell } from './ResizableShell';
import type { ChatMessage, Paper, Reference } from '../../types';
import './WritingAssistant.css';

const { Header } = Layout;
const { Title, Text } = Typography;
const { TextArea } = Input;
const { TabPane } = Tabs;

interface WritingAssistantProps {
    projectId: string;
    graphNodes: Paper[];
    onBack: () => void;
    onSelectNode?: (paperId: string) => void;
}

interface ChatHistoryItem {
    role: 'user' | 'assistant';
    content: string;
    timestamp?: string;
}

const WritingAssistant: React.FC<WritingAssistantProps> = ({
    projectId,
    graphNodes,
    onBack,
}) => {
    const { t } = useTranslation();

    // References state
    const [references, setReferences] = useState<Reference[]>([]);
    const [loadingRefs, setLoadingRefs] = useState(false);

    // Chat state
    const [chatHistory, setChatHistory] = useState<ChatHistoryItem[]>([]);
    const [chatInput, setChatInput] = useState('');
    const [chatLoading, setChatLoading] = useState(false);
    const [paperSuggestions, setPaperSuggestions] = useState<Paper[]>([]);

    // Search state
    const [searchModalVisible, setSearchModalVisible] = useState(false);

    // Agent chat drawer state
    const [agentDrawerOpen, setAgentDrawerOpen] = useState(false);

    // Active tab. The refactor removed the literature-review tab, so
    // the natural first thing a writer sees is the references panel
    // (left sider) plus the draft (CTDP) tab. AI chat is the last
    // step (polish / clarify), so default to "draft".
    const [activeTab, setActiveTab] = useState('draft');

    // Direct to Canvas mode
    const [directToCanvas, setDirectToCanvas] = useState(false);

    // Fullscreen mode
    const [isFullscreen, setIsFullscreen] = useState(false);

    // Guard so we only auto-fill references from the citation
    // network once per project mount, not on every render.
    const autoFillDoneRef = useRef(false);

    // Canvas Editor Ref
    const canvasEditorRef = useRef<CanvasEditorHandle>(null);

    // Memoized pagination config to prevent stable reset
    const graphPagination = React.useMemo(() => {
        if (graphNodes.length <= 10) return false;
        return {
            pageSize: 10,
            size: 'small' as const,
            simple: true,
            position: 'top' as const,
            style: { textAlign: 'center' as const, margin: '8px 0' },
            showSizeChanger: false
        };
    }, [graphNodes.length]);

    // Load initial data (References, Chat)
    useEffect(() => {
        const loadData = async () => {
            setLoadingRefs(true);
            try {
                // Load references
                const refsData = await writingApi.getReferences(projectId);
                setReferences(refsData.references || []);

                // Load saved chat history
                const chatData = await writingApi.getChatHistory(projectId);
                if (chatData.history && chatData.history.length > 0) {
                    setChatHistory(chatData.history.map(h => ({
                        role: h.role === 'assistant' ? 'assistant' : 'user',
                        content: h.content,
                        timestamp: h.timestamp
                    })));
                }
            } catch (error) {
                console.error('Failed to load writing data:', error);
                message.error(t('writingAssistant.loadDataFailed'));
            } finally {
                setLoadingRefs(false);
            }
        };

        if (projectId) {
            loadData();
            // Reset the auto-fill guard so a freshly-loaded project
            // gets its hidden-context papers imported once.
            autoFillDoneRef.current = false;
        }
    }, [projectId, t]);

    // Auto-fill references from the project graph (the "hidden
    // context" the writing agent needs). Runs once per project:
    // adds every graphNode that isn't already a reference. We do
    // it client-side rather than server-side so a refresh doesn't
    // re-import. Failures on individual papers are logged and
    // skipped so one bad record can't block the rest.
    useEffect(() => {
        if (autoFillDoneRef.current) return;
        if (!projectId) return;
        if (graphNodes.length === 0) return;
        if (loadingRefs) return;  // wait for initial references load
        autoFillDoneRef.current = true;

        const existing = new Set(references.map(r => r.paper?.id).filter(Boolean));
        const toAdd = graphNodes.filter(p => p && p.id && !existing.has(p.id));
        if (toAdd.length === 0) return;

        (async () => {
            let added = 0;
            for (const paper of toAdd) {
                try {
                    const res = await writingApi.addReference(projectId, paper.id, 'graph');
                    if (res?.success) added += 1;
                } catch (e) {
                    console.error('Auto-add reference failed for', paper.id, e);
                }
            }
            if (added > 0) {
                message.success(t('writingAssistant.autoAddedRefs', { count: added }));
                loadReferences();
            }
        })();
    }, [projectId, graphNodes, loadingRefs, references, t]);

    // Save Chat History
    useEffect(() => {
        if (chatHistory.length > 0 && projectId) {
            const nowIso = new Date().toISOString();
            const historyToSave: ChatMessage[] = chatHistory.map((h) => ({
                role: h.role,
                content: h.content,
                timestamp: h.timestamp ?? nowIso,
            }));

            writingApi.saveChatHistory(projectId, historyToSave)
                .catch(e => console.error('Failed to save chat history:', e));
        }
    }, [chatHistory, projectId]);

    const loadReferences = async () => {
        setLoadingRefs(true);
        try {
            const data = await writingApi.getReferences(projectId);
            setReferences(data.references || []);
        } catch (error) {
            console.error('Failed to load references:', error);
        } finally {
            setLoadingRefs(false);
        }
    };

    const handleAddFromGraph = async (paper: Paper) => {
        try {
            const result = await writingApi.addReference(projectId, paper.id, 'graph');
            if (result.success) {
                message.success(`${t('writingAssistant.addedRef')} ${paper.title.slice(0, 30)}...`);
                loadReferences();
            } else {
                message.warning(result.message || t('writingAssistant.refExists'));
            }
        } catch (error) {
            message.error(t('writingAssistant.addFailed'));
        }
    };

    const handleRemoveReference = async (refId: string) => {
        try {
            await writingApi.removeReference(projectId, refId);
            message.success(t('writingAssistant.removed'));
            loadReferences();
        } catch (error) {
            message.error(t('writingAssistant.removeFailed'));
        }
    };

    const handleChatSend = async () => {
        if (!chatInput.trim()) return;

        const userMessage = chatInput.trim();
        const userTimestamp = new Date().toISOString();
        setChatInput('');
        setChatHistory(prev => [...prev, { role: 'user', content: userMessage, timestamp: userTimestamp }]);
        setChatLoading(true);

        try {
            const result = await writingApi.chat(
                projectId,
                userMessage,
                chatHistory.map(h => ({ role: h.role, content: h.content }))
            );

            if (result.success) {
                const assistantContent = result.message?.content || '';
                if (!assistantContent) {
                    throw new Error(t('writingAssistant.aiReturnEmpty'));
                }

                setChatHistory(prev => [...prev, {
                    role: 'assistant',
                    content: assistantContent,
                    timestamp: result.message?.timestamp || new Date().toISOString(),
                }]);

                // Handle paper suggestions
                const suggestions =
                    (result.message as any).paperSuggestions ||
                    (result.message as any).paper_suggestions;
                if (suggestions) {
                    setPaperSuggestions(suggestions);
                }

                // Auto-insert to canvas if direct mode is enabled
                if (directToCanvas && canvasEditorRef.current) {
                    canvasEditorRef.current.insertContent(assistantContent);
                }
            }
        } catch (error: any) {
            console.error('Chat error details:', error);
            const errorDetail = error?.response?.data?.detail
                || error?.message
                || (typeof error === 'string' ? error : t('writingAssistant.unknownError'));

            message.error(t('writingAssistant.aiReplyFailed') + errorDetail);

            setChatHistory(prev => [...prev, {
                role: 'assistant',
                content: `${t('writingAssistant.sorryError')}${errorDetail}\n${t('writingAssistant.checkConsole')}`,
                timestamp: new Date().toISOString(),
            }]);
        } finally {
            setChatLoading(false);
        }
    };

    const handleAddFromSearch = async (paper: Paper) => {
        try {
            const result = await writingApi.addReferenceFromSearch(projectId, paper);
            if (result.success) {
                message.success(t('writingAssistant.addedToRefs'));
                loadReferences();
            }
        } catch (error) {
            message.error(t('writingAssistant.addFailed'));
        }
    };

    return (
        <Layout className="writing-assistant">
            {/* Header */}
            <Header className="writing-header">
                <Space>
                    <Button
                        icon={<ArrowLeftOutlined />}
                        onClick={onBack}
                    >
                        {t('writingAssistant.backToGraph')}
                    </Button>
                    <Title level={4} style={{ margin: 0, color: '#fff' }}>
                        <RobotOutlined /> {t('app.aiWritingAssistant')}
                    </Title>
                </Space>
                <Space>
                    <Button
                        icon={<ApiOutlined />}
                        onClick={() => setAgentDrawerOpen(true)}
                    >
                        {t('agent.askAgent')}
                    </Button>
                    <Button
                        icon={<SearchOutlined />}
                        onClick={() => setSearchModalVisible(true)}
                    >
                        {t('writingAssistant.searchPapers')}
                    </Button>
                    <Button
                        icon={<DownloadOutlined />}
                        href={writingApi.exportBibtexUrl(projectId)}
                        target="_blank"
                    >
                        {t('writingAssistant.exportBibtex')}
                    </Button>
                </Space>
            </Header>

            {/*
             * 3-panel resizable shell. The right (canvas) side is
             * collapsed by default — the writer gets the full main
             * column until they explicitly pull the canvas out.
             */}
            <ResizableShell
                leftLabel="References"
                rightLabel="Canvas"
                left={
                    <div className="ref-panel">
                        <div className="sider-header">
                            <Title level={5}>
                                <BookOutlined /> {t('writingAssistant.references')} ({references.length})
                            </Title>
                        </div>

                        <div className="ref-list">
                            {loadingRefs ? (
                                <Spin tip={t('common.loading')} />
                            ) : references.length === 0 ? (
                                <Empty
                                    description={t('writingAssistant.noReferences')}
                                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                                >
                                    <Text type="secondary">
                                        {t('writingAssistant.clickToAddRef')}
                                    </Text>
                                </Empty>
                            ) : (
                                <List
                                    size="small"
                                    dataSource={references}
                                    renderItem={(ref: Reference) => (
                                        <List.Item
                                            actions={[
                                                <Popconfirm
                                                    title={t('writingAssistant.removeRefConfirm')}
                                                    onConfirm={() => handleRemoveReference(ref.id)}
                                                >
                                                    <Button
                                                        type="text"
                                                        danger
                                                        size="small"
                                                        icon={<DeleteOutlined />}
                                                    />
                                                </Popconfirm>,
                                            ]}
                                        >
                                            <List.Item.Meta
                                                title={
                                                    <Tooltip title={ref.paper.title}>
                                                        <Text ellipsis style={{ maxWidth: 180 }}>
                                                            [{ref.citationKey}] {ref.paper.title}
                                                        </Text>
                                                    </Tooltip>
                                                }
                                                description={
                                                    <Text type="secondary" style={{ fontSize: 12 }}>
                                                        {ref.paper.year} · {ref.source}
                                                    </Text>
                                                }
                                            />
                                        </List.Item>
                                    )}
                                />
                            )}
                        </div>

                        <div className="graph-papers">
                            <Title level={5}>{t('writingAssistant.graphPapers')}</Title>
                            <List
                                size="small"
                                dataSource={graphNodes}
                                pagination={graphPagination}
                                renderItem={(paper: Paper) => (
                                    <List.Item
                                        actions={[
                                            <Button
                                                type="text"
                                                size="small"
                                                icon={<PlusOutlined />}
                                                onClick={() => handleAddFromGraph(paper)}
                                            />,
                                        ]}
                                    >
                                        <Tooltip title={paper.title}>
                                            <Text ellipsis style={{ maxWidth: 200 }}>
                                                {paper.title}
                                            </Text>
                                        </Tooltip>
                                    </List.Item>
                                )}
                            />
                        </div>
                    </div>
                }
                center={
                    <Tabs activeKey={activeTab} onChange={setActiveTab} className="writing-tabs">
                        <TabPane
                            tab={<span><RobotOutlined />{t('writingAssistant.aiWriting')}</span>}
                            key="writing"
                        >
                            <div className="chat-section">
                                <div className="chat-messages">
                                    {chatHistory.length === 0 ? (
                                        <Empty
                                            description={
                                                <div>
                                                    <p>{t('writingAssistant.startChatWithAi')}</p>
                                                    <p>{t('writingAssistant.chatExample')}</p>
                                                </div>
                                            }
                                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                                        />
                                    ) : (
                                        chatHistory.map((msg, idx) => (
                                            <div
                                                key={idx}
                                                className={`chat-message ${msg.role}`}
                                            >
                                                <div className="message-content">
                                                    {msg.role === 'assistant' ? (
                                                        <>
                                                            <ReactMarkdown>{msg.content}</ReactMarkdown>
                                                            <Tooltip title={t('writingAssistant.insertToCanvas')}>
                                                                <Button
                                                                    type="text"
                                                                    size="small"
                                                                    icon={<CopyOutlined />}
                                                                    className="insert-to-canvas-btn"
                                                                    onClick={() => {
                                                                        if (canvasEditorRef.current) {
                                                                            canvasEditorRef.current.insertContent(msg.content);
                                                                        } else {
                                                                            message.warning(t('writingAssistant.editorNotConnected'));
                                                                        }
                                                                    }}
                                                                />
                                                            </Tooltip>
                                                        </>
                                                    ) : (
                                                        <Text>{msg.content}</Text>
                                                    )}
                                                </div>
                                            </div>
                                        ))
                                    )}
                                    {chatLoading && (
                                        <div className="chat-message assistant loading">
                                            <Spin tip={t('writingAssistant.aiThinking')} />
                                        </div>
                                    )}
                                </div>

                                {paperSuggestions.length > 0 && (
                                    <div className="paper-suggestions">
                                        <Title level={5}>{t('writingAssistant.foundPapers')}</Title>
                                        <List
                                            size="small"
                                            dataSource={paperSuggestions}
                                            renderItem={(paper) => (
                                                <List.Item
                                                    actions={[
                                                        <Button
                                                            size="small"
                                                            onClick={() => handleAddFromSearch(paper)}
                                                        >
                                                            {t('common.add')}
                                                        </Button>,
                                                    ]}
                                                >
                                                    <List.Item.Meta
                                                        title={paper.title}
                                                        description={`${paper.authors?.join(', ')} (${paper.year})`}
                                                    />
                                                </List.Item>
                                            )}
                                        />
                                    </div>
                                )}

                                <div className="chat-input">
                                    <div className="direct-mode-toggle">
                                        <Switch
                                            size="small"
                                            checked={directToCanvas}
                                            onChange={setDirectToCanvas}
                                        />
                                        <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                                            {t('writingAssistant.directToCanvas')}
                                        </Text>
                                    </div>
                                    <Input.Group compact style={{ display: 'flex' }}>
                                        <TextArea
                                            value={chatInput}
                                            onChange={(e) => setChatInput(e.target.value)}
                                            placeholder={t('writingAssistant.chatPlaceholder')}
                                            autoSize={{ minRows: 1, maxRows: 4 }}
                                            onPressEnter={(e) => {
                                                if (!e.shiftKey) {
                                                    e.preventDefault();
                                                    handleChatSend();
                                                }
                                            }}
                                            style={{ flex: 1 }}
                                        />
                                        <Button
                                            type="primary"
                                            icon={<SendOutlined />}
                                            onClick={handleChatSend}
                                            loading={chatLoading}
                                        >
                                            {t('common.send')}
                                        </Button>
                                    </Input.Group>
                                </div>
                            </div>
                        </TabPane>

                        <TabPane
                            tab={<span><RocketOutlined />{t('draftGenerator.tabLabel')}</span>}
                            key="draft"
                        >
                            <DraftGenerator projectId={projectId} />
                        </TabPane>
                    </Tabs>
                }
                right={
                    <CanvasEditor
                        ref={canvasEditorRef}
                        projectId={projectId}
                        onFullscreen={() => setIsFullscreen(true)}
                    />
                }
            />

            {/* Search Modal */}
            <Modal
                title={t('writingAssistant.searchPapersModal')}
                open={searchModalVisible}
                onCancel={() => setSearchModalVisible(false)}
                footer={null}
                width={700}
            >
                <PaperSearchPanel
                    mode="reference-adder"
                    projectId={projectId}
                    onAddReference={handleAddFromSearch}
                    limit={15}
                />
            </Modal>

            {/* Agent Chat Drawer */}
            <Drawer
                title={
                    <Space>
                        <ApiOutlined />
                        {t('agent.panelTitle')}
                    </Space>
                }
                placement="right"
                width={520}
                open={agentDrawerOpen}
                onClose={() => setAgentDrawerOpen(false)}
                destroyOnClose
                extra={
                    <Button
                        type="text"
                        icon={<CloseOutlined />}
                        onClick={() => setAgentDrawerOpen(false)}
                    />
                }
            >
                <AgentChatPanel
                    projectId={projectId}
                    extraContext={t('agent.writingContextHint')}
                />
            </Drawer>

            {/* Fullscreen Canvas with AI Chat */}
            {isFullscreen && (
                <FullscreenCanvas
                    projectId={projectId}
                    onExit={() => setIsFullscreen(false)}
                    initialChatHistory={chatHistory}
                    onChatHistoryChange={setChatHistory}
                />
            )}
        </Layout>
    );
};

export default WritingAssistant;
