/**
 * SearchBar Component - Paper search and project creation
 * Uses unified PaperSearchPanel for multi-source search
 */
import React, { useState, useEffect } from 'react';
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

            message.success('开始构建引用图谱...');

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
                            message.success(`图谱构建完成！${nodeCount} 篇论文，${edgeCount} 条引用（按被引量排序）`);
                        } else {
                            message.warning('未能获取论文数据，可能是 API 限流，请稍后重试');
                        }
                        onProjectCreated?.();
                    });
                    eventSource.close();
                } else if (progress.status === 'failed') {
                    setIsBuilding(false);
                    setBuildProgress(null);
                    message.error('图谱构建失败：' + progress.message);
                    eventSource.close();
                }
            });

        } catch (error) {
            console.error('Build failed:', error);
            message.error('创建项目失败');
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
                                ⏳ API 访问频率受限
                                {retryCountdown > 0 && ` (${retryCountdown}秒后可重试)`}
                            </span>
                            {retryCountdown === 0 && (
                                <Button
                                    size="small"
                                    icon={<ReloadOutlined />}
                                    onClick={() => setRateLimited(false)}
                                    style={{ marginLeft: 8 }}
                                >
                                    重试
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
                    placeholder="输入 DOI、arXiv ID 或论文标题..."
                />
            )}

            {/* Selected Paper & Build Options */}
            {selectedPaper && (
                <Card className="selected-paper" size="small" title="已选择论文">
                    <div className="paper-info">
                        <h4>{selectedPaper.title}</h4>
                        <p>{selectedPaper.authors?.slice(0, 5).join(', ')}</p>
                        <Space>
                            {selectedPaper.year && <Tag color="blue">{selectedPaper.year}</Tag>}
                            <Tag color="green">被引 {selectedPaper.citation_count || 0}</Tag>
                        </Space>
                    </div>

                    <div className="build-options">
                        <div className="option-row">
                            <span className="option-label">论文数量:</span>
                            <Select value={maxPapers} onChange={setMaxPapers} style={{ width: 100 }}>
                                <Option value={10}>10篇 (快速)</Option>
                                <Option value={20}>20篇 (推荐)</Option>
                                <Option value={30}>30篇</Option>
                                <Option value={50}>50篇</Option>
                            </Select>
                        </div>
                        <div className="option-row">
                            <span className="option-label">递归深度:</span>
                            <Select value={depth} onChange={setDepth} style={{ width: 100 }}>
                                <Option value={1}>1层 (推荐)</Option>
                                <Option value={2}>2层</Option>
                            </Select>
                        </div>
                        <div className="option-row">
                            <span className="option-label">抓取方向:</span>
                            <Select value={direction} onChange={setDirection} style={{ width: 100 }}>
                                <Option value="both">双向</Option>
                                <Option value="forward">参考文献</Option>
                                <Option value="backward">施引文献</Option>
                            </Select>
                        </div>
                        <Space style={{ marginTop: 12, width: '100%' }}>
                            <Button
                                onClick={() => setSelectedPaper(null)}
                            >
                                重新搜索
                            </Button>
                            <Button
                                type="primary"
                                icon={<PlusOutlined />}
                                onClick={handleBuildGraph}
                                loading={isBuilding}
                            >
                                构建图谱
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
                            {buildProgress.message || '正在构建引用图谱...'}
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
                            API 限流中，正在尝试备用数据源...
                        </div>
                    )}
                </Card>
            )}
        </div>
    );
};
