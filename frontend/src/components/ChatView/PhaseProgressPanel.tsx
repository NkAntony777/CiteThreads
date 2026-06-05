/**
 * PhaseProgressPanel — rich progress cards for the CTDP pipeline.
 *
 * 2026-06: replaces the flat "1.调研 / 2.结构 / 3.写作 / 4.编译"
 * button list with per-phase cards. Each card surfaces:
 *   - status badge (pending / running / done / failed / skipped)
 *   - elapsed time when running, total time when done
 *   - last error message on failure
 *   - "Resume from checkpoint" hint when applicable
 *   - per-section progress (the 6 sections of compose)
 *   - per-phase action buttons (Run / Retry / Rerun)
 *
 * Designed to be embedded in the ChatView message stream as a
 * single "system" turn that updates over time.
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Spin, Tag, Tooltip, Typography, message } from 'antd';
import {
    CheckCircleFilled,
    CloseCircleFilled,
    LoadingOutlined,
    MinusCircleFilled,
    ReloadOutlined,
    ThunderboltFilled,
} from '@ant-design/icons';
import type { PhaseName, PhaseStatus } from '../../services/draftApi';
import { draftApi } from '../../services/draftApi';
import './PhaseProgressPanel.css';

export type PhaseProgress = NonNullable<
    Awaited<ReturnType<typeof draftApi.getStatus>>['ctx']
>;

const { Text } = Typography;

export const SECTION_LABELS: Record<string, string> = {
    introduction: 'Introduction',
    literature_review: 'Literature Review',
    methodology: 'Methodology',
    results: 'Results',
    discussion: 'Discussion',
    conclusion: 'Conclusion',
};

export interface PhaseProgressPanelProps {
    projectId: string;
    /** A snapshot returned by draftApi.getStatus(); the panel
     * re-renders whenever this changes. */
    status: PhaseProgress | null;
    /** Currently running phase (so the spinner can be shown live
     * even if the next status poll is a few seconds away). */
    runningPhase?: string | null;
    /** Optional error from a recent phase run (overrides the
     * status's per-phase error for that phase). */
    lastError?: { phase: string; message: string } | null;
    /** Called when a user clicks "Run" / "Retry" on a card. */
    onRunPhase?: (phase: string) => Promise<void> | void;
    /** Whether a phase run is currently in flight. */
    busy?: boolean;
}

const STATUS_META: Record<
    PhaseStatus,
    { color: string; icon: React.ReactNode; label: string }
> = {
    pending: { color: 'default', icon: <MinusCircleFilled />, label: 'pending' },
    running: { color: 'processing', icon: <LoadingOutlined spin />, label: 'running' },
    succeeded: { color: 'success', icon: <CheckCircleFilled />, label: 'done' },
    failed: { color: 'error', icon: <CloseCircleFilled />, label: 'failed' },
    skipped: { color: 'default', icon: <MinusCircleFilled />, label: 'skipped' },
};

const PHASE_LABELS: Record<string, string> = {
    research: '1. Research',
    structure: '2. Structure',
    compose: '3. Compose',
    validate: '4. Validate',
    compile: '5. Compile',
};

