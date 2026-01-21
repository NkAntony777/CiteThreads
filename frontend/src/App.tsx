/**
 * CiteThreads - Main App Component
 */
import React, { useState, useCallback } from 'react';
import { Layout, Typography, Button, Space, Tooltip, Dropdown, message } from 'antd';
import {
    MenuUnfoldOutlined,
    MenuFoldOutlined,
    DownloadOutlined,
    GithubOutlined,
    SettingOutlined,
    QuestionCircleOutlined,
    FolderOutlined,
    RobotOutlined,
    ShareAltOutlined,
    EditOutlined
} from '@ant-design/icons';
import {
    SearchBar,
    GraphCanvas,
    NodePanel,
    AISettings,
    ProjectList,
    EdgePanel,
    GraphFilters
} from './components';
import { WritingAssistant } from './components/WritingAssistant';
import { useGraphStore } from './stores/graphStore';
import { projectApi } from './services/api';
import { initializeAI, aiConfigService } from './services/aiConfig';
import './App.css';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

const App: React.FC = () => {
    const [siderCollapsed, setSiderCollapsed] = useState(false);
    const [settingsVisible, setSettingsVisible] = useState(false);
    const [projectListVisible, setProjectListVisible] = useState(false);
    const [viewMode, setViewMode] = useState<'graph' | 'writing'>('graph');
    const { currentProject, loadProject, analyzeProject, buildProgress } = useGraphStore();

    // Initialize AI config on mount
    React.useEffect(() => {
        initializeAI().catch(e => console.error('Failed to initialize AI:', e));
    }, []);

    const handleAnalyze = async () => {
        if (!currentProject) return;

        // Ensure AI is configured and synced
        const chatConfig = aiConfigService.getConfig();
        if (!chatConfig?.apiKey) {
            message.error('请先在设置中配置 AI API 密钥');
            setSettingsVisible(true);
            return;
        }

        try {
            message.loading({ content: '正在同步 AI 配置并启动分析...', key: 'analyzing' });

            // Force sync config to backend
            await aiConfigService.applyConfig(chatConfig);
            // Embedding sync removed as per user request

            await analyzeProject();
            message.success({ content: 'AI 分析完成', key: 'analyzing' });
        } catch (e) {
            message.error({ content: 'AI 分析失败', key: 'analyzing' });
        }
    };

    const handleExport = (format: 'bibtex' | 'ris' | 'json') => {
        if (!currentProject) {
            message.warning('请先构建图谱');
            return;
        }

        const url = projectApi.exportUrl(currentProject.metadata.id, format);
        window.open(url, '_blank');
    };

    const handleSelectProject = useCallback(async (projectId: string) => {
        try {
            await loadProject(projectId);
            message.success('项目已加载');
        } catch (e) {
            message.error('加载项目失败');
        }
    }, [loadProject]);

    const exportMenuItems = [
        { key: 'bibtex', label: '导出 BibTeX', onClick: () => handleExport('bibtex') },
        { key: 'ris', label: '导出 RIS', onClick: () => handleExport('ris') },
        { key: 'json', label: '导出 JSON', onClick: () => handleExport('json') },
    ];

    return (
        <Layout className="app-layout">
            {/* Header */}
            <Header className="app-header">
                <div className="header-left">
                    <Button
                        type="text"
                        icon={siderCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                        onClick={() => setSiderCollapsed(!siderCollapsed)}
                        style={{ color: '#666', fontSize: '18px', marginRight: 8 }}
                    />
                    <div className="logo">
                        <ShareAltOutlined className="logo-icon" />
                        <Title level={4} className="logo-text">CiteThreads</Title>
                    </div>
                    <span className="tagline">学术引用脉络可视化</span>
                    <Tooltip title="查看我的项目历史">
                        <Button
                            type="default"
                            className="header-action"
                            icon={<FolderOutlined />}
                            onClick={() => setProjectListVisible(true)}
                            style={{ marginLeft: 16 }}
                        >
                            我的项目
                        </Button>
                    </Tooltip>
                </div>

                <div className="header-right">
                    <Space>
                        {currentProject && (
                            <>
                                <Tooltip title="AI 意图分析">
                                    <Button
                                        icon={<RobotOutlined />}
                                        onClick={handleAnalyze}
                                        loading={buildProgress?.status === 'analyzing'}
                                    >
                                        意图分析
                                    </Button>
                                </Tooltip>
                                <Tooltip title="AI论文写作助手">
                                    <Button
                                        icon={<EditOutlined />}
                                        onClick={() => setViewMode('writing')}
                                    >
                                        论文助手
                                    </Button>
                                </Tooltip>
                                <Dropdown menu={{ items: exportMenuItems }} placement="bottomRight">
                                    <Button icon={<DownloadOutlined />}>导出</Button>
                                </Dropdown>
                            </>
                        )}
                        <Tooltip title="AI 设置">
                            <Button
                                type="text"
                                icon={<SettingOutlined />}
                                onClick={() => setSettingsVisible(true)}
                            />
                        </Tooltip>
                        <Tooltip title="帮助">
                            <Button type="text" icon={<QuestionCircleOutlined />} />
                        </Tooltip>
                        <Tooltip title="GitHub">
                            <Button
                                type="text"
                                icon={<GithubOutlined />}
                                onClick={() => window.open('https://github.com', '_blank')}
                            />
                        </Tooltip>
                    </Space>
                </div>
            </Header>

            <Layout>
                {/* Sidebar - Search & Controls */}
                <Sider
                    width={380}
                    collapsedWidth={0}
                    collapsed={siderCollapsed}
                    className="app-sider"
                    theme="light"
                >

                    <div className="sider-content">
                        <SearchBar />
                        {currentProject && <GraphFilters />}
                    </div>
                </Sider>

                {/* Main Content - Graph Visualization or Writing Assistant */}
                <Content className="app-content">
                    {viewMode === 'graph' ? (
                        <GraphCanvas />
                    ) : (
                        currentProject && (
                            <WritingAssistant
                                projectId={currentProject.metadata.id}
                                graphNodes={currentProject.graph?.nodes || []}
                                onBack={() => setViewMode('graph')}
                            />
                        )
                    )}
                </Content>
            </Layout>

            {/* Node Detail Panel */}
            <NodePanel />
            <EdgePanel />

            {/* AI Settings Panel */}
            <AISettings
                visible={settingsVisible}
                onClose={() => setSettingsVisible(false)}
            />

            {/* Project List Panel */}
            <ProjectList
                visible={projectListVisible}
                onClose={() => setProjectListVisible(false)}
                onSelectProject={handleSelectProject}
                currentProjectId={currentProject?.metadata.id}
            />
        </Layout>
    );
};

export default App;
