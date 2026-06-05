"""
Tests for the SSE keepalive behavior of the /api/agent/chat/stream
endpoint. The keepalive interval is set to a tiny value so the test
runs in <1s.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app
from app.agent_runtime import agent_runtime
from app.routers import agent as agent_router
from tests.test_agent_runtime import (
    MockAsyncOpenAI,
    MockResponse,
    MockChoice,
    _mk_assistant_message,
)


def test_sse_keeps_alive_with_pings_during_long_pauses(monkeypatch):
    """When the agent takes longer than the keepalive interval to
    produce its next event, the SSE writer should emit a `: ping`
    comment frame in between. We make the agent's first LLM call
    sleep so we can observe the ping before the response arrives."""

    async def slow_then_done():
        # First emit a tool_start, then sleep long enough to trigger
        # a keepalive, then emit done.
        from app.agent_runtime.runtime import (
            EVT_DONE,
            EVT_TOOL_END,
            EVT_TOOL_START,
            Event,
        )
        yield Event(type=EVT_TOOL_START, payload={"tool": "x", "arguments": {}})
        await asyncio.sleep(0.4)  # > keepalive interval (0.2s)
        yield Event(type=EVT_TOOL_END, payload={"tool": "x", "arguments": {}, "result_preview": "", "result_raw": "", "latency_ms": 0, "error": None, "tool_call_id": "c1"})
        await asyncio.sleep(0.4)
        yield Event(type=EVT_DONE, payload={"iterations": 1, "truncated": False, "content": "", "action_type": "answer", "paper_suggestions": [], "tool_calls": [], "error": None})

    # Monkey-patch the runtime's run_stream to a slow scripted version
    # and the keepalive interval to something testable.
    def _fake_stream(**kwargs):
        return slow_then_done()
    monkeypatch.setattr(agent_runtime, "run_stream", _fake_stream)
    monkeypatch.setattr(agent_router, "SSE_KEEPALIVE_INTERVAL_S", 0.2)

    with TestClient(app) as client:
        r = client.post(
            "/api/agent/chat/stream",
            json={"message": "hi", "project_id": "keepalive-test"},
            headers={"Accept": "text/event-stream"},
        )
        assert r.status_code == 200
        body = r.text

    # We expect at least one ping comment line in the body (the agent
    # sleeps 0.8s total, and interval is 0.2s, so >=3 pings possible).
    assert ": ping" in body, f"no keepalive ping in body:\n{body}"
    # We also expect the original events to be present.
    assert "tool_start" in body
    assert "tool_end" in body
    assert '"type": "done"' in body


def test_sse_handles_fast_response_without_pings(monkeypatch):
    """If the agent finishes quickly (< keepalive interval), no pings
    should be emitted — only the real events and the opening comment."""

    async def fast_turn():
        from app.agent_runtime.runtime import EVT_DONE, EVT_TEXT_DELTA, Event
        yield Event(type=EVT_TEXT_DELTA, payload={"delta": "ok"})
        yield Event(type=EVT_DONE, payload={"iterations": 1, "truncated": False, "content": "ok", "action_type": "answer", "paper_suggestions": [], "tool_calls": [], "error": None})

    def _fake_stream(**kwargs):
        return fast_turn()
    monkeypatch.setattr(agent_runtime, "run_stream", _fake_stream)
    monkeypatch.setattr(agent_router, "SSE_KEEPALIVE_INTERVAL_S", 1.0)

    with TestClient(app) as client:
        r = client.post(
            "/api/agent/chat/stream",
            json={"message": "hi", "project_id": "fast-test"},
        )
        body = r.text

    assert ": stream-open" in body
    # The two events from the agent should be there.
    assert "text_delta" in body
    assert "done" in body
    # No pings because the turn finished well under 1 second.
    assert ": ping" not in body
