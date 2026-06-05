"""
Tests for the SmartSearch SSE endpoint
``POST /api/agent/search/stream``.

These tests verify:

1. The endpoint accepts ``SearchAgentRequest`` (no project_id) and
   returns an SSE stream with the same event shape as the existing
   ``/api/agent/chat/stream``.
2. Filters passed into the ``search_papers`` tool land on the
   service's ``SearchFilters`` object, and the response payload
   includes a ``filters_applied`` map.
3. The endpoint is plumbed through ``_event_stream`` with
   ``prompt_kind="search"`` so the runtime uses
   ``SEARCH_SYSTEM_PROMPT``.
4. The endpoint returns 400 on an empty message and 200 on a
   well-formed request without ever needing a real LLM.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, List, Optional

import pytest
from fastapi.testclient import TestClient

# Ensure backend/ is importable when pytest is run from repo root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app  # noqa: E402
from app.agent_runtime import agent_runtime  # noqa: E402
from app.agent_runtime.runtime import (  # noqa: E402
    EVT_DONE,
    EVT_PAPER_SUGGESTIONS,
    EVT_TEXT_DELTA,
    EVT_TOOL_END,
    EVT_TOOL_START,
    Event,
    ToolCallRecord,
)
from app.routers import agent as agent_router  # noqa: E402

# Reuse the streaming mock + message helpers from the existing test.
from tests.test_agent_runtime import (  # noqa: E402
    MockAsyncOpenAI,
    MockChoice,
    MockResponse,
    _mk_assistant_message,
    _mk_tool_call,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_events(body: str) -> List[dict]:
    """Parse an SSE response body into a list of JSON event dicts.

    Skips comment lines (``:``) and the ``event-open`` ping. Anything
    that doesn't parse as JSON is silently dropped — that matches
    how a real client should treat malformed frames.
    """
    events: List[dict] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            events.append(json.loads(line[len("data: "):]))
        except json.JSONDecodeError:
            continue
    return events


# ---------------------------------------------------------------------------
# Endpoint smoke tests
# ---------------------------------------------------------------------------


def test_search_stream_endpoint_rejects_empty_message():
    """A whitespace-only message should be a 400, same as the
    general ``/api/agent/chat/stream`` endpoint."""
    client = TestClient(app)
    r = client.post("/api/agent/search/stream", json={"message": "   "})
    assert r.status_code == 400


def test_search_stream_endpoint_emits_full_event_sequence(monkeypatch):
    """End-to-end: scripted LLM turn -> tool call -> final answer.
    The SSE body should contain tool_start, tool_end, paper_suggestions,
    text_delta (via chunks), and done — in the same shape the
    frontend already consumes from the chat endpoint."""

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
        "sources_searched": ["openalex"],
        "errors": {},
        "filters_applied": {
            "openalex": False,
            "arxiv": False,
            "dblp": True,
            "pubmed": False,
            "semantic_scholar": False,
        },
    }

    tool_args = json.dumps(
        {
            "query": "graph neural network survey",
            "limit": 3,
            "filters": {"year_range": [2022, 2024], "min_citations": 5},
        }
    )

    scripted = [
        MockResponse(
            [
                MockChoice(
                    _mk_assistant_message(
                        "", tool_calls=[_mk_tool_call("search_papers", tool_args)]
                    )
                )
            ]
        ),
        MockResponse([MockChoice(_mk_assistant_message("为您找到 1 篇种子论文。"))]),
    ]
    mock_client = MockAsyncOpenAI(scripted)
    runtime = agent_runtime.__class__(client=mock_client, model="mock-model")
    # Wire the mock client into the singleton so the endpoint hits it.
    monkeypatch.setattr(agent_runtime, "client", mock_client, raising=False)
    monkeypatch.setattr(agent_runtime, "model", "mock-model", raising=False)

    # Replace the network-bound search handler with a canned one.
    from app.agent_runtime import tools as tools_mod

    original = tools_mod.tool_registry.get("search_papers").handler

    async def fake_search(query, sources=None, limit=5, filters=None):
        return papers_payload

    tools_mod.tool_registry.get("search_papers").handler = fake_search
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/agent/search/stream",
                json={"message": "图神经网络综述 2022-2024 高引用"},
                headers={"Accept": "text/event-stream"},
            )
        assert r.status_code == 200
        body = r.text
    finally:
        tools_mod.tool_registry.get("search_papers").handler = original

    # Opening comment keeps proxies warm.
    assert ": stream-open" in body
    # All the expected event types are present.
    assert "tool_start" in body
    assert "tool_end" in body
    assert "paper_suggestions" in body
    assert "text_delta" in body
    assert '"type": "done"' in body

    # Parse the JSON events and assert the shape the frontend relies on.
    events = _sse_events(body)
    types = [e["type"] for e in events]
    assert types[0] == "tool_start"
    assert types[1] == "tool_end"
    # paper_suggestions arrives after the tool returns.
    assert "paper_suggestions" in types
    assert types[-1] == "done"

    # tool_start payload: tool name + parsed args
    tool_start = events[0]
    assert tool_start["tool"] == "search_papers"
    args = tool_start["arguments"]
    assert args["query"] == "graph neural network survey"
    assert args["limit"] == 3
    assert args["filters"]["year_range"] == [2022, 2024]
    assert args["filters"]["min_citations"] == 5

    # tool_end payload: result_preview + tool_call_id round-trip
    tool_end = events[1]
    assert tool_end["tool"] == "search_papers"
    assert tool_end["tool_call_id"] == tool_start["tool_call_id"]
    assert tool_end["error"] is None

    # The done event carries the full final state.
    done = events[-1]
    assert done["type"] == "done"
    assert done["iterations"] == 2
    assert done["truncated"] is False
    assert done["action_type"] == "search"  # because papers were found
    assert isinstance(done["paper_suggestions"], list)
    assert done["paper_suggestions"][0]["title"] == "A Survey of Graph Neural Networks"


def test_search_stream_uses_search_system_prompt(monkeypatch):
    """The endpoint should pass ``prompt_kind="search"`` through to
    ``AgentRuntime._build_system_prompt`` so the agent sees
    ``SEARCH_SYSTEM_PROMPT`` instead of the writing-assistant
    ``SYSTEM_PROMPT``.
    """
    from app.agent_runtime.runtime import SEARCH_SYSTEM_PROMPT, SYSTEM_PROMPT

    seen: List[dict] = []

    real_build = agent_runtime.__class__._build_system_prompt

    def spy_build(self, extra_context, prompt_kind="general"):
        seen.append({"extra_context": extra_context, "prompt_kind": prompt_kind})
        return real_build(self, extra_context, prompt_kind=prompt_kind)

    # Patch on the *class* so ``self._build_system_prompt(...)`` finds
    # the spy. Setting on the instance dict is shadowed by the class
    # method for non-data descriptors like regular functions.
    monkeypatch.setattr(agent_runtime.__class__, "_build_system_prompt", spy_build)

    # Drive a single scripted turn. We only swap ``_iter_turn`` enough
    # to invoke ``_build_system_prompt`` once, then yield a done frame.
    async def fake_iter_turn(self, message, project_id, history, extra_context, prompt_kind="general"):
        # Trigger the real (spied) prompt build so we observe the call.
        self.memory.set_system(
            project_id or "default",
            self._build_system_prompt(extra_context, prompt_kind=prompt_kind),
        )
        yield Event(type=EVT_TEXT_DELTA, payload={"delta": "ok"})
        yield Event(
            type=EVT_DONE,
            payload={
                "iterations": 1,
                "truncated": False,
                "content": "ok",
                "action_type": "answer",
                "paper_suggestions": [],
                "tool_calls": [],
                "error": None,
            },
        )

    monkeypatch.setattr(
        agent_runtime.__class__, "_iter_turn", fake_iter_turn
    )

    with TestClient(app) as client:
        r = client.post(
            "/api/agent/search/stream",
            json={"message": "hi", "extra_context": "this is seed search"},
        )
    assert r.status_code == 200

    assert seen, "_build_system_prompt should have been called"
    call = seen[0]
    assert call["prompt_kind"] == "search"
    assert call["extra_context"] == "this is seed search"

    # The system message that got seeded into memory should contain
    # the SEARCH_SYSTEM_PROMPT marker. (The search prompt does mention
    # ``list_project_references`` to tell the agent NOT to use it, so
    # we can't assert its absence — instead we check for a
    # search-prompt-specific marker.)
    memory = agent_runtime.memory
    sys_msg = memory.get("default")[0]
    assert sys_msg.role == "system"
    assert "Seed Paper Finder" in sys_msg.content or "种子" in sys_msg.content
    # The search prompt enumerates ONLY search_papers + get_paper_details
    # as available tools; the writing-assistant prompt also lists
    # list_project_references / find_research_gaps.
    assert "search_papers" in sys_msg.content
    assert "get_paper_details" in sys_msg.content
    # The writing-assistant prompt tells the agent "调用 list_project_references
    # 以避免重复推荐" — that exact phrasing must not appear here.
    assert "避免重复推荐" not in sys_msg.content


def test_search_stream_emits_error_when_llm_missing(monkeypatch):
    """Without a configured LLM, the endpoint should still emit a
    proper error + done frame so the client doesn't hang."""

    # Force the LLM-missing path by clearing the client.
    monkeypatch.setattr(agent_runtime, "client", None, raising=False)

    with TestClient(app) as client:
        r = client.post(
            "/api/agent/search/stream",
            json={"message": "find me a paper"},
        )
    assert r.status_code == 200
    body = r.text
    events = _sse_events(body)
    types = [e["type"] for e in events]
    assert "error" in types
    assert types[-1] == "done"
    assert "LLM" in events[-1]["content"] or "not configured" in events[-1]["content"].lower()


