/**
 * DraftGenerator — long-form paper draft tab for the WritingAssistant.
 *
 * Drives the 4 user-facing phases of the draft pipeline:
 *   research → structure → compose → compile
 *
 * Two more phases (`validate`, `export`) run server-side as part of
 * `compile` and are reflected in the progress bar / status panel, but
 * the user has no separate button for them.
 *
 * The component is intentionally self-contained: it owns the form
 * state, the per-phase response data, the polling loop, and the
 * output panels. The parent (WritingAssistant) only passes in the
 * active ``projectId``.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Alert,
    Button,
    Card,
    Col,
    Empty,
    Input,
    InputNumber,
    List,
    Modal,
    Progress,
    Row,
    Select,
    Space,
    Spin,
    Steps,
    Tag,
    Typography,
    message,
} from 'antd';
import {
    SearchOutlined,
    PartitionOutlined,
    EditOutlined,
    RocketOutlined,
    BookOutlined,
    ExperimentOutlined,
    FileTextOutlined,
    WarningOutlined,
    ReloadOutlined,
    StopOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import draftApi, {
    type CancelResponse,
    type CitationStyle,
    type CompileContext,
    type ComposeContext,
    type PhaseName,
    type PhaseResult,
    type PhaseStatus,
    type RegenerateResponse,
    type ResearchContext,
    type StatusContext,
    type StructureContext,
    type DraftApiError,
} from '../../services/draftApi';
import './DraftGenerator.css';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface DraftGeneratorProps {
    /** Project to run the pipeline against. */
    projectId: string;
}

type PhaseSlot = 'research' | 'structure' | 'compose' | 'validate' | 'compile' | 'export';

const PIPELINE_PHASES: PhaseSlot[] = [
    'research',
    'structure',
    'compose',
    'validate',
    'compile',
    'export',
];

const USER_FACING_PHASES: PhaseSlot[] = [
    'research',
    'structure',
    'compose',
    'compile',
];

function errorKey(err: unknown): { key: string; message: string } {
    const e = err as DraftApiError;
    if (e?.code === 'auth_required') {
        return {
            key: 'draftGenerator.error.authRequiredFriendly',
            message: e.message,
        };
    }
    if (e?.code === 'llm_key_missing') {
        return {
            key: 'draftGenerator.error.noLLMKeyFriendly',
            message: e.message,
        };
    }
    // HTTP 5xx from the upstream LLM provider (we surface this via
    // normaliseError's status field — anything in 500-599).
    if (typeof e?.status === 'number' && e.status >= 500 && e.status < 600) {
        return {
            key: 'draftGenerator.error.llmServerError',
            message: e.message,
        };
    }
    if (e?.code === 'network_error') {
        return {
            key: 'draftGenerator.error.networkError',
            message: e.message,
        };
    }
    // Cheap heuristic: when the LLM returns nothing usable (empty
    // body, garbage JSON, etc.) the body parses to a near-empty
    // string. The crafter surfaces this by raising with a detail
    // matching the markdown-strip pattern.
    if (typeof e?.message === 'string' && /garbage|no content|empty response/i.test(e.message)) {
        return {
            key: 'draftGenerator.error.garbageOutput',
            message: e.message,
        };
    }
    return {
        key: 'draftGenerator.error.generic',
        message: e?.message || 'Unknown error',
    };
}

