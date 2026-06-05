/**
 * ConversationList — left sider of the ChatView. Renders the
 * list of saved conversations with rename / delete affordances.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Input, List, Popconfirm, Spin, Tooltip, Typography } from 'antd';
import {
    DeleteOutlined,
    EditOutlined,
    MessageOutlined,
    PlusOutlined,
} from '@ant-design/icons';
import './ConversationList.css';

const { Text } = Typography;

export interface ConversationSummary {
    id: string;
    name: string;
    updated_at: string;
    paper_count: number;
    section_draft_count: number;
    last_message_preview?: string;
}

export interface ConversationListProps {
    conversations: ConversationSummary[];
    activeId?: string;
    loading?: boolean;
    creatingNew?: boolean;
    onSelect: (id: string) => void;
    onDelete: (id: string) => void;
    onRename: (id: string, name: string) => void;
    onNew: () => void;
}

export const ConversationList: React.FC<ConversationListProps> = ({
    conversations,
    activeId,
    loading = false,
    creatingNew = false,
    onSelect,
    onDelete,
    onRename,
    onNew,
}) => {
    const { t } = useTranslation();
    const [renamingId, setRenamingId] = useState<string | null>(null);
    const [renameValue, setRenameValue] = useState('');

    const beginRename = (id: string, currentName: string) => {
        setRenamingId(id);
        setRenameValue(currentName);
    };

    const commitRename = () => {
        if (renamingId && renameValue.trim()) {
            onRename(renamingId, renameValue.trim());
        }
        setRenamingId(null);
        setRenameValue('');
    };

    return (
        <div className="conversation-list">
            <div className="conversation-list__header">
                <Button
                    type="primary"
                    block
                    icon={<PlusOutlined />}
                    loading={creatingNew}
                    onClick={onNew}
                >
                    {t('chat.newChat')}
                </Button>
            </div>

            <div className="conversation-list__scroll">
                {loading ? (
                    <div className="conversation-list__loading">
                        <Spin />
                    </div>
                ) : conversations.length === 0 ? (
                    <div className="conversation-list__empty">
                        <Text type="secondary" style={{ fontSize: 12 }}>
                            {t('chat.emptyList')}
                        </Text>
                    </div>
                ) : (
                    <List
                        size="small"
                        dataSource={conversations}
                        renderItem={(c) => {
                            const isActive = c.id === activeId;
                            const isRenaming = renamingId === c.id;
                            return (
                                <List.Item
                                    className={`conversation-list__item ${
                                        isActive ? 'is-active' : ''
                                    }`}
                                    onClick={() => !isRenaming && onSelect(c.id)}
                                >
                                    <div className="conversation-list__item-main">
                                        {isRenaming ? (
                                            <Input
                                                size="small"
                                                value={renameValue}
                                                autoFocus
                                                onChange={(e) =>
                                                    setRenameValue(e.target.value)
                                                }
                                                onPressEnter={commitRename}
                                                onBlur={commitRename}
                                            />
                                        ) : (
                                            <>
                                                <div className="conversation-list__item-title">
                                                    <MessageOutlined />
                                                    <span>{c.name}</span>
                                                </div>
                                                {c.last_message_preview && (
                                                    <Text
                                                        type="secondary"
                                                        ellipsis
                                                        style={{ fontSize: 11 }}
                                                    >
                                                        {c.last_message_preview}
                                                    </Text>
                                                )}
                                                <div className="conversation-list__item-meta">
                                                    <span>
                                                        {c.paper_count} {t('chat.papers')}
                                                    </span>
                                                    {c.section_draft_count > 0 && (
                                                        <span>
                                                            {c.section_draft_count}{' '}
                                                            {t('chat.sections')}
                                                        </span>
                                                    )}
                                                </div>
                                            </>
                                        )}
                                    </div>
                                    {!isRenaming && (
                                        <div
                                            className="conversation-list__item-actions"
                                            onClick={(e) => e.stopPropagation()}
                                        >
                                            <Tooltip title={t('common.rename')}>
                                                <Button
                                                    type="text"
                                                    size="small"
                                                    icon={<EditOutlined />}
                                                    onClick={() => beginRename(c.id, c.name)}
                                                />
                                            </Tooltip>
                                            <Popconfirm
                                                title={t('chat.deleteConfirm')}
                                                okText={t('common.delete')}
                                                cancelText={t('common.cancel')}
                                                onConfirm={() => onDelete(c.id)}
                                            >
                                                <Tooltip title={t('common.delete')}>
                                                    <Button
                                                        type="text"
                                                        size="small"
                                                        danger
                                                        icon={<DeleteOutlined />}
                                                    />
                                                </Tooltip>
                                            </Popconfirm>
                                        </div>
                                    )}
                                </List.Item>
                            );
                        }}
                    />
                )}
            </div>
        </div>
    );
};

export default ConversationList;
