/**
 * CiteThreads - Main App Component
 */
import React, { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Layout, Typography, Button, Space, Tooltip, Dropdown, message } from 'antd';
import {
    MenuUnfoldOutlined,
    MenuFoldOutlined,
    DownloadOutlined,
    GithubOutlined,
    SettingOutlined,
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
    const { t } = useTranslation();
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
            message.error(t('app.pleaseConfigureApiKey'));
            setSettingsVisible(true);
            return;
        }

        try {
            message.loading({ content: t('app.syncingAiConfig'), key: 'analyzing' });

            // Force sync config to backend
            await aiConfigService.applyConfig(chatConfig);
            // Embedding sync removed as per user request

            await analyzeProject();
            message.success({ content: t('app.aiAnalysisComplete'), key: 'analyzing' });
        } catch (e) {
            message.error({ content: t('app.aiAnalysisFailed'), key: 'analyzing' });
        }
    };

    const handleExport = (format: 'bibtex' | 'ris' | 'json') => {
        if (!currentProject) {
            message.warning(t('app.pleaseCreateGraphFirst'));
            return;
        }

        const url = projectApi.exportUrl(currentProject.metadata.id, format);
        window.open(url, '_blank');
    };

    const handleSelectProject = useCallback(async (projectId: string) => {
        try {
            await loadProject(projectId);
            message.success(t('app.projectLoaded'));
        } catch (e) {
            message.error(t('app.loadProjectFailed'));
        }
    }, [loadProject, t]);

    const exportMenuItems = [
        { key: 'bibtex', label: t('app.exportBibtex'), onClick: () => handleExport('bibtex') },
        { key: 'ris', label: t('app.exportRis'), onClick: () => handleExport('ris') },
        { key: 'json', label: t('app.exportJson'), onClick: () => handleExport('json') },
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
                    <span className="tagline">{t('app.tagline')}</span>
                    <Tooltip title={t('app.viewProjects')}>
                        <Button
                            type="default"
                            className="header-action"
                            icon={<FolderOutlined />}
                            onClick={() => setProjectListVisible(true)}
                            style={{ marginLeft: 16 }}
                        >
                            {t('app.myProjects')}
                        </Button>
                    </Tooltip>
                </div>

                <div className="header-right">
                    <Space>
                        {currentProject && (
                            <>
                                <Tooltip title={t('app.aiIntentAnalysis')}>
                                    <Button
                                        icon={<RobotOutlined />}
                                        onClick={handleAnalyze}
                                        loading={buildProgress?.status === 'analyzing'}
                                    >
                                        {t('app.intentAnalysis')}
                                    </Button>
                                </Tooltip>
                                <Tooltip title={t('app.aiWritingAssistant')}>
                                    <Button
                                        icon={<EditOutlined />}
                                        onClick={() => setViewMode('writing')}
                                    >
                                        {t('app.writingAssistant')}
                                    </Button>
                                </Tooltip>
                                <Dropdown menu={{ items: exportMenuItems }} placement="bottomRight">
                                    <Button icon={<DownloadOutlined />}>{t('app.export')}</Button>
                                </Dropdown>
                            </>
                        )}
                        <Tooltip title={t('app.aiSettings')}>
                            <Button
                                type="text"
                                icon={<SettingOutlined />}
                                onClick={() => setSettingsVisible(true)}
                            />
                        </Tooltip>
                        <Tooltip title="GitHub">
                            <Button
                                type="text"
                                icon={<GithubOutlined />}
                                onClick={() => window.open('https://github.com/NkAntony777/CiteThreads', '_blank')}
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

