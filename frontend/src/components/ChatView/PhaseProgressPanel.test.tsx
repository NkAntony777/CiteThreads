/**
 * PhaseProgressPanel — verify the per-phase card renders the
 * expected status / error / retry affordances.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { ConfigProvider } from 'antd';
import { PhaseProgressPanel } from './PhaseProgressPanel';
import type { PhaseProgress } from './PhaseProgressPanel';

vi.mock('react-i18next', () => ({
    useTranslation: () => ({ t: (k: string) => k }),
    I18nextProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    initReactI18next: { type: '3rdParty', init: () => {} },
}));

const wrap = (ui: React.ReactNode) => (
    <ConfigProvider>
        <I18nextProvider i18n={{ t: () => '', changeLanguage: () => Promise.resolve(), language: 'en' } as never}>
            {ui}
        </I18nextProvider>
    </ConfigProvider>
);

describe('PhaseProgressPanel', () => {
    it('renders all five phases with the right status', () => {
        const status: PhaseProgress = {
            progress_pct: 60,
            phases: {
                research: { status: 'succeeded', started_at: '2026-06-05T00:00:00Z', finished_at: '2026-06-05T00:01:00Z' },
                structure: { status: 'succeeded', started_at: '2026-06-05T00:01:00Z', finished_at: '2026-06-05T00:02:00Z' },
                compose: { status: 'failed', started_at: '2026-06-05T00:02:00Z', error: 'LLM timeout' },
                validate: { status: 'pending' },
                compile: { status: 'pending' },
            },
        };
        render(wrap(
            <PhaseProgressPanel
                projectId="p1"
                status={status}
                onRunPhase={vi.fn()}
            />,
        ));
        // All five phase labels should render
        expect(screen.getByText(/Research/)).toBeTruthy();
        expect(screen.getByText(/Structure/)).toBeTruthy();
        expect(screen.getByText(/Compose/)).toBeTruthy();
        expect(screen.getByText(/Validate/)).toBeTruthy();
        expect(screen.getByText(/Compile/)).toBeTruthy();
        // The failure message should be visible
        expect(screen.getByText(/LLM timeout/)).toBeTruthy();
        // The "can resume / partial failure" hint should appear
        // (research + structure done, compose failed)
        expect(screen.getByText(/partialFailure|partial failure|部分阶段失败/i)).toBeTruthy();
    });

    it('renders the compose sub-section progress when given', () => {
        const status: PhaseProgress = {
            progress_pct: 30,
            phases: {
                research: { status: 'succeeded' },
                structure: { status: 'succeeded' },
                compose: {
                    status: 'succeeded',
                    sections_done: ['introduction', 'literature_review'],
                    sections_total: ['introduction', 'literature_review', 'methodology', 'results', 'discussion', 'conclusion'],
                },
                validate: { status: 'pending' },
                compile: { status: 'pending' },
            },
        };
        render(wrap(
            <PhaseProgressPanel projectId="p1" status={status} onRunPhase={vi.fn()} />,
        ));
        // All six section labels should render under compose
        for (const label of [
            'Introduction', 'Literature Review', 'Methodology', 'Results', 'Discussion', 'Conclusion',
        ]) {
            expect(screen.getByText(label)).toBeTruthy();
        }
    });

    it('invokes onRunPhase with the right phase when Retry is clicked', () => {
        const onRun = vi.fn();
        const status: PhaseProgress = {
            progress_pct: 0,
            phases: {
                research: { status: 'failed', error: 'oops' },
                structure: { status: 'pending' },
                compose: { status: 'pending' },
                validate: { status: 'pending' },
                compile: { status: 'pending' },
            },
        };
        render(wrap(
            <PhaseProgressPanel projectId="p1" status={status} onRunPhase={onRun} />,
        ));
        // The Research card has a Retry button (it's the only
        // failed phase). Click it.
        const retryButtons = screen.getAllByText(/chat\.phase\.retry|Retry/);
        fireEvent.click(retryButtons[0]);
        expect(onRun).toHaveBeenCalledWith('research');
    });
});