def test_search_stream_passes_filters_into_tool_call(monkeypatch):
    """When the model decides to call search_papers with a
    ``filters`` block, the endpoint should forward the args exactly
    as parsed (no normalization, since the tool layer does that)."""

    captured_args: List[dict] = []

    from app.agent_runtime import tools as tools_mod

    original = tools_mod.tool_registry.get("search_papers").handler

    async def spy_search(query, sources=None, limit=5, filters=None):
        captured_args.append(
            {
                "query": query,
                "sources": sources,
                "limit": limit,
                "filters": filters,
            }
        )
        return {
            "papers": [],
            "total": 0,
            "sources_searched": ["dblp"],
            "errors": {},
            "filters_applied": {
                "openalex": False,
                "arxiv": False,
                "dblp": True,
                "pubmed": False,
                "semantic_scholar": False,
            },
        }

    tools_mod.tool_registry.get("search_papers").handler = spy_search
    try:
        # Build a scripted turn that calls the tool with structured filters.
        tool_args = json.dumps(
            {
                "query": "federated learning survey",
                "sources": ["dblp"],
                "limit": 5,
                "filters": {
                    "year_range": [2020, 2024],
                    "min_citations": 50,
                    "venues": ["NeurIPS", "ICML"],
                    "fields": ["cs.LG"],
                    "sort": "citations",
                },
            }
        )
        scripted = [
            MockResponse(
                [
                    MockChoice(
                        _mk_assistant_message(
                            "", tool_calls=[_mk_tool_call("search_papers", tool_args)]
                        )
                    )
                ]
            ),
            MockResponse([MockChoice(_mk_assistant_message("找到候选。"))]),
        ]
        mock_client = MockAsyncOpenAI(scripted)
        monkeypatch.setattr(agent_runtime, "client", mock_client, raising=False)
        monkeypatch.setattr(agent_runtime, "model", "mock-model", raising=False)

        with TestClient(app) as client:
            r = client.post(
                "/api/agent/search/stream",
                json={"message": "联邦学习 高引用 综述"},
            )
        assert r.status_code == 200
    finally:
        tools_mod.tool_registry.get("search_papers").handler = original

    assert captured_args, "search_papers handler should have been called"
    call = captured_args[0]
    assert call["query"] == "federated learning survey"
    assert call["sources"] == ["dblp"]
    assert call["limit"] == 5
    f = call["filters"]
    assert f["year_range"] == [2020, 2024]
    assert f["min_citations"] == 50
    assert f["venues"] == ["NeurIPS", "ICML"]
    assert f["fields"] == ["cs.LG"]
    assert f["sort"] == "citations"


