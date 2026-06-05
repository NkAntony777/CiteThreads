"""
In-memory rate limiter
======================

A simple per-user sliding-window limiter used to protect the
LLM-calling endpoints (``/api/draft/*`` and ``/api/agent/*``) from
runaway traffic. Designed for a single-process dev/test server; not
suitable for a multi-replica production deploy (each process would
keep its own counter).

Algorithm
---------
For each user we keep a deque of the last N request timestamps. On
``check()`` we drop entries older than 60s, then compare the deque
length against the user's limit. If we're over, the call is rejected
and we compute the number of seconds until the oldest in-window
request rolls off (= the ``Retry-After`` hint).

The limiter is intentionally permissive: the default limit is 10
req/min, configurable per user via ``users.json``. When no users are
configured the dev ``ANONYMOUS_ADMIN`` carries a 10k/min budget,
which effectively disables the limiter.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe in-memory rate limiter, one bucket per user_id."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = float(window_seconds)
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    # -- public API -----------------------------------------------------

    def check(
        self, user_id: str, limit_per_window: int
    ) -> Tuple[bool, int]:
        """Check whether ``user_id`` may make one more request right
        now under the given per-window limit.

        Returns ``(allowed, retry_after_seconds)``. ``retry_after``
        is 0 when ``allowed`` is True; otherwise it's the number of
        whole seconds the caller should wait before retrying.
        """
        if not user_id or limit_per_window <= 0:
            return True, 0

        now = time.monotonic()
        cutoff = now - self._window

        with self._lock:
            bucket = self._buckets.setdefault(user_id, deque())
            # Drop entries that have aged out of the window.
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) < limit_per_window:
                bucket.append(now)
                return True, 0

            # Over the limit — Retry-After = time until the oldest
            # entry ages out, rounded up to the next whole second so
            # clients don't immediately retry and bounce again.
            oldest = bucket[0]
            retry = max(1, int((oldest + self._window) - now + 0.999))
            return False, retry

    def reset(self, user_id: Optional[str] = None) -> None:
        """Clear one user's bucket (or all of them). Tests use this
        to start each case from a clean slate."""
        with self._lock:
            if user_id is None:
                self._buckets.clear()
            else:
                self._buckets.pop(user_id, None)

    def snapshot(self) -> Dict[str, int]:
        """Return ``{user_id: current_bucket_size}`` for debugging /
        observability."""
        with self._lock:
            return {uid: len(b) for uid, b in self._buckets.items()}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


RATE_LIMITER = RateLimiter()


# ---------------------------------------------------------------------------
# FastAPI dependency helpers
# ---------------------------------------------------------------------------


def _enforce_rate_limit(
    user_id: str, limit_per_minute: int
) -> None:
    """Raise HTTPException(429) when the user is over the limit. The
    dependency injection path uses this."""
    from fastapi import HTTPException, status  # local import keeps the
    # module importable from contexts that don't have FastAPI

    allowed, retry_after = RATE_LIMITER.check(user_id, limit_per_minute)
    if not allowed:
        logger.info(
            "rate-limit: user=%s over limit (%d/min); retry-after=%ds",
            user_id,
            limit_per_minute,
            retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"rate limit exceeded ({limit_per_minute} req/min); "
                f"retry in {retry_after}s"
            ),
            headers={"Retry-After": str(retry_after)},
        )


def enforce_llm_guard(user, phase: str) -> None:
    """Combined dependency body for the LLM-calling endpoints.

    - Sets the current-user + current-phase context vars so the
      usage-recording wrapper inside ``AsyncOpenAI`` attributes
      every LLM call correctly.
    - Enforces the per-user rate limit (HTTP 429 + ``Retry-After``).
    - Enforces the per-user monthly budget (HTTP 429 +
      ``X-Reason: budget_exceeded``).

    ``user`` is the :class:`UserContext` resolved by ``require_user``;
    ``phase`` is a short label like ``"draft.research"`` recorded
    with the usage.
    """
    from .users import ANONYMOUS_ADMIN
    from .services.llm_factory import set_current_user, set_current_phase
    from . import cost_guard

    # ContextVar bindings — token-based so a concurrent request in
    # the same task doesn't leak.
    set_current_user(user.user_id)
    set_current_phase(phase)

    # Rate limit
    _enforce_rate_limit(user.user_id, user.rate_limit_per_minute)

    # Budget — the dev ``ANONYMOUS_ADMIN`` carries a 1B budget, so
    # this is effectively disabled in dev mode. Real users hit a
    # hard 429 once they cross the line.
    if user.user_id != ANONYMOUS_ADMIN.user_id or user.monthly_token_budget < 10**8:
        under, used, budget = cost_guard.COST_GUARD.check_budget(
            user.user_id, user.monthly_token_budget
        )
        if not under:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"monthly token budget exceeded: "
                    f"used={used}, budget={budget}"
                ),
                headers={
                    "X-Reason": "budget_exceeded",
                    "X-Used-Tokens": str(used),
                    "X-Budget-Tokens": str(budget),
                },
            )


def make_llm_guard_dependency(phase: str):
    """Build a FastAPI dependency that runs the LLM guard for a
    specific phase. Use it via ``Depends(make_llm_guard_dependency("draft.research"))``
    on a route signature that also has ``user: UserContext = UserAuthDep``."""
    from fastapi import Depends
    from .auth import require_user

    def _dep(user=Depends(require_user)) -> None:
        enforce_llm_guard(user, phase)

    return _dep


__all__ = [
    "RateLimiter",
    "RATE_LIMITER",
    "_enforce_rate_limit",
    "enforce_llm_guard",
    "make_llm_guard_dependency",
]
