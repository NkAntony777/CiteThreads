/**
 * i18n Configuration - Internationalization setup for CiteThreads
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import zhCN from './locales/zh-CN.json';
import enUS from './locales/en-US.json';

// Get saved language from localStorage or default to Chinese
const savedLanguage = localStorage.getItem('citethreads-language') || 'zh-CN';

i18n
    .use(initReactI18next)
    .init({
        resources: {
            'zh-CN': { translation: zhCN },
            'en-US': { translation: enUS },
        },
        lng: savedLanguage,
        fallbackLng: 'zh-CN',
        interpolation: {
            escapeValue: false, // React already escapes values
        },
    });

// Helper to change language and persist to localStorage
export const changeLanguage = (lang: 'zh-CN' | 'en-US') => {
    i18n.changeLanguage(lang);
    localStorage.setItem('citethreads-language', lang);
    // Dispatch a custom event so components can react
    window.dispatchEvent(new CustomEvent('languageChange', { detail: lang }));
};

// Get current language
export const getCurrentLanguage = (): 'zh-CN' | 'en-US' => {
    return (i18n.language || 'zh-CN') as 'zh-CN' | 'en-US';
};

export default i18n;