function formatDuration(startedAt?: string | null, finishedAt?: string | null): string {
    if (!startedAt) return '';
    const start = new Date(startedAt).getTime();
    const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
    if (Number.isNaN(start) || Number.isNaN(end)) return '';
    const sec = Math.max(0, Math.round((end - start) / 1000));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s}s`;
}

export const PhaseProgressCard: React.FC<{
    phase: string;
    status: PhaseStatus;
    startedAt?: string | null;
    finishedAt?: string | null;
    error?: string | null;
    hasCheckpoint?: boolean;
    resumedFromCheckpoint?: boolean;
    sectionsDone?: string[];
    sectionsTotal?: string[];
    isRunning?: boolean;
    onRun?: () => void;
    busy?: boolean;
    onOpenSection?: (section: string) => void;
}> = ({
    phase,
    status,
    startedAt,
    finishedAt,
    error,
    hasCheckpoint,
    resumedFromCheckpoint,
    sectionsDone,
    sectionsTotal,
    isRunning,
    onRun,
    busy,
    onOpenSection,
}) => {
    const { t } = useTranslation();
    const meta = STATUS_META[status] ?? STATUS_META.pending;
    const duration = formatDuration(startedAt, finishedAt);
    const isFailed = status === 'failed';
    const isDone = status === 'succeeded';
    const isCompose = phase === 'compose';

    const sectionProgress = useMemo(() => {
        if (!isCompose || !sectionsTotal) return null;
        const doneSet = new Set(sectionsDone ?? []);
        return sectionsTotal.map((s) => ({
            name: s,
            label: SECTION_LABELS[s] ?? s,
            done: doneSet.has(s),
        }));
    }, [isCompose, sectionsDone, sectionsTotal]);

    return (
        <div className={`phase-card phase-card--${status}`}>
            <div className="phase-card__head">
                <div className="phase-card__head-left">
                    <span className="phase-card__icon" style={{ color: meta.color === 'processing' ? '#1677ff' : undefined }}>
                        {isRunning ? <LoadingOutlined spin /> : meta.icon}
                    </span>
                    <Text strong>{PHASE_LABELS[phase] ?? phase}</Text>
                    <Tag color={meta.color} bordered={false}>
                        {t(`chat.phase.${meta.label}`, meta.label)}
                    </Tag>
                    {hasCheckpoint && !isRunning && (
                        <Tooltip title={t('chat.phase.checkpointHint')}>
                            <Tag color="cyan" bordered={false}>
                                {t('chat.phase.checkpoint')}
                            </Tag>
                        </Tooltip>
                    )}
                    {resumedFromCheckpoint && (
                        <Tag color="blue" bordered={false} icon={<ThunderboltFilled />}>
                            {t('chat.phase.resumed')}
                        </Tag>
                    )}
                    {duration && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                            {duration}
                        </Text>
                    )}
                </div>
                <div className="phase-card__head-right">
                    {(status === 'pending' || isFailed) && onRun && (
                        <Button
                            type={isFailed ? 'primary' : 'default'}
                            danger={isFailed}
                            size="small"
                            icon={isFailed ? <ReloadOutlined /> : <ThunderboltFilled />}
                            onClick={onRun}
                            loading={busy}
                        >
                            {isFailed
                                ? t('chat.phase.retry')
                                : t('chat.phase.run')}
                        </Button>
                    )}
                    {isDone && onRun && (
                        <Tooltip title={t('chat.phase.rerun')}>
                            <Button
                                size="small"
                                type="text"
                                icon={<ReloadOutlined />}
                                onClick={onRun}
                                loading={busy}
                            >
                                {t('chat.phase.rerun')}
                            </Button>
                        </Tooltip>
                    )}
                </div>
            </div>

            {isFailed && error && (
                <div className="phase-card__error">
                    <Text type="danger" style={{ fontSize: 12 }}>
                        {error}
                    </Text>
                </div>
            )}

            {sectionProgress && sectionProgress.length > 0 && (
                <div className="phase-card__sections">
                    {sectionProgress.map((s) => (
                        <div
                            key={s.name}
                            className={`phase-card__section ${s.done ? 'is-done' : ''}`}
                            onClick={() => onOpenSection?.(s.name)}
                        >
                            <span className="phase-card__section-dot" />
                            <span className="phase-card__section-label">{s.label}</span>
                            {s.done && (
                                <CheckCircleFilled
                                    className="phase-card__section-check"
                                    style={{ color: '#52c41a' }}
                                />
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export const PhaseProgressPanel: React.FC<PhaseProgressPanelProps> = ({
    projectId,
    status,
    runningPhase,
    lastError,
    onRunPhase,
    busy = false,
}) => {
    const { t } = useTranslation();

    const handleRun = async (phase: string) => {
        if (!onRunPhase || busy) return;
        try {
            await onRunPhase(phase);
        } catch (e: unknown) {
            const err = e as { message?: string };
            message.error(err?.message || t('chat.phase.runFailed'));
        }
    };

    const phases: PhaseName[] = [
        'research', 'structure', 'compose', 'validate', 'compile',
    ];
    const anyFailed = phases.some(
        (p) => status?.phases?.[p]?.status === 'failed',
    );
    const anyRunning = !!runningPhase;
    const completed = phases.filter(
        (p) => status?.phases?.[p]?.status === 'succeeded',
    ).length;
    const hasPartial = completed > 0 && completed < phases.length;

    return (
        <div className="phase-progress-panel">
            {anyFailed && (
                <div className="phase-progress-panel__banner phase-progress-panel__banner--warn">
                    ⚠ {t('chat.phase.partialFailure')}
                </div>
            )}
            {hasPartial && !anyFailed && !anyRunning && (
                <div className="phase-progress-panel__banner phase-progress-panel__banner--hint">
                    ↻ {t('chat.phase.canResume', { completed, total: phases.length })}
                </div>
            )}
            {anyRunning && (
                <div className="phase-progress-panel__banner phase-progress-panel__banner--running">
                    <Spin size="small" /> {t('chat.phase.running', { phase: runningPhase })}
                </div>
            )}
            <div className="phase-progress-panel__cards">
                {phases.map((p) => {
                    const s = status?.phases?.[p];
                    return (
                        <PhaseProgressCard
                            key={p}
                            phase={p}
                            status={(s?.status ?? 'pending') as PhaseStatus}
                            startedAt={s?.started_at}
                            finishedAt={s?.finished_at}
                            error={
                                lastError?.phase === p
                                    ? lastError.message
                                    : s?.error
                            }
                            hasCheckpoint={s?.has_checkpoint}
                            sectionsDone={s?.sections_done}
                            sectionsTotal={s?.sections_total}
                            isRunning={runningPhase === p}
                            busy={busy && runningPhase === p}
                            onRun={() => handleRun(p)}
                            onOpenSection={(section) => {
                                // Tapping a section in the compose sub-list
                                // regenerates it (overwriting the prior body).
                                // The main action button is the dedicated
                                // "Regenerate" on each section in the full
                                // draft view; this is a quick in-place edit.
                                void draftApi
                                    .regenerateSection(projectId, section, {})
                                    .catch(() => undefined);
                            }}
                        />
                    );
                })}
            </div>
        </div>
    );
};

export default PhaseProgressPanel;
