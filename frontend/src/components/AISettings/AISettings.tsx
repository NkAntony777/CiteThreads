/**
 * AISettings Component - AI Service Provider Configuration Panel
 * Supports Chat models
 */
import React, { useState, useEffect } from 'react';
import {
    Drawer, Form, Select, Input, Button, Space, Alert, Tag, Typography, message, Tabs
} from 'antd';
import {
    SettingOutlined, CheckCircleOutlined, ApiOutlined,
    SaveOutlined, DeleteOutlined, ExperimentOutlined
} from '@ant-design/icons';
import {
    AI_PROVIDERS, AIProvider, AIProviderConfig, aiConfigService, ProviderInfo
} from '../../services/aiConfig';
import './AISettings.css';

const { Paragraph } = Typography;
const { Option } = Select;

interface AISettingsProps {
    visible: boolean;
    onClose: () => void;
}

export const AISettings: React.FC<AISettingsProps> = ({ visible, onClose }) => {
    // Chat model state
    const [chatForm] = Form.useForm();
    const [chatProvider, setChatProvider] = useState<AIProvider>('siliconflow');
    const [customChatModel, setCustomChatModel] = useState('');
    const [showChatKey, setShowChatKey] = useState(false);
    const [chatTesting, setChatTesting] = useState(false);
    const [chatTestResult, setChatTestResult] = useState<{ success: boolean; message: string } | null>(null);
    const [savedChatConfig, setSavedChatConfig] = useState<AIProviderConfig | null>(null);

    // Load saved configs on mount
    useEffect(() => {
        // Chat config
        const chatConfig = aiConfigService.getConfig();
        if (chatConfig) {
            setSavedChatConfig(chatConfig);
            setChatProvider(chatConfig.provider);
            chatForm.setFieldsValue({
                provider: chatConfig.provider,
                model: chatConfig.model,
                apiKey: chatConfig.apiKey,
                baseUrl: chatConfig.baseUrl,
            });
            if (!AI_PROVIDERS[chatConfig.provider].models.find(m => m.id === chatConfig.model)) {
                setCustomChatModel(chatConfig.model);
            }
        }
    }, [chatForm, visible]);

    const chatProviderInfo: ProviderInfo = AI_PROVIDERS[chatProvider];

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
            await chatForm.validateFields(['apiKey', 'model']);
        } catch {
            message.error('请填写完整的配置信息');
            return;
        }

        const values = chatForm.getFieldsValue();
        const config: AIProviderConfig = {
            provider: chatProvider,
            apiKey: values.apiKey,
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
                message.success('Chat 模型连接测试成功！');
            }
        } catch (e: any) {
            setChatTestResult({ success: false, message: e.message });
        } finally {
            setChatTesting(false);
        }
    };

    const handleChatSave = async () => {
        try {
            await chatForm.validateFields();
        } catch {
            message.error('请填写完整的配置信息');
            return;
        }

        const values = chatForm.getFieldsValue();
        const config: AIProviderConfig = {
            provider: chatProvider,
            apiKey: values.apiKey,
            model: customChatModel || values.model,
            baseUrl: values.baseUrl,
            isConfigured: true,
            lastTested: chatTestResult?.success ? new Date().toISOString() : undefined,
            testStatus: chatTestResult?.success ? 'success' : chatTestResult ? 'failed' : 'untested',
        };

        aiConfigService.saveConfig(config);
        setSavedChatConfig(config);
        message.success('Chat 模型配置已保存');
    };

    const handleClearAll = () => {
        aiConfigService.clearConfig();
        setSavedChatConfig(null);
        setChatTestResult(null);
        chatForm.resetFields();
        setChatProvider('siliconflow');
        message.info('所有配置已清除');
    };

    const tabItems = [
        {
            key: 'chat',
            label: (
                <span>
                    <ApiOutlined /> Chat 模型
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
                                    当前: <Tag color="blue">{AI_PROVIDERS[savedChatConfig.provider].name}</Tag>
                                    <Tag>{savedChatConfig.model}</Tag>
                                </span>
                            }
                            style={{ marginBottom: 16 }}
                        />
                    )}

                    <Form form={chatForm} layout="vertical" size="small">
                        <Form.Item name="provider" label="供应商" initialValue={chatProvider}>
                            <Select value={chatProvider} onChange={handleChatProviderChange}>
                                {Object.values(AI_PROVIDERS).map((p) => (
                                    <Option key={p.id} value={p.id}>
                                        {p.name} - {p.description}
                                    </Option>
                                ))}
                            </Select>
                        </Form.Item>

                        <Form.Item name="model" label="模型" rules={[{ required: !customChatModel }]}>
                            <Select disabled={chatProvider === 'custom'} onChange={() => setCustomChatModel('')}>
                                {chatProviderInfo.models.length > 0 && (
                                    <>
                                        <Select.OptGroup label="旗舰">
                                            {chatProviderInfo.models.filter(m => m.tier === 'flagship').map((m) => (
                                                <Option key={m.id} value={m.id}>{m.name}</Option>
                                            ))}
                                        </Select.OptGroup>
                                        <Select.OptGroup label="均衡">
                                            {chatProviderInfo.models.filter(m => m.tier === 'balanced').map((m) => (
                                                <Option key={m.id} value={m.id}>{m.name}</Option>
                                            ))}
                                        </Select.OptGroup>
                                        <Select.OptGroup label="经济">
                                            {chatProviderInfo.models.filter(m => m.tier === 'economy').map((m) => (
                                                <Option key={m.id} value={m.id}>{m.name}</Option>
                                            ))}
                                        </Select.OptGroup>
                                    </>
                                )}
                            </Select>
                        </Form.Item>

                        <Form.Item label="自定义模型 (可选)">
                            <Input
                                placeholder="手动输入模型名称"
                                value={customChatModel}
                                onChange={(e) => setCustomChatModel(e.target.value)}
                            />
                        </Form.Item>

                        <Form.Item name="apiKey" label="API 密钥" rules={[{ required: true }]}>
                            <Input.Password
                                placeholder={chatProviderInfo.keyPlaceholder}
                                visibilityToggle={{ visible: showChatKey, onVisibleChange: setShowChatKey }}
                            />
                        </Form.Item>

                        {chatProvider === 'custom' && (
                            <Form.Item name="baseUrl" label="API URL" rules={[{ required: true }]}>
                                <Input placeholder="https://api.example.com/v1" />
                            </Form.Item>
                        )}

                        <Space style={{ width: '100%' }}>
                            <Button icon={<ExperimentOutlined />} onClick={handleChatTest} loading={chatTesting}>
                                测试
                            </Button>
                            <Button type="primary" icon={<SaveOutlined />} onClick={handleChatSave}>
                                保存
                            </Button>
                        </Space>

                        {chatTestResult && (
                            <Alert
                                type={chatTestResult.success ? 'success' : 'error'}
                                message={chatTestResult.success ? '连接成功' : '连接失败'}
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
                    <span>AI 服务配置</span>
                </Space>
            }
            placement="right"
            width={440}
            open={visible}
            onClose={onClose}
            footer={
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Button danger icon={<DeleteOutlined />} onClick={handleClearAll} size="small">
                        清除所有
                    </Button>
                    <Button onClick={onClose}>关闭</Button>
                </Space>
            }
        >
            <div className="ai-settings">
                <Tabs items={tabItems} size="small" />

                <Paragraph type="secondary" style={{ fontSize: 11, marginTop: 16 }}>
                    API 密钥加密保存在浏览器本地，不会上传至服务器。
                </Paragraph>
            </div>
        </Drawer>
    );
};
