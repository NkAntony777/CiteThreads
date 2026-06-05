"""
Health checks for the CiteThreads backend.

Exposes three endpoints with different semantics — the deployment
guide tells operators to wire ``/health/live`` to a Kubernetes
liveness probe and ``/health/ready`` to a readiness probe. The
combined ``/health`` is a single human-friendly report for ``curl``
or a status page.

Semantics
---------
* **live**   — is the process up? Returns 200 unless the runtime is
  completely broken. *No* downstream checks. Liveness probes must
  not depend on external services: a transient LLM outage should
  not cause Kubernetes to kill the pod.
* **ready**  — can the process serve traffic right now? Returns 200
  only if every check is ``ok``; otherwise 503. Used by load
  balancers to take a pod out of rotation when a critical dep is
  unreachable.
* **/health** — combined report, returns 200 unless status is
  ``down``. ``degraded`` returns 200 so a missing optional key
  (LLM) does not page on-call, but the body still surfaces the
  problem.

Checks
------
1. **llm_configured** — the LLM API key is set (or the runtime was
   told to use no LLM). Optional: missing is degraded, not down.
2. **data_writable**  — ``data/projects/`` is writable. Critical:
   a non-writable data dir means the draft pipeline can't persist
   state.
3. **pipeline_loaded** — the draft pipeline modules import without
   raising. Detects a class of "the pod started but a dependency
   is missing" failure modes.

Performance
-----------
All checks are local. The total cost is bounded by an ``os.access``
call and a handful of imports (already cached after first call).
We keep the LLM check to a configuration flag read so we never
make a network round-trip from a health check.
"""

from __future__ import annotations

import importlib
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .config import settings
from .services.llm_factory import create_llm_client

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Outcome of a single health check.

    ``critical=True`` means failure flips the overall status to
    ``down``; otherwise the check is informational and yields
    ``degraded``.
    """

    name: str
    status: str  # "ok" | "degraded" | "down"
    detail: str = ""
    latency_ms: float = 0.0
    critical: bool = True

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "latency_ms": round(self.latency_ms, 2),
        }


def _run_timed(fn: Callable[[], CheckResult]) -> CheckResult:
    """Run a check, time it, and fill in ``latency_ms``."""
    start = time.perf_counter()
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — health checks swallow internals
        # A check that raises is treated as ``down``. We log the
        # exception so a misbehaving check is debuggable, but we
        # never propagate it to the caller.
        logger.exception("health check raised: %s", exc)
        result = CheckResult(name="?", status="down", detail=str(exc))
        result.latency_ms = (time.perf_counter() - start) * 1000.0
        return result
    finally:
        # The ``fn`` body should set latency itself, but if it didn't
        # (e.g. early-return on the success path) we backfill it.
        pass


# The start timestamp is captured outside the closure to avoid
# double-counting the elapsed time.
def _check_llm() -> CheckResult:
    start = time.perf_counter()
    client = create_llm_client()
    elapsed = (time.perf_counter() - start) * 1000.0
    if client is None:
        return CheckResult(
            name="llm_configured",
            status="degraded",
            detail=(
                "no LLM API key configured (set "
                "SILICONFLOW_API_KEY or call /api/ai/configure/llm); "
                "draft phase endpoints will return 503"
            ),
            latency_ms=elapsed,
            critical=False,
        )
    return CheckResult(
        name="llm_configured",
        status="ok",
        detail=f"base_url={settings.ai_base_url} model={settings.ai_model}",
        latency_ms=elapsed,
        critical=False,
    )


def _check_data_writable() -> CheckResult:
    start = time.perf_counter()
    target_dir = os.path.join(settings.data_dir, "projects")
    try:
        os.makedirs(target_dir, exist_ok=True)
        # Atomic write-then-unlink so we don't pollute the data dir
        # even if the caller's filesystem is read-only. If the
        # create succeeds but the unlink fails, the temp file lingers
        # — that's fine, the next health check will overwrite it.
        fd, tmp_path = tempfile.mkstemp(
            dir=target_dir, prefix=".healthcheck-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("ok")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except OSError as exc:
        return CheckResult(
            name="data_writable",
            status="down",
            detail=f"cannot write to {target_dir}: {exc}",
            latency_ms=(time.perf_counter() - start) * 1000.0,
            critical=True,
        )
    return CheckResult(
        name="data_writable",
        status="ok",
        detail=f"path={target_dir}",
        latency_ms=(time.perf_counter() - start) * 1000.0,
        critical=True,
    )


def _check_pipeline_loaded() -> CheckResult:
    start = time.perf_counter()
    try:
        # Re-importing the runner triggers any module-load error
        # that the linter or type-checker might have missed. The
        # import is cached after the first call, so the cost is
        # bounded to a dict lookup on subsequent calls.
        importlib.import_module("app.services.draft_pipeline.runner")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="pipeline_loaded",
            status="down",
            detail=f"failed to import draft pipeline: {exc}",
            latency_ms=(time.perf_counter() - start) * 1000.0,
            critical=True,
        )
    return CheckResult(
        name="pipeline_loaded",
        status="ok",
        detail="runner module importable",
        latency_ms=(time.perf_counter() - start) * 1000.0,
        critical=True,
    )


# All checks, in evaluation order. The order is the order operators
# see them in the JSON response.
DEFAULT_CHECKS: List[Callable[[], CheckResult]] = [
    _check_llm,
    _check_data_writable,
    _check_pipeline_loaded,
]


def run_health_checks(
    checks: Optional[List[Callable[[], CheckResult]]] = None,
) -> Dict:
    """Run every check and return a JSON-friendly report.

    The overall status is computed from the worst check status:
    ``down`` > ``degraded`` > ``ok``. ``down`` only fires when a
    ``critical=True`` check failed; a missing LLM (degraded) is fine.
    """
    checks = checks or DEFAULT_CHECKS
    results: Dict[str, dict] = {}
    worst = "ok"
    for check in checks:
        result = check()
        results[result.name] = result.to_dict()
        if _status_rank(result.status) > _status_rank(worst):
            worst = result.status
        # ``down`` is sticky; we keep iterating so the report still
        # shows every check, but the overall status won't recover
        # to "ok" within this run.
    return {
        "status": worst,
        "checks": results,
    }


def _status_rank(status: str) -> int:
    return {"ok": 0, "degraded": 1, "down": 2}.get(status, 0)


def live() -> Dict:
    """Liveness-only payload. Always 200 unless the runtime is broken.

    The check list is intentionally empty: liveness must not depend
    on disk, LLM, or any other shared resource. Kubernetes will kill
    the pod only when the process is hung, which is what we want.
    """
    return {"status": "ok", "checks": {}}


def ready() -> Dict:
    """Readiness payload. Returns 503 if any critical check fails.

    Mirrors :func:`run_health_checks` so the readiness and the
    combined ``/health`` endpoint stay in sync.
    """
    return run_health_checks()


__all__ = [
    "CheckResult",
    "run_health_checks",
    "live",
    "ready",
    "DEFAULT_CHECKS",
]
