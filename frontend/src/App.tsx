/**
 * CiteThreads - Main App Component
 *
 * 2026-06 refactor: the entire app is now a single ChatGPT-style
 * surface (ChatView) with the conversation list as a left sider.
 * Search, snowball, and CTDP long-form drafting all live inside
 * one chat session — the user just types in plain language.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Layout, Button, Tooltip, Space } from 'antd';
import {
    GithubOutlined,
    SettingOutlined,
    FolderOutlined,
    ShareAltOutlined,
} from '@ant-design/icons';
import { ChatView } from './components/ChatView';
import { AISettings } from './components/AISettings';
import { HistoryPanel } from './components/HistoryPanel';
import { useGraphStore } from './stores/graphStore';
import { chatApi } from './services/chatApi';
import { initializeAI } from './services/aiConfig';
import './App.css';

const { Header, Content } = Layout;

const App: React.FC = () => {
    const { t } = useTranslation();
    const { currentProject } = useGraphStore();
    const [settingsVisible, setSettingsVisible] = useState(false);
    const [historyVisible, setHistoryVisible] = useState(false);

    // On mount: try to restore the most recent conversation so the
    // user lands in their last thread. If none exist, ChatView
    // renders an empty state with prompt suggestions.
    useEffect(() => {
        initializeAI().catch((e) => console.error('Failed to initialize AI:', e));
        chatApi
            .list()
            .then(async (items) => {
                if (items.length > 0 && !currentProject) {
                    try {
                        const full = await chatApi.getFull(items[0].id);
                        useGraphStore.getState().setProject(full);
                    } catch (e) {
                        console.error('Failed to restore last conversation:', e);
                    }
                }
            })
            .catch((e) => console.error('Failed to list conversations:', e));
    }, []);

    return (
        <Layout className="app-layout">
            {/* Floating top bar — minimal, ChatGPT-style */}
            <Header className="app-header app-header--floating">
                <div className="header-left">
                    <div className="logo">
                        <ShareAltOutlined className="logo-icon" />
                        <span className="logo-text">CiteThreads</span>
                    </div>
                    <span className="tagline">{t('app.tagline')}</span>
                </div>
                <div className="header-right">
                    <Space>
                        <Tooltip title={t('app.viewProjects')}>
                            <Button
                                type="text"
                                icon={<FolderOutlined />}
                                onClick={() => setHistoryVisible(true)}
                            />
                        </Tooltip>
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

            <Content className="app-content">
                <ChatView />
            </Content>

            {/* AI Settings Panel */}
            <AISettings
                visible={settingsVisible}
                onClose={() => setSettingsVisible(false)}
            />

            {/* History panel (multi-conversation sider) */}
            <HistoryPanel
                visible={historyVisible}
                onClose={() => setHistoryVisible(false)}
            />
        </Layout>
    );
};

export default App;
