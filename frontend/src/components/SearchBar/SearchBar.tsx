/**
 * SearchBar Component - Paper search and project creation
 * Uses unified PaperSearchPanel for multi-source search
 */
import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Select, Card, Tag, Space, message, Spin, Alert, Progress } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { projectApi } from '../../services/api';
import { useGraphStore } from '../../stores/graphStore';
import { PaperSearchPanel } from '../PaperSearchPanel';
import type { Paper, CrawlProgress } from '../../types';
import './SearchBar.css';

const { Option } = Select;

interface SearchBarProps {
    onProjectCreated?: () => void;
}

export const SearchBar: React.FC<SearchBarProps> = ({ onProjectCreated }) => {
    const { t } = useTranslation();
    const [selectedPaper, setSelectedPaper] = useState<Paper | null>(null);
    const [depth, setDepth] = useState(1);
    const [maxPapers, setMaxPapers] = useState(20);
    const [direction, setDirection] = useState<'forward' | 'backward' | 'both'>('both');
    const [rateLimited, setRateLimited] = useState(false);
    const [retryCountdown, setRetryCountdown] = useState(0);

    const { setProject, setBuildProgress, setIsBuilding, isBuilding, buildProgress } = useGraphStore();

    // Countdown timer for rate limit
    useEffect(() => {
        if (retryCountdown > 0) {
            const timer = setTimeout(() => setRetryCountdown(retryCountdown - 1), 1000);
            return () => clearTimeout(timer);
        } else if (retryCountdown === 0 && rateLimited) {
            setRateLimited(false);
        }
    }, [retryCountdown, rateLimited]);

    const handleSelectPaper = (paper: Paper) => {
        setSelectedPaper(paper);
    };

    const handleBuildGraph = async () => {
        if (!selectedPaper) return;

        setIsBuilding(true);
        setRateLimited(false);
        try {
            const seedPaperId = selectedPaper.doi
                ? selectedPaper.doi
                : selectedPaper.arxiv_id
                    ? `arXiv:${selectedPaper.arxiv_id}`
                    : selectedPaper.id;

            const metadata = await projectApi.create({
                seed_paper_id: seedPaperId,
                name: selectedPaper.title.slice(0, 50),
                depth,
                direction,
                max_papers: maxPapers,
            });

            message.success(t('searchBar.startBuildingGraph'));

            const eventSource = projectApi.subscribeProgress(metadata.id, (progress: CrawlProgress) => {
                setBuildProgress(progress);

                // Check for rate limit status
                if (progress.status === 'rate_limited') {
                    setRateLimited(true);
                    setRetryCountdown(30);
                }

                if (progress.status === 'completed') {
                    projectApi.get(metadata.id).then(project => {
                        setProject(project);
                        setIsBuilding(false);
                        setBuildProgress(null);
                        setRateLimited(false);

                        const nodeCount = project.graph.nodes.length;
                        const edgeCount = project.graph.edges.length;
                        if (nodeCount > 0) {
                            message.success(`${t('searchBar.buildComplete')} ${nodeCount}${t('searchBar.papersAndCitations', { edges: edgeCount })}`);
                        } else {
                            message.warning(t('searchBar.noDataRetry'));
                        }
                        onProjectCreated?.();
                    });
                    eventSource.close();
                } else if (progress.status === 'failed') {
                    setIsBuilding(false);
                    setBuildProgress(null);
                    message.error(t('searchBar.buildFailed') + progress.message);
                    eventSource.close();
                }
            });

        } catch (error) {
            console.error('Build failed:', error);
            message.error(t('searchBar.createProjectFailed'));
            setIsBuilding(false);
        }
    };

    return (
        <div className="search-bar">
            {/* Rate Limit Alert */}
            {rateLimited && (
                <Alert
                    type="warning"
                    showIcon
                    message={
                        <div className="rate-limit-alert">
                            <span>
                                ⏳ {t('searchBar.rateLimited')}
                                {retryCountdown > 0 && ` (${retryCountdown}${t('searchBar.retryIn')})`}
                            </span>
                            {retryCountdown === 0 && (
                                <Button
                                    size="small"
                                    icon={<ReloadOutlined />}
                                    onClick={() => setRateLimited(false)}
                                    style={{ marginLeft: 8 }}
                                >
                                    {t('common.retry')}
                                </Button>
                            )}
                        </div>
                    }
                    style={{ marginBottom: 12 }}
                />
            )}

            {/* Paper Search Panel */}
            {!selectedPaper && (
                <PaperSearchPanel
                    mode="graph-builder"
                    onSelectForGraph={handleSelectPaper}
                    limit={15}
                    placeholder={t('searchBar.placeholder')}
                />
            )}

            {/* Selected Paper & Build Options */}
            {selectedPaper && (
                <Card className="selected-paper" size="small" title={t('searchBar.selectedPaper')}>
                    <div className="paper-info">
                        <h4>{selectedPaper.title}</h4>
                        <p>{selectedPaper.authors?.slice(0, 5).join(', ')}</p>
                        <Space>
                            {selectedPaper.year && <Tag color="blue">{selectedPaper.year}</Tag>}
                            <Tag color="green">{t('searchBar.cited')} {selectedPaper.citation_count || 0}</Tag>
                        </Space>
                    </div>

                    <div className="build-options">
                        <div className="option-row">
                            <span className="option-label">{t('searchBar.paperCount')}:</span>
                            <Select value={maxPapers} onChange={setMaxPapers} style={{ width: 100 }}>
                                <Option value={10}>{t('searchBar.papers10')}</Option>
                                <Option value={20}>{t('searchBar.papers20')}</Option>
                                <Option value={30}>{t('searchBar.papers30')}</Option>
                                <Option value={50}>{t('searchBar.papers50')}</Option>
                            </Select>
                        </div>
                        <div className="option-row">
                            <span className="option-label">{t('searchBar.depth')}:</span>
                            <Select value={depth} onChange={setDepth} style={{ width: 100 }}>
                                <Option value={1}>{t('searchBar.depth1')}</Option>
                                <Option value={2}>{t('searchBar.depth2')}</Option>
                            </Select>
                        </div>
                        <div className="option-row">
                            <span className="option-label">{t('searchBar.direction')}:</span>
                            <Select value={direction} onChange={setDirection} style={{ width: 100 }}>
                                <Option value="both">{t('searchBar.directionBoth')}</Option>
                                <Option value="forward">{t('searchBar.directionForward')}</Option>
                                <Option value="backward">{t('searchBar.directionBackward')}</Option>
                            </Select>
                        </div>
                        <Space style={{ marginTop: 12, width: '100%' }}>
                            <Button
                                onClick={() => setSelectedPaper(null)}
                            >
                                {t('searchBar.reSearch')}
                            </Button>
                            <Button
                                type="primary"
                                icon={<PlusOutlined />}
                                onClick={handleBuildGraph}
                                loading={isBuilding}
                            >
                                {t('searchBar.buildGraph')}
                            </Button>
                        </Space>
                    </div>
                </Card>
            )}

            {/* Build Progress */}
            {isBuilding && buildProgress && (
                <Card className="build-progress" size="small">
                    <div className="progress-content">
                        <Spin size="small" />
                        <span className="progress-message">
                            {buildProgress.message || t('searchBar.buildingGraph')}
                        </span>
                    </div>
                    {buildProgress.total > 0 && (
                        <Progress
                            percent={Math.round((buildProgress.progress / buildProgress.total) * 100)}
                            size="small"
                            status={buildProgress.status === 'rate_limited' ? 'exception' : 'active'}
                        />
                    )}
                    {buildProgress.status === 'rate_limited' && (
                        <div className="rate-limit-hint">
                            {t('searchBar.rateLimitedTrying')}
                        </div>
                    )}
                </Card>
            )}
        </div>
    );
};
