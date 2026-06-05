"""
Bearer-token auth middleware
============================

Two modes, selected automatically at startup:

1. **Users file mode** (``CITETHREADS_USERS_JSON`` points at a JSON
   file with the per-user table). Every protected request must carry
   ``Authorization: Bearer <one-of-the-tokens>`` matching a row in
   the file. The dependency resolves to a :class:`UserContext` that
   the routers can read for rate-limiting and budget enforcement.

2. **Single-secret mode** (the dev default). When no users file is
   configured, the bearer token check falls back to the legacy
   ``settings.auth_token`` (or ``CITETHREADS_AUTH_TOKEN``) and the
   resolved identity is :data:`ANONYMOUS_ADMIN`.

Public endpoints (no token required):

* ``/``                — health/info
* ``/health``          — health probe
* ``/docs``            — Swagger UI
* ``/redoc``           — ReDoc
* ``/openapi.json``    — OpenAPI schema

Everything under ``/api/`` is protected. Tests that need to exercise
protected endpoints must either set ``settings.auth_token`` (legacy
mode) or set up a users.json fixture and ``initialise_user_store()``.

Why bearer only: a single shared secret in a header is enough to stop
anonymous scraping and accidental public exposure. Per-user tokens +
the rate limiter + the cost guard are the follow-up hardening that
this module now wires up.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security.utils import get_authorization_scheme_param
from starlette.requests import Request

from .config import settings
from .users import (
    ANONYMOUS_ADMIN,
    USER_STORE,
    UserContext,
    _tokens_match,
    initialise_user_store,
    is_users_mode,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy single-secret helpers (still used by ``_resolve_token`` below)
# ---------------------------------------------------------------------------


def _resolve_token() -> str:
    """Resolve the configured single-secret bearer token.

    Priority: ``settings.auth_token`` → ``CITETHREADS_AUTH_TOKEN``
    environment variable → empty string. The empty fallback means
    "auth disabled" — any call without a token is allowed. This is
    intentional so the default dev setup keeps working; production
    deployments must set the env var.
    """
    return (
        getattr(settings, "auth_token", "")
        or os.environ.get("CITETHREADS_AUTH_TOKEN", "")
        or ""
    )


def is_auth_enabled() -> bool:
    """Return True when *either* a single-secret token is configured
    *or* the users.json store is loaded."""
    if is_users_mode():
        return True
    return bool(_resolve_token())


# Paths that never require a token. Kept as a tuple (not a list) for
# cheap prefix scans.
PUBLIC_PATH_PREFIXES: tuple = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/",
)


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATH_PREFIXES:
        return True
    return False


def _extract_bearer_token(request: Request) -> Optional[str]:
    """Return the bearer token from the Authorization header, or None."""
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth:
        return None
    scheme, param = get_authorization_scheme_param(auth)
    if scheme.lower() != "bearer" or not param:
        return None
    return param


# ---------------------------------------------------------------------------
# Per-user dependency
# ---------------------------------------------------------------------------


# Module-level flag that ``require_user`` consults to decide whether
# to enforce auth. Initialised lazily on first request so tests that
# set ``settings.users_json_path`` after import still work.
_init_done = False


def _ensure_init() -> None:
    """Deprecated: kept as a private alias for ``init_if_needed``.
    New code should call :func:`init_if_needed` directly."""
    init_if_needed()


def reset_auth_state() -> None:
    """Force the next request to re-initialise the user store. Tests
    that mutate ``settings.users_json_path`` between cases call this
    so the change takes effect without re-importing the module.

    After reset, the next request will re-load users from the
    configured JSON file. If a test wants to swap users *without*
    touching the file, it should populate ``USER_STORE`` directly
    and rely on ``_ensure_init`` skipping re-load while the store
    is non-empty.
    """
    global _init_done
    USER_STORE.clear()
    _init_done = False
    # Don't re-initialise eagerly — let the next request do it so
    # tests that load a list directly between reset and request
    # see their users.


def init_if_needed() -> None:
    """Lazy initialiser used by the FastAPI dependency. Loads the
    user store from disk only if it's currently empty. Tests that
    inject a user list directly into ``USER_STORE`` therefore keep
    that list (because it's non-empty) and skip the file read."""
    global _init_done
    if _init_done:
        return
    if not USER_STORE.is_loaded:
        initialise_user_store()
    _init_done = True


async def require_user(request: Request) -> UserContext:
    """FastAPI dependency that resolves the request to a
    :class:`UserContext`.

    - In **users-file mode** the bearer token must match a configured
      user; mismatches return 401.
    - In **single-secret mode** (dev default) any request that
      supplies the configured token is mapped to
      :data:`ANONYMOUS_ADMIN` with full permissions, preserving the
      pre-P2-1 behaviour for the existing test suite and dev setups.
    - When **no auth is configured at all** (no users file and no
      single secret), the request is allowed through anonymously so
      the dev experience keeps working without a token.
    """
    init_if_needed()
    path = request.url.path
    if _is_public_path(path):
        return ANONYMOUS_ADMIN

    provided = _extract_bearer_token(request)

    # Users file mode: token must match a configured user.
    if is_users_mode():
        if not provided:
            logger.warning("auth: rejected request to %s (no token)", path)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = USER_STORE.find_by_token(provided)
        if user is None:
            logger.warning("auth: rejected request to %s (unknown token)", path)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    # Single-secret mode: token must match the configured secret.
    expected = _resolve_token()
    if not expected:
        # No auth at all — let the request through anonymously.
        return ANONYMOUS_ADMIN
    if not provided or not _tokens_match(expected, provided):
        logger.warning("auth: rejected request to %s (bad token)", path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ANONYMOUS_ADMIN


# ---------------------------------------------------------------------------
# Backward-compat dependency
# ---------------------------------------------------------------------------


async def require_bearer_token(request: Request) -> None:
    """Legacy dependency used by routers that don't need the
    resolved :class:`UserContext`. Calls :func:`require_user` and
    discards the result. Returns 401 on failure exactly as before.
    """
    await require_user(request)


def get_authorization_status() -> dict:
    """Snapshot of the current auth configuration. Safe to expose via
    ``/api/ai/status``-style endpoint: never reveals any token."""
    return {
        "enabled": is_auth_enabled(),
        "users_mode": is_users_mode(),
        "users_loaded": USER_STORE.size,
        "public_paths": list(PUBLIC_PATH_PREFIXES),
    }


# ---------------------------------------------------------------------------
# Re-exports for compact route signatures
# ---------------------------------------------------------------------------


#: ``Depends(require_user)`` alias so route signatures stay short.
UserAuthDep = Depends(require_user)

#: ``Depends(require_bearer_token)`` — kept for back-compat with
#: routers that don't need the resolved user.
BearerAuthDep = Depends(require_bearer_token)


__all__ = [
    "require_user",
    "require_bearer_token",
    "is_auth_enabled",
    "get_authorization_status",
    "reset_auth_state",
    "BearerAuthDep",
    "UserAuthDep",
    "UserContext",
    "ANONYMOUS_ADMIN",
]
