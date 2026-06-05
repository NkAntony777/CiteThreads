/**
 * SearchBar Component — paper search and "start writing" trigger.
 *
 * 2026-06 refactor: removed the depth / direction / maxPapers
 * selectors. The post-pick flow is now a single "Start Writing"
 * action that creates a project with a fixed light crawl
 * (depth=1, both directions, max 30 papers) and immediately hands
 * the project to the writing view; the crawl finishes in the
 * background and the references panel auto-fills as it goes.
 */
import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Card, Tag, Space, message, Spin, Alert, Progress, Segmented } from 'antd';
import { EditOutlined, ReloadOutlined } from '@ant-design/icons';
import { projectApi } from '../../services/api';
import { useGraphStore } from '../../stores/graphStore';
import { PaperSearchPanel } from '../PaperSearchPanel';
import { SmartSearchPanel } from '../SmartSearchPanel';
import type { Paper, CrawlProgress, ProjectMetadata } from '../../types';
import './SearchBar.css';

interface SearchBarProps {
    /**
     * Called once the project is created and saved in the store.
     * The parent (App) uses this to switch the main view to the
     * writing flow immediately, instead of waiting for the
     * background crawl to finish.
     */
    onProjectCreated?: (project: ProjectMetadata) => void;
}

export const SearchBar: React.FC<SearchBarProps> = ({ onProjectCreated }) => {
    const { t } = useTranslation();
    const [selectedPaper, setSelectedPaper] = useState<Paper | null>(null);
    const [rateLimited, setRateLimited] = useState(false);
    const [retryCountdown, setRetryCountdown] = useState(0);
    const [searchMode, setSearchMode] = useState<'smart' | 'classic'>('smart');

    const { setProject, setBuildProgress, setIsBuilding, isBuilding, buildProgress } = useGraphStore();

    const eventSourceRef = useRef<EventSource | null>(null);

    useEffect(() => {
        return () => {
            eventSourceRef.current?.close();
            eventSourceRef.current = null;
        };
    }, []);

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

    const handleStartWriting = async () => {
        if (!selectedPaper) return;

        eventSourceRef.current?.close();
        eventSourceRef.current = null;

        setIsBuilding(true);
        setRateLimited(false);
        try {
            const seedPaperId = selectedPaper.doi
                ? selectedPaper.doi
                : selectedPaper.arxiv_id
                    ? `arXiv:${selectedPaper.arxiv_id}`
                    : selectedPaper.id;

            // Light crawl defaults: 1 hop, both directions, 30 papers.
            // These are the "hidden context" budget — enough to give
            // the writing agent something to snowball from, small
            // enough to keep the start latency low.
            const metadata = await projectApi.create({
                seed_paper_id: seedPaperId,
                name: selectedPaper.title.slice(0, 50),
            });

            // Hand the project to the store and notify the parent
            // immediately. The user is now in the writing view;
            // the crawl continues in the background and the
            // references panel re-fetches when it completes.
            onProjectCreated?.(metadata);

            // Subscribe to the SSE progress stream for the small
            // "preparing context" indicator. Don't block on it.
            const eventSource = projectApi.subscribeProgress(metadata.id, (progress: CrawlProgress) => {
                if (eventSourceRef.current !== eventSource) return;

                setBuildProgress(progress);

                if (progress.status === 'rate_limited') {
                    setRateLimited(true);
                    setRetryCountdown(30);
                }

                if (progress.status === 'completed') {
                    projectApi
                        .get(metadata.id)
                        .then(project => {
                            setProject(project);
                            const nodeCount = project.graph.nodes.length;
                            const edgeCount = project.graph.edges.length;
                            if (nodeCount > 0) {
                                message.success(
                                    `${t('searchBar.startWritingComplete')} ${nodeCount}${t('searchBar.papersAndCitations', { edges: edgeCount })}`,
                                );
                            } else {
                                message.warning(t('searchBar.noDataRetry'));
                            }
                        })
                        .catch(error => {
                            console.error('Failed to fetch project after build completion:', error);
                            message.error(t('searchBar.fetchProjectFailed'));
                        })
                        .finally(() => {
                            setIsBuilding(false);
                            setBuildProgress(null);
                            setRateLimited(false);
                        });
                    eventSource.close();
                    if (eventSourceRef.current === eventSource) {
                        eventSourceRef.current = null;
                    }
                } else if (progress.status === 'failed') {
                    setIsBuilding(false);
                    setBuildProgress(null);
                    message.error(t('searchBar.buildFailed') + progress.message);
                    eventSource.close();
                    if (eventSourceRef.current === eventSource) {
                        eventSourceRef.current = null;
                    }
                }
            });

            eventSourceRef.current = eventSource;

            eventSource.onerror = () => {
                eventSource.close();
                if (eventSourceRef.current === eventSource) {
                    eventSourceRef.current = null;
                }
                setIsBuilding(false);
                setBuildProgress(null);
                setRateLimited(false);
            };

        } catch (error) {
            console.error('Start writing failed:', error);
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
                <>
                    <Segmented
                        block
                        value={searchMode}
                        onChange={(value) => setSearchMode(value as 'smart' | 'classic')}
                        options={[
                            { label: t('smartSearch.modeSmart'), value: 'smart' },
                            { label: t('smartSearch.modeClassic'), value: 'classic' },
                        ]}
                    />
                    {searchMode === 'smart' ? (
                        <SmartSearchPanel onSelectForGraph={handleSelectPaper} />
                    ) : (
                        <PaperSearchPanel
                            mode="graph-builder"
                            onSelectForGraph={handleSelectPaper}
                            limit={15}
                            placeholder={t('searchBar.placeholder')}
                        />
                    )}
                </>
            )}

            {/* Selected Paper — single Start Writing CTA */}
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
                    <Space style={{ marginTop: 12, width: '100%' }}>
                        <Button onClick={() => setSelectedPaper(null)}>
                            {t('searchBar.reSearch')}
                        </Button>
                        <Button
                            type="primary"
                            icon={<EditOutlined />}
                            onClick={handleStartWriting}
                            loading={isBuilding}
                            block
                        >
                            {t('searchBar.startWriting')}
                        </Button>
                    </Space>
                </Card>
            )}

            {/* Preparing context (small inline progress) */}
            {isBuilding && buildProgress && (
                <Card className="build-progress" size="small">
                    <div className="progress-content">
                        <Spin size="small" />
                        <span className="progress-message">
                            {t('searchBar.preparingContext')}{' '}
                            {buildProgress.progress}/{buildProgress.total}
                        </span>
                    </div>
                    {buildProgress.total > 0 && (
                        <Progress
                            percent={Math.round((buildProgress.progress / buildProgress.total) * 100)}
                            size="small"
                            status={buildProgress.status === 'rate_limited' ? 'exception' : 'active'}
                            showInfo={false}
                        />
                    )}
                </Card>
            )}
        </div>
    );
};
