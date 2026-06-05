"""
Draft Pipeline API Router
=========================

Thin HTTP layer over :class:`DraftRunner`. Each phase endpoint runs
one phase, persists state to ``data/projects/{id}/draft_state.json``,
and returns a JSON shape the frontend can render.

Endpoints (all require ``Authorization: Bearer <token>``):

- ``POST /api/draft/projects/{id}/research``  — run Scout+Scribe+Signal
- ``POST /api/draft/projects/{id}/structure`` — run Architect+Formatter
- ``POST /api/draft/projects/{id}/compose``   — run Crafter (long)
- ``POST /api/draft/projects/{id}/validate``  — run Referee+FactCheck
- ``POST /api/draft/projects/{id}/compile``   — run Compiler
- ``POST /api/draft/projects/{id}/run-all``   — run every phase in order
- ``POST /api/draft/projects/{id}/sections/{section}/regenerate`` — re-craft one section
- ``POST /api/draft/projects/{id}/cancel``   — request cancellation of a running phase
- ``GET  /api/draft/projects/{id}/status``    — phase progress + last error
- ``GET  /api/draft/projects/{id}/draft.md``  — final draft (404 if not compiled)
- ``GET  /api/draft/projects/{id}/export.pdf``  — compiled draft as PDF
- ``GET  /api/draft/projects/{id}/export.docx`` — compiled draft as DOCX
- ``GET  /api/draft/projects/{id}/export.tex``  — compiled draft as LaTeX source

LLM client
-----------
The router resolves an LLM client from ``settings.siliconflow_api_key``
(via :func:`create_llm_client`). If no key is configured the router
returns ``503`` for phase endpoints so the frontend can show a
"configure LLM" hint. ``/status``, ``/draft.md``, and the three
``/export.*`` endpoints are LLM-free and work regardless of key state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel

from ..auth import BearerAuthDep
from ..rate_limit import make_llm_guard_dependency
from ..services.draft_pipeline.context import PhaseName, PhaseStatus
from ..services.draft_pipeline.exporters import to_docx, to_latex, to_pdf
from ..services.draft_pipeline.phases.compose import (
    SECTION_NAMES,
    crafter,
)
from ..services.draft_pipeline.progress import (
    EVT_DONE,
    Event as ProgressEvent,
    ProgressBus,
    get_bus,
    get_bus_sync,
)
from ..services.draft_pipeline.runner import DraftRunner
from ..services.llm_factory import create_llm_client
from ..services.storage import project_storage
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/draft", tags=["draft"])


# How often to send a keepalive comment frame while a phase is
# running. Proxies (nginx, Cloudflare, corporate gateways) often kill
# idle connections after 30-60s; 15s is a safe middle ground. The
# compose phase can run for several minutes, so without pings the
# browser will silently drop the stream.
SSE_KEEPALIVE_INTERVAL_S = float(15.0)


# ``_PROJECT_ID_RE`` mirrors the regex in storage.py. We duplicate it
# here so the router can reject obviously bad ids with 400 *before*
# touching the storage layer (and so a typo doesn't trigger a 500).
_PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_project_id(project_id: str) -> str:
    if not project_id or not _PROJECT_ID_RE.match(project_id):
        raise HTTPException(
            status_code=400, detail=f"invalid project_id: {project_id!r}"
        )
    return project_id


def _ensure_project_exists(project_id: str) -> None:
    if project_storage.get_project(project_id) is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )


def _resolve_llm_client() -> Optional[AsyncOpenAI]:
    """Return a configured LLM client, or None if no key is set."""
    return create_llm_client()


def _require_llm_client() -> AsyncOpenAI:
    """Return a configured LLM client or raise 503."""
    client = _resolve_llm_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "LLM is not configured: set SILICONFLOW_API_KEY in the "
                "server environment, or POST /api/ai/configure/llm first."
            ),
        )
    return client


def _build_runner(project_id: str) -> DraftRunner:
    """Build a ``DraftRunner`` for ``project_id`` with a live LLM client
    if one is configured. The runner falls back to LLM-less mode
    internally, but the phase endpoints call ``_require_llm_client``
    first so a 503 surfaces before the runner is invoked.

    The runner is wired to the project's per-project
    :class:`ProgressBus` (lazily created) so any work it does
    publishes events that the ``/stream`` endpoint can subscribe to.
    We use ``get_bus_sync`` here to avoid an ``await`` in the
    synchronous call paths; ``/stream`` will await ``get_bus`` on the
    first subscriber to ensure the registry entry exists.
    """
    bus = get_bus_sync(project_id)
    return DraftRunner(
        project_id=project_id,
        llm_client=_resolve_llm_client(),
        event_bus=bus,
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PhaseResponse(BaseModel):
    """Response body returned by every phase POST endpoint."""

    success: bool
    project_id: str
    phase: str
    status: str
    progress_pct: float
    error: Optional[str] = None
    summary: dict[str, Any] = {}
    # True when the endpoint short-circuited via runner.resume_from
    # because a checkpoint already existed. Drives the "Resumed"
    # indicator in the FE progress card.
    resumed_from_checkpoint: bool = False


def _phase_summary(ctx, phase: PhaseName) -> dict[str, Any]:
    """Build a small per-phase summary the frontend can render."""
    if phase == PhaseName.RESEARCH:
        return {
            "candidate_papers": len(ctx.candidate_papers or []),
            "paper_summaries": len(ctx.paper_summaries or []),
            "research_gaps": len(ctx.research_gaps or []),
        }
    if phase == PhaseName.STRUCTURE:
        sections: list[dict[str, Any]] = []
        outline = ctx.outline if isinstance(ctx.outline, dict) else {}
        for s in outline.get("sections", []) or []:
            sections.append(
                {
                    "number": s.get("number"),
                    "title": s.get("title"),
                    "target_words": s.get("target_words"),
                }
            )
        return {
            "paper_type": outline.get("paper_type", ""),
            "section_count": len(sections),
            "sections": sections,
            "formatted_outline_chars": len(ctx.formatted_outline or ""),
        }
    if phase == PhaseName.COMPOSE:
        drafts = ctx.section_drafts or {}
        total_chars = sum(len(v or "") for v in drafts.values())
        return {
            "section_count": len(drafts),
            "sections_written": list(drafts.keys()),
            "total_chars": total_chars,
        }
    if phase == PhaseName.VALIDATE:
        qa = ctx.qa_report or ""
        return {
            "qa_report_chars": len(qa),
            "verdict": _last_validate_verdict(ctx),
        }
    if phase == PhaseName.COMPILE:
        return {
            "final_draft_chars": len(ctx.final_draft or ""),
            "quality_history_len": len(ctx.quality_history or []),
        }
    return {}


def _last_validate_verdict(ctx) -> Optional[str]:
    """Pull the most recent validate verdict off ``ctx.quality_history``."""
    for entry in reversed(ctx.quality_history or []):
        if isinstance(entry, dict) and entry.get("phase") == PhaseName.VALIDATE.value:
            verdict = entry.get("verdict")
            if verdict:
                return str(verdict)
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def _run_phase_endpoint(
    project_id: str, phase: PhaseName
) -> PhaseResponse:
    """Shared body for all five phase endpoints.

    Per-phase auto-resume: when a checkpoint already exists for
    ``phase`` on disk, the runner's ``resume_from`` is used so
    already-completed substeps aren't re-run. This is what the
    "Retry" button on a failed-phase card relies on.
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id)
    _require_llm_client()
    runner = _build_runner(project_id)
    try:
        # Auto-resume: if a checkpoint exists, only the missing
        # substeps run; otherwise this falls through to a fresh
        # run_phase.
        if runner.has_phase_checkpoint(phase):
            await runner.resume_from(phase)
        else:
            await runner.run_phase(phase)
    except Exception as exc:
        # Phase already marked FAILED inside the orchestrator.
        logger.exception("draft %s failed for %s", phase.value, project_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    rec = runner.ctx.phase_results.get(phase)
    return PhaseResponse(
        success=True,
        project_id=project_id,
        phase=phase.value,
        status=(rec.status.value if rec else "succeeded"),
        progress_pct=runner.ctx.progress_pct(),
        error=(rec.error if rec and rec.error else None),
        summary=_phase_summary(runner.ctx, phase),
        # New: tell the FE whether we actually used the checkpoint
        # path so the progress card can show a subtle "Resumed"
        # indicator instead of a fresh "Running" pulse.
        resumed_from_checkpoint=runner.has_phase_checkpoint(phase),
    )


@router.post(
    "/projects/{project_id}/research",
    response_model=PhaseResponse,
    dependencies=[
        BearerAuthDep,
        Depends(make_llm_guard_dependency("draft.research")),
    ],
)
async def run_research(project_id: str = Path(..., min_length=1)) -> PhaseResponse:
    """Run the research phase (Scout + Scribe + Signal)."""
    return await _run_phase_endpoint(project_id, PhaseName.RESEARCH)


@router.post(
    "/projects/{project_id}/structure",
    response_model=PhaseResponse,
    dependencies=[
        BearerAuthDep,
        Depends(make_llm_guard_dependency("draft.structure")),
    ],
)
async def run_structure(project_id: str = Path(..., min_length=1)) -> PhaseResponse:
    """Run the structure phase (Architect + Formatter)."""
    return await _run_phase_endpoint(project_id, PhaseName.STRUCTURE)


@router.post(
    "/projects/{project_id}/compose",
    response_model=PhaseResponse,
    dependencies=[
        BearerAuthDep,
        Depends(make_llm_guard_dependency("draft.compose")),
    ],
)
async def run_compose(project_id: str = Path(..., min_length=1)) -> PhaseResponse:
    """Run the compose phase (Crafter for all sections). Long-running."""
    return await _run_phase_endpoint(project_id, PhaseName.COMPOSE)


@router.post(
    "/projects/{project_id}/validate",
    response_model=PhaseResponse,
    dependencies=[
        BearerAuthDep,
        Depends(make_llm_guard_dependency("draft.validate")),
    ],
)
async def run_validate(project_id: str = Path(..., min_length=1)) -> PhaseResponse:
    """Run the validate phase (Referee + FactCheck)."""
    return await _run_phase_endpoint(project_id, PhaseName.VALIDATE)


@router.post(
    "/projects/{project_id}/compile",
    response_model=PhaseResponse,
    dependencies=[
        BearerAuthDep,
        Depends(make_llm_guard_dependency("draft.compile")),
    ],
)
async def run_compile(project_id: str = Path(..., min_length=1)) -> PhaseResponse:
    """Run the compile phase (Compiler). Produces ``ctx.final_draft``."""
    return await _run_phase_endpoint(project_id, PhaseName.COMPILE)


@router.post(
    "/projects/{project_id}/run-all",
    response_model=PhaseResponse,
    dependencies=[BearerAuthDep,
                  Depends(make_llm_guard_dependency("draft.run_all"))],
)
async def run_all_phases(
    project_id: str = Path(..., min_length=1),
) -> PhaseResponse:
    """Run every phase in pipeline order. Long-running; intended for
    nightly jobs or a single "regenerate" button."""
    _validate_project_id(project_id)
    _ensure_project_exists(project_id)
    _require_llm_client()
    runner = _build_runner(project_id)
    try:
        await runner.run_all()
    except Exception as exc:
        logger.exception("draft run-all failed for %s", project_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    compile_rec = runner.ctx.phase_results.get(PhaseName.COMPILE)
    return PhaseResponse(
        success=True,
        project_id=project_id,
        phase="run-all",
        status=(compile_rec.status.value if compile_rec else "succeeded"),
        progress_pct=runner.ctx.progress_pct(),
        error=(compile_rec.error if compile_rec and compile_rec.error else None),
        summary={
            "final_draft_chars": len(runner.ctx.final_draft or ""),
        },
    )


# ---------------------------------------------------------------------------
# Per-section regeneration
# ---------------------------------------------------------------------------


class RegenerateRequest(BaseModel):
    """Optional body for the per-section regenerate endpoint."""

    custom_instructions: Optional[str] = None
    model: Optional[str] = None


class RegenerateResponse(BaseModel):
    """Response body for the per-section regenerate endpoint."""

    success: bool
    project_id: str
    section: str
    body: str
    body_chars: int
    progress_pct: float
    message: str = ""


@router.post(
    "/projects/{project_id}/sections/{section_name}/regenerate",
    response_model=RegenerateResponse,
    dependencies=[BearerAuthDep],
)
async def regenerate_section(
    project_id: str = Path(..., min_length=1),
    section_name: str = Path(..., min_length=1),
    request: RegenerateRequest = Body(default_factory=RegenerateRequest),
) -> RegenerateResponse:
    """Regenerate a single section's draft.

    Reads the existing ``ctx.section_drafts[section_name]`` (so the
    previous draft is overwritten, not appended), re-runs the
    matching Crafter function with optional ``custom_instructions``
    appended to the user message, and persists state.

    Returns:
        200 — new section body and progress.
        400 — unknown section_name.
        404 — section has not been drafted yet (the frontend should
              point the user at running the compose phase first).
        503 — LLM not configured.
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id)
    if section_name not in SECTION_NAMES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown section {section_name!r}. "
                f"Expected one of {SECTION_NAMES}."
            ),
        )
    runner = _build_runner(project_id)
    drafts = runner.ctx.section_drafts or {}
    if section_name not in drafts or not drafts.get(section_name):
        raise HTTPException(
            status_code=404,
            detail=(
                f"section {section_name!r} has not been drafted yet; "
                "POST /api/draft/projects/{id}/compose first."
            ),
        )
    client = _require_llm_client()
    try:
        result = await crafter(
            runner.ctx,
            client,
            section_name,
            custom_instructions=request.custom_instructions,
        )
    except Exception as exc:
        # Persist whatever state was mutated before bubbling the error.
        runner._save_state()
        logger.exception(
            "draft regenerate %s failed for %s", section_name, project_id
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not isinstance(runner.ctx.section_drafts, dict):
        runner.ctx.section_drafts = {}
    runner.ctx.section_drafts[section_name] = result.draft.body
    # Mark the compose phase as still succeeded if it was — regenerating
    # a single section shouldn't downgrade the overall verdict.
    runner.ctx.mark_phase(PhaseName.COMPOSE, PhaseStatus.SUCCEEDED)
    runner._save_state()
    body = result.draft.body
    return RegenerateResponse(
        success=True,
        project_id=project_id,
        section=section_name,
        body=body,
        body_chars=len(body),
        progress_pct=runner.ctx.progress_pct(),
        message=(
            "regenerated with custom instructions"
            if (request.custom_instructions or "").strip()
            else "regenerated with default prompt"
        ),
    )


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class CancelResponse(BaseModel):
    """Response body for the cancel endpoint."""

    cancelled: bool
    project_id: str
    already_running: bool
    message: str = ""


@router.post(
    "/projects/{project_id}/cancel",
    response_model=CancelResponse,
    dependencies=[BearerAuthDep],
)
async def cancel_pipeline(
    project_id: str = Path(..., min_length=1),
) -> CancelResponse:
    """Request cancellation of any in-flight draft phase.

    Sets the ``DraftContext.cancellation_requested`` flag, which the
    runner checks between phases and between sub-operations within
    the compose phase. The next LLM call boundary is the natural
    cancel point.

    Idempotent: calling cancel when no phase is running is a no-op
    that still returns 200 with ``cancelled=True`` so the frontend
    can settle the UI state.
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id)
    runner = _build_runner(project_id)
    already = bool(runner.ctx.cancellation_requested)
    runner.request_cancellation()
    runner._save_state()
    return CancelResponse(
        cancelled=True,
        project_id=project_id,
        already_running=already,
        message="cancellation flag set",
    )


@router.get(
    "/projects/{project_id}/status",
    dependencies=[BearerAuthDep],
)
async def get_status(project_id: str = Path(..., min_length=1)) -> dict:
    """Return the current phase status for ``project_id``.

    LLM-free: works even when no key is configured. Returns 404 only
    if the project itself doesn't exist; an un-started pipeline just
    reports ``progress_pct=0`` and all phases ``pending``.
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id)
    runner = _build_runner(project_id)
    return runner.get_status()


@router.get(
    "/projects/{project_id}/draft.md",
    response_class=PlainTextResponse,
    dependencies=[BearerAuthDep],
)
async def get_draft_markdown(
    project_id: str = Path(..., min_length=1),
) -> PlainTextResponse:
    """Return ``ctx.final_draft`` as plain text/markdown.

    Returns 404 when the compile phase has not produced a final draft
    yet (or when the project doesn't exist).
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id)
    runner = _build_runner(project_id)
    if not runner.ctx.final_draft:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no compiled draft for project {project_id!r}: "
                "POST /api/draft/projects/{id}/compile first."
            ),
        )
    return PlainTextResponse(content=runner.ctx.final_draft)


