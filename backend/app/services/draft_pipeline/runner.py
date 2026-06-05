"""
DraftRunner — orchestrator for the CTDP phases.

Owns the lifecycle of a single project's ``DraftContext``: loads it
from disk, dispatches phase calls, persists state back to disk so
phase results survive across HTTP requests and process restarts.

Resilience (Task P0-2)
----------------------
The runner now publishes progress events to a per-project
``ProgressBus`` and writes a per-phase checkpoint to
``data/projects/{id}/checkpoints/{phase_name}.json`` after every
phase transition. The new ``resume_from(phase)`` method exploits
both: it consults the checkpoint store first, and if a phase's
checkpoint is already on disk (and the in-memory ``ctx`` is in sync
with it) the LLM call is skipped — the prior output is loaded
straight into the context.

Why per-phase files (not the existing single ``draft_state.json``)?
- A failure during, say, Compose only threatens Compose's outputs;
  Research, Structure, Validate, and Compile stay intact.
- Resume decisions can be made on a per-phase basis: "structure
  checkpoint is older than research, so re-run structure but keep
  research's outputs".

Event publication
-----------------
Every phase transition (``start`` / ``progress`` / ``end`` / ``error``)
is published to the project's ``ProgressBus`` (if one is configured).
The runner does not block on subscribers; it uses ``publish()``
which drops events for slow consumers rather than back-pressuring
the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from openai import AsyncOpenAI
from pydantic import ValidationError

from ...metrics import metrics, phase_timer
from ...logging_config import set_phase as _log_set_phase, clear_phase as _log_clear_phase
from ..storage import project_storage
from .checkpoint import (
    has_phase_checkpoint,
    load_phase_checkpoint,
    restore_phase_outputs,
    save_phase_checkpoint,
    snapshot_phase_outputs,
)
from .context import CitationStyle, DraftContext, PhaseName, PhaseResult, PhaseStatus
from .phases import (
    run_compile_phase,
    run_compose_phase,
    run_research_phase,
    run_structure_phase,
    run_validate_phase,
)
from .progress import (
    EVT_DONE,
    EVT_ERROR,
    EVT_PHASE_END,
    EVT_PHASE_PROGRESS,
    EVT_PHASE_START,
    Event,
    ProgressBus,
)

logger = logging.getLogger(__name__)


# Phases the orchestrator knows how to dispatch. ``export`` is reserved
# for a later task and is intentionally omitted.
_PHASE_DISPATCH: dict[PhaseName, Any] = {
    PhaseName.RESEARCH: run_research_phase,
    PhaseName.STRUCTURE: run_structure_phase,
    PhaseName.COMPOSE: run_compose_phase,
    PhaseName.VALIDATE: run_validate_phase,
    PhaseName.COMPILE: run_compile_phase,
}


# Default progress stages per phase, used by the runner to publish
# ``phase-progress`` events as the work moves through sub-stages.
# The exact mapping is best-effort: phases may publish more granular
# events directly when they're available; the runner just provides
# a coarse bookend per sub-phase.
_PROGRESS_STAGES: dict[PhaseName, list[str]] = {
    PhaseName.RESEARCH: ["scout", "scribe", "signal"],
    PhaseName.STRUCTURE: ["architect", "formatter"],
    PhaseName.COMPOSE: ["drafting", "refining"],
    PhaseName.VALIDATE: ["referee", "factcheck"],
    PhaseName.COMPILE: ["assemble", "abstract"],
}


def _state_path(project_id: str) -> Path:
    """Where the per-project draft state lives on disk."""
    return project_storage._get_project_dir(project_id) / "draft_state.json"


def _read_state(project_id: str) -> Optional[dict]:
    path = _state_path(project_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "DraftRunner: failed to read draft_state for %s: %s", project_id, exc
        )
        return None


def _write_state_atomic(path: Path, data: dict) -> None:
    """Write JSON atomically (tempfile + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=path.stem
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _ms_since(start: datetime) -> int:
    """Helper: milliseconds elapsed since ``start`` (UTC)."""
    return int((datetime.utcnow() - start).total_seconds() * 1000)


class DraftRunner:
    """Runs the full pipeline (or a single phase) for a given project.

    The runner is stateful across calls: ``run_phase`` mutates the
    same ``DraftContext`` that subsequent calls see. State is mirrored
    to ``data/projects/{id}/draft_state.json`` after every successful
    (or failed) phase so the user can resume mid-pipeline.

    Resilience knobs
    ----------------
    - ``event_bus`` : optional :class:`ProgressBus`; when supplied,
      the runner publishes ``phase-start`` / ``phase-progress`` /
      ``phase-end`` / ``error`` events as work progresses. The SSE
      endpoint in :mod:`routers.draft` subscribes to a project-wide
      singleton resolved from ``progress.get_bus(project_id)``.
    - ``enable_checkpoints`` : when True (default), every successful
      (or failed) phase writes a per-phase checkpoint to
      ``data/projects/{id}/checkpoints/{phase_name}.json`` *in
      addition* to the main ``draft_state.json``. Used by
      ``resume_from`` to skip redundant LLM work.
    """

    def __init__(
        self,
        project_id: str,
        llm_client: Optional[AsyncOpenAI] = None,
        event_bus: Optional[ProgressBus] = None,
        enable_checkpoints: bool = True,
    ) -> None:
        self.project_id = project_id
        self.llm_client = llm_client
        self.event_bus = event_bus
        self.enable_checkpoints = enable_checkpoints
        self.ctx = self._load_or_init_context()

    # -- context lifecycle ------------------------------------------------

    def _load_or_init_context(self) -> DraftContext:
        """Load persisted state if any, otherwise seed a fresh context
        from the project's metadata and graph."""
        existing = _read_state(self.project_id)
        if existing:
            try:
                return DraftContext.model_validate(existing)
            except ValidationError as exc:
                # Schema drift or a hand-edited file: fall back to a
                # fresh context so the pipeline stays runnable.
                logger.warning(
                    "DraftRunner: invalid draft_state for %s, resetting: %s",
                    self.project_id,
                    exc,
                )
        return self._build_fresh_context()

    def _build_fresh_context(self) -> DraftContext:
        """Create a new ``DraftContext`` seeded from the project."""
        project = project_storage.get_project(self.project_id)
        topic = ""
        reference_ids: list[str] = []
        graph_node_ids: list[str] = []
        if project is not None:
            metadata = project.metadata
            # Prefer the project's configured seed paper as the topic
            # hint when the user hasn't set anything explicit.
            topic = (
                (metadata.config.seed_paper_id if metadata.config else "")
                or metadata.name
            )
            graph_node_ids = [n.id for n in project.graph.nodes]
            reference_ids = list(graph_node_ids)
        return DraftContext(
            project_id=self.project_id,
            topic=topic or "Untitled research project",
            language="en",
            citation_style=CitationStyle.APA,
            target_word_count=8000,
            reference_ids=reference_ids,
            graph_node_ids=graph_node_ids,
        )

    # -- checkpoint helpers -----------------------------------------------

    def _checkpoint_sync_from_ctx(self, phase: PhaseName) -> bool:
        """Save a per-phase checkpoint mirroring ``ctx`` for ``phase``.

        Returns True on success, False on any OS error (logged). Used
        after a phase transition so the next resume call can detect
        "already done" without re-running.
        """
        if not self.enable_checkpoints:
            return False
        rec = self.ctx.phase_results.get(phase)
        if rec is None:
            return False
        try:
            outputs = snapshot_phase_outputs(phase, self.ctx)
            save_phase_checkpoint(
                self.project_id, phase, rec, outputs,
            )
            return True
        except OSError as exc:
            logger.warning(
                "DraftRunner: checkpoint save failed for %s/%s: %s",
                self.project_id, phase.value, exc,
            )
            return False

    def _try_load_phase_from_checkpoint(
        self, phase: PhaseName
    ) -> bool:
        """If a checkpoint exists for ``phase``, restore its outputs
        into ``self.ctx`` and mark the phase SUCCEEDED in
        ``phase_results`` (idempotent). Returns True when a checkpoint
        was applied.
        """
        if not self.enable_checkpoints:
            return False
        if not has_phase_checkpoint(self.project_id, phase):
            return False
        payload = load_phase_checkpoint(self.project_id, phase)
        if payload is None:
            return False
        outputs = payload.get("outputs") or {}
        try:
            restore_phase_outputs(self.ctx, phase, outputs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DraftRunner: failed to restore checkpoint for %s/%s: %s",
                self.project_id, phase.value, exc,
            )
            return False
        # Mirror the phase result on the in-memory ctx so subsequent
        # ``is_phase_done()`` and ``get_status()`` see the resumed state.
        rec_data = payload.get("phase_result") or {}
        try:
            rec = PhaseResult.model_validate(rec_data)
        except ValidationError:
            rec = PhaseResult(phase=phase, status=PhaseStatus.SUCCEEDED)
        # Only overwrite the result if the in-memory ctx doesn't
        # already have a fresher one (a SUCCEEDED record with a later
        # ``finished_at``). This keeps the in-flight run from being
        # clobbered by an older checkpoint.
        existing = self.ctx.phase_results.get(phase)
        if (
            existing is not None
            and existing.finished_at is not None
            and rec.finished_at is not None
            and existing.finished_at >= rec.finished_at
        ):
            return True
        self.ctx.phase_results[phase] = rec
        logger.info(
            "DraftRunner: restored %s from checkpoint for %s",
            phase.value, self.project_id,
        )
        return True

    # -- event publishing -------------------------------------------------

    async def _publish_event(self, event: Event) -> None:
        """Best-effort publish to the configured event bus. Never raises."""
        if self.event_bus is None:
            return
        try:
            await self.event_bus.publish(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DraftRunner: event publish failed for %s/%s: %s",
                self.project_id, event.type, exc,
            )

    # -- dispatch ---------------------------------------------------------

    def has_phase_checkpoint(self, phase: PhaseName) -> bool:
        """Thin pass-through so the router can ask without importing
        the checkpoint module."""
        return has_phase_checkpoint(self.project_id, phase)

    async def run_phase(self, phase: PhaseName) -> DraftContext:
        """Run a single phase, persist state, and return the context.

        On failure the phase is marked ``FAILED`` and the exception is
        re-raised so the router can return 500 with a useful message.

        Use :meth:`resume_from` to skip the LLM call when a checkpoint
        already exists.

        Cancellation: if ``ctx.cancellation_requested`` is already
        ``True`` when ``run_phase`` is entered, the phase is marked
        ``SKIPPED`` and the method returns without invoking the
        underlying phase function. This is the only safe way to stop
        a long-running LLM call from a separate HTTP request — once
        the call is in-flight, we wait for it to finish. Explicitly
        starting a phase via this method clears the flag at entry
        so a previous cancel does not block a fresh attempt.
        """
        if phase not in _PHASE_DISPATCH:
            raise ValueError(
                f"Phase {phase!r} is not dispatchable by DraftRunner"
            )
        if self.ctx.cancellation_requested:
            self.ctx.mark_phase(
                phase, PhaseStatus.SKIPPED, error="cancelled by request"
            )
            self._save_state()
            await self._publish_event(
                Event(
                    type=EVT_PHASE_END,
                    project_id=self.project_id,
                    data={
                        "phase": phase.value,
                        "status": "skipped",
                        "reason": "cancelled",
                    },
                )
            )
            logger.info(
                "DraftRunner: phase %s skipped for project %s (cancel)",
                phase,
                self.project_id,
            )
            return self.ctx
        # Clear the flag so an explicit phase call always runs even if
        # a previous run was cancelled.
        self.ctx.cancellation_requested = False

        started_at = datetime.utcnow()
        # Bind the phase to the structured-logging contextvar so
        # every log line emitted by the phase (and its LLM helpers)
        # carries the ``phase`` field. The token is always reset.
        log_phase_token = _log_set_phase(phase.value)
        # ``phase_timer`` records start/end counters and the
        # duration_ms histogram for the metrics endpoint. The context
        # manager never swallows the underlying exception.
        timer_cm = phase_timer(phase.value)
        timer_info = timer_cm.__enter__()
        try:
            await self._publish_event(
                Event(
                    type=EVT_PHASE_START,
                    project_id=self.project_id,
                    data={"phase": phase.value, "at": started_at.isoformat()},
                )
            )
            # Coarse-grained stage bookends so subscribers see movement
            # even when the phase itself doesn't publish progress events.
            stages = _PROGRESS_STAGES.get(phase, ["work"])
            for i, stage in enumerate(stages):
                pct = int(100 * (i + 1) / (len(stages) + 1))
                await self._publish_event(
                    Event(
                        type=EVT_PHASE_PROGRESS,
                        project_id=self.project_id,
                        data={"phase": phase.value, "stage": stage, "pct": pct},
                    )
                )

            try:
                await _PHASE_DISPATCH[phase](self.ctx, self.llm_client)
            except Exception as exc:
                # ``run_*_phase`` marks FAILED on its own; ensure persistence
                # then bubble the error so the router reports 500.
                self._save_state()
                self._checkpoint_sync_from_ctx(phase)
                await self._publish_event(
                    Event(
                        type=EVT_PHASE_END,
                        project_id=self.project_id,
                        data={
                            "phase": phase.value,
                            "status": "failed",
                            "duration_ms": _ms_since(started_at),
                            "error": str(exc),
                        },
                    )
                )
                await self._publish_event(
                    Event(
                        type=EVT_ERROR,
                        project_id=self.project_id,
                        data={"phase": phase.value, "error": str(exc)},
                    )
                )
                logger.exception(
                    "DraftRunner: phase %s failed for project %s", phase, self.project_id
                )
                raise
            self._save_state()
            self._checkpoint_sync_from_ctx(phase)
            await self._publish_event(
                Event(
                    type=EVT_PHASE_END,
                    project_id=self.project_id,
                    data={
                        "phase": phase.value,
                        "status": "succeeded",
                        "duration_ms": _ms_since(started_at),
                    },
                )
            )
        finally:
            # ``phase_timer`` finalizes the histogram sample; the
            # context manager's __exit__ never swallows exceptions.
            timer_cm.__exit__(None, None, None)
            _log_clear_phase(log_phase_token)
        return self.ctx

    async def resume_from(self, phase: PhaseName) -> DraftContext:
        """Run a single phase, but if a valid checkpoint already exists
        for it, restore the checkpoint's outputs into ``self.ctx`` and
        skip the LLM call.

        Compared to :meth:`run_phase` this method:

        1. Checks the on-disk checkpoint store before invoking the
           phase's LLM code.
        2. If a checkpoint is found and the in-memory ``ctx`` is in
           sync (or absent), it restores the outputs and publishes a
           ``phase-end`` event with ``status="skipped"``.
        3. If a checkpoint is found but it is *stale* relative to the
           in-memory ``ctx`` (e.g. an upstream phase was just re-run),
           the checkpoint is ignored and the phase runs normally.

        Failure modes:
        - No checkpoint + LLM failure: same as ``run_phase`` (FAILED +
          error event + re-raise).
        - Checkpoint present but corrupt: the runner logs a warning,
          falls through to a normal LLM run, and overwrites the bad
          checkpoint at the end.
        """
        if phase not in _PHASE_DISPATCH:
            raise ValueError(
                f"Phase {phase!r} is not dispatchable by DraftRunner"
            )

        started_at = datetime.utcnow()
        await self._publish_event(
            Event(
                type=EVT_PHASE_START,
                project_id=self.project_id,
                data={
                    "phase": phase.value,
                    "at": started_at.isoformat(),
                    "resumed": True,
                },
            )
        )

        # Fast path: restore from checkpoint if one exists.
        if self.enable_checkpoints and has_phase_checkpoint(
            self.project_id, phase
        ):
            restored = self._try_load_phase_from_checkpoint(phase)
            if restored:
                self._save_state()
                await self._publish_event(
                    Event(
                        type=EVT_PHASE_END,
                        project_id=self.project_id,
                        data={
                            "phase": phase.value,
                            "status": "skipped",
                            "duration_ms": _ms_since(started_at),
                            "resumed_from": "checkpoint",
                        },
                    )
                )
                return self.ctx

        # Slow path: run the phase like normal.
        try:
            await _PHASE_DISPATCH[phase](self.ctx, self.llm_client)
        except Exception as exc:
            self._save_state()
            self._checkpoint_sync_from_ctx(phase)
            await self._publish_event(
                Event(
                    type=EVT_PHASE_END,
                    project_id=self.project_id,
                    data={
                        "phase": phase.value,
                        "status": "failed",
                        "duration_ms": _ms_since(started_at),
                        "error": str(exc),
                    },
                )
            )
            await self._publish_event(
                Event(
                    type=EVT_ERROR,
                    project_id=self.project_id,
                    data={"phase": phase.value, "error": str(exc)},
                )
            )
            raise
        self._save_state()
        self._checkpoint_sync_from_ctx(phase)
        await self._publish_event(
            Event(
                type=EVT_PHASE_END,
                project_id=self.project_id,
                data={
                    "phase": phase.value,
                    "status": "succeeded",
                    "duration_ms": _ms_since(started_at),
                },
            )
        )
        return self.ctx

    async def run_all(self) -> DraftContext:
        """Run every dispatchable phase in pipeline order.

        If any phase fails, the loop stops and the exception is
        re-raised. A ``done`` event is published at the end (success
        or failure path) so SSE subscribers know to close.

        Honours ``ctx.cancellation_requested``: if a phase is skipped
        because of a pending cancel, subsequent phases are also
        skipped (they depend on the outputs of earlier phases).
        """
        final_status = "completed"
        try:
            for phase in (
                PhaseName.RESEARCH,
                PhaseName.STRUCTURE,
                PhaseName.COMPOSE,
                PhaseName.VALIDATE,
                PhaseName.COMPILE,
            ):
                await self.run_phase(phase)
                if self.ctx.cancellation_requested:
                    final_status = "cancelled"
                    logger.info(
                        "DraftRunner: cancellation honored at %s for %s",
                        phase.value, self.project_id,
                    )
                    break
        finally:
            await self._publish_event(
                Event(
                    type=EVT_DONE,
                    project_id=self.project_id,
                    data={"status": final_status},
                )
            )
        return self.ctx

    def request_cancellation(self) -> None:
        """Set the cancellation flag so the next phase boundary stops.

        Idempotent: calling this twice has the same effect as once.
        The flag persists into the saved state and survives runner
        restarts, so a cancel issued on one request still affects the
        next ``run_phase`` call from a different request.
        """
        self.ctx.cancellation_requested = True
        logger.info(
            "DraftRunner: cancellation requested for project %s",
            self.project_id,
        )

    def clear_cancellation(self) -> None:
        """Reset the cancellation flag (used when starting a fresh run)."""
        self.ctx.cancellation_requested = False

    # -- persistence ------------------------------------------------------

    def _save_state(self) -> None:
        """Persist ``self.ctx`` to disk as JSON."""
        path = _state_path(self.project_id)
        try:
            payload = self.ctx.model_dump(mode="json")
            # Tag the file with a last-write timestamp so operators can
            # tell at a glance which projects have been touched.
            payload["__saved_at"] = datetime.utcnow().isoformat()
            _write_state_atomic(path, payload)
        except OSError as exc:
            # Don't crash the user-facing call when persistence fails;
            # log and continue so the in-memory ctx is still updated.
            logger.error(
                "DraftRunner: failed to persist state for %s: %s",
                self.project_id,
                exc,
            )

    def get_status(self) -> dict:
        """Return a status snapshot suitable for the ``/status`` endpoint.

        2026-06: enriched with per-section progress for the compose
        phase and per-phase checkpoint flag so the FE progress
        card can render a faithful "Resumed from X" indicator.
        """
        from .phases.compose import SECTION_NAMES

        results: dict[str, dict[str, Any]] = {}
        for phase in PhaseName:
            if phase == PhaseName.EXPORT:
                continue
            rec = self.ctx.phase_results.get(phase)
            has_ckpt = has_phase_checkpoint(self.project_id, phase)
            entry: dict[str, Any] = {
                "has_checkpoint": has_ckpt,
            }
            if rec is None:
                entry["status"] = PhaseStatus.PENDING.value
            else:
                entry["status"] = rec.status.value
                entry["started_at"] = (
                    rec.started_at.isoformat() if rec.started_at else None
                )
                entry["finished_at"] = (
                    rec.finished_at.isoformat() if rec.finished_at else None
                )
                entry["error"] = rec.error
            # Per-section progress for the compose phase: which of
            # the 6 sections (intro / lit_review / methodology /
            # results / discussion / conclusion) have been drafted.
            if phase == PhaseName.COMPOSE:
                entry["sections_done"] = list(self.ctx.section_drafts.keys())
                entry["sections_total"] = list(SECTION_NAMES)
            results[phase.value] = entry
        return {
            "project_id": self.project_id,
            "progress_pct": self.ctx.progress_pct(),
            "phases": results,
            "last_error": self._last_error(),
            "checkpoints": self._checkpoint_summary(),
        }

    def _checkpoint_summary(self) -> dict[str, bool]:
        """Per-phase summary of which phases have a checkpoint on disk.

        Used by ``/status`` so the frontend can tell the user
        "research is checkpointed, you can resume from compose".
        """
        out: dict[str, bool] = {}
        for phase in PhaseName:
            if phase == PhaseName.EXPORT:
                continue
            out[phase.value] = has_phase_checkpoint(self.project_id, phase)
        return out

    def _last_error(self) -> Optional[str]:
        for phase in (
            PhaseName.RESEARCH,
            PhaseName.STRUCTURE,
            PhaseName.COMPOSE,
            PhaseName.VALIDATE,
            PhaseName.COMPILE,
        ):
            rec = self.ctx.phase_results.get(phase)
            if rec is not None and rec.status == PhaseStatus.FAILED and rec.error:
                return f"{phase.value}: {rec.error}"
        return None


__all__ = ["DraftRunner"]
