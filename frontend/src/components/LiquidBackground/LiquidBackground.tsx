import React from 'react';
import './LiquidBackground.css';

export const LiquidBackground: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    return (
        <div className="liquid-layout">
            <div className="liquid-background">
                <div className="blob blob-1" />
                <div className="blob blob-2" />
                <div className="blob blob-3" />
                <div className="glass-overlay" />
            </div>
            <div className="liquid-content">
                {children}
            </div>
        </div>
    );
};
