import React, { useState, useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import App from './App'
import './index.css'
import './i18n' // Initialize i18n
import { getCurrentLanguage } from './i18n'

const Root: React.FC = () => {
    const [locale, setLocale] = useState(getCurrentLanguage() === 'en-US' ? enUS : zhCN);

    useEffect(() => {
        const handleLanguageChange = (e: CustomEvent) => {
            setLocale(e.detail === 'en-US' ? enUS : zhCN);
        };
        window.addEventListener('languageChange', handleLanguageChange as EventListener);
        return () => window.removeEventListener('languageChange', handleLanguageChange as EventListener);
    }, []);

    return (
        <ConfigProvider
            locale={locale}
            theme={{
                token: {
                    colorPrimary: '#1890ff',
                    borderRadius: 6,
                },
            }}
        >
            <App />
        </ConfigProvider>
    );
};

ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
        <Root />
    </React.StrictMode>,
)
