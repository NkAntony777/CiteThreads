/**
 * HistoryPanel — left sider that lists all conversations and
 * lets the user switch / rename / delete them. The actual
 * conversation-list UI lives inside ChatView itself; this
 * panel is a quick-access drawer opened from the folder icon
 * in the top bar.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Drawer, List, Popconfirm, Spin, Tooltip, Typography, message } from 'antd';
import { DeleteOutlined, MessageOutlined } from '@ant-design/icons';
import { chatApi, type ConversationListItem } from '../../services/chatApi';
import { useGraphStore } from '../../stores/graphStore';
import './HistoryPanel.css';

const { Text } = Typography;

export interface HistoryPanelProps {
    visible: boolean;
    onClose: () => void;
}

export const HistoryPanel: React.FC<HistoryPanelProps> = ({ visible, onClose }) => {
    const { t } = useTranslation();
    const [items, setItems] = useState<ConversationListItem[]>([]);
    const [loading, setLoading] = useState(false);
    const { currentProject, setProject } = useGraphStore();

    useEffect(() => {
        if (!visible) return;
        void load();
    }, [visible]);

    const load = async () => {
        setLoading(true);
        try {
            const list = await chatApi.list();
            setItems(list);
        } catch (e) {
            message.error(t('chat.loadFailed'));
        } finally {
            setLoading(false);
        }
    };

    const open = async (id: string) => {
        try {
            const full = await chatApi.getFull(id);
            setProject(full);
            onClose();
        } catch (e) {
            message.error(t('chat.loadFailed'));
        }
    };

    const remove = async (id: string) => {
        try {
            await chatApi.remove(id);
            setItems((prev) => prev.filter((c) => c.id !== id));
            if (currentProject?.metadata.id === id) {
                // Switch to first remaining or clear.
                const remaining = items.filter((c) => c.id !== id);
                if (remaining.length > 0) {
                    const full = await chatApi.getFull(remaining[0].id);
                    setProject(full);
                } else {
                    useGraphStore.setState({ currentProject: null });
                }
            }
        } catch (e) {
            message.error(t('chat.deleteFailed'));
        }
    };

    return (
        <Drawer
            title={t('app.viewProjects')}
            placement="left"
            open={visible}
            onClose={onClose}
            width={360}
        >
            {loading ? (
                <div className="history-panel__loading">
                    <Spin />
                </div>
            ) : items.length === 0 ? (
                <div className="history-panel__empty">
                    <Text type="secondary">{t('chat.emptyList')}</Text>
                </div>
            ) : (
                <List
                    size="small"
                    dataSource={items}
                    renderItem={(c) => {
                        const isActive = c.id === currentProject?.metadata.id;
                        return (
                            <List.Item
                                className={`history-panel__item ${
                                    isActive ? 'is-active' : ''
                                }`}
                                onClick={() => void open(c.id)}
                                actions={[
                                    <Popconfirm
                                        key="del"
                                        title={t('chat.deleteConfirm')}
                                        onConfirm={(e) => {
                                            e?.stopPropagation();
                                            void remove(c.id);
                                        }}
                                        onCancel={(e) => e?.stopPropagation()}
                                    >
                                        <Tooltip title={t('common.delete')}>
                                            <Button
                                                type="text"
                                                size="small"
                                                danger
                                                icon={<DeleteOutlined />}
                                                onClick={(e) => e.stopPropagation()}
                                            />
                                        </Tooltip>
                                    </Popconfirm>,
                                ]}
                            >
                                <List.Item.Meta
                                    avatar={<MessageOutlined />}
                                    title={
                                        <span>
                                            {c.name}{' '}
                                            {isActive && (
                                                <Text type="secondary" style={{ fontSize: 11 }}>
                                                    · {t('app.aiWritingAssistant')}
                                                </Text>
                                            )}
                                        </span>
                                    }
                                    description={
                                        c.last_message_preview ? (
                                            <Text
                                                type="secondary"
                                                ellipsis
                                                style={{ fontSize: 11 }}
                                            >
                                                {c.last_message_preview}
                                            </Text>
                                        ) : (
                                            <Text type="secondary" style={{ fontSize: 11 }}>
                                                {c.paper_count} {t('chat.papers')}
                                            </Text>
                                        )
                                    }
                                />
                            </List.Item>
                        );
                    }}
                />
            )}
        </Drawer>
    );
};

export default HistoryPanel;