# ---------------------------------------------------------------------------
# Document export (PDF / DOCX / LaTeX)
# ---------------------------------------------------------------------------


def _resolve_final_draft(project_id: str) -> str:
    """Load ``ctx.final_draft`` for ``project_id`` or 404.

    Centralized so all three export endpoints agree on the "no
    compiled draft yet" semantics. Returns the markdown body when
    present; the exporters consume the markdown directly.
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id)
    runner = _build_runner(project_id)
    if not runner.ctx.final_draft:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no compiled draft for project {project_id!r}: "
                "POST /api/draft/projects/{id}/compile first."
            ),
        )
    return runner.ctx.final_draft


@router.get(
    "/projects/{project_id}/export.pdf",
    response_class=Response,
    dependencies=[BearerAuthDep],
)
async def export_draft_pdf(
    project_id: str = Path(..., min_length=1),
) -> Response:
    """Return the compiled draft as a PDF attachment.

    The PDF is produced by :func:`app.services.draft_pipeline.exporters.to_pdf`,
    which uses WeasyPrint to render an HTML+CSS representation of the
    compiled markdown. WeasyPrint depends on the GTK3 / Pango native
    libraries — on platforms where those are missing (notably stock
    Windows) this endpoint returns 503 with a clear message instead
    of a 500. The test suite detects the missing dependency and
    ``pytest.skip``\s the import-level smoke test rather than fail.
    """
    md = _resolve_final_draft(project_id)
    try:
        body = to_pdf(md, project_id)
    except OSError as exc:
        # WeasyPrint's libgobject/pango dlopen failure surfaces as
        # OSError("cannot load library 'libgobject-2.0-0'") on
        # Windows and as a similar libdl error on minimal Linux
        # containers. Convert to 503 so the frontend can offer a
        # "configure export dependencies" hint.
        logger.warning(
            "export.pdf: native library missing for %s: %s", project_id, exc
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "PDF export is unavailable on this server: a required "
                "native library (libgobject / libpango) is missing. "
                "Install GTK3 runtime or use export.docx / export.tex."
            ),
        ) from exc
    return Response(
        content=body,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}.pdf"'
        },
    )


@router.get(
    "/projects/{project_id}/export.docx",
    response_class=Response,
    dependencies=[BearerAuthDep],
)
async def export_draft_docx(
    project_id: str = Path(..., min_length=1),
) -> Response:
    """Return the compiled draft as a DOCX attachment.

    DOCX is produced by :func:`app.services.draft_pipeline.exporters.to_docx`
    using the ``python-docx`` library. Inline markdown is reduced to
    plain text; block-level structure (headings, paragraphs, block
    quotes, pipe tables) is preserved. The ``[@paper_id]`` citation
    markers are kept verbatim so the user can post-process them.
    """
    md = _resolve_final_draft(project_id)
    body = to_docx(md, project_id)
    return Response(
        content=body,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}.docx"'
        },
    )


@router.get(
    "/projects/{project_id}/export.tex",
    response_class=Response,
    dependencies=[BearerAuthDep],
)
async def export_draft_latex(
    project_id: str = Path(..., min_length=1),
) -> Response:
    """Return the compiled draft as a LaTeX source file.

    The exporter emits a self-contained ``\\documentclass{article}``
    document with the first H1 promoted to ``\\title`` and ``[@id]``
    citations mapped to ``\\cite{...}`` keys. No external
    ``\\usepackage{hyperref}`` is pulled in so the file compiles on
    a vanilla TeX Live install; the bibliography block is left as
    a comment so the user can paste in their own ``.bib``.
    """
    md = _resolve_final_draft(project_id)
    body = to_latex(md, project_id)
    return Response(
        content=body,
        media_type="application/x-tex",
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}.tex"'
        },
    )


# ---------------------------------------------------------------------------
# Server-Sent Events progress stream (Task P0-2)
# ---------------------------------------------------------------------------


def _sse_format(event: ProgressEvent) -> str:
    """Format a :class:`ProgressEvent` as one SSE message::

        event: <type>\\n
        data: <json>\\n
        \\n
    """
    return (
        f"event: {event.type}\n"
        f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
    )


async def _draft_event_stream(
    project_id: str,
    bus: ProgressBus,
    keepalive_interval: Optional[float] = None,
) -> AsyncIterator[str]:
    """Bridge a :class:`ProgressBus` subscription to SSE frames.

    Behaviour:
    - Subscribes to ``bus`` on entry; the returned queue receives
      every event published for ``project_id`` (from this request and
      any concurrent runner calls).
    - Translates each event to an SSE frame with the event name and a
      JSON payload containing ``type``, ``project_id``, ``at``,
      ``data``.
    - Sends a ``: heartbeat\\n\\n`` comment every ``keepalive_interval``
      seconds (default ``SSE_KEEPALIVE_INTERVAL_S``) so intermediate
      proxies do not silently drop the connection while a long
      compose phase is running.
    - On normal completion the ``done`` event is followed by a final
      comment and the generator returns. On client disconnect
      (``asyncio.CancelledError``) the queue is unsubscribed and the
      task ends.

    The generator is testable by setting ``keepalive_interval`` to a
    small value (e.g. ``0.05``) and asserting the comment line
    appears between events.
    """
    interval = (
        keepalive_interval
        if keepalive_interval is not None
        else SSE_KEEPALIVE_INTERVAL_S
    )
    # Opening comment: tell clients + proxies we're alive.
    yield ": stream-open\n\n"

    queue = await bus.subscribe()
    last_ping = time.monotonic()
    try:
        while True:
            timeout = max(0.05, interval - (time.monotonic() - last_ping))
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                # No event for the keepalive window -> emit a comment
                # so the connection stays warm.
                yield ": heartbeat\n\n"
                last_ping = time.monotonic()
                continue
            yield _sse_format(event)
            last_ping = time.monotonic()
            if event.type == EVT_DONE:
                # Terminal event: clients may now close the stream.
                yield ": stream-end\n\n"
                break
    except asyncio.CancelledError:
        # Client disconnected; quietly clean up the subscription.
        logger.info(
            "draft /stream: client disconnected for project %s", project_id,
        )
        raise
    finally:
        await bus.unsubscribe(queue)


@router.get(
    "/projects/{project_id}/stream",
    dependencies=[BearerAuthDep],
)
async def stream_draft_progress(
    project_id: str = Path(..., min_length=1),
) -> StreamingResponse:
    """Stream ``DraftRunner`` progress events as Server-Sent Events.

    The endpoint is purely passive — it does not run a phase itself.
    Subscribe before kicking off a phase (``POST /research`` etc.) to
    watch progress in real time. Multiple subscribers on the same
    project each receive every event; the runner is unaffected.

    Event sequence (one run of one phase)::

        event: phase-start
        data: {"type": "phase-start", "data": {"phase": "research", ...}}

        event: phase-progress   (zero or more)
        data: {"type": "phase-progress", "data": {"phase": "research", "stage": "scout", "pct": 30}, ...}

        event: phase-end
        data: {"type": "phase-end", "data": {"phase": "research", "status": "succeeded", "duration_ms": 12000}, ...}

        event: done              (only after ``run-all``)
        data: {"type": "done", "data": {"status": "completed"}, ...}

    On error the runner publishes ``phase-end`` with ``status=failed``
    followed by ``error`` (with the error message), then ``done``.

    Keepalive: a ``: heartbeat`` SSE comment is sent every
    ``SSE_KEEPALIVE_INTERVAL_S`` seconds (15s) when no event has
    arrived.
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id)
    bus = await get_bus(project_id)
    generator = _draft_event_stream(project_id, bus)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # tell nginx not to buffer
            "Connection": "keep-alive",
        },
    )


__all__ = ["router"]
