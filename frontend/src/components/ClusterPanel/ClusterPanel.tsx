/**
 * ClusterPanel Component - Literature Clustering & Genre Identification
 */
import React, { useState } from 'react';
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
            message.success('文献聚类完成！');
        } catch (e) {
            message.error('聚类失败，请检查是否配置了嵌入模型');
        }
    };

    return (
        <Drawer
            title={
                <Space>
                    <BulbOutlined style={{ color: '#faad14' }} />
                    <span>文献智能流派识别</span>
                </Space>
            }
            placement="right"
            width={400}
            open={visible}
            onClose={onClose}
        >
            <div className="cluster-panel">
                <div className="cluster-config">
                    <Title level={5}>聚类参数配置</Title>

                    <div className="config-item">
                        <Space className="config-label">
                            <Text>流派/簇数量:</Text>
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
                                <Text>使用摘要嵌入</Text>
                                <Tooltip title="包含摘要会提高准确度，但会消耗更多Token">
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
                        {clusters.length > 0 ? '重新聚类' : '开始智能分析'}
                    </Button>
                </div>

                <div className="cluster-results">
                    <Title level={5} style={{ marginTop: 24, marginBottom: 12 }}>
                        分析结果
                        {clusters.length > 0 &&
                            <Text type="secondary" style={{ fontSize: 12, fontWeight: 'normal', marginLeft: 8 }}>
                                (共 {clusters.length} 个流派)
                            </Text>
                        }
                    </Title>

                    {isClustering ? (
                        <div className="loading-container">
                            <Spin size="large" tip="正在分析文献语义..." />
                        </div>
                    ) : clusters.length === 0 ? (
                        <Empty
                            description="点击上方按钮开始分析文献流派"
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
                                        <Tag>{cluster.paper_ids.length} 篇</Tag>
                                    </div>

                                    <Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 8, lineHeight: 1.4 }}>
                                        {cluster.description}
                                    </Paragraph>

                                    {cluster.key_innovation && (
                                        <div className="cluster-innovation" style={{ background: '#f9f9f9', padding: '8px 10px', borderRadius: 4, marginBottom: 8, borderLeft: `3px solid ${cluster.color}` }}>
                                            <Space align="start">
                                                <BulbOutlined style={{ fontSize: 12, color: '#faad14', marginTop: 3 }} />
                                                <Text style={{ fontSize: 12, color: '#666' }}>
                                                    <Text strong>核心创新: </Text>
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
