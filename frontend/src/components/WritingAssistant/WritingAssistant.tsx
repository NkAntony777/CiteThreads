/**
 * WritingAssistant - AI Paper Writing Assistant Component
 * Combines literature review and AI writing capabilities
 */
import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Layout,
    Typography,
    Button,
    Space,
    Card,
    List,
    Input,
    Tabs,
    message,
    Spin,
    Empty,
    Modal,
    Select,
    Tooltip,
    Popconfirm,
    Switch
} from 'antd';
import {
    PlusOutlined,
    DeleteOutlined,
    SendOutlined,
    FileTextOutlined,
    SearchOutlined,
    DownloadOutlined,
    RobotOutlined,
    BookOutlined,
    ArrowLeftOutlined,
    CopyOutlined
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { writingApi } from '../../services/writingApi';
import CanvasEditor, { CanvasEditorHandle } from './CanvasEditor';
import FullscreenCanvas from './FullscreenCanvas';
import { PaperSearchPanel } from '../PaperSearchPanel';
import type { Paper, Reference } from '../../types';
import './WritingAssistant.css';

const { Header, Sider, Content } = Layout;
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

    // Literature Review state
    const [reviewContent, setReviewContent] = useState<string>('');
    const [generatingReview, setGeneratingReview] = useState(false);
    const [reviewStyle, setReviewStyle] = useState<string>('academic');

    // Chat state
    const [chatHistory, setChatHistory] = useState<ChatHistoryItem[]>([]);
    const [chatInput, setChatInput] = useState('');
    const [chatLoading, setChatLoading] = useState(false);
    const [paperSuggestions, setPaperSuggestions] = useState<Paper[]>([]);

    // Search state
    const [searchModalVisible, setSearchModalVisible] = useState(false);

    // Active tab
    const [activeTab, setActiveTab] = useState('review');

    // Direct to Canvas mode
    const [directToCanvas, setDirectToCanvas] = useState(false);

    // Fullscreen mode
    const [isFullscreen, setIsFullscreen] = useState(false);

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

    // Load initial data (References, Review, Chat)
    useEffect(() => {
        const loadData = async () => {
            setLoadingRefs(true);
            try {
                // Load references
                const refsData = await writingApi.getReferences(projectId);
                setReferences(refsData.references || []);

                // Load saved review
                const reviewData = await writingApi.getReview(projectId);
                if (reviewData.content) {
                    setReviewContent(reviewData.content);
                }

                // Load saved chat history
                const chatData = await writingApi.getChatHistory(projectId);
                if (chatData.history && chatData.history.length > 0) {
                    setChatHistory(chatData.history.map(h => ({
                        role: h.role as 'user' | 'assistant',
                        content: h.content
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
        }
    }, [projectId, t]);

    // Save Review Content (Debounced)
    useEffect(() => {
        const timer = setTimeout(() => {
            if (reviewContent && projectId) {
                writingApi.saveReview(projectId, reviewContent)
                    .catch(e => console.error('Failed to save review:', e));
            }
        }, 2000);

        return () => clearTimeout(timer);
    }, [reviewContent, projectId]);

    // Save Chat History
    useEffect(() => {
        if (chatHistory.length > 0 && projectId) {
            const historyToSave = chatHistory.map(h => ({
                role: h.role,
                content: h.content,
                timestamp: (h as any).timestamp || new Date().toISOString()
            }));

            // Cast to any to bypass strict type check for now, or we should update local state type
            writingApi.saveChatHistory(projectId, historyToSave as any[])
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

    const handleGenerateReview = async () => {
        if (references.length === 0) {
            message.warning(t('writingAssistant.pleaseAddRefs'));
            return;
        }

        setGeneratingReview(true);
        try {
            const result = await writingApi.generateReview(
                projectId,
                undefined,
                reviewStyle,
                true
            );
            if (result.success) {
                setReviewContent(result.review.content);
                message.success(t('writingAssistant.reviewGenerated'));
            }
        } catch (error: any) {
            message.error(error?.response?.data?.detail || t('writingAssistant.generateFailed'));
        } finally {
            setGeneratingReview(false);
        }
    };

    const handleChatSend = async () => {
        if (!chatInput.trim()) return;

        const userMessage = chatInput.trim();
        setChatInput('');
        setChatHistory(prev => [...prev, { role: 'user', content: userMessage }]);
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
                    content: assistantContent
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
                content: `${t('writingAssistant.sorryError')}${errorDetail}\n${t('writingAssistant.checkConsole')}`
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

            <Layout>
                {/* Left Sidebar - References */}
                <Sider width={300} className="ref-sider">
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
                                            </Popconfirm>
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

                    {/* Available papers from graph */}
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
                                        />
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
                </Sider>

                {/* Main Content */}
                <Content className="writing-content">
                    <Tabs activeKey={activeTab} onChange={setActiveTab}>
                        <TabPane
                            tab={<span><FileTextOutlined />{t('writingAssistant.literatureReview')}</span>}
                            key="review"
                        >
                            <div className="review-section">
                                <div className="review-controls">
                                    <Space>
                                        <Select
                                            value={reviewStyle}
                                            onChange={setReviewStyle}
                                            style={{ width: 120 }}
                                        >
                                            <Select.Option value="academic">{t('writingAssistant.academicStyle')}</Select.Option>
                                            <Select.Option value="concise">{t('writingAssistant.conciseStyle')}</Select.Option>
                                            <Select.Option value="detailed">{t('writingAssistant.detailedStyle')}</Select.Option>
                                        </Select>
                                        <Button
                                            type="primary"
                                            icon={<RobotOutlined />}
                                            loading={generatingReview}
                                            onClick={handleGenerateReview}
                                        >
                                            {t('writingAssistant.generateReview')}
                                        </Button>
                                    </Space>
                                </div>

                                <div className="review-content">
                                    {reviewContent ? (
                                        <Card className="markdown-card">
                                            <ReactMarkdown>{reviewContent}</ReactMarkdown>
                                        </Card>
                                    ) : (
                                        <Empty
                                            description={t('writingAssistant.addRefsThenGenerate')}
                                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                                        />
                                    )}
                                </div>
                            </div>
                        </TabPane>

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

                                {/* Paper suggestions */}
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
                                                        </Button>
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
                    </Tabs>
                </Content>

                {/* Right Canvas Editor */}
                <div className="canvas-panel">
                    <CanvasEditor
                        ref={canvasEditorRef}
                        projectId={projectId}
                        onFullscreen={() => setIsFullscreen(true)}
                    />
                </div>
            </Layout>

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
