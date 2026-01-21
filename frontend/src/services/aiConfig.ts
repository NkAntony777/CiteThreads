/**
 * AI Service Configuration - Types and Storage
 * Updated: January 2025 - Latest models from all major providers
 */

// Supported AI providers
export type AIProvider = 'openai' | 'deepseek' | 'siliconflow' | 'google' | 'anthropic' | 'custom';

// Provider configuration
export interface AIProviderConfig {
    provider: AIProvider;
    apiKey: string;
    model: string;
    baseUrl?: string;  // For custom providers
    isConfigured: boolean;
    lastTested?: string;
    testStatus?: 'success' | 'failed' | 'untested';
}

// Provider metadata
export interface ProviderInfo {
    id: AIProvider;
    name: string;
    description: string;
    models: { id: string; name: string; tier: 'flagship' | 'balanced' | 'economy' }[];
    defaultModel: string;
    baseUrl: string;
    keyPlaceholder: string;
}

// Available providers with latest 2025 models
export const AI_PROVIDERS: Record<AIProvider, ProviderInfo> = {
    openai: {
        id: 'openai',
        name: 'OpenAI',
        description: 'GPT-5系列、O3推理模型',
        models: [
            // Flagship
            { id: 'gpt-5', name: 'GPT-5 (旗舰)', tier: 'flagship' },
            { id: 'gpt-5.1', name: 'GPT-5.1 (最新)', tier: 'flagship' },
            { id: 'o3', name: 'O3 (推理增强)', tier: 'flagship' },
            { id: 'o3-pro', name: 'O3 Pro (高级推理)', tier: 'flagship' },
            // Balanced
            { id: 'gpt-4o', name: 'GPT-4o (均衡)', tier: 'balanced' },
            { id: 'o3-mini', name: 'O3 Mini (高性价比推理)', tier: 'balanced' },
            { id: 'o1', name: 'O1 (推理)', tier: 'balanced' },
            // Economy
            { id: 'gpt-5-mini', name: 'GPT-5 Mini (经济)', tier: 'economy' },
            { id: 'gpt-4o-mini', name: 'GPT-4o Mini (低成本)', tier: 'economy' },
            { id: 'gpt-3.5-turbo', name: 'GPT-3.5 Turbo (超低成本)', tier: 'economy' },
        ],
        defaultModel: 'gpt-4o-mini',
        baseUrl: 'https://api.openai.com/v1',
        keyPlaceholder: 'sk-...',
    },
    deepseek: {
        id: 'deepseek',
        name: 'DeepSeek',
        description: '高性价比国产模型，V3.2推理增强',
        models: [
            // Flagship
            { id: 'deepseek-v3.2', name: 'DeepSeek V3.2 (最新旗舰)', tier: 'flagship' },
            { id: 'deepseek-v3.1', name: 'DeepSeek V3.1 (混合思考)', tier: 'flagship' },
            { id: 'deepseek-r1-0528', name: 'DeepSeek R1 (推理)', tier: 'flagship' },
            // Balanced
            { id: 'deepseek-v3-0324', name: 'DeepSeek V3 (均衡)', tier: 'balanced' },
            { id: 'deepseek-chat', name: 'DeepSeek Chat', tier: 'balanced' },
            // Economy
            { id: 'deepseek-coder', name: 'DeepSeek Coder (代码专用)', tier: 'economy' },
        ],
        defaultModel: 'deepseek-chat',
        baseUrl: 'https://api.deepseek.com/v1',
        keyPlaceholder: 'sk-...',
    },
    siliconflow: {
        id: 'siliconflow',
        name: '硅基流动 SiliconFlow',
        description: '国内API代理，支持多种开源模型',
        models: [
            // Flagship
            { id: 'deepseek-ai/DeepSeek-V3', name: 'DeepSeek V3', tier: 'flagship' },
            { id: 'deepseek-ai/DeepSeek-R1', name: 'DeepSeek R1 (推理)', tier: 'flagship' },
            // Balanced
            { id: 'Qwen/Qwen2.5-72B-Instruct', name: 'Qwen 2.5 72B', tier: 'balanced' },
            { id: 'Qwen/Qwen2.5-32B-Instruct', name: 'Qwen 2.5 32B', tier: 'balanced' },
            { id: 'THUDM/glm-4-9b-chat', name: 'GLM-4 9B', tier: 'balanced' },
            // Economy
            { id: 'Qwen/Qwen2.5-7B-Instruct', name: 'Qwen 2.5 7B (经济)', tier: 'economy' },
            { id: 'deepseek-ai/DeepSeek-V2.5', name: 'DeepSeek V2.5 (经济)', tier: 'economy' },
        ],
        defaultModel: 'deepseek-ai/DeepSeek-V3',
        baseUrl: 'https://api.siliconflow.cn/v1',
        keyPlaceholder: 'sk-...',
    },
    google: {
        id: 'google',
        name: 'Google (Gemini)',
        description: 'Gemini 3.0 最新发布，2.5系列稳定',
        models: [
            // Flagship
            { id: 'gemini-3.0-pro', name: 'Gemini 3.0 Pro (最新)', tier: 'flagship' },
            { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro (深度推理)', tier: 'flagship' },
            // Balanced
            { id: 'gemini-3.0-flash', name: 'Gemini 3.0 Flash (快速)', tier: 'balanced' },
            { id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', tier: 'balanced' },
            // Economy
            { id: 'gemini-2.5-flash-lite', name: 'Gemini 2.5 Flash-Lite (超快)', tier: 'economy' },
            { id: 'gemini-2.0-flash', name: 'Gemini 2.0 Flash (经济)', tier: 'economy' },
        ],
        defaultModel: 'gemini-2.5-flash',
        baseUrl: 'https://generativelanguage.googleapis.com/v1beta',
        keyPlaceholder: 'AIza...',
    },
    anthropic: {
        id: 'anthropic',
        name: 'Anthropic (Claude)',
        description: 'Claude 4系列，Opus 4.5最强编码能力',
        models: [
            // Flagship
            { id: 'claude-opus-4.5', name: 'Claude Opus 4.5 (最新旗舰)', tier: 'flagship' },
            { id: 'claude-sonnet-4.5', name: 'Claude Sonnet 4.5 (编码强)', tier: 'flagship' },
            { id: 'claude-opus-4.1', name: 'Claude Opus 4.1', tier: 'flagship' },
            // Balanced
            { id: 'claude-sonnet-4', name: 'Claude Sonnet 4', tier: 'balanced' },
            { id: 'claude-haiku-4.5', name: 'Claude Haiku 4.5 (快速)', tier: 'balanced' },
            // Economy (legacy)
            { id: 'claude-3-5-sonnet-20241022', name: 'Claude 3.5 Sonnet (经济)', tier: 'economy' },
            { id: 'claude-3-5-haiku-20241022', name: 'Claude 3.5 Haiku (超快)', tier: 'economy' },
        ],
        defaultModel: 'claude-3-5-sonnet-20241022',
        baseUrl: 'https://api.anthropic.com/v1',
        keyPlaceholder: 'sk-ant-...',
    },
    custom: {
        id: 'custom',
        name: '自定义 / 第三方代理',
        description: 'OpenAI 兼容的第三方 API',
        models: [],
        defaultModel: '',
        baseUrl: '',
        keyPlaceholder: '请输入 API 密钥',
    },
};

// Helper: Get all models as flat array for a provider
export function getProviderModels(providerId: AIProvider): string[] {
    const provider = AI_PROVIDERS[providerId];
    return provider.models.map(m => m.id);
}

// Helper: Get model display name
export function getModelDisplayName(providerId: AIProvider, modelId: string): string {
    const provider = AI_PROVIDERS[providerId];
    const model = provider.models.find(m => m.id === modelId);
    return model ? model.name : modelId;
}

// Simple encryption for localStorage (not production-level security)
const STORAGE_KEY = 'citethreads_ai_config';
const ENCRYPTION_KEY = 'CT_AI_2025';

function simpleEncrypt(text: string): string {
    return btoa(unescape(encodeURIComponent(text + ENCRYPTION_KEY)));
}

function simpleDecrypt(encoded: string): string {
    try {
        const decoded = decodeURIComponent(escape(atob(encoded)));
        return decoded.replace(ENCRYPTION_KEY, '');
    } catch {
        return '';
    }
}

// Storage service
export const aiConfigService = {
    getConfig(): AIProviderConfig | null {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (!stored) return null;

            const decrypted = simpleDecrypt(stored);
            return JSON.parse(decrypted);
        } catch (e) {
            console.error('Failed to load AI config:', e);
            return null;
        }
    },

    saveConfig(config: AIProviderConfig): void {
        try {
            const encrypted = simpleEncrypt(JSON.stringify(config));
            localStorage.setItem(STORAGE_KEY, encrypted);
            this.applyConfig(config); // Sync with backend
        } catch (e) {
            console.error('Failed to save AI config:', e);
        }
    },

    clearConfig(): void {
        localStorage.removeItem(STORAGE_KEY);
    },

    async applyConfig(config: AIProviderConfig): Promise<boolean> {
        try {
            await fetch('/api/ai/configure/llm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: config.provider,
                    api_key: config.apiKey,
                    model: config.model,
                    base_url: config.baseUrl || AI_PROVIDERS[config.provider]?.baseUrl,
                }),
            });
            return true;
        } catch (e) {
            console.error('Failed to apply AI config:', e);
            return false;
        }
    },

    async testConnection(config: AIProviderConfig): Promise<{ success: boolean; message: string }> {
        try {
            const response = await fetch('/api/ai/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: config.provider,
                    api_key: config.apiKey,
                    model: config.model,
                    base_url: config.baseUrl || AI_PROVIDERS[config.provider]?.baseUrl,
                }),
            });

            const data = await response.json();

            if (response.ok && data.success) {
                return { success: true, message: data.message || '连接成功！' };
            } else {
                return { success: false, message: data.detail || data.message || '连接失败' };
            }
        } catch (e: any) {
            return { success: false, message: e.message || '网络错误' };
        }
    },
};

// Initialize AI services from local storage
export const initializeAI = async () => {
    const chatConfig = aiConfigService.getConfig();
    if (chatConfig) {
        await aiConfigService.applyConfig(chatConfig);
    }
};