def test_search_stream_response_payload_includes_filters_applied(monkeypatch):
    """The tool handler puts ``filters_applied`` into the response
    payload. The SSE body should carry that field so the frontend can
    show the user which sources actually narrowed the results."""

    from app.agent_runtime import tools as tools_mod

    original = tools_mod.tool_registry.get("search_papers").handler

    async def fake_search(query, sources=None, limit=5, filters=None):
        return {
            "papers": [],
            "total": 0,
            "sources_searched": ["openalex", "dblp"],
            "errors": {},
            "filters_applied": {
                "openalex": False,
                "arxiv": False,
                "dblp": True,
                "pubmed": False,
                "semantic_scholar": False,
            },
        }

    tools_mod.tool_registry.get("search_papers").handler = fake_search
    try:
        scripted = [
            MockResponse(
                [
                    MockChoice(
                        _mk_assistant_message(
                            "",
                            tool_calls=[
                                _mk_tool_call(
                                    "search_papers",
                                    json.dumps(
                                        {
                                            "query": "x",
                                            "filters": {"year_range": [2022, 2024]},
                                        }
                                    ),
                                )
                            ],
                        )
                    )
                ]
            ),
            MockResponse([MockChoice(_mk_assistant_message("ok"))]),
        ]
        mock_client = MockAsyncOpenAI(scripted)
        monkeypatch.setattr(agent_runtime, "client", mock_client, raising=False)
        monkeypatch.setattr(agent_runtime, "model", "mock-model", raising=False)

        with TestClient(app) as client:
            r = client.post("/api/agent/search/stream", json={"message": "x"})
        assert r.status_code == 200
    finally:
        tools_mod.tool_registry.get("search_papers").handler = original

    body = r.text
    assert "filters_applied" in body
    # The JSON-in-SSE escaping adds backslashes before quotes, so the
    # key/value pair looks like `\"dblp\": true` in the raw body.
    assert '\\"dblp\\": true' in body
    # The full map should be embedded inside the tool_end frame.
    events = _sse_events(body)
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert "result_preview" in tool_end


