/**
 * ChatInput — ChatGPT-style sticky-bottom input.
 *
 * 2026-06 refactor: this is the single input the entire app
 * revolves around. Auto-resizes 1-6 lines, Enter sends,
 * Shift+Enter inserts newline. Disabled while a turn is running;
 * shows a stop button instead.
 */
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Tooltip } from 'antd';
import { SendOutlined, StopOutlined } from '@ant-design/icons';
import './ChatInput.css';

export interface ChatInputProps {
    onSubmit: (text: string) => void | Promise<void>;
    onCancel?: () => void;
    running?: boolean;
    disabled?: boolean;
    placeholder?: string;
}

export const ChatInput: React.FC<ChatInputProps> = ({
    onSubmit,
    onCancel,
    running = false,
    disabled = false,
    placeholder,
}) => {
    const { t } = useTranslation();
    const [value, setValue] = useState('');
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);

    // Auto-resize: 1 row at rest, up to 6 as the user types.
    useEffect(() => {
        const ta = textareaRef.current;
        if (!ta) return;
        ta.style.height = 'auto';
        ta.style.height = `${Math.min(ta.scrollHeight, 6 * 24 + 16)}px`;
    }, [value]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void handleSend();
        }
    };

    const handleSend = async () => {
        const trimmed = value.trim();
        if (!trimmed || running) return;
        setValue('');
        await onSubmit(trimmed);
    };

    const ph = placeholder ?? t('chat.inputPlaceholder');

    return (
        <div className="chat-input">
            <textarea
                ref={textareaRef}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={ph}
                disabled={disabled}
                rows={1}
                aria-label={t('chat.inputAriaLabel')}
            />
            {running ? (
                <Tooltip title={t('chat.stop')}>
                    <Button
                        type="primary"
                        danger
                        icon={<StopOutlined />}
                        onClick={onCancel}
                    />
                </Tooltip>
            ) : (
                <Tooltip title={t('chat.send')}>
                    <Button
                        type="primary"
                        icon={<SendOutlined />}
                        onClick={() => void handleSend()}
                        disabled={!value.trim() || disabled}
                    />
                </Tooltip>
            )}
        </div>
    );
};

export default ChatInput;