const DraftGenerator: React.FC<DraftGeneratorProps> = ({ projectId }) => {
    const { t } = useTranslation();

    // Form state
    const [topic, setTopic] = useState('');
    const [wordCount, setWordCount] = useState<number>(3000);
    const [citationStyle, setCitationStyle] = useState<CitationStyle>('APA');

    // Per-phase output
    const [researchCtx, setResearchCtx] = useState<ResearchContext | null>(null);
    const [structureCtx, setStructureCtx] = useState<StructureContext | null>(null);
    const [composeCtx, setComposeCtx] = useState<ComposeContext | null>(null);
    const [compileCtx, setCompileCtx] = useState<CompileContext | null>(null);

    // Per-phase running flags
    const [runningPhase, setRunningPhase] = useState<PhaseSlot | null>(null);

    // Status / progress
    const [status, setStatus] = useState<StatusContext | null>(null);
    const [statusError, setStatusError] = useState<string | null>(null);

    // Error surface
    const [phaseError, setPhaseError] = useState<{ phase: PhaseSlot; message: string } | null>(
        null,
    );

    // Cancellation: tracks the in-flight cancel API call so the user
    // can't double-fire it. Once the runner confirms the flag is set
    // (via the next status poll showing the running phase is now
    // "skipped"), we re-enable the buttons via the runningPhase=null
    // path that the runPhase finally{} already triggers.
    const [cancelPending, setCancelPending] = useState(false);
    const [cancelledNotice, setCancelledNotice] = useState<string | null>(null);

    // Per-section regenerate modal state.
    const [regenSection, setRegenSection] = useState<string | null>(null);
    const [regenInstructions, setRegenInstructions] = useState('');
    const [regenPending, setRegenPending] = useState(false);
    const [regenError, setRegenError] = useState<string | null>(null);
    const [regenResult, setRegenResult] = useState<{
        section: string;
        previous: string;
        next: string;
    } | null>(null);
    const [regenShowDiff, setRegenShowDiff] = useState(false);

    const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const mountedRef = useRef(true);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            if (pollTimerRef.current) {
                clearInterval(pollTimerRef.current);
                pollTimerRef.current = null;
            }
        };
    }, []);

    // ============================================
    // Status polling
    // ============================================

    const startPolling = useCallback(() => {
        if (pollTimerRef.current) return;
        const tick = async () => {
            if (!projectId || !mountedRef.current) return;
            try {
                const res = await draftApi.getStatus(projectId);
                if (!mountedRef.current) return;
                if (res.success && res.ctx) {
                    setStatus(res.ctx);
                    setStatusError(null);
                }
            } catch (err) {
                if (!mountedRef.current) return;
                const e = err as DraftApiError;
                setStatusError(e?.message || t('draftGenerator.status.fetchStatusFailed'));
            }
        };
        void tick();
        pollTimerRef.current = setInterval(tick, 3000);
    }, [projectId, t]);

    const stopPolling = useCallback(() => {
        if (pollTimerRef.current) {
            clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
        }
    }, []);

    useEffect(() => {
        // Kick off a one-shot status fetch when the project changes, but
        // do not start the interval until a phase is actually running.
        if (!projectId) {
            setStatus(null);
            return;
        }
        let cancelled = false;
        (async () => {
            try {
                const res = await draftApi.getStatus(projectId);
                if (cancelled) return;
                if (res.success && res.ctx) {
                    setStatus(res.ctx);
                }
            } catch {
                // Non-fatal: status is best-effort until a phase runs.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [projectId]);

    useEffect(() => () => stopPolling(), [stopPolling]);

    // ============================================
    // Phase runners
    // ============================================

    const runPhase = useCallback(
        async <T,>(
            slot: PhaseSlot,
            request: () => Promise<T>,
            apply: (data: T) => void,
        ): Promise<void> => {
            if (!projectId) {
                message.warning(t('draftGenerator.status.noProject'));
                return;
            }
            setRunningPhase(slot);
            setPhaseError(null);
            startPolling();
            try {
                const data = await request();
                if (!mountedRef.current) return;
                apply(data);
            } catch (err) {
                if (!mountedRef.current) return;
                const { key, message: msg } = errorKey(err);
                const label = t(`draftGenerator.phases.${slot}`);
                const translated = t(key);
                setPhaseError({ phase: slot, message: `${label}: ${translated} — ${msg}` });
            } finally {
                if (mountedRef.current) {
                    setRunningPhase(null);
                }
            }
        },
        [projectId, t, startPolling],
    );

    const handleResearch = useCallback(() => {
        if (!topic.trim()) {
            message.warning(t('draftGenerator.status.noTopic'));
            return;
        }
        void runPhase('research', async () => {
            const res = await draftApi.runResearch(projectId, {
                topic: topic.trim(),
                target_word_count: wordCount,
                citation_style: citationStyle,
            });
            if (!res.success) {
                throw new Error(res.error || 'research failed');
            }
            if (res.ctx) setResearchCtx(res.ctx);
            return res.ctx;
        }, () => {
            // Toast on success happens via the message listener below;
            // we keep the apply callback simple.
        });
    }, [citationStyle, projectId, runPhase, t, topic, wordCount]);

    const handleStructure = useCallback(() => {
        void runPhase('structure', async () => {
            const res = await draftApi.runStructure(projectId);
            if (!res.success) {
                throw new Error(res.error || 'structure failed');
            }
            if (res.ctx) setStructureCtx(res.ctx);
            return res.ctx;
        }, () => undefined);
    }, [projectId, runPhase]);

    const handleCompose = useCallback(() => {
        void runPhase('compose', async () => {
            const res = await draftApi.runCompose(projectId);
            if (!res.success) {
                throw new Error(res.error || 'compose failed');
            }
            if (res.ctx) setComposeCtx(res.ctx);
            return res.ctx;
        }, () => undefined);
    }, [projectId, runPhase]);

    const handleCompile = useCallback(() => {
        void runPhase('compile', async () => {
            const res = await draftApi.runCompile(projectId);
            if (!res.success) {
                throw new Error(res.error || 'compile failed');
            }
            if (res.ctx) setCompileCtx(res.ctx);
            return res.ctx;
        }, () => {
            stopPolling();
        });
    }, [projectId, runPhase, stopPolling]);

    // ============================================
    // Cancellation
    // ============================================

    const handleCancel = useCallback(async () => {
        if (!projectId || cancelPending || !runningPhase) return;
        setCancelPending(true);
        setCancelledNotice(null);
        try {
            const res: CancelResponse = await draftApi.cancelDraft(projectId);
            if (res.cancelled) {
                setCancelledNotice(t('draftGenerator.status.cancelRequested'));
                message.info(t('draftGenerator.status.cancelRequested'));
            }
        } catch (err) {
            const e = err as DraftApiError;
            setPhaseError({
                phase: runningPhase,
                message: t('draftGenerator.error.networkError') + ' — ' + (e?.message || ''),
            });
        } finally {
            setCancelPending(false);
            // The running phase's runPhase() will resolve via the runner
            // noticing the flag, the status poll will pick up the new
            // "skipped" status, and runPhase's finally{} clears the
            // local runningPhase state.
        }
    }, [cancelPending, projectId, runningPhase, t]);

    // ============================================
    // Per-section regenerate
    // ============================================

    const openRegenModal = useCallback(
        (section: string) => {
            setRegenSection(section);
            setRegenInstructions('');
            setRegenError(null);
            setRegenResult(null);
            setRegenShowDiff(false);
        },
        [],
    );

    const closeRegenModal = useCallback(() => {
        if (regenPending) return;
        setRegenSection(null);
        setRegenInstructions('');
        setRegenError(null);
        setRegenResult(null);
        setRegenShowDiff(false);
    }, [regenPending]);

    const submitRegen = useCallback(async () => {
        if (!projectId || !regenSection || regenPending) return;
        setRegenPending(true);
        setRegenError(null);
        const previous =
            (composeCtx?.section_drafts ?? []).find(
                (s) => s.section === regenSection,
            )?.content || '';
        try {
            const res: RegenerateResponse = await draftApi.regenerateSection(
                projectId,
                regenSection,
                { custom_instructions: regenInstructions.trim() || undefined },
            );
            setRegenResult({
                section: res.section,
                previous,
                next: res.body,
            });
            // Update local compose ctx so the section list reflects the
            // new body without a full status re-fetch.
            setComposeCtx((prev) => {
                const drafts = prev?.section_drafts ?? [];
                const updated = drafts.map((s) =>
                    s.section === res.section ? { ...s, content: res.body } : s,
                );
                return { ...(prev ?? { section_drafts: [], phase_results: {} }), section_drafts: updated };
            });
            message.success(t('draftGenerator.regen.success'));
        } catch (err) {
            const { key, message: msg } = errorKey(err);
            setRegenError(`${t(key)} — ${msg}`);
        } finally {
            setRegenPending(false);
        }
    }, [composeCtx, projectId, regenInstructions, regenPending, regenSection, t]);

    // ============================================
    // Derived UI state
    // ============================================

    const phaseResults: Partial<Record<PhaseSlot, PhaseResult>> = useMemo(() => {
        const fromStatus = (status?.phase_results ?? {}) as Partial<Record<PhaseSlot, PhaseResult>>;
        const fromResearch = (researchCtx?.phase_results ?? {}) as Partial<Record<PhaseSlot, PhaseResult>>;
        const fromStructure = (structureCtx?.phase_results ?? {}) as Partial<Record<PhaseSlot, PhaseResult>>;
        const fromCompose = (composeCtx?.phase_results ?? {}) as Partial<Record<PhaseSlot, PhaseResult>>;
        const fromCompile = (compileCtx?.phase_results ?? {}) as Partial<Record<PhaseSlot, PhaseResult>>;
        return {
            ...fromStatus,
            ...fromResearch,
            ...fromStructure,
            ...fromCompose,
            ...fromCompile,
        };
    }, [compileCtx, composeCtx, researchCtx, status, structureCtx]);

    const progressPct = useMemo(() => {
        if (typeof status?.progress_pct === 'number') {
            return Math.max(0, Math.min(100, Math.round(status.progress_pct)));
        }
        // Local fallback: count completed user-facing phases.
        const done = USER_FACING_PHASES.filter((p) => phaseResults[p]?.status === 'succeeded').length;
        return Math.round((done / USER_FACING_PHASES.length) * 100);
    }, [phaseResults, status]);

    const currentStepIndex = useMemo(() => {
        for (let i = 0; i < PIPELINE_PHASES.length; i += 1) {
            const phase = PIPELINE_PHASES[i];
            const st = phaseResults[phase]?.status;
            if (st === 'running' || st === 'pending') {
                // The first non-completed phase is "current".
                if (st === 'running') return i;
                // Pending: only consider it current if all earlier phases are done.
                const allEarlierDone = PIPELINE_PHASES.slice(0, i).every(
                    (p) => phaseResults[p]?.status === 'succeeded' || phaseResults[p]?.status === 'skipped',
                );
                if (allEarlierDone) return i;
            }
        }
        return PIPELINE_PHASES.length - 1;
    }, [phaseResults]);

    const phaseTag = (phase: PhaseSlot): React.ReactNode => {
        const st: PhaseStatus = phaseResults[phase]?.status ?? 'pending';
        const colour: Record<PhaseStatus, string> = {
            pending: 'default',
            running: 'processing',
            succeeded: 'success',
            failed: 'error',
            skipped: 'default',
        };
        return (
            <Tag color={colour[st]} data-testid={`phase-tag-${phase}`} data-phase-status={st}>
                {t(`draftGenerator.progress.${st}`)}
            </Tag>
        );
    };

    const isRunning = (phase: PhaseSlot): boolean => runningPhase === phase;
    const researchDone = phaseResults.research?.status === 'succeeded';
    const structureDone = phaseResults.structure?.status === 'succeeded';
    const composeDone = phaseResults.compose?.status === 'succeeded';

    // ============================================
    // Render
    // ============================================

    if (!projectId) {
        return (
            <div className="draft-generator empty">
                <Empty description={t('draftGenerator.status.noProject')} />
            </div>
        );
    }

    return (
        <div className="draft-generator" data-testid="draft-generator">
            <Card className="draft-intro" variant="borderless">
                <Title level={4} style={{ marginTop: 0 }}>
                    <RocketOutlined /> {t('draftGenerator.title')}
                </Title>
                <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                    {t('draftGenerator.intro')}
                </Paragraph>
            </Card>

            {phaseError && (
                <Alert
                    className="draft-alert"
                    type="error"
                    showIcon
                    icon={<WarningOutlined />}
                    message={phaseError.message}
                    closable
                    onClose={() => setPhaseError(null)}
                />
            )}

            <Card title={t('draftGenerator.title')} className="draft-form-card" variant="outlined">
                <Row gutter={16}>
                    <Col xs={24} md={12}>
                        <label className="draft-field-label" htmlFor="draft-topic">
                            {t('draftGenerator.form.topic')}
                        </label>
                        <TextArea
                            id="draft-topic"
                            data-testid="draft-topic"
                            value={topic}
                            onChange={(e) => setTopic(e.target.value)}
                            placeholder={t('draftGenerator.form.topicPlaceholder')}
                            autoSize={{ minRows: 2, maxRows: 4 }}
                            disabled={isRunning('research')}
                        />
                    </Col>
                    <Col xs={12} md={6}>
                        <label className="draft-field-label" htmlFor="draft-word-count">
                            {t('draftGenerator.form.wordCount')}
                        </label>
                        <InputNumber
                            id="draft-word-count"
                            data-testid="draft-word-count"
                            value={wordCount}
                            onChange={(value) => setWordCount(typeof value === 'number' ? value : 3000)}
                            min={500}
                            max={50000}
                            step={500}
                            style={{ width: '100%' }}
                            disabled={isRunning('research')}
                        />
                    </Col>
                    <Col xs={12} md={6}>
                        <label className="draft-field-label" htmlFor="draft-citation-style">
                            {t('draftGenerator.form.citationStyle')}
                        </label>
                        <Select<CitationStyle>
                            id="draft-citation-style"
                            data-testid="draft-citation-style"
                            value={citationStyle}
                            onChange={setCitationStyle}
                            style={{ width: '100%' }}
                            disabled={isRunning('research')}
                            options={(['APA', 'IEEE', 'CHICAGO', 'MLA'] as CitationStyle[]).map((cs) => ({
                                value: cs,
                                label: t(`draftGenerator.citationStyles.${cs}`),
                            }))}
                        />
                    </Col>
                </Row>

                <Space className="draft-actions" wrap>
                    <Button
                        type="primary"
                        icon={<SearchOutlined />}
                        loading={isRunning('research')}
                        onClick={handleResearch}
                        data-testid="btn-research"
                    >
                        {t('draftGenerator.buttons.research')}
                    </Button>
                    <Button
                        icon={<PartitionOutlined />}
                        loading={isRunning('structure')}
                        onClick={handleStructure}
                        disabled={!researchDone && !researchCtx}
                        data-testid="btn-structure"
                    >
                        {t('draftGenerator.buttons.structure')}
                    </Button>
                    <Button
                        icon={<EditOutlined />}
                        loading={isRunning('compose')}
                        onClick={handleCompose}
                        disabled={!structureDone && !structureCtx}
                        data-testid="btn-compose"
                    >
                        {t('draftGenerator.buttons.compose')}
                    </Button>
                    <Button
                        type="primary"
                        icon={<RocketOutlined />}
                        loading={isRunning('compile')}
                        onClick={handleCompile}
                        disabled={!composeDone && !composeCtx}
                        data-testid="btn-compile"
                    >
                        {t('draftGenerator.buttons.compile')}
                    </Button>
                    {runningPhase && (
                        <Button
                            danger
                            icon={<StopOutlined />}
                            className="draft-cancel-btn"
                            onClick={handleCancel}
                            loading={cancelPending}
                            disabled={cancelPending}
                            aria-label={t('draftGenerator.buttons.cancelAriaLabel')}
                            data-testid="btn-cancel"
                        >
                            {t('draftGenerator.buttons.cancel')}
                        </Button>
                    )}
                </Space>
                {cancelledNotice && (
                    <Alert
                        type="info"
                        showIcon
                        className="draft-alert"
                        style={{ marginTop: 12 }}
                        message={cancelledNotice}
                        closable
                        onClose={() => setCancelledNotice(null)}
                    />
                )}
            </Card>

            <Card
                className="draft-progress-card"
                title={
                    <Space>
                        <ExperimentOutlined />
                        {t('draftGenerator.progress.label')}
                    </Space>
                }
                extra={<Text type="secondary">{progressPct}%</Text>}
            >
                <Progress
                    percent={progressPct}
                    status={
                        phaseResults.compile?.status === 'failed' ||
                        phaseResults.compose?.status === 'failed' ||
                        phaseResults.structure?.status === 'failed' ||
                        phaseResults.research?.status === 'failed'
                            ? 'exception'
                            : progressPct === 100
                            ? 'success'
                            : 'active'
                    }
                />
                <Steps
                    className="draft-steps"
                    size="small"
                    current={currentStepIndex}
                    items={PIPELINE_PHASES.map((phase) => ({
                        title: t(`draftGenerator.phases.${phase}`),
                        description: phaseTag(phase),
                    }))}
                />
                {statusError && (
                    <Alert
                        type="warning"
                        showIcon
                        className="draft-alert"
                        message={statusError}
                        style={{ marginTop: 12 }}
                    />
                )}
            </Card>

            <Row gutter={16} className="draft-output-row">
                <Col xs={24} lg={12}>
                    <Card
                        className="draft-output-card"
                        title={
                            <Space>
                                <BookOutlined />
                                {t('draftGenerator.output.candidatePapers')}
                            </Space>
                        }
                        extra={phaseTag('research')}
                    >
                        {researchCtx?.candidate_papers && researchCtx.candidate_papers.length > 0 ? (
                            <List
                                size="small"
                                dataSource={researchCtx.candidate_papers}
                                renderItem={(paper) => (
                                    <List.Item>
                                        <List.Item.Meta
                                            title={paper.title}
                                            description={
                                                <Text type="secondary" style={{ fontSize: 12 }}>
                                                    {paper.authors?.slice(0, 3).join(', ')}
                                                    {paper.year ? ` (${paper.year})` : ''}
                                                </Text>
                                            }
                                        />
                                    </List.Item>
                                )}
                            />
                        ) : (
                            <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description={t('draftGenerator.output.empty')}
                            />
                        )}
                    </Card>
                </Col>

                <Col xs={24} lg={12}>
                    <Card
                        className="draft-output-card"
                        title={
                            <Space>
                                <FileTextOutlined />
                                {t('draftGenerator.output.outline')}
                            </Space>
                        }
                        extra={phaseTag('structure')}
                    >
                        {structureCtx?.formatted_outline ? (
                            <div className="draft-markdown">
                                <ReactMarkdown>{structureCtx.formatted_outline}</ReactMarkdown>
                            </div>
                        ) : (
                            <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description={t('draftGenerator.output.empty')}
                            />
                        )}
                    </Card>
                </Col>

                <Col xs={24} lg={12}>
                    <Card
                        className="draft-output-card"
                        title={
                            <Space>
                                <EditOutlined />
                                {t('draftGenerator.output.sections')}
                            </Space>
                        }
                        extra={phaseTag('compose')}
                    >
                        {composeCtx?.section_drafts && composeCtx.section_drafts.length > 0 ? (
                            <List
                                size="small"
                                dataSource={composeCtx.section_drafts}
                                renderItem={(sec) => (
                                    <List.Item
                                        actions={[
                                            <Button
                                                key="regen"
                                                type="link"
                                                size="small"
                                                icon={<ReloadOutlined />}
                                                onClick={() => openRegenModal(sec.section)}
                                                data-testid={`btn-regen-${sec.section}`}
                                                disabled={Boolean(runningPhase) || regenPending}
                                            >
                                                {t('draftGenerator.regen.submit')}
                                            </Button>,
                                        ]}
                                    >
                                        <List.Item.Meta
                                            title={sec.section}
                                            description={
                                                <Paragraph
                                                    type="secondary"
                                                    ellipsis={{ rows: 3 }}
                                                    style={{ marginBottom: 0, fontSize: 12 }}
                                                >
                                                    {sec.content}
                                                </Paragraph>
                                            }
                                        />
                                    </List.Item>
                                )}
                            />
                        ) : (
                            <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description={t('draftGenerator.output.empty')}
                            />
                        )}
                    </Card>
                </Col>

                <Col xs={24} lg={12}>
                    <Card
                        className="draft-output-card"
                        title={
                            <Space>
                                <RocketOutlined />
                                {t('draftGenerator.output.finalDraft')}
                            </Space>
                        }
                        extra={phaseTag('compile')}
                    >
                        {compileCtx?.final_draft ? (
                            <Spin spinning={isRunning('compile')}>
                                <div className="draft-markdown final-draft">
                                    <ReactMarkdown>{compileCtx.final_draft}</ReactMarkdown>
                                </div>
                            </Spin>
                        ) : (
                            <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description={t('draftGenerator.output.empty')}
                            />
                        )}
                    </Card>
                </Col>
            </Row>

            <Modal
                title={
                    regenSection
                        ? t('draftGenerator.regen.modalTitle', { section: regenSection })
                        : ''
                }
                open={Boolean(regenSection)}
                onCancel={closeRegenModal}
                footer={null}
                destroyOnHidden
                data-testid="regen-modal"
            >
                {regenSection && (
                    <div className="draft-section-regen">
                        <label className="draft-field-label" htmlFor="regen-instructions">
                            {t('draftGenerator.regen.instructionsLabel')}
                        </label>
                        <Input.TextArea
                            id="regen-instructions"
                            data-testid="regen-instructions"
                            value={regenInstructions}
                            onChange={(e) => setRegenInstructions(e.target.value)}
                            placeholder={t('draftGenerator.regen.instructionsPlaceholder')}
                            autoSize={{ minRows: 3, maxRows: 6 }}
                            disabled={regenPending}
                        />
                        {regenError && (
                            <Alert
                                type="error"
                                showIcon
                                className="draft-alert"
                                style={{ marginTop: 12 }}
                                message={`${t('draftGenerator.regen.errorTitle')}: ${regenError}`}
                            />
                        )}
                        {regenResult && (
                            <Alert
                                type="success"
                                showIcon
                                className="draft-alert"
                                style={{ marginTop: 12 }}
                                message={t('draftGenerator.regen.success')}
                                description={
                                    <Button
                                        size="small"
                                        type="link"
                                        onClick={() => setRegenShowDiff((v) => !v)}
                                        data-testid="btn-toggle-diff"
                                        style={{ paddingLeft: 0 }}
                                    >
                                        {regenShowDiff
                                            ? t('draftGenerator.regen.hideDiff')
                                            : t('draftGenerator.regen.viewDiff')}
                                    </Button>
                                }
                            />
                        )}
                        {regenResult && regenShowDiff && (
                            <div className="draft-diff" data-testid="regen-diff">
                                <pre data-testid="regen-diff-prev">
                                    {regenResult.previous || '(empty)'}
                                </pre>
                                <pre data-testid="regen-diff-next">
                                    {regenResult.next || '(empty)'}
                                </pre>
                            </div>
                        )}
                        <Space style={{ marginTop: 16, width: '100%', justifyContent: 'flex-end' }}>
                            <Button onClick={closeRegenModal} disabled={regenPending}>
                                {t('common.cancel')}
                            </Button>
                            <Button
                                type="primary"
                                onClick={submitRegen}
                                loading={regenPending}
                                disabled={regenPending}
                                data-testid="btn-regen-submit"
                            >
                                {regenPending
                                    ? t('draftGenerator.regen.running')
                                    : t('draftGenerator.regen.submit')}
                            </Button>
                        </Space>
                    </div>
                )}
            </Modal>
        </div>
    );
};

// Helper: build a per-phase record for the type system. The literal
// `as PhaseName` from the server is mapped to our local PhaseSlot;
// the server may also return more phase names than the 4 user-facing
// ones, which is fine.
export type { PhaseName };

export default DraftGenerator;
