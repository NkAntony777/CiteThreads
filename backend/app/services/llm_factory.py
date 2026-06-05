"""
LLM Client Factory - Shared AsyncOpenAI client creation and configuration.
Eliminates duplicated LLM init logic across ai_classifier, review_generator, writing_assistant.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any, Awaitable, Callable, Optional

from openai import AsyncOpenAI

from ..config import settings

logger = logging.getLogger(__name__)


def create_llm_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 30.0,
) -> Optional[AsyncOpenAI]:
    """
    Create an AsyncOpenAI client from settings or explicit parameters.

    Returns None if no API key is available.
    """
    key = api_key or settings.siliconflow_api_key
    if not key:
        return None

    url = base_url or settings.ai_base_url
    return AsyncOpenAI(api_key=key, base_url=url, timeout=timeout)


def configure_llm_client(
    service: object,
    api_key: str,
    model: str,
    base_url: str,
    timeout: float = 30.0,
) -> None:
    """
    Configure an LLM client on a service object.
    Sets service.llm_client and service.model (or service.llm_model).
    """
    service.llm_client = AsyncOpenAI(
        api_key=api_key, base_url=base_url, timeout=timeout
    )
    if hasattr(service, 'llm_model'):
        service.llm_model = model
    elif hasattr(service, 'model'):
        service.model = model
    logger.info(f"LLM configured for {service.__class__.__name__}: model={model}")


# ---------------------------------------------------------------------------
# Per-request LLM usage tracking (P2-1)
# ---------------------------------------------------------------------------
#
# Routers (draft + agent) attach a ``user_id`` and a ``phase`` label to the
# current asyncio context before invoking the LLM. The wrapper below reads
# that context, records the token usage to the cost guard after each
# ``chat.completions.create`` call, and returns the original response
# untouched. When the context vars are unset (e.g. a background task with
# no caller) usage is logged with ``user_id="anonymous"`` so it still
# shows up in admin /usage, just unattributed.
#
# This indirection means the LLM call sites in
# ``ai_classifier``/``review_generator``/``writing_assistant`` and the
# agent runtime don't have to know about the cost guard — they just call
# ``client.chat.completions.create`` and the wrapper records usage
# automatically.


_user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_user_id", default="anonymous"
)
_phase_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_phase", default="unknown"
)


def set_current_user(user_id: str) -> contextvars.Token:
    """Bind ``user_id`` to the current async context. Returns a token
    suitable for :func:`contextvars.ContextVar.reset`."""
    return _user_id_var.set(user_id or "anonymous")


def set_current_phase(phase: str) -> contextvars.Token:
    """Bind a phase label (e.g. ``"draft.research"``) to the current
    async context."""
    return _phase_var.set(phase or "unknown")


def reset_current_user(token: contextvars.Token) -> None:
    _user_id_var.reset(token)


def reset_current_phase(token: contextvars.Token) -> None:
    _phase_var.reset(token)


def get_current_user() -> str:
    return _user_id_var.get()


def get_current_phase() -> str:
    return _phase_var.get()


def _extract_usage(response: Any) -> tuple[int, int]:
    """Pull ``prompt_tokens`` and ``completion_tokens`` off an OpenAI
    chat-completion response. Returns (0, 0) when the field is absent
    (older models, mocks, error responses, etc.)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    return int(prompt), int(completion)


class _UsageRecordingCompletions:
    """Wraps an ``AsyncOpenAI`` chat-completions handle and records
    token usage after every successful call.

    The wrapped object exposes the same ``create`` signature the
    real client uses, so call sites don't need to know they're
    being instrumented.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        response = await self._inner.create(*args, **kwargs)
        try:
            from . import cost_guard  # local import avoids a cycle
            prompt, completion = _extract_usage(response)
            cost_guard.record_llm_usage(
                user_id=_user_id_var.get(),
                phase=_phase_var.get(),
                prompt_tokens=prompt,
                completion_tokens=completion,
            )
        except Exception as exc:  # noqa: BLE001 — never fail an LLM call because of bookkeeping
            logger.debug("usage recording skipped: %s", exc)
        return response


class _UsageRecordingChat:
    def __init__(self, inner: Any) -> None:
        self.completions = _UsageRecordingCompletions(inner.completions)


class _UsageRecordingClient:
    """Drop-in ``AsyncOpenAI`` wrapper that records per-call token
    usage. Use this when constructing an LLM client for a code path
    that should be attributed to a user."""

    def __init__(self, inner: AsyncOpenAI) -> None:
        self._inner = inner
        self.chat = _UsageRecordingChat(inner.chat)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def wrap_with_usage_tracking(client: AsyncOpenAI) -> AsyncOpenAI:
    """Return ``client`` itself (already instrumented) or a wrapper
    that records usage. The wrapper is a separate object so we can
    detect the "already wrapped" case and avoid double-wrapping."""
    if isinstance(client, _UsageRecordingClient):
        return client
    return _UsageRecordingClient(client)


__all__ = [
    "create_llm_client",
    "configure_llm_client",
    "set_current_user",
    "set_current_phase",
    "reset_current_user",
    "reset_current_phase",
    "get_current_user",
    "get_current_phase",
    "wrap_with_usage_tracking",
]
