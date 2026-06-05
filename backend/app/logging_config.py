"""
Structured JSON logging for CiteThreads.

Replaces the default ``logging.basicConfig`` text format with a JSON
formatter suitable for log aggregators (Loki, ELK, CloudWatch). Each
record carries the standard fields plus optional context — request id,
user id, phase, duration — that callers can attach via the
:class:`RequestIdFilter`, a ``logger.info(..., extra=...)`` call, or a
``contextvars.ContextVar``-backed contextual filter.

Why this shape
---------------
- ``ts`` is ISO-8601 in UTC so log lines are comparable across hosts.
- ``level``, ``logger``, ``msg`` mirror the stdlib naming so existing
  log-grep tooling keeps working.
- ``request_id`` / ``user_id`` / ``phase`` / ``duration_ms`` are
  additive: when missing they simply don't appear in the JSON object,
  so a log line emitted by a phase function and a log line emitted
  by a request middleware share the same schema.
- The formatter does not break record mutation: ``record.exc_info``
  and stack traces are still serialized as ``"exc_info"`` strings.

Usage
-----
Call :func:`configure_json_logging` once at process start (the
application entry point does this). Existing ``logging.getLogger(__name__)``
calls work without changes.

Public API
----------
- :func:`configure_json_logging`
- :class:`JsonFormatter`
- :class:`RequestIdFilter`
- :func:`set_request_id` / :func:`get_request_id` / :func:`clear_request_id`
- :func:`set_user_id` / :func:`clear_user_id`
- :func:`set_phase` / :func:`clear_phase`
- :class:`RequestIdMiddleware`
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# ContextVars carry the per-request / per-phase metadata that the
# formatter and filter splice into every log record. They default to
# empty values so background work (no request scope) still produces
# well-formed log lines.
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default=""
)
_phase_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "phase", default=""
)


def set_request_id(request_id: str) -> contextvars.Token:
    """Bind a request id to the current async context.

    Returns a token so callers can restore the previous value (handy
    inside middleware that nests scopes).
    """
    return _request_id_var.set(request_id)


def get_request_id() -> str:
    """Return the current request id, or empty string when none is set."""
    return _request_id_var.get()


def clear_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)


def set_user_id(user_id: str) -> contextvars.Token:
    return _user_id_var.set(user_id)


def clear_user_id(token: contextvars.Token) -> None:
    _user_id_var.reset(token)


def set_phase(phase: str) -> contextvars.Token:
    return _phase_var.set(phase)


def clear_phase(token: contextvars.Token) -> None:
    _phase_var.reset(token)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


# Standard LogRecord attributes that we *don't* want to echo verbatim
# into the JSON payload. They either duplicate our explicit fields or
# carry Python-internal data the log consumer doesn't need.
_STD_LOGRECORD_ATTRS = frozenset(
    (
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "asctime", "taskName",
    )
)


def _utc_iso(ts: float) -> str:
    """Return an ISO-8601 UTC timestamp with millisecond precision."""
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as a single-line JSON object.

    Always emits ``ts``, ``level``, ``logger``, ``msg``. Adds the
    context-var fields (``request_id``, ``user_id``, ``phase``) when
    they are non-empty. Adds ``duration_ms`` when present on the
    record (callers can attach it via ``extra=`` or :class:`RequestIdFilter`).
    Extra fields passed through ``logger.info(..., extra={...})`` are
    also included so phase code can attach ``project_id`` etc. without
    a separate log-record subclass.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 - stdlib name
        payload: dict[str, Any] = {
            "ts": _utc_iso(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Pull context-var values directly so any caller can log
        # without threading request scope through every function.
        request_id = getattr(record, "request_id", "") or _request_id_var.get()
        user_id = getattr(record, "user_id", "") or _user_id_var.get()
        phase = getattr(record, "phase", "") or _phase_var.get()
        if request_id:
            payload["request_id"] = request_id
        if user_id:
            payload["user_id"] = user_id
        if phase:
            payload["phase"] = phase

        duration_ms = getattr(record, "duration_ms", None)
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms

        # Pass through any ``extra={...}`` fields. This is how phase
        # code attaches ``project_id``, ``phase_name``, etc.
        for key, value in record.__dict__.items():
            if key in _STD_LOGRECORD_ATTRS or key in payload or key.startswith("_"):
                continue
            if key in ("request_id", "user_id", "phase", "duration_ms"):
                # Already handled above; preserve the first non-empty value.
                continue
            payload[key] = _coerce_for_json(value)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # ``default=str`` is a last-resort: a future code path that
        # attaches a non-JSON-serializable object (datetime, Path) is
        # still logged instead of crashing the request.
        return json.dumps(payload, ensure_ascii=False, default=str)


def _coerce_for_json(value: Any) -> Any:
    """Best-effort coercion of arbitrary values into JSON-friendly types.

    The formatter tolerates non-serializable values via the top-level
    ``default=str`` fallback. This helper exists so a value that *is*
    serializable but needs a tweak (e.g. a frozenset, a Path) doesn't
    fall back to its repr unnecessarily.
    """
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_for_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce_for_json(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_coerce_for_json(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class RequestIdFilter(logging.Filter):
    """Inject ``request_id`` from the contextvar into every record.

    Most call sites don't bother attaching request metadata via
    ``extra=``; this filter pulls the value from the contextvar
    automatically so every log line is correlatable.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 - stdlib name
        if not getattr(record, "request_id", ""):
            record.request_id = _request_id_var.get()
        if not getattr(record, "user_id", ""):
            record.user_id = _user_id_var.get()
        if not getattr(record, "phase", ""):
            record.phase = _phase_var.get()
        return True


