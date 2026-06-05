"""
Agent API Router
================

Single endpoint that exposes the tool-calling agent runtime to the
frontend. The request/response shape mirrors the existing
``/api/writing/.../writing/chat`` so the existing UI can adopt it
without breaking changes, while adding new fields:

- ``tool_calls``  : list of {tool, arguments, result_preview, latency_ms, error}
- ``iterations``  : how many LLM round-trips this turn used
- ``truncated``   : true if the iteration cap was hit before a final answer

Streaming variant: ``POST /api/agent/chat/stream`` returns
``text/event-stream`` (SSE) so the frontend can render text deltas
and tool activity as the agent works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent_runtime import agent_runtime
from ..agent_runtime.runtime import (
    EVT_DONE,
    EVT_ERROR,
    EVT_PAPER_SUGGESTIONS,
    EVT_TEXT_DELTA,
    EVT_TOOL_END,
    EVT_TOOL_START,
    Event,
)
from ..auth import BearerAuthDep
from ..rate_limit import make_llm_guard_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# How often to send a keepalive comment frame while the agent works.
# Proxies (nginx, Cloudflare, corporate gateways) often kill idle
# connections after 30-60s; 15s is a safe middle ground.
SSE_KEEPALIVE_INTERVAL_S = float(15.0)


class AgentChatRequest(BaseModel):
    """Mirrors ChatRequest in writing.py to ease frontend adoption."""

    message: str = Field(..., min_length=1, description="User's message")
    project_id: Optional[str] = Field(None, description="Writing project id (used for memory + tool scope)")
    history: Optional[List[Dict[str, Any]]] = Field(
        None, description="Prior turns as [{role, content}, ...]"
    )
    extra_context: Optional[str] = Field(
        None, description="Optional additional system context (e.g. paper topic)"
    )


class ToolCallOut(BaseModel):
    tool: str
    arguments: Dict[str, Any]
    result_preview: str
    latency_ms: int
    error: Optional[str] = None
    tool_call_id: Optional[str] = None


class AgentChatResponse(BaseModel):
    success: bool
    message: Dict[str, Any]
    tool_calls: List[ToolCallOut] = Field(default_factory=list)
    iterations: int = 0
    truncated: bool = False
    error: Optional[str] = None


@router.post(
    "/chat",
    response_model=AgentChatResponse,
    dependencies=[BearerAuthDep,
                  Depends(make_llm_guard_dependency("agent.chat"))],
)
async def agent_chat(request: AgentChatRequest) -> AgentChatResponse:
    """Run one agent turn with autonomous tool use."""
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    try:
        result = await agent_runtime.run(
            message=request.message,
            project_id=request.project_id,
            history=request.history,
            extra_context=request.extra_context,
        )
    except Exception as exc:
        logger.exception("agent_chat crashed")
        raise HTTPException(status_code=500, detail=str(exc))

    chat_msg = result.to_chat_message()
    return AgentChatResponse(
        success=True,
        message={
            "role": chat_msg.role,
            "content": chat_msg.content,
            "timestamp": chat_msg.timestamp.isoformat(),
            "paper_suggestions": [p for p in (chat_msg.paper_suggestions or [])] or None,
            "action_type": chat_msg.action_type,
        },
        tool_calls=[
            ToolCallOut(
                tool=tc.tool,
                arguments=tc.arguments,
                result_preview=tc.result_preview,
                latency_ms=tc.latency_ms,
                error=tc.error,
                tool_call_id=tc.tool_call_id,
            )
            for tc in result.tool_calls
        ],
        iterations=result.iterations,
        truncated=result.truncated,
        error=result.error,
    )


# ---------------------------------------------------------------------------
# Streaming (Server-Sent Events)
# ---------------------------------------------------------------------------

def _sse_format(payload: Dict[str, Any]) -> str:
    """Format a dict as one SSE message: ``data: <json>\\n\\n``."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _event_stream(
    message: str,
    project_id: Optional[str],
    history: Optional[List[Dict[str, Any]]],
    extra_context: Optional[str],
    keepalive_interval: Optional[float] = None,
    prompt_kind: str = "general",
) -> AsyncIterator[str]:
    """Bridge ``AgentRuntime.run_stream`` to SSE.

    Uses a queue to decouple the agent from the SSE writer so we can
    interleave keepalive pings without pausing the agent. Yields
    ``data: {json}\\n\\n`` frames; the final frame has ``type=done``.

    ``keepalive_interval`` (seconds) overrides the module-level
    default; pass a small value in tests to verify ping behavior.
    ``prompt_kind`` selects the system prompt flavor inside the
    runtime (``"general"`` for the writing assistant,
    ``"search"`` for the SmartSearch seed-paper finder).
    """
    interval = keepalive_interval if keepalive_interval is not None else SSE_KEEPALIVE_INTERVAL_S
    # Opening comment line so proxies that buffer don't sit idle.
    yield ": stream-open\n\n"

    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    producer_done = asyncio.Event()
    producer_error: Dict[str, Any] = {}

    async def _producer() -> None:
        """Push agent events into the queue, then signal completion."""
        try:
            async for ev in agent_runtime.run_stream(
                message=message,
                project_id=project_id,
                history=history,
                extra_context=extra_context,
                prompt_kind=prompt_kind,
            ):
                await queue.put(("event", ev))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            producer_error["error"] = exc
            logger.exception("agent_chat/stream producer crashed")
        finally:
            producer_done.set()
            # Sentinel so the consumer wakes up even if the queue is empty.
            await queue.put(("__end__", None))

    producer_task = asyncio.create_task(_producer())
    last_ping = time.monotonic()

    try:
        while True:
            timeout = max(0.1, interval - (time.monotonic() - last_ping))
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                # No agent activity for the keepalive interval -> emit
                # a comment frame to keep the connection warm. Proxies
                # silently drop these.
                yield ": ping\n\n"
                last_ping = time.monotonic()
                continue

            if kind == "__end__":
                break

            assert kind == "event" and isinstance(payload, Event)
            yield _sse_format({"type": payload.type, **payload.payload})
            last_ping = time.monotonic()
    except asyncio.CancelledError:
        # Client disconnected mid-stream. Cancel the producer so we
        # don't leak tasks or keep calling the LLM.
        logger.info("agent_chat/stream client disconnected")
        producer_task.cancel()
        raise
    except Exception as exc:
        logger.exception("agent_chat/stream crashed")
        yield _sse_format({"type": EVT_ERROR, "message": str(exc), "code": "stream_error"})
    finally:
        if not producer_task.done():
            producer_task.cancel()
            try:
                await producer_task
            except (asyncio.CancelledError, Exception):
                pass
        if producer_error:
            logger.warning("agent_chat/stream producer error: %s", producer_error["error"])


