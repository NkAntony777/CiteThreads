/**
 * AI Service Configuration - Types and Storage
 *
 * Security model (June 2026 review):
 * - The raw LLM API key NEVER lives in the browser. The previous
 *   ``simpleEncrypt``/``simpleDecrypt`` Base64+concat was trivial to
 *   decode, so the threat was "any user with DevTools can read your
 *   key" rather than "your key is encrypted".
 * - The backend reads its key from ``settings.siliconflow_api_key``
 *   (env var). The frontend only chooses the model name and the base
 *   URL.
 * - ``APIProviderConfig`` no longer carries an ``apiKey`` field. Old
 *   localStorage entries that *do* contain a key are stripped on load.
 *
 * Updated: June 2026 - server-side key only.
 */

import i18n from '../i18n';

// Supported AI providers
export type AIProvider = 'openai' | 'deepseek' | 'siliconflow' | 'google' | 'anthropic' | 'custom';

// Provider configuration. Note: apiKey was removed in the security
// review. The server uses SILICONFLOW_API_KEY (or equivalent) from
// its own environment.
export interface AIProviderConfig {
    provider: AIProvider;
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

// Storage key. Kept the same name so existing localStorage entries get
// migrated (the apiKey field is just dropped on load).
const STORAGE_KEY = 'citethreads_ai_config';

// Storage service. The "encryption" was removed in the security
// review. The browser never sees the raw API key, so there's nothing
// to protect in localStorage. The model + base URL are not sensitive.
export const aiConfigService = {
    getConfig(): AIProviderConfig | null {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (!stored) return null;
            const parsed = JSON.parse(stored) as Record<string, unknown>;
            // Strip any pre-review apiKey field. Older localStorage
            // entries may still carry one; we drop it on read so
            // nothing sensitive lingers in the browser.
            const { apiKey: _apiKey, ...rest } = parsed;
            void _apiKey;
            const provider = rest.provider;
            const model = rest.model;
            if (typeof provider !== 'string' || typeof model !== 'string') {
                return null;
            }
            return {
                provider: provider as AIProviderConfig['provider'],
                model,
                baseUrl: typeof rest.baseUrl === 'string' ? rest.baseUrl : undefined,
                isConfigured: rest.isConfigured !== false,
                lastTested: typeof rest.lastTested === 'string' ? rest.lastTested : undefined,
                testStatus: rest.testStatus as AIProviderConfig['testStatus'],
            };
        } catch (e) {
            console.error('Failed to load AI config:', e);
            return null;
        }
    },

    saveConfig(config: AIProviderConfig): void {
        try {
            // Persist only non-sensitive fields. The apiKey field is
            // not in the type, but be defensive in case a caller
            // tried to smuggle one in.
            const { apiKey: _apiKey, ...safe } = config as AIProviderConfig & {
                apiKey?: string;
            };
            void _apiKey;
            localStorage.setItem(STORAGE_KEY, JSON.stringify(safe));
            void this.applyConfig(safe as AIProviderConfig);
        } catch (e) {
            console.error('Failed to save AI config:', e);
        }
    },

    clearConfig(): void {
        localStorage.removeItem(STORAGE_KEY);
    },

    async applyConfig(config: AIProviderConfig): Promise<boolean> {
        try {
            const resp = await fetch('/api/ai/configure/llm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: config.provider,
                    model: config.model,
                    base_url: config.baseUrl || AI_PROVIDERS[config.provider]?.baseUrl,
                }),
            });
            return resp.ok;
        } catch (e) {
            console.error('Failed to apply AI config:', e);
            return false;
        }
    },

    /**
     * Test the connection through the server-side default key. The
     * legacy "send the user's key" path is removed; the server is
     * the only one that can test against its configured key.
     */
    async testConnection(config: AIProviderConfig): Promise<{ success: boolean; message: string }> {
        try {
            const response = await fetch('/api/ai/test-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: config.provider,
                    model: config.model,
                    base_url: config.baseUrl || AI_PROVIDERS[config.provider]?.baseUrl,
                }),
            });
            const data = await response.json();
            if (response.ok && data.success) {
                return { success: true, message: data.message || i18n.t('settings.connectionSuccess') };
            }
            return { success: false, message: data.detail || data.message || i18n.t('settings.connectionFailed') };
        } catch (e) {
            const err = e as { message?: string };
            return { success: false, message: err.message || i18n.t('settings.networkError') };
        }
    },

    /** Read the current server-side AI status (whether a default key
     * is configured, current LLM/embedding status). Used to show the
     * "no key on server" warning in the UI. */
    async getServerStatus(): Promise<{
        defaultKeyConfigured: boolean;
        defaultModel: string;
        defaultBaseUrl: string;
    }> {
        try {
            const response = await fetch('/api/ai/status');
            if (!response.ok) {
                return { defaultKeyConfigured: false, defaultModel: '', defaultBaseUrl: '' };
            }
            const data = await response.json() as {
                default_key_configured?: boolean;
                default_model?: string;
                default_base_url?: string;
            };
            return {
                defaultKeyConfigured: Boolean(data.default_key_configured),
                defaultModel: data.default_model ?? '',
                defaultBaseUrl: data.default_base_url ?? '',
            };
        } catch (e) {
            console.error('Failed to load AI server status:', e);
            return { defaultKeyConfigured: false, defaultModel: '', defaultBaseUrl: '' };
        }
    },
};

// Initialize AI services from local storage. The apiKey field is no
// longer sent, so this just registers the model choice.
export const initializeAI = async () => {
    const chatConfig = aiConfigService.getConfig();
    if (chatConfig) {
        await aiConfigService.applyConfig(chatConfig);
    }
};
