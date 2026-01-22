/**
 * NodePanel Component - Paper detail sidebar
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
    FileTextOutlined,
    TeamOutlined,
    CloseOutlined,
    ExportOutlined,
    DeleteOutlined
} from '@ant-design/icons';
import { Drawer, Descriptions, Tag, Space, Button, Divider, Typography, Popconfirm } from 'antd';
import { useGraphStore } from '../../stores/graphStore';
import { applyGraphFilters } from '../../utils/graphFilters';
import './NodePanel.css';

const { Paragraph, Text } = Typography;

export const NodePanel: React.FC = () => {
    const { t } = useTranslation();
    const {
        selectedNode,
        setSelectedNode,
        links,
        nodes,
        yearRange,
        intentFilter,
        clusterFilter,
        paperClusters,
        deleteNode,
    } = useGraphStore();

    const { links: visibleLinks } = useMemo(
        () => applyGraphFilters(nodes, links, { yearRange, intentFilter, clusterFilter, paperClusters }),
        [nodes, links, yearRange, intentFilter, clusterFilter, paperClusters]
    );

    if (!selectedNode) {
        return null;
    }

    // Count incoming and outgoing edges
    const incomingEdges = visibleLinks.filter(l => l.target === selectedNode.id);
    const outgoingEdges = visibleLinks.filter(l => l.source === selectedNode.id);

    const handleClose = () => {
        setSelectedNode(null);
    };

    const handleOpenUrl = () => {
        if (selectedNode.url) {
            window.open(selectedNode.url, '_blank');
        } else if (selectedNode.doi) {
            window.open(`https://doi.org/${selectedNode.doi}`, '_blank');
        }
    };

    return (
        <Drawer
            title={
                <div className="panel-header">
                    <FileTextOutlined style={{ marginRight: 8 }} />
                    {t('nodePanel.title')}
                </div>
            }
            placement="right"
            width={400}
            open={!!selectedNode}
            onClose={handleClose}
            closeIcon={<CloseOutlined />}
            extra={
                <Button
                    type="link"
                    icon={<ExportOutlined />}
                    onClick={handleOpenUrl}
                    disabled={!selectedNode.url && !selectedNode.doi}
                >
                    {t('nodePanel.viewOriginal')}
                </Button>
            }
        >
            <div className="node-panel-content">
                {/* Title */}
                <Typography.Title level={5} className="paper-title">
                    {selectedNode.title}
                </Typography.Title>

                {/* Authors */}
                <div className="authors-section">
                    <TeamOutlined style={{ marginRight: 8 }} />
                    <Text type="secondary">
                        {selectedNode.authors.length > 5
                            ? selectedNode.authors.slice(0, 5).join(', ') + ` ${t('nodePanel.authors', { count: selectedNode.authors.length })}`
                            : selectedNode.authors.join(', ')
                        }
                    </Text>
                </div>

                {/* Tags */}
                <Space className="tags-section" wrap>
                    {selectedNode.year && <Tag color="blue">{selectedNode.year}</Tag>}
                    {selectedNode.venue && <Tag>{selectedNode.venue}</Tag>}
                    <Tag color="green">{t('paperSearch.cited')} {selectedNode.citation_count}</Tag>
                    <Tag color="orange">{t('searchBar.cited')} {selectedNode.reference_count}</Tag>
                </Space>

                <Divider />

                {/* Abstract */}
                {selectedNode.abstract && (
                    <>
                        <div className="section-title">{t('nodePanel.abstract')}</div>
                        <Paragraph
                            ellipsis={{ rows: 6, expandable: true, symbol: t('nodePanel.expand') }}
                            className="abstract-text"
                        >
                            {selectedNode.abstract}
                        </Paragraph>
                        <Divider />
                    </>
                )}

                {/* Fields */}
                {selectedNode.fields.length > 0 && (
                    <>
                        <div className="section-title">{t('nodePanel.researchFields')}</div>
                        <Space wrap>
                            {selectedNode.fields.map((field, index) => (
                                <Tag key={index} color="purple">{field}</Tag>
                            ))}
                        </Space>
                        <Divider />
                    </>
                )}

                {/* Citation Stats in Graph */}
                <div className="section-title">{t('nodePanel.citationInGraph')}</div>
                <Descriptions column={1} size="small">
                    <Descriptions.Item label={t('nodePanel.citedInGraph')}>
                        <Tag color="cyan">{incomingEdges.length}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label={t('nodePanel.referencesInGraph')}>
                        <Tag color="magenta">{outgoingEdges.length}</Tag>
                    </Descriptions.Item>
                </Descriptions>

                {/* IDs */}
                <Divider />
                <div className="section-title">{t('nodePanel.identifiers')}</div>
                <Descriptions column={1} size="small">
                    {selectedNode.doi && (
                        <Descriptions.Item label="DOI">
                            <a href={`https://doi.org/${selectedNode.doi}`} target="_blank" rel="noopener noreferrer">
                                {selectedNode.doi}
                            </a>
                        </Descriptions.Item>
                    )}
                    {selectedNode.arxiv_id && (
                        <Descriptions.Item label="arXiv">
                            <a href={`https://arxiv.org/abs/${selectedNode.arxiv_id}`} target="_blank" rel="noopener noreferrer">
                                {selectedNode.arxiv_id}
                            </a>
                        </Descriptions.Item>
                    )}
                    <Descriptions.Item label={t('nodePanel.internalId')}>
                        <Text code copyable={{ text: selectedNode.id }}>
                            {selectedNode.id.length > 20 ? selectedNode.id.slice(0, 20) + '...' : selectedNode.id}
                        </Text>
                    </Descriptions.Item>
                </Descriptions>
            </div>

            <Divider />

            <div style={{ padding: '0 0 16px 0', textAlign: 'center' }}>
                <Popconfirm
                    title={t('nodePanel.deleteNode')}
                    description={t('nodePanel.deleteNodeConfirm')}
                    onConfirm={() => {
                        deleteNode(selectedNode.id);
                        handleClose();
                    }}
                    okText={t('common.delete')}
                    cancelText={t('common.cancel')}
                    okButtonProps={{ danger: true }}
                >
                    <Button danger icon={<DeleteOutlined />}>
                        {t('nodePanel.deleteThisNode')}
                    </Button>
                </Popconfirm>
            </div>
        </Drawer>
    );
};