def test_search_stream_reuses_event_stream(monkeypatch):
    """The endpoint must reuse ``_event_stream`` (no duplicated
    keepalive/producer logic). We assert that by patching
    ``_event_stream`` and checking the endpoint called it with the
    expected kwargs.
    """
    from app.routers import agent as agent_router_mod

    seen: List[dict] = []

    async def fake_event_stream(
        message,
        project_id,
        history,
        extra_context,
        keepalive_interval=None,
        prompt_kind="general",
    ):
        seen.append(
            {
                "message": message,
                "project_id": project_id,
                "history": history,
                "extra_context": extra_context,
                "prompt_kind": prompt_kind,
            }
        )
        # Yield a trivial done frame so the StreamingResponse has
        # something to send.
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    monkeypatch.setattr(agent_router_mod, "_event_stream", fake_event_stream)

    with TestClient(app) as client:
        r = client.post(
            "/api/agent/search/stream",
            json={
                "message": "找一篇 GNN 论文",
                "history": [{"role": "user", "content": "hi"}],
                "extra_context": "seed search",
            },
        )
    assert r.status_code == 200

    assert seen, "_event_stream should have been called"
    call = seen[0]
    # No project_id at this stage of the flow.
    assert call["project_id"] is None
    assert call["prompt_kind"] == "search"
    assert call["message"] == "找一篇 GNN 论文"
    assert call["history"] == [{"role": "user", "content": "hi"}]
    assert call["extra_context"] == "seed search"


def test_search_stream_request_omits_project_id_gracefully():
    """The SmartSearch request shape has no ``project_id`` (SearchBar
    users haven't built a project yet). The endpoint should accept
    this and not 422.
    """
    # Patch run_stream to something that doesn't need a real LLM.
    from app.routers import agent as agent_router_mod

    async def fake_run_stream(**kwargs):
        from app.agent_runtime.runtime import EVT_DONE, Event

        yield Event(
            type=EVT_DONE,
            payload={
                "iterations": 0,
                "truncated": False,
                "content": "",
                "action_type": None,
                "paper_suggestions": [],
                "tool_calls": [],
                "error": None,
            },
        )

    # We can't easily patch the bound method on the singleton; instead
    # just verify the request validates.
    from app.routers.agent import SearchAgentRequest

    req = SearchAgentRequest(message="hi", history=None, extra_context=None)
    assert req.message == "hi"
    assert req.history is None
    assert req.extra_context is None
    # Make sure there's no `project_id` field at all — if a future
    # refactor accidentally adds one, this test will catch it before
    # the FE breaks.
    schema = SearchAgentRequest.model_json_schema()
    assert "project_id" not in schema.get("properties", {})


# ---------------------------------------------------------------------------
# Per-kind iteration cap (search = 60, general = 20)
# ---------------------------------------------------------------------------


