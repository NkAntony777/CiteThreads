"""
Cost guard — per-user LLM token usage tracking
==============================================

Records every LLM call's token usage to append-only JSONL files at::

    data/usage/{user_id}/{YYYY-MM}.jsonl

and enforces each user's :attr:`UserContext.monthly_token_budget`.

The format is one JSON object per line:

.. code-block:: json

    {"user_id": "alice", "phase": "draft.research",
     "prompt_tokens": 1234, "completion_tokens": 567,
     "total_tokens": 1801, "timestamp": "2026-06-05T10:11:12Z"}

Why JSONL: easy to ``tail -f`` and ``grep``, atomic appends, and we
can compute aggregates by reading line-by-line without holding the
whole month in memory.

Public surface
--------------
- :class:`TokenUsage` — Pydantic model for a single record
- :class:`UsageSummary` — aggregated view (total, by phase)
- :class:`CostGuard` — record/aggregate/check
- :data:`COST_GUARD` — module-level singleton
- :func:`record_llm_usage` — convenience used by the LLM factory
  wrapper
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class TokenUsage(BaseModel):
    """One LLM call's worth of token consumption."""

    user_id: str
    phase: str = Field(default="unknown", description="calling subsystem, e.g. draft.research")
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_dump_jsonl(self) -> str:
        """Serialise to a single JSON line (compact, no newlines)."""
        return self.model_dump_json()


class UsageSummary(BaseModel):
    """Aggregated usage for one user/month."""

    user_id: str
    month: str  # YYYY-MM
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    call_count: int = 0
    by_phase: Dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cost guard
# ---------------------------------------------------------------------------


# User ids with non-alphanumeric characters would break the directory
# layout. We validate aggressively because the path is constructed
# from user input and a stray ``../`` would be a vulnerability.
_SAFE_USER_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
)


def _safe_user_segment(user_id: str) -> str:
    """Return a directory-safe version of ``user_id`` or raise
    :class:`ValueError` for unsafe input."""
    if not user_id:
        raise ValueError("user_id must be non-empty")
    for ch in user_id:
        if ch not in _SAFE_USER_ID_CHARS:
            raise ValueError(f"unsafe character {ch!r} in user_id")
    return user_id


class CostGuard:
    """JSONL-backed per-user LLM usage tracker.

    Thread-safe for concurrent writes (the in-memory cache is
    protected by ``_lock``; the on-disk append is best-effort — a
    failed write is logged but does not crash the LLM call).
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self._base = Path(base_dir or os.path.join(settings.data_dir, "usage"))
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Per-user running totals so the budget check doesn't have to
        # reread the JSONL every time. Keyed by (user_id, YYYY-MM).
        self._totals: Dict[Tuple[str, str], UsageSummary] = {}

    # -- path helpers ---------------------------------------------------

    def _month_path(self, user_id: str, month: str) -> Path:
        seg = _safe_user_segment(user_id)
        if not month or len(month) != 7 or month[4] != "-":
            raise ValueError(f"month must be YYYY-MM, got {month!r}")
        d = self._base / seg / f"{month}.jsonl"
        d.parent.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _current_month() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    # -- write -----------------------------------------------------------

    def record(
        self,
        user_id: str,
        phase: str,
        prompt_tokens: int,
        completion_tokens: int,
        timestamp: Optional[datetime] = None,
    ) -> TokenUsage:
        """Append a usage record to disk and update the in-memory
        running total. Returns the persisted :class:`TokenUsage`.

        Disk-write failures are logged but do not propagate: the LLM
        call has already happened and we don't want to fail the
        user's request because the usage log is unwritable.
        """
        ts = timestamp or datetime.now(timezone.utc)
        total = max(0, int(prompt_tokens)) + max(0, int(completion_tokens))
        record = TokenUsage(
            user_id=user_id,
            phase=phase or "unknown",
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            total_tokens=total,
            timestamp=ts,
        )
        month = ts.strftime("%Y-%m")
        try:
            path = self._month_path(user_id, month)
            with open(path, "a", encoding="utf-8") as f:
                f.write(record.model_dump_jsonl() + "\n")
        except Exception as exc:  # noqa: BLE001 — never fail the LLM call
            logger.warning("cost_guard: failed to append usage for %s: %s", user_id, exc)

        with self._lock:
            key = (user_id, month)
            summary = self._totals.get(key)
            if summary is None:
                summary = UsageSummary(user_id=user_id, month=month)
                self._totals[key] = summary
            summary.total_tokens += record.total_tokens
            summary.prompt_tokens += record.prompt_tokens
            summary.completion_tokens += record.completion_tokens
            summary.call_count += 1
            summary.by_phase[record.phase] = (
                summary.by_phase.get(record.phase, 0) + record.total_tokens
            )

        return record

    # -- read ------------------------------------------------------------

    def get_summary(self, user_id: str, month: Optional[str] = None) -> UsageSummary:
        """Compute aggregated usage for ``user_id``/``month``. Reads
        the JSONL from disk (does not trust the in-memory cache) so
        a process restart doesn't reset the totals."""
        month = month or self._current_month()
        path = self._month_path(user_id, month)
        summary = UsageSummary(user_id=user_id, month=month)
        if not path.is_file():
            return summary
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = TokenUsage.model_validate_json(line)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("cost_guard: skipping bad line in %s: %s", path, exc)
                    continue
                summary.total_tokens += rec.total_tokens
                summary.prompt_tokens += rec.prompt_tokens
                summary.completion_tokens += rec.completion_tokens
                summary.call_count += 1
                summary.by_phase[rec.phase] = (
                    summary.by_phase.get(rec.phase, 0) + rec.total_tokens
                )
        return summary

    def check_budget(
        self, user_id: str, monthly_budget: int, month: Optional[str] = None
    ) -> Tuple[bool, int, int]:
        """Return ``(under_budget, used, budget)``.

        ``under_budget`` is True if the user can still spend more
        tokens this month. ``used`` is the running total (as of
        this moment), ``budget`` is the configured limit.
        """
        used = self.get_summary(user_id, month).total_tokens
        return (used < monthly_budget, used, monthly_budget)

    # -- maintenance ----------------------------------------------------

    def reset(self, user_id: Optional[str] = None) -> None:
        """Clear in-memory totals (does not touch disk). Tests use
        this between cases."""
        with self._lock:
            if user_id is None:
                self._totals.clear()
            else:
                self._totals = {
                    k: v for k, v in self._totals.items() if k[0] != user_id
                }


# ---------------------------------------------------------------------------
# Module-level singleton + convenience helpers
# ---------------------------------------------------------------------------


COST_GUARD = CostGuard()


def record_llm_usage(
    user_id: str,
    phase: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> TokenUsage:
    """Module-level convenience used by the LLM factory wrapper so
    callers don't have to import the singleton directly."""
    return COST_GUARD.record(
        user_id=user_id,
        phase=phase,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


__all__ = [
    "TokenUsage",
    "UsageSummary",
    "CostGuard",
    "COST_GUARD",
    "record_llm_usage",
]
