/**
 * WritingAssistant - AI Paper Writing Assistant Component
 * Combines literature review and AI writing capabilities
 */
import React, { useState, useEffect, useRef } from 'react';
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
                message.error('加载数据失败');
            } finally {
                setLoadingRefs(false);
            }
        };

        if (projectId) {
            loadData();
        }
    }, [projectId]);

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
                message.success(`已添加: ${paper.title.slice(0, 30)}...`);
                loadReferences();
            } else {
                message.warning(result.message || '该文献已存在');
            }
        } catch (error) {
            message.error('添加失败');
        }
    };

    const handleRemoveReference = async (refId: string) => {
        try {
            await writingApi.removeReference(projectId, refId);
            message.success('已移除');
            loadReferences();
        } catch (error) {
            message.error('移除失败');
        }
    };

    const handleGenerateReview = async () => {
        if (references.length === 0) {
            message.warning('请先添加参考文献');
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
                message.success('文献综述生成成功');
            }
        } catch (error: any) {
            message.error(error?.response?.data?.detail || '生成失败，请检查AI配置');
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
                    throw new Error('AI 返回内容为空');
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
                || (typeof error === 'string' ? error : '未知错误');

            message.error('AI回复失败: ' + errorDetail);

            setChatHistory(prev => [...prev, {
                role: 'assistant',
                content: `抱歉，发生错误: ${errorDetail}。\n请检查控制台获取更多详细信息。`
            }]);
        } finally {
            setChatLoading(false);
        }
    };

    const handleAddFromSearch = async (paper: Paper) => {
        try {
            const result = await writingApi.addReferenceFromSearch(projectId, paper);
            if (result.success) {
                message.success('已添加到参考文献');
                loadReferences();
            }
        } catch (error) {
            message.error('添加失败');
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
                        返回图谱
                    </Button>
                    <Title level={4} style={{ margin: 0, color: '#fff' }}>
                        <RobotOutlined /> AI论文写作助手
                    </Title>
                </Space>
                <Space>
                    <Button
                        icon={<SearchOutlined />}
                        onClick={() => setSearchModalVisible(true)}
                    >
                        搜索论文
                    </Button>
                    <Button
                        icon={<DownloadOutlined />}
                        href={writingApi.exportBibtexUrl(projectId)}
                        target="_blank"
                    >
                        导出BibTeX
                    </Button>
                </Space>
            </Header>

            <Layout>
                {/* Left Sidebar - References */}
                <Sider width={300} className="ref-sider">
                    <div className="sider-header">
                        <Title level={5}>
                            <BookOutlined /> 参考文献 ({references.length})
                        </Title>
                    </div>

                    <div className="ref-list">
                        {loadingRefs ? (
                            <Spin tip="加载中..." />
                        ) : references.length === 0 ? (
                            <Empty
                                description="暂无参考文献"
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                            >
                                <Text type="secondary">
                                    点击下方论文添加到参考文献
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
                                                title="确定要移除这篇文献吗？"
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
                        <Title level={5}>图谱论文</Title>
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
                            tab={<span><FileTextOutlined />文献综述</span>}
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
                                            <Select.Option value="academic">学术风格</Select.Option>
                                            <Select.Option value="concise">简洁风格</Select.Option>
                                            <Select.Option value="detailed">详细风格</Select.Option>
                                        </Select>
                                        <Button
                                            type="primary"
                                            icon={<RobotOutlined />}
                                            loading={generatingReview}
                                            onClick={handleGenerateReview}
                                        >
                                            生成文献综述
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
                                            description="添加参考文献后点击生成"
                                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                                        />
                                    )}
                                </div>
                            </div>
                        </TabPane>

                        <TabPane
                            tab={<span><RobotOutlined />AI写作</span>}
                            key="writing"
                        >
                            <div className="chat-section">
                                <div className="chat-messages">
                                    {chatHistory.length === 0 ? (
                                        <Empty
                                            description={
                                                <div>
                                                    <p>开始与AI助手对话</p>
                                                    <p>你可以说："帮我找一下关于XXX的论文"</p>
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
                                                            <Tooltip title="插入到画布">
                                                                <Button
                                                                    type="text"
                                                                    size="small"
                                                                    icon={<CopyOutlined />}
                                                                    className="insert-to-canvas-btn"
                                                                    onClick={() => {
                                                                        if (canvasEditorRef.current) {
                                                                            canvasEditorRef.current.insertContent(msg.content);
                                                                        } else {
                                                                            message.warning('编辑器未连接');
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
                                            <Spin tip="AI思考中..." />
                                        </div>
                                    )}
                                </div>

                                {/* Paper suggestions */}
                                {paperSuggestions.length > 0 && (
                                    <div className="paper-suggestions">
                                        <Title level={5}>找到的论文：</Title>
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
                                                            添加
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
                                            直接写入画布
                                        </Text>
                                    </div>
                                    <Input.Group compact style={{ display: 'flex' }}>
                                        <TextArea
                                            value={chatInput}
                                            onChange={(e) => setChatInput(e.target.value)}
                                            placeholder="输入消息，例如：帮我写introduction部分"
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
                                            发送
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
                title="搜索论文"
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
                    placeholder="输入关键词搜索论文并添加到参考文献..."
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