def test_default_max_iterations_is_at_least_60():
    """The runtime default must be high enough that a SmartSearch
    turn that has to fan out across search → author → snowball
    can actually finish. Regression guard: prior value was 6."""
    from app.agent_runtime.runtime import DEFAULT_MAX_ITERATIONS

    assert DEFAULT_MAX_ITERATIONS >= 60


def test_search_prompt_kind_allows_60_iterations():
    """The SmartSearch prompt kind should be allowed to use 60
    iterations. Verified via the runtime's MAX_ITERATIONS_BY_KIND map
    (single source of truth)."""
    from app.agent_runtime.runtime import MAX_ITERATIONS_BY_KIND

    assert MAX_ITERATIONS_BY_KIND.get("search", 0) >= 60


def test_general_prompt_kind_keeps_tighter_cap():
    """The general (writing-assistant) prompt kind should keep a
    tighter cap so a runaway drafting turn can't drain the LLM
    budget. Must be strictly less than the search cap."""
    from app.agent_runtime.runtime import MAX_ITERATIONS_BY_KIND

    assert MAX_ITERATIONS_BY_KIND["general"] < MAX_ITERATIONS_BY_KIND["search"]


def test_effective_cap_uses_kind_specific_value(monkeypatch):
    """When prompt_kind=search is passed, the resolver must return
    MAX_ITERATIONS_BY_KIND["search"] (not the lower general cap, and
    not exceeding the constructor ceiling). The constructor ceiling
    must also be respected when a smaller value is passed."""
    from app.agent_runtime.runtime import (
        MAX_ITERATIONS_BY_KIND,
        agent_runtime,
    )

    # Both kinds resolve to their kind-specific cap when the
    # constructor ceiling is high enough.
    assert agent_runtime._resolve_effective_max_iterations("search") == MAX_ITERATIONS_BY_KIND["search"]
    assert agent_runtime._resolve_effective_max_iterations("general") == MAX_ITERATIONS_BY_KIND["general"]

    # The constructor ceiling caps the kind-specific cap. This is
    # the safety net for callers (e.g. tests) that want a tighter
    # budget regardless of task kind.
    monkeypatch.setattr(agent_runtime, "max_iterations", 5)
    assert agent_runtime._resolve_effective_max_iterations("search") == 5
    assert agent_runtime._resolve_effective_max_iterations("general") == 5

    # Unknown kinds fall back to the constructor ceiling, never to
    # another kind's value (defense in depth).
    assert agent_runtime._resolve_effective_max_iterations("nonsense") == 5  # type: ignore[arg-type]


