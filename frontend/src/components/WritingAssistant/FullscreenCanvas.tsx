/**
 * FullscreenCanvas - Fullscreen writing mode with AI chat sidebar
 * Renders a fullscreen view with left 25% AI chat + right 75% editor
 */
import React, { useState, useEffect, useRef } from 'react';
import { Button, Input, Spin, Switch, Typography, message, Empty, Tooltip } from 'antd';
import {
    FullscreenExitOutlined,
    SendOutlined,
    RobotOutlined,
    CopyOutlined
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import Vditor from 'vditor';
import 'vditor/dist/index.css';
import { writingApi } from '../../services/writingApi';
import './CanvasEditor.css';

const { TextArea } = Input;
const { Text } = Typography;

interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
}

interface FullscreenCanvasProps {
    projectId: string;
    onExit: () => void;
    initialChatHistory?: ChatMessage[];
    onChatHistoryChange?: (history: ChatMessage[]) => void;
}

const FullscreenCanvas: React.FC<FullscreenCanvasProps> = ({
    projectId,
    onExit,
    initialChatHistory = [],
    onChatHistoryChange,
}) => {
    // Editor state
    const vditorRef = useRef<Vditor | null>(null);
    const editorContainerRef = useRef<HTMLDivElement>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);

    // Chat state
    const [chatHistory, setChatHistory] = useState<ChatMessage[]>(initialChatHistory);
    const [chatInput, setChatInput] = useState('');
    const [chatLoading, setChatLoading] = useState(false);
    const [directToCanvas, setDirectToCanvas] = useState(false);
    const chatMessagesRef = useRef<HTMLDivElement>(null);

    // Escape key to exit fullscreen
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                onExit();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [onExit]);

    // Initialize editor
    useEffect(() => {
        if (!editorContainerRef.current) return;

        const initEditor = async () => {
            let initialContent = '';

            try {
                const data = await writingApi.getCanvas(projectId);
                if (data.content) {
                    initialContent = data.content;
                }
            } catch (e) {
                const localContent = localStorage.getItem(`canvas_draft_${projectId}`);
                if (localContent) {
                    initialContent = localContent;
                }
            }

            vditorRef.current = new Vditor('fullscreen-vditor', {
                mode: 'ir',
                height: '100%',
                cache: { enable: false },
                placeholder: '在这里撰写你的论文...\n\n支持 Markdown 语法，使用 [@引用键] 格式添加引用。',
                toolbar: [
                    'headings', 'bold', 'italic', 'strike', '|',
                    'quote', 'list', 'ordered-list', 'check', '|',
                    'code', 'inline-code', 'link', '|',
                    'undo', 'redo',
                ],
                outline: { enable: false, position: 'right' },
                input: (value) => {
                    localStorage.setItem(`canvas_draft_${projectId}`, value);
                    if (saveTimeoutRef.current) {
                        clearTimeout(saveTimeoutRef.current);
                    }
                    saveTimeoutRef.current = setTimeout(() => {
                        handleAutoSave(value);
                    }, 2000);
                },
                after: () => {
                    vditorRef.current?.setValue(initialContent);
                    setLoading(false);
                },
            });
        };

        initEditor();

        return () => {
            if (saveTimeoutRef.current) {
                clearTimeout(saveTimeoutRef.current);
            }
            vditorRef.current?.destroy();
        };
    }, [projectId]);

    // Scroll to bottom when new messages arrive
    useEffect(() => {
        if (chatMessagesRef.current) {
            chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
        }
    }, [chatHistory]);

    const handleAutoSave = async (content: string) => {
        setSaving(true);
        try {
            await writingApi.saveCanvas(projectId, content);
        } catch (e) {
            console.error('Auto-save failed:', e);
        } finally {
            setSaving(false);
        }
    };

    const handleChatSend = async () => {
        if (!chatInput.trim()) return;

        const userMessage = chatInput.trim();
        setChatInput('');
        const newHistory = [...chatHistory, { role: 'user' as const, content: userMessage }];
        setChatHistory(newHistory);
        setChatLoading(true);

        try {
            const result = await writingApi.chat(
                projectId,
                userMessage,
                newHistory.map(h => ({ role: h.role, content: h.content }))
            );

            if (result.success) {
                const assistantContent = result.message?.content || '';
                if (!assistantContent) {
                    throw new Error('AI 返回内容为空');
                }

                const updatedHistory = [...newHistory, {
                    role: 'assistant' as const,
                    content: assistantContent
                }];
                setChatHistory(updatedHistory);
                onChatHistoryChange?.(updatedHistory);

                // Auto-insert to canvas if direct mode is enabled
                if (directToCanvas && vditorRef.current) {
                    vditorRef.current.insertValue(assistantContent);
                    message.success('已插入到画布');
                }
            }
        } catch (error: any) {
            const errorMsg = error?.response?.data?.detail || error?.message || '未知错误';
            message.error('AI回复失败: ' + errorMsg);
            setChatHistory([...newHistory, {
                role: 'assistant',
                content: `抱歉，发生错误: ${errorMsg}`
            }]);
        } finally {
            setChatLoading(false);
        }
    };

    const handleInsertToCanvas = (content: string) => {
        if (vditorRef.current) {
            vditorRef.current.insertValue(content);
            message.success('已插入到画布');
        }
    };

    return (
        <div className="fullscreen-container">
            {/* Left AI Chat Panel */}
            <div className="fullscreen-chat-panel">
                <div className="fullscreen-chat-header">
                    <RobotOutlined /> AI 写作助手
                </div>

                <div className="fullscreen-chat-messages" ref={chatMessagesRef}>
                    {chatHistory.length === 0 ? (
                        <Empty
                            description={
                                <div style={{ textAlign: 'center', color: '#999' }}>
                                    <p>与 AI 助手对话</p>
                                    <p style={{ fontSize: 12 }}>例如：帮我写 Introduction 部分</p>
                                </div>
                            }
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        />
                    ) : (
                        chatHistory.map((msg, idx) => (
                            <div
                                key={idx}
                                className={`fullscreen-chat-message ${msg.role}`}
                            >
                                {msg.role === 'assistant' ? (
                                    <div>
                                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                                        <Tooltip title="插入到画布">
                                            <Button
                                                type="text"
                                                size="small"
                                                icon={<CopyOutlined />}
                                                onClick={() => handleInsertToCanvas(msg.content)}
                                                style={{ marginTop: 4 }}
                                            >
                                                插入
                                            </Button>
                                        </Tooltip>
                                    </div>
                                ) : (
                                    <Text>{msg.content}</Text>
                                )}
                            </div>
                        ))
                    )}
                    {chatLoading && (
                        <div className="fullscreen-chat-message assistant">
                            <Spin size="small" /> <Text type="secondary">AI 思考中...</Text>
                        </div>
                    )}
                </div>

                <div className="fullscreen-chat-input">
                    <div className="fullscreen-direct-mode">
                        <Switch
                            size="small"
                            checked={directToCanvas}
                            onChange={setDirectToCanvas}
                        />
                        <Text type="secondary">直接写入画布</Text>
                    </div>
                    <div className="fullscreen-chat-input-row" style={{ marginTop: 8 }}>
                        <TextArea
                            value={chatInput}
                            onChange={(e) => setChatInput(e.target.value)}
                            placeholder="输入消息..."
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
                        />
                    </div>
                </div>
            </div>

            {/* Right Editor Panel */}
            <div className="fullscreen-editor-panel" ref={editorContainerRef}>
                <Button
                    className="fullscreen-exit-btn"
                    type="default"
                    icon={<FullscreenExitOutlined />}
                    onClick={onExit}
                >
                    退出全屏 (Esc)
                </Button>

                {loading && (
                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
                        <Spin tip="加载编辑器..." />
                    </div>
                )}
                <div id="fullscreen-vditor" style={{ display: loading ? 'none' : 'block', height: '100%' }} />

                {saving && (
                    <div style={{ position: 'absolute', bottom: 12, right: 12 }}>
                        <Spin size="small" /> <Text type="secondary" style={{ fontSize: 12 }}>保存中...</Text>
                    </div>
                )}
            </div>
        </div>
    );
};

export default FullscreenCanvas;
