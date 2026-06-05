/**
 * AISettings Component - AI Service Provider Configuration Panel
 *
 * Updated June 2026: the API key field is no longer shown to the user.
 * The server holds the key (SILICONFLOW_API_KEY env var) and the
 * frontend just chooses the model + base URL. The "Test" button hits
 * ``/api/ai/test-config`` which runs the test against the server-side
 * key. A warning alert is rendered when the server has no default
 * key configured, so the user knows AI features will fail.
 */
import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Drawer, Form, Select, Input, Button, Space, Alert, Tag, Typography, message, Tabs, Divider
} from 'antd';
import {
    SettingOutlined, CheckCircleOutlined, ApiOutlined, SafetyOutlined,
    SaveOutlined, DeleteOutlined, ExperimentOutlined, GlobalOutlined,
    WarningOutlined
} from '@ant-design/icons';
import {
    AI_PROVIDERS, AIProvider, AIProviderConfig, aiConfigService, ProviderInfo
} from '../../services/aiConfig';
import { changeLanguage, getCurrentLanguage } from '../../i18n';
import './AISettings.css';

const { Paragraph } = Typography;
const { Option } = Select;

interface AISettingsProps {
    visible: boolean;
    onClose: () => void;
}

export const AISettings: React.FC<AISettingsProps> = ({ visible, onClose }) => {
    const { t } = useTranslation();

    // Language state
    const [currentLang, setCurrentLang] = useState<'zh-CN' | 'en-US'>(getCurrentLanguage());

    // Chat model state
    const [chatForm] = Form.useForm();
    const [chatProvider, setChatProvider] = useState<AIProvider>('siliconflow');
    const [customChatModel, setCustomChatModel] = useState('');
    const [chatTesting, setChatTesting] = useState(false);
    const [chatTestResult, setChatTestResult] = useState<{ success: boolean; message: string } | null>(null);
    const [savedChatConfig, setSavedChatConfig] = useState<AIProviderConfig | null>(null);
    const [defaultKeyConfigured, setDefaultKeyConfigured] = useState<boolean | null>(null);

    // Load saved configs + server status on mount
    useEffect(() => {
        // Chat config
        const chatConfig = aiConfigService.getConfig();
        if (chatConfig) {
            setSavedChatConfig(chatConfig);
            setChatProvider(chatConfig.provider);
            chatForm.setFieldsValue({
                provider: chatConfig.provider,
                model: chatConfig.model,
                baseUrl: chatConfig.baseUrl,
            });
            if (!AI_PROVIDERS[chatConfig.provider].models.find(m => m.id === chatConfig.model)) {
                setCustomChatModel(chatConfig.model);
            }
        }

        void aiConfigService.getServerStatus().then((s) => {
            setDefaultKeyConfigured(s.defaultKeyConfigured);
        });
    }, [chatForm, visible]);

    const chatProviderInfo: ProviderInfo = AI_PROVIDERS[chatProvider];

    // Language change handler
    const handleLanguageChange = (lang: 'zh-CN' | 'en-US') => {
        setCurrentLang(lang);
        changeLanguage(lang);
    };

    // Chat model handlers
    const handleChatProviderChange = (value: AIProvider) => {
        setChatProvider(value);
        setChatTestResult(null);
        setCustomChatModel('');
        chatForm.setFieldsValue({
            model: AI_PROVIDERS[value].defaultModel,
            baseUrl: AI_PROVIDERS[value].baseUrl,
        });
    };

    const handleChatTest = async () => {
        try {
            await chatForm.validateFields(['model']);
        } catch {
            message.error(t('settings.pleaseEnterCompleteConfig'));
            return;
        }

        const values = chatForm.getFieldsValue();
        const config: AIProviderConfig = {
            provider: chatProvider,
            model: customChatModel || values.model,
            baseUrl: values.baseUrl,
            isConfigured: true,
        };

        setChatTesting(true);
        setChatTestResult(null);

        try {
            const result = await aiConfigService.testConnection(config);
            setChatTestResult(result);
            if (result.success) {
                message.success(t('settings.chatTestSuccess'));
            }
        } catch (e) {
            const err = e as { message?: string };
            setChatTestResult({ success: false, message: err.message ?? '' });
        } finally {
            setChatTesting(false);
        }
    };

    const handleChatSave = async () => {
        try {
            await chatForm.validateFields();
        } catch {
            message.error(t('settings.pleaseEnterCompleteConfig'));
            return;
        }

        const values = chatForm.getFieldsValue();
        const config: AIProviderConfig = {
            provider: chatProvider,
            model: customChatModel || values.model,
            baseUrl: values.baseUrl,
            isConfigured: true,
            lastTested: chatTestResult?.success ? new Date().toISOString() : undefined,
            testStatus: chatTestResult?.success ? 'success' : chatTestResult ? 'failed' : 'untested',
        };

        aiConfigService.saveConfig(config);
        setSavedChatConfig(config);
        message.success(t('settings.configSaved'));
    };

    const handleClearAll = () => {
        aiConfigService.clearConfig();
        setSavedChatConfig(null);
        setChatTestResult(null);
        chatForm.resetFields();
        setChatProvider('siliconflow');
        message.info(t('settings.allConfigCleared'));
    };

    const tabItems = [
        {
            key: 'chat',
            label: (
                <span>
                    <ApiOutlined /> {t('settings.chatModel')}
                </span>
            ),
            children: (
                <div className="ai-settings-tab">
                    {savedChatConfig && (
                        <Alert
                            type={savedChatConfig.testStatus === 'success' ? 'success' : 'info'}
                            showIcon
                            icon={savedChatConfig.testStatus === 'success' ? <CheckCircleOutlined /> : <ApiOutlined />}
                            message={
                                <span>
                                    {t('settings.current')}: <Tag color="blue">{AI_PROVIDERS[savedChatConfig.provider].name}</Tag>
                                    <Tag>{savedChatConfig.model}</Tag>
                                </span>
                            }
                            style={{ marginBottom: 16 }}
                        />
                    )}

                    {defaultKeyConfigured === false && (
                        <Alert
                            type="warning"
                            showIcon
                            icon={<WarningOutlined />}
                            message={t('settings.defaultKeyMissing')}
                            style={{ marginBottom: 16 }}
                        />
                    )}
                    {defaultKeyConfigured === true && (
                        <Alert
                            type="success"
                            showIcon
                            icon={<SafetyOutlined />}
                            message={t('settings.defaultKeyConfigured')}
                            style={{ marginBottom: 16 }}
                        />
                    )}

                    <Form form={chatForm} layout="vertical" size="small">
                        <Form.Item name="provider" label={t('settings.provider')} initialValue={chatProvider}>
                            <Select value={chatProvider} onChange={handleChatProviderChange}>
                                {Object.values(AI_PROVIDERS).map((p) => (
                                    <Option key={p.id} value={p.id}>
                                        {p.name} - {p.description}
                                    </Option>
                                ))}
                            </Select>
                        </Form.Item>

                        <Form.Item name="model" label={t('settings.model')} rules={[{ required: !customChatModel }]}>
                            <Select disabled={chatProvider === 'custom'} onChange={() => setCustomChatModel('')}>
                                {chatProviderInfo.models.length > 0 && (
                                    <>
                                        <Select.OptGroup label={t('settings.flagship')}>
                                            {chatProviderInfo.models.filter(m => m.tier === 'flagship').map((m) => (
                                                <Option key={m.id} value={m.id}>{m.name}</Option>
                                            ))}
                                        </Select.OptGroup>
                                        <Select.OptGroup label={t('settings.balanced')}>
                                            {chatProviderInfo.models.filter(m => m.tier === 'balanced').map((m) => (
                                                <Option key={m.id} value={m.id}>{m.name}</Option>
                                            ))}
                                        </Select.OptGroup>
                                        <Select.OptGroup label={t('settings.economy')}>
                                            {chatProviderInfo.models.filter(m => m.tier === 'economy').map((m) => (
                                                <Option key={m.id} value={m.id}>{m.name}</Option>
                                            ))}
                                        </Select.OptGroup>
                                    </>
                                )}
                            </Select>
                        </Form.Item>

                        <Form.Item label={t('settings.customModel')}>
                            <Input
                                placeholder={t('settings.customModelPlaceholder')}
                                value={customChatModel}
                                onChange={(e) => setCustomChatModel(e.target.value)}
                            />
                        </Form.Item>

                        {chatProvider === 'custom' && (
                            <Form.Item name="baseUrl" label={t('settings.apiUrl')} rules={[{ required: true }]}>
                                <Input placeholder="https://api.example.com/v1" />
                            </Form.Item>
                        )}

                        <Space style={{ width: '100%' }}>
                            <Button icon={<ExperimentOutlined />} onClick={handleChatTest} loading={chatTesting}>
                                {t('settings.testConnection')}
                            </Button>
                            <Button type="primary" icon={<SaveOutlined />} onClick={handleChatSave}>
                                {t('common.save')}
                            </Button>
                        </Space>

                        {chatTestResult && (
                            <Alert
                                type={chatTestResult.success ? 'success' : 'error'}
                                message={chatTestResult.success ? t('settings.connectionSuccess') : t('settings.connectionFailed')}
                                description={chatTestResult.message}
                                style={{ marginTop: 12 }}
                            />
                        )}
                    </Form>
                </div>
            ),
        },
    ];

    return (
        <Drawer
            title={
                <Space>
                    <SettingOutlined />
                    <span>{t('settings.title')}</span>
                </Space>
            }
            placement="right"
            width={440}
            open={visible}
            onClose={onClose}
            footer={
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Button danger icon={<DeleteOutlined />} onClick={handleClearAll} size="small">
                        {t('settings.clearAll')}
                    </Button>
                    <Button onClick={onClose}>{t('common.close')}</Button>
                </Space>
            }
        >
            <div className="ai-settings">
                {/* Language Switcher */}
                <div className="language-section" style={{ marginBottom: 16 }}>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Space>
                            <GlobalOutlined />
                            <span>{t('settings.language')}</span>
                        </Space>
                        <Select
                            value={currentLang}
                            onChange={handleLanguageChange}
                            style={{ width: 120 }}
                            size="small"
                        >
                            <Option value="zh-CN">中文</Option>
                            <Option value="en-US">English</Option>
                        </Select>
                    </Space>
                </div>

                <Divider style={{ margin: '12px 0' }} />

                <Tabs items={tabItems} size="small" />

                <Paragraph type="secondary" style={{ fontSize: 11, marginTop: 16 }}>
                    {t('settings.apiKeyFromServerNote')}
                </Paragraph>
            </div>
        </Drawer>
    );
};