@router.post(
    "/chat/stream",
    dependencies=[BearerAuthDep,
                  Depends(make_llm_guard_dependency("agent.chat_stream"))],
)
async def agent_chat_stream(request: AgentChatRequest) -> StreamingResponse:
    """Run one agent turn, streaming progress as Server-Sent Events.

    Each event frame is JSON with a ``type`` discriminator:
    ``text_delta``, ``tool_start``, ``tool_end``,
    ``paper_suggestions``, ``error``, ``done``. The ``done`` frame is
    always the last one and carries the full final state.
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    generator = _event_stream(
        message=request.message,
        project_id=request.project_id,
        history=request.history,
        extra_context=request.extra_context,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # tell nginx not to buffer
            "Connection": "keep-alive",
        },
    )


@router.get("/tools")
async def list_tools() -> Dict[str, Any]:
    """Return the list of tools the agent currently has access to.
    Useful for debugging and for the frontend to render an activity panel."""
    return {
        "success": True,
        "tools": [
            {
                "name": schema["function"]["name"],
                "description": schema["function"]["description"],
                "parameters": schema["function"]["parameters"],
            }
            for schema in agent_runtime.registry.schemas()
        ],
    }


@router.post("/configure")
async def configure_agent(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Hot-swap the LLM client used by the agent runtime. Mirrors
    ``/api/ai/configure/llm`` so the existing AI settings UI can target
    the agent without changes."""
    api_key = payload.get("api_key")
    model = payload.get("model")
    base_url = payload.get("base_url")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")
    try:
        await agent_runtime.configure(api_key=api_key, model=model, base_url=base_url)
    except ValueError as exc:
        # Raised by the SSRF guard when the base_url points at a
        # disallowed host (loopback, private IP, etc.). Surface as
        # 400 so the frontend can show a clear error.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "model": agent_runtime.model,
        "tools": agent_runtime.registry.names(),
    }


@router.post("/memory/clear")
async def clear_memory(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Clear the in-memory conversation history for a session."""
    project_id = payload.get("project_id") or "default"
    agent_runtime.memory.clear(project_id)
    return {"success": True, "session_id": project_id}


# ---------------------------------------------------------------------------
# SmartSearch — search-as-chat endpoint for the SearchBar
# ---------------------------------------------------------------------------


class SearchAgentRequest(BaseModel):
    """Request body for ``POST /api/agent/search/stream``.

    Intentionally has no ``project_id``: at the moment a user types
    into the SearchBar they haven't built a project yet. ``history``
    and ``extra_context`` mirror ``AgentChatRequest`` so the frontend
    can use the same plumbing, but the runtime will pick a different
    system prompt (the seed-paper finder) and tool scope.
    """

    message: str = Field(..., min_length=1, description="User's natural-language query")
    history: Optional[List[Dict[str, Any]]] = Field(
        None, description="Prior turns as [{role, content}, ...]"
    )
    extra_context: Optional[str] = Field(
        None,
        description=(
            "Optional additional system context. Often used to mark this "
            "turn as 'seed search, not writing'."
        ),
    )


@router.post(
    "/search/stream",
    dependencies=[BearerAuthDep,
                  Depends(make_llm_guard_dependency("agent.search_stream"))],
)
async def agent_search_stream(request: SearchAgentRequest) -> StreamingResponse:
    """SmartSearch endpoint: natural-language seed-paper finder.

    Same SSE contract as ``/api/agent/chat/stream`` (text_delta,
    tool_start, tool_end, paper_suggestions, error, done) but uses
    the ``search`` system prompt and is rate-limited separately so
    SearchBar activity doesn't compete with writing turns.
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    generator = _event_stream(
        message=request.message,
        project_id=None,  # SearchBar has no project yet
        history=request.history,
        extra_context=request.extra_context,
        prompt_kind="search",
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # tell nginx not to buffer
            "Connection": "keep-alive",
        },
    )
