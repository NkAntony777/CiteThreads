/**
 * EdgePanel Component - Citation relationship detail sidebar
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Drawer, Descriptions, Tag, Divider, Typography, Card, Rate, Space } from 'antd';
import {
    ShareAltOutlined,
    ArrowRightOutlined,
    CloseOutlined,
    RobotOutlined,
    BulbOutlined
} from '@ant-design/icons';
import { useGraphStore } from '../../stores/graphStore';
import { CitationIntent } from '../../types';
import { aiConfigService } from '../../services/aiConfig';
import './EdgePanel.css';

const { Paragraph, Text } = Typography;

const FUNCTION_COLORS: Record<string, string> = {
    BACKGROUND: 'default',
    METHODOLOGY: 'blue',
    COMPARISON: 'orange',
    CRITIQUE: 'red',
    BASIS: 'green',
    UNKNOWN: 'default'
};

export const EdgePanel: React.FC = () => {
    const { t } = useTranslation();
    const { selectedEdge, setSelectedEdge, nodes } = useGraphStore();

    const INTENT_CONFIG: Record<CitationIntent, { color: string; label: string; description: string }> = {
        SUPPORT: { color: 'success', label: t('edgePanel.support'), description: t('edgePanel.supportDesc') },
        OPPOSE: { color: 'error', label: t('edgePanel.oppose'), description: t('edgePanel.opposeDesc') },
        NEUTRAL: { color: 'default', label: t('edgePanel.neutral'), description: t('edgePanel.neutralDesc') },
        UNKNOWN: { color: 'warning', label: t('edgePanel.uncategorized'), description: t('edgePanel.uncategorizedDesc') },
    };

    if (!selectedEdge) {
        return null;
    }

    const handleClose = () => {
        setSelectedEdge(null);
    };

    // Find source and target papers
    const sourcePaper = nodes.find(n => n.id === selectedEdge.source);
    const targetPaper = nodes.find(n => n.id === selectedEdge.target);

    const intentInfo = INTENT_CONFIG[selectedEdge.intent] || INTENT_CONFIG.UNKNOWN;

    // Get current model config
    const aiConfig = aiConfigService.getConfig();
    const modelName = aiConfig?.model || t('edgePanel.unknownModel');

    return (
        <Drawer
            title={
                <div className="panel-header">
                    <ShareAltOutlined style={{ marginRight: 8 }} />
                    {t('edgePanel.title')}
                </div>
            }
            placement="right"
            width={400}
            open={!!selectedEdge}
            onClose={handleClose}
            closeIcon={<CloseOutlined />}
            mask={false}
        >
            <div className="edge-panel-content">
                {/* Intent Status */}
                <div className="intent-section">
                    <div className="intent-header">
                        <Tag color={intentInfo.color} style={{ fontSize: '14px', padding: '4px 10px' }}>
                            {intentInfo.label}
                        </Tag>
                        {selectedEdge.confidence > 0 && (
                            <span className="confidence-text">
                                {t('edgePanel.confidence')}: {(selectedEdge.confidence * 100).toFixed(0)}%
                            </span>
                        )}
                    </div>
                    <Paragraph type="secondary" style={{ marginTop: 8, fontSize: '12px' }}>
                        {intentInfo.description}
                    </Paragraph>
                </div>

                {/* Deep Insight Analysis */}
                {(selectedEdge.importance_score && selectedEdge.importance_score > 0) && (
                    <div className="deep-insight-box" style={{ marginTop: 16, background: '#fafafa', padding: 12, borderRadius: 6, border: '1px solid #f0f0f0' }}>
                        <div className="insight-row" style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
                            <Text type="secondary" style={{ marginRight: 8, fontSize: 12 }}>{t('edgePanel.importance')}:</Text>
                            <Rate disabled defaultValue={selectedEdge.importance_score} count={5} style={{ fontSize: 14 }} />
                        </div>
                        <div className="insight-row" style={{ marginBottom: 8 }}>
                            <Space wrap>
                                {selectedEdge.citation_function && selectedEdge.citation_function !== 'UNKNOWN' && (
                                    <Tag color={FUNCTION_COLORS[selectedEdge.citation_function] || 'default'}>
                                        {selectedEdge.citation_function}
                                    </Tag>
                                )}
                                {selectedEdge.citation_sentiment && selectedEdge.citation_sentiment !== 'UNKNOWN' && (
                                    <Tag color={selectedEdge.citation_sentiment === 'POSITIVE' ? 'success' : selectedEdge.citation_sentiment === 'NEGATIVE' ? 'error' : 'default'}>
                                        SENTIMENT: {selectedEdge.citation_sentiment}
                                    </Tag>
                                )}
                            </Space>
                        </div>
                        {selectedEdge.key_concept && (
                            <div className="key-concept" style={{ background: '#e6f7ff', padding: '6px 10px', borderRadius: 4, fontSize: 13, border: '1px solid #91d5ff' }}>
                                <BulbOutlined style={{ color: '#1890ff', marginRight: 6 }} />
                                <Text strong style={{ color: '#0050b3' }}>{t('edgePanel.keyConcept')}: </Text>
                                <Text style={{ color: '#003a8c' }}>{selectedEdge.key_concept}</Text>
                            </div>
                        )}
                    </div>
                )}

                <Divider />

                {/* Reasoning */}
                <div className="section-title">
                    <RobotOutlined style={{ marginRight: 6 }} />
                    {t('edgePanel.aiReasoning')}
                </div>
                <div className="reasoning-box">
                    <Paragraph>
                        {selectedEdge.reasoning || t('edgePanel.noReasoning')}
                    </Paragraph>
                </div>

                <Divider />

                {/* Relationship Visual */}
                <div className="relationship-visual">
                    <Card size="small" title={t('edgePanel.sourceCard')} className="paper-card source-card">
                        <Text strong>{sourcePaper?.title || selectedEdge.source}</Text>
                        <div style={{ marginTop: 4 }}>
                            <Tag>{sourcePaper?.year || 'Unknown'}</Tag>
                            <Text type="secondary" style={{ fontSize: 12 }}>{sourcePaper?.authors[0]}</Text>
                        </div>
                    </Card>

                    <div className="arrow-connector">
                        <ArrowRightOutlined style={{ fontSize: 24, color: '#999' }} />
                    </div>

                    <Card size="small" title={t('edgePanel.targetCard')} className="paper-card target-card">
                        <Text strong>{targetPaper?.title || selectedEdge.target}</Text>
                        <div style={{ marginTop: 4 }}>
                            <Tag>{targetPaper?.year || 'Unknown'}</Tag>
                            <Text type="secondary" style={{ fontSize: 12 }}>{targetPaper?.authors[0]}</Text>
                        </div>
                    </Card>
                </div>

                {/* Context (if available) */}
                {(selectedEdge.citation_contexts?.length || 0) > 0 && (
                    <>
                        <Divider />
                        <div className="section-title">{t('edgePanel.citationContext')}</div>
                        <div className="context-list">
                            {selectedEdge.citation_contexts?.map((ctx, idx) => (
                                <div key={idx} className="context-item">
                                    <Paragraph className="context-text">
                                        "...{ctx.trim()}..."
                                    </Paragraph>
                                </div>
                            ))}
                        </div>
                    </>
                )}

                <Divider />

                <Descriptions column={1} size="small" bordered>
                    <Descriptions.Item label={t('edgePanel.citationId')}>{selectedEdge.source.slice(0, 8)}...{selectedEdge.target.slice(0, 8)}</Descriptions.Item>
                    <Descriptions.Item label={t('edgePanel.analysisModel')}>{modelName}</Descriptions.Item>
                </Descriptions>
            </div>
        </Drawer>
    );
};
