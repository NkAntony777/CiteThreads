/**
 * CanvasEditor - Markdown editor with AI interaction
 * Uses Vditor in instant rendering mode
 */
import { useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Tooltip, message, Spin, Space } from 'antd';
import { SaveOutlined, ThunderboltOutlined, EditOutlined, ExportOutlined, ExpandOutlined } from '@ant-design/icons';
import Vditor from 'vditor';
import 'vditor/dist/index.css';
import { writingApi } from '../../services/writingApi';
import { streamAgentChat } from '../../services/agentStream';
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
    const { t, i18n } = useTranslation();
    const vditorRef = useRef<Vditor | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [aiRunning, setAiRunning] = useState(false);
    const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const aiAbortRef = useRef<AbortController | null>(null);

    useImperativeHandle(ref, () => ({
        insertContent: (content: string) => {
            if (vditorRef.current) {
                try {
                    // Focus ensure the cursor position is active (defaults to end if lost)
                    vditorRef.current.focus();

                    // Insert at cursor
                    vditorRef.current.insertValue(content);

                    // Force update local storage immediately to ensure persistence
                    const newContent = vditorRef.current.getValue();
                    localStorage.setItem(`canvas_draft_${projectId}`, newContent);

                    message.success(t('canvasEditor.insertedToCanvas'));
                } catch (e) {
                    console.error('CanvasEditor: insert failed', e);
                    message.error(t('canvasEditor.insertFailed') + e);
                }
            } else {
                console.warn('CanvasEditor: Vditor instance not found');
                message.warning(t('canvasEditor.editorNotReady'));
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
                    message.info(t('canvasEditor.restoredDraft'));
                }
            }

            vditorRef.current = new Vditor('vditor-container', {
                mode: 'ir',
                height: '100%',
                lang: i18n.language === 'en-US' ? 'en_US' : 'zh_CN',
                cache: { enable: false }, // We handle caching manually
                placeholder: t('canvasEditor.placeholder'),
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
            if (aiAbortRef.current) {
                aiAbortRef.current.abort();
                aiAbortRef.current = null;
            }
            vditorRef.current?.destroy();
        };
    }, [projectId, i18n.language, t]);

    const handleAutoSave = async (content: string) => {
        setSaving(true);
        try {
            await writingApi.saveCanvas(projectId, content);
            // On success, maybe verify against local? For now, we keep both.
        } catch (e) {
            console.error('Auto-save failed:', e);
            message.warning(t('canvasEditor.cloudSaveFailed'), 2);
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
            message.success(t('canvasEditor.saveSuccess'));
        } catch (e) {
            message.error(t('canvasEditor.cloudSaveFailedLocal'));
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
        if (!selectedText || aiRunning) return;
        setMenuVisible(false);
        setAiRunning(true);

        // Cancel any previous in-flight request and start a fresh one.
        if (aiAbortRef.current) {
            aiAbortRef.current.abort();
        }
        const controller = new AbortController();
        aiAbortRef.current = controller;

        // Build the prompt. The agent runtime already has tool support,
        // but for canvas continue/polish we want a direct text answer
        // with no tool calls, so we pass an explicit ``extra_context``
        // describing the task and the user-selected text.
        const userPrompt = type === 'continue'
            ? `请对下面的内容进行续写, 直接给出续写的正文, 不要重复原文, 也不要写解释: \n\n${selectedText}`
            : `请对下面的内容进行学术风格润色, 保持原意, 输出润色后的完整内容: \n\n${selectedText}`;

        const hide = message.loading(t('canvasEditor.aiThinking'), 0);

        // Buffer for streamed tokens. We append a marker so the user
        // can see what's been AI-written vs. what was already there.
        let buffer = '';
        let insertedMarker = false;

        try {
            const handle = streamAgentChat(
                {
                    message: userPrompt,
                    project_id: projectId,
                    extra_context: type === 'continue'
                        ? 'Task: continue the given text in the same academic style. Do not include explanations, do not echo the original text.'
                        : 'Task: polish the given text in academic style. Keep the original meaning. Do not include explanations, do not echo the original text.',
                },
                {
                    onTextDelta: (delta) => {
                        if (!insertedMarker) {
                            const label = type === 'continue'
                                ? `\n\n[AI 续写] `
                                : `\n\n[AI 润色] `;
                            vditorRef.current?.insertValue(label);
                            insertedMarker = true;
                        }
                        buffer += delta;
                        vditorRef.current?.insertValue(delta);
                    },
                    onError: (msg) => {
                        console.error('Canvas AI error:', msg);
                        message.error(t('canvasEditor.aiFailed') + msg);
                    },
                },
                { signal: controller.signal },
            );

            await handle.done;
            if (buffer.length > 0) {
                message.success(t('canvasEditor.aiComplete'));
            }
        } catch (e) {
            const err = e as { name?: string; message?: string };
            if (err.name === 'AbortError') {
                message.info(t('canvasEditor.aiComplete'));
            } else {
                console.error('Canvas AI stream crashed:', e);
                message.error(t('canvasEditor.aiFailed') + (err.message ?? ''));
            }
        } finally {
            hide();
            if (aiAbortRef.current === controller) {
                aiAbortRef.current = null;
            }
            setAiRunning(false);
        }
    };

    const handleExport = () => {
        const content = vditorRef.current?.getValue();
        if (!content) {
            message.warning(t('canvasEditor.canvasEmpty'));
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
        message.success(t('canvasEditor.exportSuccess'));
    };

    return (
        <div className="canvas-editor" ref={containerRef}>
            <div className="canvas-toolbar">
                <span className="canvas-title">📝 {t('canvasEditor.title')}</span>
                <div className="canvas-actions">
                    {saving && <Spin size="small" />}
                    <Space>
                        <Tooltip title={t('canvasEditor.exportTooltip')}>
                            <Button
                                size="small"
                                icon={<ExportOutlined />}
                                onClick={handleExport}
                            >
                                {t('canvasEditor.export')}
                            </Button>
                        </Tooltip>
                        <Tooltip title={t('canvasEditor.saveTooltip')}>
                            <Button
                                size="small"
                                icon={<SaveOutlined />}
                                onClick={handleManualSave}
                                loading={saving}
                            >
                                {t('canvasEditor.save')}
                            </Button>
                        </Tooltip>
                        {onFullscreen && (
                            <Tooltip title={t('canvasEditor.fullscreenTooltip')}>
                                <Button
                                    size="small"
                                    icon={<ExpandOutlined />}
                                    onClick={onFullscreen}
                                >
                                    {t('canvasEditor.fullscreen')}
                                </Button>
                            </Tooltip>
                        )}
                    </Space>
                </div>
            </div>
            {loading && (
                <div className="canvas-loading">
                    <Spin tip={t('canvasEditor.loading')} />
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
                            disabled={aiRunning}
                        >
                            {t('canvasEditor.aiContinue')}
                        </Button>
                        <Button
                            type="text"
                            size="small"
                            icon={<EditOutlined style={{ color: '#52c41a' }} />}
                            onClick={() => handleAIAction('polish')}
                            disabled={aiRunning}
                        >
                            {t('canvasEditor.aiPolish')}
                        </Button>
                    </Space>
                </div>
            )}
        </div>
    );
});

export default CanvasEditor;
