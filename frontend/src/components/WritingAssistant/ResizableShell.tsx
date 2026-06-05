/**
 * ResizableShell — 3-panel resizable layout for the writing workspace.
 *
 * Left:   reference list (Papers / Graph papers)
 * Center: chat / draft tabs
 * Right:  Canvas editor (collapsed by default — the writer gets the
 *         full center column until they pull the canvas out)
 *
 * Backed by `react-resizable-panels` (already in package.json).
 */
import React from 'react';
import { Group, Panel, Separator } from 'react-resizable-panels';
import './ResizableShell.css';

export interface ResizableShellProps {
    left: React.ReactNode;
    center: React.ReactNode;
    right: React.ReactNode;
    leftLabel?: string;
    rightLabel?: string;
}

export const ResizableShell: React.FC<ResizableShellProps> = ({
    left,
    center,
    right,
    leftLabel,
    rightLabel,
}) => {
    return (
        <div className="resizable-shell">
            <Group orientation="horizontal" className="resizable-shell__group">
                <Panel
                    defaultSize={22}
                    minSize={12}
                    className="resizable-shell__panel resizable-shell__panel--left"
                >
                    {leftLabel && (
                        <div className="resizable-shell__label">{leftLabel}</div>
                    )}
                    <div className="resizable-shell__content">{left}</div>
                </Panel>
                <Separator className="resizable-shell__handle" />
                <Panel
                    defaultSize={78}
                    minSize={30}
                    className="resizable-shell__panel resizable-shell__panel--center"
                >
                    <div className="resizable-shell__content">{center}</div>
                </Panel>
                <Separator className="resizable-shell__handle resizable-shell__handle--right" />
                <Panel
                    collapsible
                    collapsedSize={0}
                    defaultSize={0}
                    minSize={15}
                    className="resizable-shell__panel resizable-shell__panel--right"
                    title={rightLabel}
                >
                    {rightLabel && (
                        <div className="resizable-shell__label">{rightLabel}</div>
                    )}
                    <div className="resizable-shell__content">{right}</div>
                </Panel>
            </Group>
        </div>
    );
};

export default ResizableShell;
