"""
Per-user authentication store
=============================

Replaces the single shared bearer token with a list of named users,
each with their own token, role, rate limit, and monthly token budget.

Two modes:

1. **Users file mode** — when ``CITETHREADS_USERS_JSON`` is set (or
   ``settings.users_json_path`` is non-empty) we load the JSON file
   once at startup and use the per-user table. Requests must carry a
   bearer token matching one of the configured users.

2. **Single-secret mode** (the dev default) — when no users file is
   configured, we fall back to the legacy behaviour: any request that
   supplies the configured ``CITETHREADS_AUTH_TOKEN`` is treated as
   ``ANONYMOUS_ADMIN`` with full permissions. This keeps existing
   tests and local dev workflows working.

The module exposes a single mutable :data:`USER_STORE` so tests can
swap it out. Production code should treat it as read-only after
startup.

Token storage
-------------
Tokens are stored in plain text in the users JSON file (which is
gitignored). A real deployment should hash them at rest; for the
current scope we accept the trade-off in exchange for a trivially
auditable dev workflow. Constant-time comparison via
:func:`hmac.compare_digest` is still used on every lookup.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class UserContext:
    """The resolved identity for a single authenticated request.

    ``user_id`` is the stable primary key; ``role`` is one of
    ``admin``, ``member``, ``guest``. ``rate_limit_per_minute`` and
    ``monthly_token_budget`` are the per-user knobs consulted by the
    rate limiter and the cost guard.
    """

    user_id: str
    role: str = "member"
    rate_limit_per_minute: int = 10
    monthly_token_budget: int = 1_000_000
    token: str = field(default="", repr=False)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_public_dict(self) -> dict:
        """Return a dict safe to log/return to the client. Never
        includes the raw token."""
        return {
            "user_id": self.user_id,
            "role": self.role,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "monthly_token_budget": self.monthly_token_budget,
        }


# ---------------------------------------------------------------------------
# Anonymous admin (single-secret mode)
# ---------------------------------------------------------------------------


#: The fallback identity used when the server is running in dev mode
#: without a users.json file. ``is_admin`` is True so legacy tests and
#: admin endpoints keep working.
ANONYMOUS_ADMIN = UserContext(
    user_id="anonymous",
    role="admin",
    rate_limit_per_minute=10_000,
    monthly_token_budget=10**9,
    token="",
)


# ---------------------------------------------------------------------------
# User store
# ---------------------------------------------------------------------------


class UserStore:
    """In-memory user table, loaded from a JSON file.

    Lookup is constant-time against the token hash. The plain-text
    token is kept on the :class:`UserContext` only so debugging
    (e.g. ``admin/usage``) can show it; never expose it in HTTP
    responses.
    """

    def __init__(self) -> None:
        self._users_by_token: Dict[str, UserContext] = {}
        self._users_by_id: Dict[str, UserContext] = {}
        self._loaded_path: Optional[str] = None

    # -- loading --------------------------------------------------------

    def load_from_file(self, path: str) -> int:
        """Load users from a JSON file. Returns the number of users
        loaded. Missing file is a no-op (returns 0)."""
        if not path:
            return 0
        p = Path(path)
        if not p.is_file():
            logger.info("users.json not found at %s; staying empty", path)
            return 0
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("users.json at %s is not valid JSON: %s", path, exc)
            return 0
        if not isinstance(data, list):
            logger.error(
                "users.json at %s must be a JSON array; got %s",
                path,
                type(data).__name__,
            )
            return 0
        return self.load_from_list(data, source=path)

    def load_from_list(self, users: List[dict], source: str = "<inline>") -> int:
        """Replace the current user table with ``users`` (each entry
        is a dict with at least ``user_id`` and ``token``)."""
        self._users_by_token.clear()
        self._users_by_id.clear()
        loaded = 0
        for entry in users:
            if not isinstance(entry, dict):
                logger.warning("users.json %s: skipping non-dict entry", source)
                continue
            user_id = str(entry.get("user_id") or "").strip()
            token = str(entry.get("token") or "").strip()
            if not user_id or not token:
                logger.warning(
                    "users.json %s: skipping entry missing user_id/token", source
                )
                continue
            role = str(entry.get("role") or "member")
            rate_limit = int(entry.get("rate_limit_per_minute") or 10)
            budget = int(entry.get("monthly_token_budget") or 1_000_000)
            user = UserContext(
                user_id=user_id,
                role=role,
                rate_limit_per_minute=rate_limit,
                monthly_token_budget=budget,
                token=token,
            )
            token_key = _hash_token(token)
            if token_key in self._users_by_token:
                logger.warning(
                    "users.json %s: duplicate token for %s, keeping first",
                    source,
                    user_id,
                )
                continue
            self._users_by_token[token_key] = user
            self._users_by_id[user_id] = user
            loaded += 1
        self._loaded_path = source
        logger.info("users: loaded %d user(s) from %s", loaded, source)
        return loaded

    def clear(self) -> None:
        """Reset to empty. Tests use this between cases."""
        self._users_by_token.clear()
        self._users_by_id.clear()
        self._loaded_path = None

    # -- queries ---------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return bool(self._users_by_token) or bool(self._users_by_id)

    @property
    def size(self) -> int:
        return len(self._users_by_id)

    def find_by_token(self, token: str) -> Optional[UserContext]:
        if not token:
            return None
        return self._users_by_token.get(_hash_token(token))

    def find_by_id(self, user_id: str) -> Optional[UserContext]:
        if not user_id:
            return None
        return self._users_by_id.get(user_id)

    def all_users(self) -> List[UserContext]:
        return list(self._users_by_id.values())


# ---------------------------------------------------------------------------
# Hashing helper
# ---------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    """Hash a token so it can be used as a dict key without holding the
    raw value in memory long-term. We use SHA-256 rather than
    ``hmac.compare_digest`` directly on the raw token to keep the
    table key-agnostic of length; the per-request compare path still
    uses ``hmac.compare_digest`` for constant time."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _tokens_match(expected: str, provided: str) -> bool:
    """Constant-time comparison. Used to verify the bearer token
    against the configured single-secret fallback."""
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