# ---------------------------------------------------------------------------
# Configuration entry point
# ---------------------------------------------------------------------------


def configure_json_logging(
    level: int = logging.INFO,
    stream: Any = None,
) -> None:
    """Replace the root logger handler with a JSON-emitting one.

    Idempotent: re-calling clears handlers that were previously
    installed by this function so the dev workflow (``uvicorn --reload``)
    doesn't accumulate duplicate handlers.

    Args:
        level: Logging level for the root logger. Defaults to INFO.
        stream: Stream to write to. Defaults to ``sys.stderr`` which
            matches the stdlib ``basicConfig`` default.
    """
    root = logging.getLogger()
    root.setLevel(level)
    # Drop handlers we previously installed (identified by our
    # formatter class) so reloads don't pile them up.
    for handler in list(root.handlers):
        if isinstance(handler.formatter, JsonFormatter):
            root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)

    # uvicorn installs its own loggers; redirect them through ours so
    # access logs and error logs also serialize to JSON.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lgr = logging.getLogger(noisy)
        lgr.handlers.clear()
        lgr.propagate = True


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware:
    """ASGI middleware that adds ``X-Request-ID`` to every request.

    - If the client sends ``X-Request-ID`` we use it (so a front-end
      proxy / LB correlation id is preserved).
    - Otherwise we generate a UUID4.
    - The id is bound to a contextvar for the lifetime of the request
      so every log line (including the ones emitted by the route
      handler and downstream services) carries the same id.
    - The id is also echoed in the response header.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: D401 - ASGI signature
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or ())
        incoming = headers.get(b"x-request-id", b"").decode("latin-1", errors="ignore")
        request_id = incoming.strip() or uuid.uuid4().hex

        token = _request_id_var.set(request_id)
        start = time.perf_counter()
        status_code = 500  # default if the response is malformed

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                raw_headers = list(message.get("headers") or [])
                raw_headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = raw_headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logging.getLogger("ct.access").exception(
                "request failed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": 500,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            # Emit the access log AFTER the response is sent so we
            # know the final status. This goes through the JSON
            # formatter just like every other log line.
            logging.getLogger("ct.access").info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )
            _request_id_var.reset(token)


__all__ = [
    "configure_json_logging",
    "JsonFormatter",
    "RequestIdFilter",
    "RequestIdMiddleware",
    "set_request_id",
    "get_request_id",
    "clear_request_id",
    "set_user_id",
    "clear_user_id",
    "set_phase",
    "clear_phase",
]
