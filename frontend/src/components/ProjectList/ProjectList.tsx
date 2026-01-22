/**
 * ProjectList Component - Display and manage saved projects
 */
import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Drawer, List, Button, Space, Typography, Tag, Popconfirm,
    Input, Empty, Spin, message, Modal, Tooltip
} from 'antd';
import {
    FolderOutlined, DeleteOutlined, EditOutlined,
    ReloadOutlined, ExportOutlined, ClockCircleOutlined,
    NodeIndexOutlined, FileTextOutlined
} from '@ant-design/icons';
import './ProjectList.css';

const { Text } = Typography;

interface ProjectMetadata {
    id: string;
    name: string;
    created_at: string;
    updated_at: string;
    status: string;
    stats?: {
        total_nodes: number;
        total_edges: number;
        year_range?: [number, number];
    };
    config: {
        seed_paper_id: string;
        depth: number;
        direction: string;
    };
}

interface ProjectListProps {
    visible: boolean;
    onClose: () => void;
    onSelectProject: (projectId: string) => void;
    currentProjectId?: string;
}

export const ProjectList: React.FC<ProjectListProps> = ({
    visible,
    onClose,
    onSelectProject,
    currentProjectId
}) => {
    const { t, i18n } = useTranslation();
    const [projects, setProjects] = useState<ProjectMetadata[]>([]);
    const [loading, setLoading] = useState(false);
    const [renaming, setRenaming] = useState<string | null>(null);
    const [newName, setNewName] = useState('');

    const fetchProjects = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/projects');
            if (response.ok) {
                const data = await response.json();
                setProjects(data);
            }
        } catch (e) {
            console.error('Failed to fetch projects:', e);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (visible) {
            fetchProjects();
        }
    }, [visible, fetchProjects]);

    const handleDelete = async (projectId: string) => {
        try {
            const response = await fetch(`/api/projects/${projectId}`, {
                method: 'DELETE'
            });
            if (response.ok) {
                message.success(t('projectList.projectDeleted'));
                setProjects(prev => prev.filter(p => p.id !== projectId));
            }
        } catch (e) {
            message.error(t('projectList.deleteFailed'));
        }
    };

    const handleRename = async (projectId: string) => {
        if (!newName.trim()) {
            message.warning(t('projectList.pleaseEnterName'));
            return;
        }

        try {
            const response = await fetch(`/api/projects/${projectId}/rename`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName.trim() })
            });

            if (response.ok) {
                message.success(t('projectList.renameSuccess'));
                setProjects(prev => prev.map(p =>
                    p.id === projectId ? { ...p, name: newName.trim() } : p
                ));
                setRenaming(null);
                setNewName('');
            }
        } catch (e) {
            message.error(t('projectList.renameFailed'));
        }
    };

    const handleExport = async (projectId: string, format: 'json' | 'bibtex') => {
        try {
            const response = await fetch(`/api/projects/${projectId}/export?format=${format}`);
            if (response.ok) {
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${projectId}.${format === 'bibtex' ? 'bib' : 'json'}`;
                a.click();
                URL.revokeObjectURL(url);
                message.success(t('projectList.exportSuccess'));
            }
        } catch (e) {
            message.error(t('projectList.exportFailed'));
        }
    };

    const formatDate = (dateStr: string) => {
        const date = new Date(dateStr);
        const now = new Date();
        const diff = now.getTime() - date.getTime();

        if (diff < 60000) return t('projectList.justNow');
        if (diff < 3600000) return `${Math.floor(diff / 60000)}${t('projectList.minutesAgo')}`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}${t('projectList.hoursAgo')}`;
        if (diff < 604800000) return `${Math.floor(diff / 86400000)}${t('projectList.daysAgo')}`;

        return date.toLocaleDateString(i18n.language === 'en-US' ? 'en-US' : 'zh-CN');
    };

    const getStatusTag = (status: string) => {
        const statusMap: Record<string, { color: string; textKey: string }> = {
            'completed': { color: 'success', textKey: 'projectList.statusCompleted' },
            'crawling': { color: 'processing', textKey: 'projectList.statusCrawling' },
            'failed': { color: 'error', textKey: 'projectList.statusFailed' },
            'created': { color: 'default', textKey: 'projectList.statusCreated' },
        };
        const s = statusMap[status] || { color: 'default', textKey: status };
        return <Tag color={s.color}>{t(s.textKey)}</Tag>;
    };

    return (
        <Drawer
            title={
                <Space>
                    <FolderOutlined />
                    <span>{t('projectList.title')}</span>
                    <Button
                        type="text"
                        icon={<ReloadOutlined />}
                        onClick={fetchProjects}
                        loading={loading}
                        size="small"
                    />
                </Space>
            }
            placement="left"
            width={400}
            open={visible}
            onClose={onClose}
        >
            <Spin spinning={loading}>
                {projects.length === 0 ? (
                    <Empty
                        description={t('projectList.empty')}
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                    />
                ) : (
                    <List
                        dataSource={projects}
                        renderItem={(project) => (
                            <List.Item
                                className={`project-item ${project.id === currentProjectId ? 'active' : ''}`}
                                actions={[
                                    <Tooltip title={t('projectList.rename')} key="rename">
                                        <Button
                                            type="text"
                                            size="small"
                                            icon={<EditOutlined />}
                                            onClick={() => {
                                                setRenaming(project.id);
                                                setNewName(project.name);
                                            }}
                                        />
                                    </Tooltip>,
                                    <Tooltip title={t('projectList.exportJson')} key="export">
                                        <Button
                                            type="text"
                                            size="small"
                                            icon={<ExportOutlined />}
                                            onClick={() => handleExport(project.id, 'json')}
                                        />
                                    </Tooltip>,
                                    <Popconfirm
                                        title={t('projectList.deleteConfirm')}
                                        description={t('projectList.cannotUndo')}
                                        onConfirm={() => handleDelete(project.id)}
                                        key="delete"
                                    >
                                        <Button
                                            type="text"
                                            size="small"
                                            danger
                                            icon={<DeleteOutlined />}
                                        />
                                    </Popconfirm>
                                ]}
                            >
                                <List.Item.Meta
                                    title={
                                        <Space
                                            style={{ cursor: 'pointer' }}
                                            onClick={() => {
                                                onSelectProject(project.id);
                                                onClose();
                                            }}
                                        >
                                            <Text strong ellipsis style={{ maxWidth: 180 }}>
                                                {project.name}
                                            </Text>
                                            {getStatusTag(project.status)}
                                        </Space>
                                    }
                                    description={
                                        <Space direction="vertical" size={2}>
                                            <Space size="small">
                                                <ClockCircleOutlined />
                                                <Text type="secondary" style={{ fontSize: 12 }}>
                                                    {formatDate(project.created_at)}
                                                </Text>
                                            </Space>
                                            {project.stats && (
                                                <Space size="small">
                                                    <FileTextOutlined />
                                                    <Text type="secondary" style={{ fontSize: 12 }}>
                                                        {project.stats.total_nodes}{t('projectList.papers')}
                                                    </Text>
                                                    <NodeIndexOutlined />
                                                    <Text type="secondary" style={{ fontSize: 12 }}>
                                                        {project.stats.total_edges}{t('projectList.citations')}
                                                    </Text>
                                                </Space>
                                            )}
                                        </Space>
                                    }
                                />
                            </List.Item>
                        )}
                    />
                )}
            </Spin>

            {/* Rename Modal */}
            <Modal
                title={t('projectList.renameProject')}
                open={renaming !== null}
                onOk={() => renaming && handleRename(renaming)}
                onCancel={() => {
                    setRenaming(null);
                    setNewName('');
                }}
                okText={t('common.confirm')}
                cancelText={t('common.cancel')}
            >
                <Input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder={t('projectList.newNamePlaceholder')}
                    onPressEnter={() => renaming && handleRename(renaming)}
                />
            </Modal>
        </Drawer>
    );
};
