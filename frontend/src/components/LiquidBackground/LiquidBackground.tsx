import React from 'react';
import { motion } from 'framer-motion';
import './LiquidBackground.css';

export const LiquidBackground: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    return (
        <div className="liquid-layout">
            <div className="liquid-background">
                <motion.div
                    className="blob blob-1"
                    animate={{
                        x: [0, 100, 0],
                        y: [0, -50, 0],
                        scale: [1, 1.2, 1]
                    }}
                    transition={{
                        duration: 20,
                        repeat: Infinity,
                        ease: "easeInOut"
                    }}
                />
                <motion.div
                    className="blob blob-2"
                    animate={{
                        x: [0, -80, 0],
                        y: [0, 60, 0],
                        scale: [1, 1.3, 1]
                    }}
                    transition={{
                        duration: 25,
                        repeat: Infinity,
                        ease: "easeInOut"
                    }}
                />
                <motion.div
                    className="blob blob-3"
                    animate={{
                        x: [0, 60, 0],
                        y: [0, 40, 0],
                        scale: [1, 1.1, 1]
                    }}
                    transition={{
                        duration: 22,
                        repeat: Infinity,
                        ease: "easeInOut"
                    }}
                />
                <div className="glass-overlay" />
            </div>
            <div className="liquid-content">
                {children}
            </div>
        </div>
    );
};
