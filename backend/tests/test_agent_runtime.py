"""
Unit tests for the agent runtime tool-calling loop.

These tests do not require a real LLM API key. They inject a mock
``AsyncOpenAI`` client that simulates two model turns:
1. First turn: model returns a tool_call to search_papers
2. Second turn: model returns a final assistant message

The test asserts the agent:
- executes the tool
- feeds the result back
- stops at the final answer
- surfaces a paper_suggestions list from the tool result
- records the tool invocation in ``tool_calls``
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

# Ensure backend/ is importable when pytest is run from repo root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent_runtime.runtime import AgentRuntime, AgentTurnResult  # noqa: E402


def _mk_tool_call(name: str, arguments: Any, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _mk_assistant_message(content: str, tool_calls: Optional[List[SimpleNamespace]] = None) -> SimpleNamespace:
    return SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls or [])


class MockChoice:
    def __init__(self, message: SimpleNamespace) -> None:
        self.message = message


class MockResponse:
    def __init__(self, choices: List[MockChoice]) -> None:
        self.choices = choices


class MockChatCompletions:
    """Mock OpenAI chat.completions that supports both non-streaming
    (return a single ``MockResponse``) and streaming (return an async
    iterator of chunks). The shape of each chunk mirrors what the
    real OpenAI SDK yields with ``stream=True``: each chunk has
    ``choices[0].delta`` with optional ``content`` and/or
    ``tool_calls``."""

    def __init__(self, scripted_responses: List[MockResponse]) -> None:
        self._scripted = list(scripted_responses)
        self.calls: List[dict] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._scripted:
            return MockResponse(
                [MockChoice(_mk_assistant_message("(no more scripted responses)"))]
            )
        next_resp = self._scripted.pop(0)
        if kwargs.get("stream"):
            return _ResponseToStream(next_resp)
        return next_resp


class _ChunkDelta:
    """Mimics OpenAI streaming chunk delta."""

    def __init__(
        self,
        content: Optional[str] = None,
        tool_calls: Optional[List[SimpleNamespace]] = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _StreamChunk:
    def __init__(
        self,
        content: Optional[str] = None,
        tool_calls: Optional[List[SimpleNamespace]] = None,
        finish_reason: Optional[str] = None,
    ) -> None:
        self.choices = [
            SimpleNamespace(delta=_ChunkDelta(content, tool_calls), finish_reason=finish_reason)
        ]


class _ResponseToStream:
    """Convert a non-streaming ``MockResponse`` (one full assistant
    message) into an async iterator of streaming chunks. Splits text
    content into character-level deltas so we can verify the runtime
    emits per-token ``text_delta`` events."""

    def __init__(self, response: MockResponse) -> None:
        self._chunks: List[_StreamChunk] = []
        for choice in response.choices:
            msg = choice.message
            content = msg.content or ""
            tool_calls = msg.tool_calls or []
            # Emit text as character chunks; then any tool calls; then
            # a final chunk with finish_reason.
            for ch in content:
                self._chunks.append(_StreamChunk(content=ch))
            if tool_calls:
                for idx, tc in enumerate(tool_calls):
                    # One chunk per tool call carrying the id+name+args
                    # (matches the simple non-incremental tool format
                    # used by the test mocks).
                    fn_args = tc.function.arguments
                    if not isinstance(fn_args, str):
                        fn_args = json.dumps(fn_args)
                    self._chunks.append(
                        _StreamChunk(
                            tool_calls=[
                                SimpleNamespace(
                                    index=idx,
                                    id=tc.id,
                                    type="function",
                                    function=SimpleNamespace(
                                        name=tc.function.name,
                                        arguments=fn_args,
                                    ),
                                )
                            ]
                        )
                    )
            self._chunks.append(_StreamChunk(finish_reason="stop"))

    def __aiter__(self) -> "_ResponseToStream":
        return self

    async def __anext__(self) -> _StreamChunk:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class MockChatNamespace:
    def __init__(self, completions: MockChatCompletions) -> None:
        self.completions = completions


class MockAsyncOpenAI:
    def __init__(self, scripted_responses: List[MockResponse]) -> None:
        self.chat = MockChatNamespace(MockChatCompletions(scripted_responses))


@pytest.mark.asyncio
async def test_agent_runs_tool_then_final_answer() -> None:
    """End-to-end: tool call -> tool result -> final answer."""
    papers_payload = {
        "papers": [
            {
                "id": "arXiv:2106.09685",
                "doi": None,
                "arxiv_id": "2106.09685",
                "title": "A Survey of Graph Neural Networks",
                "authors": ["Smith, J."],
                "year": 2023,
                "venue": "arXiv",
                "abstract": "We survey GNNs.",
                "citation_count": 12,
                "reference_count": 0,
                "fields": ["cs.LG"],
                "url": "https://arxiv.org/abs/2106.09685",
            }
        ],
        "total": 1,
        "sources_searched": ["arxiv"],
        "errors": {},
    }
    tool_args = json.dumps({"query": "graph neural network survey", "limit": 3})

    scripted = [
        # Turn 1: model wants to call search_papers
        MockResponse(
            [MockChoice(_mk_assistant_message("", tool_calls=[_mk_tool_call("search_papers", tool_args)]))]
        ),
        # Turn 2: model gives the final answer
        MockResponse(
            [MockChoice(_mk_assistant_message("已为您找到 1 篇相关论文,详见下方的建议。"))]
        ),
    ]
    mock_client = MockAsyncOpenAI(scripted)

    runtime = AgentRuntime(client=mock_client, model="mock-model")
    # The handler in tools.py calls paper_search_service which needs network.
    # Monkey-patch the underlying tool to return canned data without network.
    from app.agent_runtime import tools as tools_mod

    original_search = tools_mod.tool_registry.get("search_papers").handler

    async def fake_search(query, sources=None, limit=5):
        return {
            "papers": papers_payload["papers"][:limit],
            "total": len(papers_payload["papers"]),
            "sources_searched": ["arxiv"],
            "errors": {},
        }

    tools_mod.tool_registry.get("search_papers").handler = fake_search
    try:
        result = await runtime.run(message="找 3 篇 GNN 综述论文")
    finally:
        tools_mod.tool_registry.get("search_papers").handler = original_search

    # The LLM was called twice
    assert len(mock_client.chat.completions.calls) == 2
    # First call: should have included the tool schemas
    first_call = mock_client.chat.completions.calls[0]
    assert first_call["tools"] is not None
    assert any(t["function"]["name"] == "search_papers" for t in first_call["tools"])
    # Second call: should now contain a tool message in history
    second_messages = mock_client.chat.completions.calls[1]["messages"]
    roles = [m["role"] for m in second_messages]
    assert "tool" in roles

    # Agent result
    assert isinstance(result, AgentTurnResult)
    assert result.iterations == 2
    assert result.truncated is False
    assert result.error is None
    assert result.content == "已为您找到 1 篇相关论文,详见下方的建议。"
    assert result.action_type == "search"  # because paper_suggestions populated
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.tool == "search_papers"
    assert tc.arguments == {"query": "graph neural network survey", "limit": 3}
    assert tc.latency_ms >= 0
    assert tc.error is None
    # Paper suggestions surfaced from the tool result
    assert result.paper_suggestions is not None
    assert len(result.paper_suggestions) >= 1
    assert result.paper_suggestions[0]["title"] == "A Survey of Graph Neural Networks"


@pytest.mark.asyncio
async def test_agent_truncates_after_max_iterations() -> None:
    """If the LLM keeps calling tools forever, the agent should stop."""
    tool_args = json.dumps({"query": "loop"})

    # Always return a tool call -> never a final answer
    scripted = []
    for i in range(20):
        scripted.append(
            MockResponse(
                [MockChoice(_mk_assistant_message("", tool_calls=[_mk_tool_call("search_papers", tool_args, f"call_{i}")]))]
            )
        )
    mock_client = MockAsyncOpenAI(scripted)
    runtime = AgentRuntime(client=mock_client, model="mock-model", max_iterations=3)

    from app.agent_runtime import tools as tools_mod
    original = tools_mod.tool_registry.get("search_papers").handler

    async def fake_search(query, sources=None, limit=5):
        return {"papers": [], "total": 0, "sources_searched": ["arxiv"], "errors": {}}

    tools_mod.tool_registry.get("search_papers").handler = fake_search
    try:
        result = await runtime.run(message="loop forever")
    finally:
        tools_mod.tool_registry.get("search_papers").handler = original

    assert result.truncated is True
    assert result.iterations == 3
    assert len(result.tool_calls) == 3
    assert "上限" in result.content or "cap" in result.content.lower()


@pytest.mark.asyncio
async def test_agent_returns_error_when_llm_missing() -> None:
    """Without an LLM client the agent should return a friendly message
    and not raise."""
    runtime = AgentRuntime(client=None, model="mock-model")
    result = await runtime.run(message="hello")
    assert result.error == "llm_not_configured"
    assert "LLM" in result.content or "not configured" in result.content.lower()


@pytest.mark.asyncio
async def test_agent_stream_yields_event_sequence() -> None:
    """run_stream should emit tool_start, tool_end, text_delta, done in order."""
    from app.agent_runtime import tools as tools_mod
    from app.agent_runtime.runtime import (
        EVT_DONE,
        EVT_PAPER_SUGGESTIONS,
        EVT_TEXT_DELTA,
        EVT_TOOL_END,
        EVT_TOOL_START,
    )

    tool_args = json.dumps({"query": "graph neural network", "limit": 2})
    scripted = [
        MockResponse(
            [MockChoice(_mk_assistant_message("", tool_calls=[_mk_tool_call("search_papers", tool_args, "call_42")]))]
        ),
        MockResponse(
            [MockChoice(_mk_assistant_message("已为您找到 1 篇论文。"))]
        ),
    ]
    mock_client = MockAsyncOpenAI(scripted)
    runtime = AgentRuntime(client=mock_client, model="mock-model")

    # Replace the network-bound handler with a fake one.
    original = tools_mod.tool_registry.get("search_papers").handler

    async def fake_search(query, sources=None, limit=5):
        return {
            "papers": [
                {
                    "id": "arXiv:2106.09685",
                    "doi": None,
                    "arxiv_id": "2106.09685",
                    "title": "GNN survey",
                    "authors": ["Doe, J."],
                    "year": 2023,
                    "venue": "arXiv",
                    "abstract": "x",
                    "citation_count": 5,
                    "reference_count": 0,
                    "fields": [],
                    "url": "https://arxiv.org/abs/2106.09685",
                }
            ],
            "total": 1,
            "sources_searched": ["arxiv"],
            "errors": {},
        }

    tools_mod.tool_registry.get("search_papers").handler = fake_search
    try:
        events: List[Any] = []
        async for ev in runtime.run_stream(message="找 GNN 论文"):
            events.append(ev)
    finally:
        tools_mod.tool_registry.get("search_papers").handler = original

    types = [ev.type for ev in events]
    # Order: tool_start -> tool_end -> (paper_suggestions if papers found) -> text_delta -> done
    assert types[0] == EVT_TOOL_START
    assert types[1] == EVT_TOOL_END
    # The fake search returns one paper, so paper_suggestions should be emitted
    assert EVT_PAPER_SUGGESTIONS in types
    assert types[-1] == EVT_DONE
    # text_delta should come after tool events but before done
    assert EVT_TEXT_DELTA in types
    text_delta_idx = types.index(EVT_TEXT_DELTA)
    assert text_delta_idx > 1
    assert text_delta_idx < len(types) - 1

    # tool_start payload should carry the tool name and parsed args
    assert events[0].payload["tool"] == "search_papers"
    assert events[0].payload["arguments"]["query"] == "graph neural network"
    assert events[0].payload["tool_call_id"] == "call_42"

    # tool_end should preserve the same tool_call_id so the protocol
    # round-trip is consistent
    assert events[1].payload["tool_call_id"] == "call_42"
    assert events[1].payload["error"] is None
    assert events[1].payload["latency_ms"] >= 0

    # text_delta events are per-token (one chunk per char in the mock);
    # accumulate them to verify the full text comes through.
    text_deltas = [e.payload["delta"] for e in events if e.type == EVT_TEXT_DELTA]
    assert len(text_deltas) > 1, "expected per-token streaming, got one chunk"
    assert "".join(text_deltas) == "已为您找到 1 篇论文。"

    # paper_suggestions should carry the paper dict from the tool result
    paper_event = next(e for e in events if e.type == EVT_PAPER_SUGGESTIONS)
    assert len(paper_event.payload["papers"]) == 1
    assert paper_event.payload["papers"][0]["title"] == "GNN survey"

    # done carries full state for replay
    done = events[-1].payload
    assert done["iterations"] == 2
    assert done["truncated"] is False
    assert done["content"] == "已为您找到 1 篇论文。"
    assert done["action_type"] == "search"
    assert isinstance(done["paper_suggestions"], list)
    assert len(done["paper_suggestions"]) == 1


@pytest.mark.asyncio
async def test_agent_stream_emits_done_with_error_when_no_llm() -> None:
    """When the LLM is missing, run_stream should still emit a final
    done event so the client doesn't hang waiting."""
    from app.agent_runtime.runtime import EVT_DONE, EVT_ERROR

    runtime = AgentRuntime(client=None, model="mock-model")
    events: List[Any] = []
    async for ev in runtime.run_stream(message="hi"):
        events.append(ev)
    types = [ev.type for ev in events]
    assert EVT_ERROR in types
    assert types[-1] == EVT_DONE
    assert "LLM" in events[-1].payload["content"] or "not configured" in events[-1].payload["content"].lower()


