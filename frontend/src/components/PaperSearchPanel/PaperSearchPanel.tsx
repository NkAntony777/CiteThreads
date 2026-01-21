/**
 * PaperSearchPanel - Unified paper search component
 * Used by both main SearchBar (graph building) and WritingAssistant (reference adding)
 */
import React, { useState } from 'react';
import { Input, Button, List, Tag, Space, Empty, Spin, message } from 'antd';
import { SearchOutlined, PlusOutlined, PartitionOutlined } from '@ant-design/icons';
import type { Paper } from '../../types';
import { writingApi } from '../../services/writingApi';
import './PaperSearchPanel.css';

const { Search } = Input;

export type SearchMode = 'graph-builder' | 'reference-adder';

interface PaperSearchPanelProps {
    /** Search mode determines the action button */
    mode: SearchMode;
    /** Project ID for reference-adder mode */
    projectId?: string;
    /** Callback when paper is selected for graph building */
    onSelectForGraph?: (paper: Paper) => void;
    /** Callback when paper is added as reference */
    onAddReference?: (paper: Paper) => void;
    /** Optional: limit search results */
    limit?: number;
    /** Optional: placeholder text */
    placeholder?: string;
    /** Optional: show as compact mode */
    compact?: boolean;
}

export const PaperSearchPanel: React.FC<PaperSearchPanelProps> = ({
    mode,
    // projectId is reserved for future use
    onSelectForGraph,
    onAddReference,
    limit = 10,
    placeholder = '输入关键词搜索论文...',
    compact = false,
}) => {
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<Paper[]>([]);
    const [searching, setSearching] = useState(false);
    const [selectedPaper, setSelectedPaper] = useState<Paper | null>(null);

    const handleSearch = async (query?: string) => {
        const q = query || searchQuery;
        if (!q.trim()) return;

        setSearching(true);
        setSelectedPaper(null);
        try {
            // Use the multi-source search API
            const result = await writingApi.searchPapersUnified(q, limit);
            setSearchResults(result.papers || []);
            if (result.papers?.length === 0) {
                message.info('未找到相关论文，请尝试其他关键词');
            }
        } catch (error) {
            console.error('Search failed:', error);
            message.error('搜索失败，请重试');
        } finally {
            setSearching(false);
        }
    };

    const handleAction = (paper: Paper) => {
        if (mode === 'graph-builder') {
            setSelectedPaper(paper);
            onSelectForGraph?.(paper);
        } else {
            onAddReference?.(paper);
            message.success(`已添加: ${paper.title.slice(0, 30)}...`);
        }
    };

    const actionButton = (paper: Paper) => {
        if (mode === 'graph-builder') {
            return (
                <Button
                    type={selectedPaper?.id === paper.id ? 'primary' : 'default'}
                    size="small"
                    icon={<PartitionOutlined />}
                    onClick={() => handleAction(paper)}
                >
                    {selectedPaper?.id === paper.id ? '已选择' : '选择'}
                </Button>
            );
        }
        return (
            <Button
                type="primary"
                size="small"
                icon={<PlusOutlined />}
                onClick={() => handleAction(paper)}
            >
                添加引用
            </Button>
        );
    };

    return (
        <div className={`paper-search-panel ${compact ? 'compact' : ''}`}>
            <div className="search-input-row">
                <Search
                    placeholder={placeholder}
                    enterButton={<><SearchOutlined /> 搜索</>}
                    size={compact ? 'middle' : 'large'}
                    loading={searching}
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onSearch={() => handleSearch()}
                    className="search-input"
                />
            </div>

            <div className="search-tip">
                支持 DOI、arXiv ID 或标题关键词。数据源：OpenAlex、arXiv、DBLP、PubMed
            </div>

            {/* Search Results */}
            <div className="search-results">
                {searching ? (
                    <div className="search-loading">
                        <Spin tip="正在搜索多个数据源..." />
                    </div>
                ) : searchResults.length > 0 ? (
                    <List
                        size="small"
                        dataSource={searchResults}
                        renderItem={(paper, index) => (
                            <List.Item
                                className="paper-item"
                                actions={[actionButton(paper)]}
                            >
                                <List.Item.Meta
                                    avatar={
                                        <div className="paper-rank">
                                            {index + 1}
                                        </div>
                                    }
                                    title={
                                        <span className="paper-title" title={paper.title}>
                                            {paper.title}
                                        </span>
                                    }
                                    description={
                                        <Space size={[8, 4]} wrap>
                                            <span className="paper-authors">
                                                {paper.authors?.slice(0, 2).join(', ')}
                                                {(paper.authors?.length || 0) > 2 ? ' et al.' : ''}
                                            </span>
                                            {paper.year && <Tag color="blue">{paper.year}</Tag>}
                                            <Tag color="green">被引 {paper.citation_count || 0}</Tag>
                                            {paper.venue && (
                                                <Tag>{paper.venue.slice(0, 20)}</Tag>
                                            )}
                                        </Space>
                                    }
                                />
                            </List.Item>
                        )}
                    />
                ) : searchQuery && !searching ? (
                    <Empty
                        description="输入关键词开始搜索"
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                    />
                ) : null}
            </div>
        </div>
    );
};

export default PaperSearchPanel;
