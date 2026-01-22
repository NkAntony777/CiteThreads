/**
 * ClusterPanel Component - Literature Clustering & Genre Identification
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Drawer, Button, Space, Typography, Slider, Switch,
    List, Tag, Empty, Spin, message, Badge, Tooltip
} from 'antd';
import {
    ExperimentOutlined, ReloadOutlined,
    BulbOutlined, InfoCircleOutlined
} from '@ant-design/icons';
import { useGraphStore } from '../../stores/graphStore';
import './ClusterPanel.css';

const { Text, Paragraph, Title } = Typography;

interface ClusterPanelProps {
    visible: boolean;
    onClose: () => void;
}

export const ClusterPanel: React.FC<ClusterPanelProps> = ({ visible, onClose }) => {
    const { t } = useTranslation();
    const {
        currentProject,
        clusters,
        isClustering,
        performClustering,
    } = useGraphStore();

    const [nClusters, setNClusters] = useState(5);
    const [useAbstract, setUseAbstract] = useState(true);

    const handleCluster = async () => {
        if (!currentProject) return;

        try {
            await performClustering(nClusters, useAbstract);
            message.success(t('clusterPanel.clusterSuccess'));
        } catch (e) {
            message.error(t('clusterPanel.clusterFailed'));
        }
    };

    return (
        <Drawer
            title={
                <Space>
                    <BulbOutlined style={{ color: '#faad14' }} />
                    <span>{t('clusterPanel.title')}</span>
                </Space>
            }
            placement="right"
            width={400}
            open={visible}
            onClose={onClose}
        >
            <div className="cluster-panel">
                <div className="cluster-config">
                    <Title level={5}>{t('clusterPanel.configTitle')}</Title>

                    <div className="config-item">
                        <Space className="config-label">
                            <Text>{t('clusterPanel.clusterCount')}:</Text>
                            <Text strong>{nClusters}</Text>
                        </Space>
                        <Slider
                            min={2}
                            max={10}
                            value={nClusters}
                            onChange={setNClusters}
                            disabled={isClustering}
                        />
                    </div>

                    <div className="config-item">
                        <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                            <Space>
                                <Text>{t('clusterPanel.useAbstract')}</Text>
                                <Tooltip title={t('clusterPanel.abstractNote')}>
                                    <InfoCircleOutlined style={{ color: '#999' }} />
                                </Tooltip>
                            </Space>
                            <Switch
                                checked={useAbstract}
                                onChange={setUseAbstract}
                                disabled={isClustering}
                            />
                        </Space>
                    </div>

                    <Button
                        type="primary"
                        icon={clusters.length > 0 ? <ReloadOutlined /> : <ExperimentOutlined />}
                        onClick={handleCluster}
                        loading={isClustering}
                        block
                        className="cluster-btn"
                    >
                        {clusters.length > 0 ? t('clusterPanel.recluster') : t('clusterPanel.startAnalysis')}
                    </Button>
                </div>

                <div className="cluster-results">
                    <Title level={5} style={{ marginTop: 24, marginBottom: 12 }}>
                        {t('clusterPanel.results')}
                        {clusters.length > 0 &&
                            <Text type="secondary" style={{ fontSize: 12, fontWeight: 'normal', marginLeft: 8 }}>
                                ({t('clusterPanel.totalGenres', { count: clusters.length })})
                            </Text>
                        }
                    </Title>

                    {isClustering ? (
                        <div className="loading-container">
                            <Spin size="large" tip={t('clusterPanel.analyzingSemantic')} />
                        </div>
                    ) : clusters.length === 0 ? (
                        <Empty
                            description={t('clusterPanel.clickToAnalyze')}
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        />
                    ) : (
                        <List
                            dataSource={clusters}
                            renderItem={(cluster) => (
                                <List.Item className="cluster-item" style={{ padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
                                    <div className="cluster-header" style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                        <Space>
                                            <Badge color={cluster.color} />
                                            <Text strong style={{ fontSize: 15 }}>{cluster.name}</Text>
                                        </Space>
                                        <Tag>{cluster.paper_ids.length} {t('clusterPanel.paperUnit')}</Tag>
                                    </div>

                                    <Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 8, lineHeight: 1.4 }}>
                                        {cluster.description}
                                    </Paragraph>

                                    {cluster.key_innovation && (
                                        <div className="cluster-innovation" style={{ background: '#f9f9f9', padding: '8px 10px', borderRadius: 4, marginBottom: 8, borderLeft: `3px solid ${cluster.color}` }}>
                                            <Space align="start">
                                                <BulbOutlined style={{ fontSize: 12, color: '#faad14', marginTop: 3 }} />
                                                <Text style={{ fontSize: 12, color: '#666' }}>
                                                    <Text strong>{t('clusterPanel.coreInnovation')}: </Text>
                                                    {cluster.key_innovation}
                                                </Text>
                                            </Space>
                                        </div>
                                    )}

                                    <div className="cluster-keywords">
                                        {cluster.keywords.slice(0, 4).map(kw => (
                                            <Tag key={kw} bordered={false} style={{ fontSize: 11, background: '#f5f5f5', color: '#888' }}>
                                                {kw}
                                            </Tag>
                                        ))}
                                    </div>
                                </List.Item>
                            )}
                        />
                    )}
                </div>
            </div>
        </Drawer>
    );
};