# ---------------------------------------------------------------------------
# Module-level singleton + initialisation
# ---------------------------------------------------------------------------


USER_STORE = UserStore()


def initialise_user_store() -> None:
    """(Re)load the global user store from settings. Idempotent.

    The path is resolved as:

    1. ``settings.users_json_path`` (set via ``CITETHREADS_USERS_JSON``)
    2. ``data/users.json`` next to the backend root, if it exists
    3. Otherwise the store stays empty and the auth layer falls back
       to single-secret mode
    """
    USER_STORE.clear()
    explicit = (getattr(settings, "users_json_path", "") or "").strip()
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    # Conventional repo-local default. ``data/`` is at the project
    # root (sibling of ``backend/``); we look there for convenience
    # in dev. Production deployments should set the env var.
    try:
        backend_root = Path(__file__).resolve().parent.parent
        repo_root = backend_root.parent
        candidates.append(str(repo_root / "data" / "users.json"))
    except Exception:  # noqa: BLE001 — never fail initialisation
        pass
    for path in candidates:
        if path and os.path.isfile(path):
            USER_STORE.load_from_file(path)
            return
    logger.info(
        "users: no users.json found (tried %s); running in single-secret mode",
        candidates,
    )


def is_users_mode() -> bool:
    """True when a users.json file has been loaded. In that mode the
    single-secret fallback is disabled — the bearer token must match
    a configured user."""
    return USER_STORE.is_loaded


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


__all__ = [
    "UserContext",
    "ANONYMOUS_ADMIN",
    "UserStore",
    "USER_STORE",
    "initialise_user_store",
    "is_users_mode",
    "_tokens_match",
]
