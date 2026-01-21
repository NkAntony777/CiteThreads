/**
 * CanvasEditor - Markdown editor with AI interaction
 * Uses Vditor in instant rendering mode
 */
import React, { useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react';
import { Button, Tooltip, message, Spin, Space } from 'antd';
import { SaveOutlined, ThunderboltOutlined, EditOutlined, ExportOutlined, ExpandOutlined } from '@ant-design/icons';
import Vditor from 'vditor';
import 'vditor/dist/index.css';
import { writingApi } from '../../services/writingApi';
import './CanvasEditor.css';

export interface CanvasEditorHandle {
    insertContent: (content: string) => void;
    getValue: () => string;
}

interface CanvasEditorProps {
    projectId: string;
    onFullscreen?: () => void;
}

const CanvasEditor = forwardRef<CanvasEditorHandle, CanvasEditorProps>(({ projectId, onFullscreen }, ref) => {
    const vditorRef = useRef<Vditor | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);

    useImperativeHandle(ref, () => ({
        insertContent: (content: string) => {
            console.log('CanvasEditor: insertContent called', { length: content.length, hasVditor: !!vditorRef.current });

            if (vditorRef.current) {
                try {
                    // Focus ensure the cursor position is active (defaults to end if lost)
                    vditorRef.current.focus();

                    // Insert at cursor
                    vditorRef.current.insertValue(content);

                    // Force update local storage immediately to ensure persistence
                    const newContent = vditorRef.current.getValue();
                    localStorage.setItem(`canvas_draft_${projectId}`, newContent);

                    message.success('已插入到画布');
                } catch (e) {
                    console.error('CanvasEditor: insert failed', e);
                    message.error('插入失败: ' + e);
                }
            } else {
                console.warn('CanvasEditor: Vditor instance not found');
                message.warning('编辑器未就绪');
            }
        },
        getValue: () => vditorRef.current?.getValue() || ''
    }));

    // Load content strategy: Backend -> LocalStorage -> Empty
    useEffect(() => {
        if (!containerRef.current) return;

        const initVditor = async () => {
            let initialContent = '';

            // 1. Try to load from Backend
            try {
                const data = await writingApi.getCanvas(projectId);
                if (data.content) {
                    initialContent = data.content;
                }
            } catch (e) {
                console.warn('Failed to load canvas from cloud:', e);
            }

            // 2. If Backend empty, try LocalStorage
            if (!initialContent) {
                const localContent = localStorage.getItem(`canvas_draft_${projectId}`);
                if (localContent) {
                    initialContent = localContent;
                    message.info('已恢复本地未保存的草稿');
                }
            }

            vditorRef.current = new Vditor('vditor-container', {
                mode: 'ir',
                height: '100%',
                cache: { enable: false }, // We handle caching manually
                placeholder: '在这里撰写你的论文...\n\n支持 Markdown 语法，使用 [@引用键] 格式添加引用。',
                toolbar: [
                    'headings', 'bold', 'italic', 'strike', '|',
                    'quote', 'list', 'ordered-list', 'check', '|',
                    'code', 'inline-code', 'link', '|',
                    'undo', 'redo', '|',
                    'fullscreen',
                ],
                outline: { enable: false, position: 'right' },
                input: (value) => {
                    // Save to LocalStorage immediately
                    localStorage.setItem(`canvas_draft_${projectId}`, value);

                    // Debounced Cloud Save
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

        initVditor();

        return () => {
            if (saveTimeoutRef.current) {
                clearTimeout(saveTimeoutRef.current);
            }
            vditorRef.current?.destroy();
        };
    }, [projectId]);

    const handleAutoSave = async (content: string) => {
        setSaving(true);
        try {
            await writingApi.saveCanvas(projectId, content);
            // On success, maybe verify against local? For now, we keep both.
        } catch (e) {
            console.error('Auto-save failed:', e);
            message.warning('云端保存失败，内容已保存在本地', 2);
        } finally {
            setSaving(false);
        }
    };

    const handleManualSave = async () => {
        const content = vditorRef.current?.getValue() || '';
        // Explicitly sync to local
        localStorage.setItem(`canvas_draft_${projectId}`, content);

        setSaving(true);
        try {
            await writingApi.saveCanvas(projectId, content);
            message.success('保存成功');
        } catch (e) {
            message.error('云端保存失败，已更新本地备份');
        } finally {
            setSaving(false);
        }
    };

    // Floating Menu State
    const [menuVisible, setMenuVisible] = useState(false);
    const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
    const [selectedText, setSelectedText] = useState('');

    // Handle text selection
    useEffect(() => {
        const handleSelectionChange = () => {
            const selection = window.getSelection();
            if (!selection || selection.isCollapsed || !containerRef.current?.contains(selection.anchorNode)) {
                setMenuVisible(false);
                return;
            }

            const text = selection.toString().trim();
            if (text.length > 0) {
                const range = selection.getRangeAt(0);
                const rect = range.getBoundingClientRect();

                // Calculate position relative to viewport, creating a floating menu above selection
                setMenuPosition({
                    top: rect.top - 50, // 50px above selection
                    left: rect.left + (rect.width / 2) - 60 // Centered
                });
                setSelectedText(text);
                setMenuVisible(true);
            } else {
                setMenuVisible(false);
            }
        };

        // Vditor IR mode uses a contenteditable div, so we can listen on the document/window
        document.addEventListener('mouseup', handleSelectionChange);
        document.addEventListener('keyup', handleSelectionChange);

        return () => {
            document.removeEventListener('mouseup', handleSelectionChange);
            document.removeEventListener('keyup', handleSelectionChange);
        };
    }, []);

    const handleAIAction = async (type: 'continue' | 'polish') => {
        if (!selectedText) return;
        setMenuVisible(false);
        const hide = message.loading('AI 正在思考...', 0);

        try {
            // TODO: Call AI API
            await new Promise(resolve => setTimeout(resolve, 1000)); // Mock

            if (type === 'continue') {
                vditorRef.current?.insertValue(`\n\n[续写] ${selectedText} 的续写内容...`);
            } else {
                vditorRef.current?.updateValue(`[润色] ${selectedText}`);
            }
            message.success('AI 处理完成');
        } catch (e) {
            message.error('AI 处理失败');
        } finally {
            hide();
        }
    };

    const handleExport = () => {
        const content = vditorRef.current?.getValue();
        if (!content) {
            message.warning('画布内容为空');
            return;
        }

        const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `paper_draft_${new Date().toISOString().split('T')[0]}.md`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        message.success('导出成功');
    };

    return (
        <div className="canvas-editor" ref={containerRef}>
            <div className="canvas-toolbar">
                <span className="canvas-title">📝 论文画布</span>
                <div className="canvas-actions">
                    {saving && <Spin size="small" />}
                    <Space>
                        <Tooltip title="导出为 Markdown">
                            <Button
                                size="small"
                                icon={<ExportOutlined />}
                                onClick={handleExport}
                            >
                                导出
                            </Button>
                        </Tooltip>
                        <Tooltip title="手动保存 (自动保存已开启)">
                            <Button
                                size="small"
                                icon={<SaveOutlined />}
                                onClick={handleManualSave}
                                loading={saving}
                            >
                                保存
                            </Button>
                        </Tooltip>
                        {onFullscreen && (
                            <Tooltip title="全屏模式 (带 AI 聊天)">
                                <Button
                                    size="small"
                                    icon={<ExpandOutlined />}
                                    onClick={onFullscreen}
                                >
                                    全屏
                                </Button>
                            </Tooltip>
                        )}
                    </Space>
                </div>
            </div>
            {loading && (
                <div className="canvas-loading">
                    <Spin tip="加载编辑器..." />
                </div>
            )}
            <div id="vditor-container" style={{ display: loading ? 'none' : 'block' }} />

            {/* Floating AI Menu */}
            {menuVisible && (
                <div
                    className="floating-ai-menu"
                    style={{
                        position: 'fixed',
                        top: menuPosition.top,
                        left: menuPosition.left,
                        zIndex: 1000,
                        backgroundColor: '#fff',
                        boxShadow: '0 3px 6px -4px rgba(0, 0, 0, 0.12), 0 6px 16px 0 rgba(0, 0, 0, 0.08), 0 9px 28px 8px rgba(0, 0, 0, 0.05)',
                        borderRadius: '8px',
                        padding: '4px',
                        animation: 'fadeIn 0.2s ease-in-out'
                    }}
                    onMouseDown={(e) => e.preventDefault()} // Prevent losing selection
                >
                    <Space size={4}>
                        <Button
                            type="text"
                            size="small"
                            icon={<ThunderboltOutlined style={{ color: '#1890ff' }} />}
                            onClick={() => handleAIAction('continue')}
                        >
                            AI 续写
                        </Button>
                        <Button
                            type="text"
                            size="small"
                            icon={<EditOutlined style={{ color: '#52c41a' }} />}
                            onClick={() => handleAIAction('polish')}
                        >
                            AI 润色
                        </Button>
                    </Space>
                </div>
            )}
        </div>
    );
});

export default CanvasEditor;