@pytest.mark.asyncio
async def test_agent_stream_truncation_event() -> None:
    """When the iteration cap is hit, the final done event should
    report ``truncated=True``."""
    from app.agent_runtime import tools as tools_mod
    from app.agent_runtime.runtime import EVT_DONE

    tool_args = json.dumps({"query": "loop"})
    scripted = []
    for i in range(10):
        scripted.append(
            MockResponse(
                [MockChoice(_mk_assistant_message("", tool_calls=[_mk_tool_call("search_papers", tool_args, f"call_{i}")]))]
            )
        )
    mock_client = MockAsyncOpenAI(scripted)
    runtime = AgentRuntime(client=mock_client, model="mock-model", max_iterations=2)

    original = tools_mod.tool_registry.get("search_papers").handler

    async def fake(query, sources=None, limit=5):
        return {"papers": [], "total": 0, "sources_searched": ["arxiv"], "errors": {}}

    tools_mod.tool_registry.get("search_papers").handler = fake
    try:
        events: List[Any] = []
        async for ev in runtime.run_stream(message="loop"):
            events.append(ev)
    finally:
        tools_mod.tool_registry.get("search_papers").handler = original

    done = events[-1]
    assert done.type == EVT_DONE
    assert done.payload["truncated"] is True
    assert done.payload["iterations"] == 2
    # tool_start/tool_end pairs for each iteration
    from app.agent_runtime.runtime import EVT_TOOL_END, EVT_TOOL_START

    starts = [e for e in events if e.type == EVT_TOOL_START]
    ends = [e for e in events if e.type == EVT_TOOL_END]
    assert len(starts) == 2
    assert len(ends) == 2
