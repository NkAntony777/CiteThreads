"""
Per-phase checkpoint persistence for the CTDP draft pipeline.

Why per-phase files?
- A single ``draft_state.json`` is fine while everything is healthy, but
  it conflates "what we have" with "what we last tried". If a phase
  crash leaves a partial blob, the next request can either trust it
  (risky) or wipe the entire context (catastrophic).
- One file per phase means a failure during, say, Compose only
  threatens Compose's outputs. Research, Structure, Validate, and
  Compile outputs are isolated and recoverable.
- Resume decisions can be made on a per-phase basis: "structure
  checkpoint is stale (older than research), so re-run structure but
  keep research's outputs."

Storage layout
--------------
``data/projects/{project_id}/checkpoints/{phase_name}.json``

Each file is a JSON object with this shape::

    {
        "phase": "research",
        "version": 1,
        "saved_at": "2026-06-04T12:00:00Z",
        "phase_result": {...},   # serialized PhaseResult
        "outputs": {...}        # phase-specific fields, see PHASE_OUTPUTS
    }

Atomicity
---------
Writes use the same ``tempfile + os.replace`` trick as the main
``draft_state.json`` so a crash mid-write never leaves a half-written
file the next request will read.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .context import PhaseName, PhaseResult, PhaseStatus

logger = logging.getLogger(__name__)


# Bump when the on-disk schema changes in a breaking way. A stale
# file is detected by version mismatch and silently ignored.
CHECKPOINT_VERSION = 1


# Map of phase name -> list of DraftContext field names owned by that
# phase. When we save a checkpoint we snapshot just these fields; when
# we resume we write them back. Keep this list in sync with
# ``DraftContext`` field docstrings.
PHASE_OUTPUTS: dict[PhaseName, list[str]] = {
    PhaseName.RESEARCH: [
        "candidate_papers",
        "paper_summaries",
        "research_gaps",
    ],
    PhaseName.STRUCTURE: [
        "outline",
        "formatted_outline",
    ],
    PhaseName.COMPOSE: [
        "section_drafts",
    ],
    PhaseName.VALIDATE: [
        "qa_report",
    ],
    PhaseName.COMPILE: [
        "final_draft",
        "quality_history",
    ],
}


def _checkpoints_dir(project_id: str) -> Path:
    """Where per-phase checkpoint files live."""
    # Imported lazily to avoid a circular import at module load.
    from ..storage import project_storage

    return project_storage._get_project_dir(project_id) / "checkpoints"


def _checkpoint_path(project_id: str, phase: PhaseName) -> Path:
    return _checkpoints_dir(project_id) / f"{phase.value}.json"


def _write_atomic(path: Path, data: dict) -> None:
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_phase_checkpoint(
    project_id: str,
    phase: PhaseName,
    phase_result: PhaseResult,
    outputs: dict[str, Any],
) -> Path:
    """Persist a per-phase checkpoint.

    Args:
        project_id: Owning project id.
        phase: The phase this checkpoint represents.
        phase_result: The ``PhaseResult`` record for this phase
            (RUNNING, SUCCEEDED, FAILED, ...).
        outputs: Phase-owned fields from ``DraftContext`` (see
            ``PHASE_OUTPUTS``). Only the listed fields are snapshotted
            so a checkpoint is self-contained but minimal.

    Returns:
        The on-disk path the checkpoint was written to. Useful for
        tests; the router does not need it.

    Raises:
        OSError: if the write fails. Callers may swallow this and
            fall back to in-memory state.
    """
    path = _checkpoint_path(project_id, phase)
    payload = {
        "phase": phase.value,
        "version": CHECKPOINT_VERSION,
        "saved_at": datetime.utcnow().isoformat(),
        "phase_result": phase_result.model_dump(mode="json"),
        "outputs": outputs,
    }
    _write_atomic(path, payload)
    logger.debug(
        "checkpoint: saved %s for project %s at %s",
        phase.value, project_id, path,
    )
    return path


def load_phase_checkpoint(
    project_id: str, phase: PhaseName
) -> Optional[dict]:
    """Load a per-phase checkpoint, or return ``None`` if absent.

    Returns a dict with keys ``phase``, ``version``, ``saved_at``,
    ``phase_result`` (dict, not ``PhaseResult``), ``outputs``.

    Returns None when:
    - the file does not exist
    - the file is unreadable / corrupted (logged at WARNING)
    - the ``version`` field does not match ``CHECKPOINT_VERSION``
    """
    path = _checkpoint_path(project_id, phase)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "checkpoint: failed to read %s: %s", path, exc,
        )
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != CHECKPOINT_VERSION:
        logger.info(
            "checkpoint: ignoring stale version %s at %s",
            data.get("version"), path,
        )
        return None
    return data


def has_phase_checkpoint(project_id: str, phase: PhaseName) -> bool:
    """Cheap existence check; does not read the file contents."""
    return _checkpoint_path(project_id, phase).exists()


def delete_phase_checkpoint(project_id: str, phase: PhaseName) -> bool:
    """Remove a checkpoint file. Returns True if a file was deleted.

    Missing files are not an error — we just return False. Used when a
    phase needs to be re-run from scratch (e.g. user changed inputs)."""
    path = _checkpoint_path(project_id, phase)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("checkpoint: failed to delete %s: %s", path, exc)
        return False


def list_checkpoints(project_id: str) -> list[PhaseName]:
    """List phases that have a checkpoint on disk. Order is not
    guaranteed; callers should sort by ``PhaseName`` if they care."""
    d = _checkpoints_dir(project_id)
    if not d.exists():
        return []
    found: list[PhaseName] = []
    for child in d.iterdir():
        if not child.is_file() or not child.suffix == ".json":
            continue
        name = child.stem
        for p in PhaseName:
            if p.value == name:
                found.append(p)
                break
    return found


def snapshot_phase_outputs(
    phase: PhaseName, ctx: Any
) -> dict[str, Any]:
    """Extract the phase-owned fields from a ``DraftContext`` for
    inclusion in a checkpoint payload.

    Returns a dict keyed by ``DraftContext`` field name. Empty fields
    are still included as their empty value so the checkpoint shape
    is stable across runs.
    """
    fields = PHASE_OUTPUTS.get(phase, [])
    out: dict[str, Any] = {}
    for fname in fields:
        out[fname] = getattr(ctx, fname, None)
    return out


def restore_phase_outputs(ctx: Any, phase: PhaseName, outputs: dict[str, Any]) -> None:
    """Write the phase-owned fields from a checkpoint payload back onto
    ``ctx``. Fields present in ``outputs`` overwrite; missing fields
    are left alone (so a partial checkpoint doesn't wipe a value the
    next phase needs).
    """
    for fname, value in outputs.items():
        if fname not in PHASE_OUTPUTS.get(phase, []):
            # Ignore foreign fields defensively (e.g. hand-edited file).
            continue
        setattr(ctx, fname, value)


__all__ = [
    "CHECKPOINT_VERSION",
    "PHASE_OUTPUTS",
    "save_phase_checkpoint",
    "load_phase_checkpoint",
    "has_phase_checkpoint",
    "delete_phase_checkpoint",
    "list_checkpoints",
    "snapshot_phase_outputs",
    "restore_phase_outputs",
]
