/**
 * ProjectList Component - Display and manage saved projects
 */
import React, { useState, useEffect, useCallback } from 'react';
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
                message.success('项目已删除');
                setProjects(prev => prev.filter(p => p.id !== projectId));
            }
        } catch (e) {
            message.error('删除失败');
        }
    };

    const handleRename = async (projectId: string) => {
        if (!newName.trim()) {
            message.warning('请输入项目名称');
            return;
        }

        try {
            const response = await fetch(`/api/projects/${projectId}/rename`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName.trim() })
            });

            if (response.ok) {
                message.success('重命名成功');
                setProjects(prev => prev.map(p =>
                    p.id === projectId ? { ...p, name: newName.trim() } : p
                ));
                setRenaming(null);
                setNewName('');
            }
        } catch (e) {
            message.error('重命名失败');
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
                message.success('导出成功');
            }
        } catch (e) {
            message.error('导出失败');
        }
    };

    const formatDate = (dateStr: string) => {
        const date = new Date(dateStr);
        const now = new Date();
        const diff = now.getTime() - date.getTime();

        if (diff < 60000) return '刚刚';
        if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
        if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`;

        return date.toLocaleDateString('zh-CN');
    };

    const getStatusTag = (status: string) => {
        const statusMap: Record<string, { color: string; text: string }> = {
            'completed': { color: 'success', text: '已完成' },
            'crawling': { color: 'processing', text: '构建中' },
            'failed': { color: 'error', text: '失败' },
            'created': { color: 'default', text: '已创建' },
        };
        const s = statusMap[status] || { color: 'default', text: status };
        return <Tag color={s.color}>{s.text}</Tag>;
    };

    return (
        <Drawer
            title={
                <Space>
                    <FolderOutlined />
                    <span>我的项目</span>
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
                        description="暂无保存的项目"
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                    />
                ) : (
                    <List
                        dataSource={projects}
                        renderItem={(project) => (
                            <List.Item
                                className={`project-item ${project.id === currentProjectId ? 'active' : ''}`}
                                actions={[
                                    <Tooltip title="重命名" key="rename">
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
                                    <Tooltip title="导出 JSON" key="export">
                                        <Button
                                            type="text"
                                            size="small"
                                            icon={<ExportOutlined />}
                                            onClick={() => handleExport(project.id, 'json')}
                                        />
                                    </Tooltip>,
                                    <Popconfirm
                                        title="确定删除此项目？"
                                        description="此操作不可撤销"
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
                                                        {project.stats.total_nodes} 篇论文
                                                    </Text>
                                                    <NodeIndexOutlined />
                                                    <Text type="secondary" style={{ fontSize: 12 }}>
                                                        {project.stats.total_edges} 条引用
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
                title="重命名项目"
                open={renaming !== null}
                onOk={() => renaming && handleRename(renaming)}
                onCancel={() => {
                    setRenaming(null);
                    setNewName('');
                }}
                okText="确定"
                cancelText="取消"
            >
                <Input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="输入新的项目名称"
                    onPressEnter={() => renaming && handleRename(renaming)}
                />
            </Modal>
        </Drawer>
    );
};