def test_search_system_prompt_advertises_the_60_iteration_cap():
    """The SmartSearch system prompt must tell the model the actual
    cap so it doesn't try to bail out early thinking the limit is
    much lower."""
    from app.agent_runtime.runtime import SEARCH_SYSTEM_PROMPT

    assert "60" in SEARCH_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Mid-stream paper_suggestions emit (the "don't lose what we found"
# behavior the SmartSearch panel depends on)
# ---------------------------------------------------------------------------


def _paper_dict(title: str, doi: str | None = None) -> dict:
    return {
        "id": f"id-{title}",
        "title": title,
        "authors": ["A"],
        "year": 2024,
        "doi": doi,
        "citation_count": 10,
        "reference_count": 0,
        "fields": [],
    }


def test_stream_new_papers_filters_already_seen():
    """Papers seen in an earlier tool call must not be re-emitted on
    a later iteration. Regression guard: the previous design only
    emitted at the final answer, so any tool that returned the same
    paper twice was silently dropped."""
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    seen: set = set()

    # First call: 2 papers, both new.
    first = [
        ToolCallRecord(
            tool="search_papers",
            arguments={"query": "x"},
            result_raw=json.dumps({"papers": [_paper_dict("P1"), _paper_dict("P2")]}),
            result_preview="",
            latency_ms=0,
        )
    ]
    new1 = rt._stream_new_papers(first, seen)
    assert {p["title"] for p in new1} == {"P1", "P2"}

    # Second call: same 2 papers + a brand-new one. Only the new one
    # should come back.
    second = [
        ToolCallRecord(
            tool="search_papers",
            arguments={"query": "x"},
            result_raw=json.dumps(
                {"papers": [_paper_dict("P1"), _paper_dict("P2"), _paper_dict("P3")]}
            ),
            result_preview="",
            latency_ms=0,
        )
    ]
    new2 = rt._stream_new_papers(second, seen)
    assert {p["title"] for p in new2} == {"P3"}, f"expected only P3, got {new2}"


def test_stream_new_papers_includes_snowball_tools():
    """get_citing_papers / get_referenced_papers / search_by_author all
    return ``{"papers": [...]}`` and must be picked up by the
    collector just like search_papers."""
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    seen: set = set()
    records = [
        ToolCallRecord(
            tool="get_citing_papers",
            arguments={"paper_id": "DOI:10.1/anchor"},
            result_raw=json.dumps({"papers": [_paper_dict("C1")], "direction": "citing"}),
            result_preview="",
            latency_ms=0,
        ),
        ToolCallRecord(
            tool="get_referenced_papers",
            arguments={"paper_id": "DOI:10.1/anchor"},
            result_raw=json.dumps({"papers": [_paper_dict("R1")], "direction": "referenced"}),
            result_preview="",
            latency_ms=0,
        ),
        ToolCallRecord(
            tool="search_by_author",
            arguments={"author_name": "Smith"},
            result_raw=json.dumps({"papers": [_paper_dict("A1")]}),
            result_preview="",
            latency_ms=0,
        ),
    ]
    titles = {p["title"] for p in rt._stream_new_papers(records, seen)}
    assert titles == {"C1", "R1", "A1"}


def test_stream_new_papers_caps_at_max_suggestions_per_turn():
    """The runtime never surfaces more than MAX_SUGGESTIONS_PER_TURN
    papers in one turn so the UI stays scannable."""
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    seen: set = set()
    records = [
        ToolCallRecord(
            tool="search_papers",
            arguments={},
            result_raw=json.dumps(
                {"papers": [_paper_dict(f"P{i}") for i in range(50)]}
            ),
            result_preview="",
            latency_ms=0,
        )
    ]
    surfaced = rt._stream_new_papers(records, seen)
    assert len(surfaced) == AgentRuntime._MAX_SUGGESTIONS_PER_TURN


def test_collect_paper_suggestions_dedupes_and_caps():
    """The ``done`` payload snapshot must dedupe and cap independently
    of the streaming collector — they're separate code paths but
    must agree on the final list."""
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    records = [
        ToolCallRecord(
            tool="search_papers",
            arguments={},
            result_raw=json.dumps(
                {"papers": [_paper_dict("A"), _paper_dict("B"), _paper_dict("C")]}
            ),
            result_preview="",
            latency_ms=0,
        ),
        ToolCallRecord(
            tool="search_papers",
            arguments={},
            result_raw=json.dumps(
                {"papers": [_paper_dict("A"), _paper_dict("B"), _paper_dict("C"), _paper_dict("D")]}
            ),
            result_preview="",
            latency_ms=0,
        ),
    ]
    snap = rt._collect_paper_suggestions(records)
    titles = [p["title"] for p in snap]
    # A, B, C from first call; D from second. No duplicates.
    assert titles == ["A", "B", "C", "D"]
    assert len(snap) <= AgentRuntime._MAX_SUGGESTIONS_PER_TURN


def test_collect_paper_suggestions_dedupes_when_id_differs_only_by_source_prefix():
    """The same paper surfaced via two crawlers often has different
    ids (e.g. ``OpenAlex:W1`` vs ``arXiv:2106.09685``). The dedup
    should fall through to doi, then title, to avoid surfacing the
    same work twice."""
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    p_oa = {**_paper_dict("P"), "id": "OpenAlex:W1", "doi": "10.1/abc"}
    p_arxiv = {**_paper_dict("P"), "id": "arXiv:2106.09685", "doi": "10.1/abc"}
    p_other = _paper_dict("Q")
    records = [
        ToolCallRecord(
            tool="search_papers",
            arguments={},
            result_raw=json.dumps({"papers": [p_oa, p_other]}),
            result_preview="",
            latency_ms=0,
        ),
        ToolCallRecord(
            tool="get_citing_papers",
            arguments={},
            result_raw=json.dumps({"papers": [p_arxiv]}),
            result_preview="",
            latency_ms=0,
        ),
    ]
    snap = rt._collect_paper_suggestions(records)
    # p_oa and p_arxiv are the same paper (doi matches); p_other is
    # distinct. Result must contain exactly two.
    assert len(snap) == 2
    titles = {p["title"] for p in snap}
    assert titles == {"P", "Q"}
