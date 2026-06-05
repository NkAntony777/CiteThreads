"""
Tests for the security and reliability fixes from the 2026-06-04
deep review.

Covers the 5 issues that the user-facing spec called out:

1.1  API key server-side. Frontend never holds the raw key; the
     server uses ``settings.siliconflow_api_key`` (or the value
     supplied in the request body, for BYOK callers).
1.2  Bearer-token auth on every non-public endpoint. Missing or
     wrong token -> 401.
1.3  SSRF protection in the agent runtime: configuring a base URL
     that points at loopback / private IPs / .local domains is
     rejected.
2.2  Canvas AI no longer mocks: the runtime calls the real LLM via
     the agent streaming endpoint.
3.2  Context overflow: a 50-reference system prompt is truncated
     to fit the 8K-token budget.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# ---------------------------------------------------------------------------
# Module-scoped fixtures: each test sets up its own auth state, then
# restores the default (off). This keeps tests independent regardless
# of pytest collection order.
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _set_auth(token: str) -> None:
    """Update both the live ``settings`` instance and the auth
    module's reference to it. Two writes because some tests import
    settings before this fixture runs and Python attribute mutation
    is per-object."""
    from app.config import settings
    from app import auth as auth_mod

    settings.auth_token = token
    auth_mod.settings.auth_token = token


@pytest.fixture
def auth_token():
    """Yield with a known auth token set, then restore to "" on
    teardown so other tests that expect auth to be off keep working."""
    _set_auth("test-secret-token")
    yield "test-secret-token"
    _set_auth("")


@pytest.fixture
def no_auth():
    """Explicitly clear the token for the duration of the test."""
    _set_auth("")
    yield
    _set_auth("")


@pytest.fixture
async def app_client():
    """Plain AsyncClient against the FastAPI app. Tests manage their
    own auth state via the ``auth_token`` / ``no_auth`` fixtures."""
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ===========================================================================
# 1.2 — Bearer auth
# ===========================================================================


@pytest.mark.asyncio
async def test_auth_missing_token_returns_401(app_client, auth_token):
    """A protected endpoint with no Authorization header must 401.

    We hit ``/api/projects`` (no trailing slash) to avoid FastAPI's
    307 redirect that would mask the auth check."""
    resp = await app_client.get("/api/projects")
    assert resp.status_code == 401
    body = resp.json()
    assert "token" in (body.get("detail") or "").lower()


@pytest.mark.asyncio
async def test_auth_wrong_token_returns_401(app_client, auth_token):
    """A wrong bearer token must 401."""
    resp = await app_client.get(
        "/api/projects",
        headers={"Authorization": "Bearer not-the-right-one"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_correct_token_passes(app_client, auth_token):
    """A correct bearer token must let the request through. The
    projects endpoint may return 200 (with empty list) or 422
    depending on validation; what matters is that it's not 401."""
    resp = await app_client.get(
        "/api/projects",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_auth_public_paths_remain_open(app_client, auth_token):
    """``/``, ``/health``, ``/docs`` and ``/openapi.json`` are public
    even when a token is configured."""
    for path in ("/", "/health", "/docs", "/openapi.json"):
        resp = await app_client.get(path)
        assert resp.status_code != 401, f"public path {path} must not 401"


@pytest.mark.asyncio
async def test_auth_disabled_when_no_token_configured(app_client, no_auth):
    """With no token configured, requests pass (dev mode)."""
    resp = await app_client.get("/api/projects")
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_auth_malformed_authorization_header_returns_401(app_client, auth_token):
    """An Authorization header without ``Bearer`` prefix is rejected."""
    resp = await app_client.get(
        "/api/projects",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_empty_bearer_returns_401(app_client, auth_token):
    """``Authorization: Bearer`` with no token is treated as missing."""
    resp = await app_client.get(
        "/api/projects",
        headers={"Authorization": "Bearer"},
    )
    assert resp.status_code == 401


def test_auth_resolution_prefers_settings_token(monkeypatch, no_auth):
    """The bearer token resolver reads from settings first, then env."""
    from app import auth as auth_mod
    from app.config import settings

    monkeypatch.setenv("CITETHREADS_AUTH_TOKEN", "from-env")
    settings.auth_token = "from-settings"
    assert auth_mod._resolve_token() == "from-settings"


def test_auth_resolution_falls_back_to_env(monkeypatch, no_auth):
    """When settings.auth_token is empty, the env var is used."""
    from app import auth as auth_mod
    from app.config import settings

    settings.auth_token = ""
    monkeypatch.setenv("CITETHREADS_AUTH_TOKEN", "from-env")
    assert auth_mod._resolve_token() == "from-env"


def test_auth_is_enabled_flag(monkeypatch, no_auth):
    """``is_auth_enabled`` is True iff a non-empty token is configured."""
    from app import auth as auth_mod
    from app.config import settings

    settings.auth_token = ""
    monkeypatch.delenv("CITETHREADS_AUTH_TOKEN", raising=False)
    assert auth_mod.is_auth_enabled() is False

    monkeypatch.setenv("CITETHREADS_AUTH_TOKEN", "x")
    assert auth_mod.is_auth_enabled() is True


# ===========================================================================
# 1.1 — Server-side API key
# ===========================================================================


@pytest.mark.asyncio
async def test_configure_llm_works_without_api_key_when_server_key_set(
    app_client, no_auth, monkeypatch
):
    """``/api/ai/configure/llm`` no longer requires the frontend to ship
    a key. When the server's default key is set and the body has no
    ``api_key``, the request must succeed."""
    from app.config import settings
    monkeypatch.setattr(settings, "siliconflow_api_key", "server-key", raising=False)
    # Stub the services so we don't actually hit the LLM.
    from app.services import smart_classifier, review_generator, writing_assistant
    monkeypatch.setattr(smart_classifier, "configure_llm", lambda **kw: None)
    monkeypatch.setattr(review_generator, "configure_llm", lambda **kw: None)
    monkeypatch.setattr(writing_assistant, "configure_llm", lambda **kw: None)

    resp = await app_client.post(
        "/api/ai/configure/llm",
        json={"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V3"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True


@pytest.mark.asyncio
async def test_configure_llm_rejects_when_no_key_anywhere(app_client, no_auth, monkeypatch):
    """If the server has no default key and the body has no ``api_key``,
    return 200 with a clear failure message."""
    from app.config import settings
    monkeypatch.setattr(settings, "siliconflow_api_key", "", raising=False)

    resp = await app_client.post(
        "/api/ai/configure/llm",
        json={"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V3"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "密钥" in body["message"] or "API" in body["message"]


@pytest.mark.asyncio
async def test_configure_llm_wires_agent_runtime_singleton(
    app_client, no_auth, monkeypatch
):
    """``/api/ai/configure/llm`` must also wire the ``agent_runtime``
    singleton. Otherwise the SmartSearch panel and the standalone
    agent chat panel keep reporting "LLM client not configured" even
    after the user has set up their API key in AISettings.
    """
    from app.config import settings
    from app.agent_runtime import agent_runtime

    # Start from the bug state: agent runtime has no client.
    monkeypatch.setattr(settings, "siliconflow_api_key", "server-key", raising=False)
    monkeypatch.setattr(agent_runtime, "client", None, raising=False)

    # Stub the legacy services so we don't actually hit the LLM.
    from app.services import smart_classifier, review_generator, writing_assistant
    monkeypatch.setattr(smart_classifier, "configure_llm", lambda **kw: None)
    monkeypatch.setattr(review_generator, "configure_llm", lambda **kw: None)
    monkeypatch.setattr(writing_assistant, "configure_llm", lambda **kw: None)

    resp = await app_client.post(
        "/api/ai/configure/llm",
        json={"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V3"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # The agent runtime should now have a client and the requested
    # model. We don't assert on the key (it's stored on the client
    # object), but model + non-None client is enough to prove the
    # wire-up happened.
    assert agent_runtime.client is not None
    assert agent_runtime.model == "deepseek-ai/DeepSeek-V3"


@pytest.mark.asyncio
async def test_status_endpoint_reports_default_key_flag(app_client, no_auth, monkeypatch):
    """``/api/ai/status`` must surface whether a default key is
    configured so the UI can show the warning."""
    from app.config import settings
    monkeypatch.setattr(settings, "siliconflow_api_key", "sk-fake", raising=False)

    resp = await app_client.get("/api/ai/status")
    body = resp.json()
    assert body["default_key_configured"] is True
    assert body["default_model"] == settings.ai_model


@pytest.mark.asyncio
async def test_status_endpoint_reports_no_default_key(app_client, no_auth, monkeypatch):
    """When the env var is empty, status reports the key is missing."""
    from app.config import settings
    monkeypatch.setattr(settings, "siliconflow_api_key", "", raising=False)

    resp = await app_client.get("/api/ai/status")
    body = resp.json()
    assert body["default_key_configured"] is False


@pytest.mark.asyncio
async def test_test_config_endpoint_rejects_when_no_server_key(
    app_client, no_auth, monkeypatch
):
    """``/api/ai/test-config`` (the new server-side test) returns a
    friendly error when no default key is configured."""
    from app.config import settings
    monkeypatch.setattr(settings, "siliconflow_api_key", "", raising=False)

    resp = await app_client.post(
        "/api/ai/test-config",
        json={"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V3"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "密钥" in body["message"] or "API" in body["message"]


# ===========================================================================
# 1.3 — SSRF protection in agent runtime
# ===========================================================================


@pytest.mark.asyncio
async def test_agent_runtime_rejects_localhost_base_url():
    """``AgentRuntime.configure`` must raise ``ValueError`` when
    given a localhost URL."""
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    with pytest.raises(ValueError):
        await rt.configure(api_key="x", model="m", base_url="http://localhost:1234/v1")


@pytest.mark.asyncio
async def test_agent_runtime_rejects_loopback_ip():
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    with pytest.raises(ValueError):
        await rt.configure(api_key="x", model="m", base_url="http://127.0.0.1:9999/v1")


@pytest.mark.asyncio
async def test_agent_runtime_rejects_link_local_metadata_ip():
    """The cloud metadata IP 169.254.169.254 must be rejected."""
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    with pytest.raises(ValueError):
        await rt.configure(api_key="x", model="m", base_url="http://169.254.169.254/v1")


@pytest.mark.asyncio
async def test_agent_runtime_rejects_local_domain():
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    with pytest.raises(ValueError):
        await rt.configure(api_key="x", model="m", base_url="http://foo.localhost/v1")


@pytest.mark.asyncio
async def test_agent_runtime_rejects_unsafe_scheme():
    from app.agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime()
    with pytest.raises(ValueError):
        await rt.configure(api_key="x", model="m", base_url="file:///etc/passwd")


@pytest.mark.asyncio
async def test_agent_runtime_configure_endpoint_returns_400_for_blocked_url(
    app_client, no_auth
):
    """The HTTP endpoint ``/api/agent/configure`` must surface the
    SSRF rejection as a 400 with a meaningful message (not 500)."""
    resp = await app_client.post(
        "/api/agent/configure",
        json={
            "api_key": "sk-test",
            "model": "m",
            "base_url": "http://127.0.0.1:8000/v1",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", "")
    assert "127.0.0.1" in detail or "API" in detail or "本地" in detail


# ===========================================================================
# 2.2 — Canvas real LLM streaming
# ===========================================================================
#
# The Canvas AI mock lived in the React component. The actual
# streaming is delegated to the agent runtime's
# ``/api/agent/chat/stream`` SSE endpoint. What this file adds is a
# verification that the non-streaming agent chat endpoint accepts
# the same payload the Canvas now sends (project_id + extra_context)
# and that the runtime propagates the extra_context into the LLM
# call. The full streaming pipeline is exercised end-to-end in
# ``test_agent_runtime.py``.
# ---------------------------------------------------------------------------


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


class _StreamChunk:
    """Mimic an OpenAI streaming chunk."""

    def __init__(self, content: Optional[str] = None, finish_reason: Optional[str] = None) -> None:
        delta = SimpleNamespace(content=content, tool_calls=None)
        self.choices = [SimpleNamespace(delta=delta, finish_reason=finish_reason)]


class _MockStream:
    """Async iterator that yields the full assistant text as one chunk
    + a finish_reason chunk. Mirrors the simple non-incremental output
    of most LLM providers in our tests."""

    def __init__(self, response: MockResponse) -> None:
        msg = response.choices[0].message
        self._content = msg.content or ""
        self._yielded_content = False
        self._yielded_finish = False

    def __aiter__(self) -> "_MockStream":
        return self

    async def __anext__(self) -> _StreamChunk:
        if not self._yielded_content:
            self._yielded_content = True
            return _StreamChunk(content=self._content)
        if not self._yielded_finish:
            self._yielded_finish = True
            return _StreamChunk(finish_reason="stop")
        raise StopAsyncIteration


class MockChatCompletions:
    def __init__(self, scripted_responses: List[MockResponse]) -> None:
        self._scripted = list(scripted_responses)
        self.calls: List[dict] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._scripted:
            return MockResponse([MockChoice(_mk_assistant_message("(empty)"))])
        next_resp = self._scripted.pop(0)
        if kwargs.get("stream"):
            return _MockStream(next_resp)
        return next_resp


class MockChatNamespace:
    def __init__(self, completions: MockChatCompletions) -> None:
        self.completions = completions


class MockAsyncOpenAI:
    def __init__(self, scripted: List[MockResponse]) -> None:
        self.chat = MockChatNamespace(MockChatCompletions(scripted))


@pytest.mark.asyncio
async def test_canvas_continue_call_wires_to_agent_runtime():
    """The Canvas Continue button posts to ``/api/agent/chat/stream``
    with an ``extra_context`` describing the task. We exercise the
    underlying runtime with a canned streaming response and verify
    the result makes it into the user-facing message."""
    from app.agent_runtime.runtime import AgentRuntime

    scripted = [
        MockResponse([MockChoice(_mk_assistant_message("续写正文内容,无 Markdown 包装。"))]),
    ]
    mock = MockAsyncOpenAI(scripted)
    runtime = AgentRuntime(client=mock, model="m")

    result = await runtime.run(
        message="请对下面的内容进行续写: ...",
        project_id="proj-canvas-test",
        extra_context="Task: continue the given text in the same academic style.",
    )
    assert "续写正文内容" in result.content
    assert result.action_type == "answer"
    assert len(mock.chat.completions.calls) == 1
    sent = mock.chat.completions.calls[0]
    # The extra_context is folded into the system prompt, so the
    # LLM sees it without it appearing verbatim in the user message.
    sys_msg = next(m for m in sent["messages"] if m["role"] == "system")
    assert "Task: continue" in sys_msg["content"]


@pytest.mark.asyncio
async def test_canvas_polish_call_wires_to_agent_runtime():
    """Same as above, for the Polish action."""
    from app.agent_runtime.runtime import AgentRuntime

    scripted = [
        MockResponse([MockChoice(_mk_assistant_message("润色后的文本。"))]),
    ]
    mock = MockAsyncOpenAI(scripted)
    runtime = AgentRuntime(client=mock, model="m")

    result = await runtime.run(
        message="请对下面的内容进行润色: ...",
        project_id="proj-canvas-test",
        extra_context="Task: polish the given text in academic style.",
    )
    assert "润色" in result.content
    sent = mock.chat.completions.calls[0]
    sys_msg = next(m for m in sent["messages"] if m["role"] == "system")
    assert "Task: polish" in sys_msg["content"]


@pytest.mark.asyncio
async def test_canvas_stream_endpoint_accepts_extra_context_shape(
    app_client, no_auth
):
    """The SSE endpoint must accept the same payload the Canvas now
    sends (project_id + extra_context + message). We hit it without
    an LLM client configured; the runtime should still produce a
    well-formed SSE response with an error + done frame."""
    from app.agent_runtime.runtime import agent_runtime
    saved_client = agent_runtime.client
    agent_runtime.client = None
    try:
        resp = await app_client.post(
            "/api/agent/chat/stream",
            json={
                "message": "续写: ...",
                "project_id": "proj-canvas-test",
                "extra_context": "Task: continue the given text in the same academic style.",
            },
        )
    finally:
        agent_runtime.client = saved_client

    assert resp.status_code == 200
    body = resp.text
    # SSE response with a done frame.
    assert "data:" in body
    # The actual JSON has whitespace: ``"type": "done"``. Match both
    # for robustness.
    assert '"type": "done"' in body or '"type":"done"' in body
    assert "not configured" in body.lower() or "未配置" in body


# ===========================================================================
# 3.2 — Context overflow handling
# ===========================================================================


def _make_paper(title: str, abstract: str, year: int = 2024, id_suffix: str = "x") -> Any:
    """Build a minimal Paper for the writing_assistant tests."""
    from app.models import Paper
    return Paper(
        id=f"OpenAlex:W{id_suffix}",
        title=title,
        authors=["Doe, J."],
        year=year,
        abstract=abstract,
        citation_count=0,
        reference_count=0,
        url=f"https://example.com/{id_suffix}",
    )


def _make_reference(paper: Any) -> Any:
    """Build a Reference from a paper using the canonical constructor."""
    from app.models.references import Reference, ReferenceSource
    return Reference.from_paper(paper, ReferenceSource.SEARCH)


def test_build_system_prompt_caps_individual_abstracts():
    """Each reference's abstract must be truncated to 200 chars, even
    when the source paper has a much longer abstract."""
    from app.models.references import WritingContext
    from app.services.writing_assistant import WritingAssistantService

    long_abstract = "lorem ipsum " * 500  # ~6000 chars
    paper = _make_paper("A", long_abstract, id_suffix="1")
    ref = _make_reference(paper)
    ctx = WritingContext(project_id="test", references=[ref], current_document="")

    svc = WritingAssistantService()
    prompt = svc._build_system_prompt(ctx)

    # The full abstract should NOT appear verbatim — only a 200-char
    # version should.
    assert long_abstract not in prompt
    assert "Abstract:" in prompt
    # Find the Abstract line and check it ends with the truncation marker.
    abstract_line = next(line for line in prompt.splitlines() if line.startswith("Abstract:"))
    body = abstract_line[len("Abstract:"):].rstrip()
    # The truncation marker is '...'. Allow up to 200 + '...' (3 chars)
    # + a small slack for the leading space.
    assert len(body) <= 210, f"abstract body too long: {len(body)}"


def test_build_system_prompt_truncates_50_reference_list():
    """A 50-reference system prompt must be reduced to fit the
    8K-token budget."""
    from app.models.references import WritingContext
    from app.services.writing_assistant import (
        WritingAssistantService,
        MAX_PROMPT_TOKENS,
    )

    refs = [
        _make_reference(
            _make_paper(f"Paper {i}", "x" * 200, id_suffix=str(i))
        )
        for i in range(50)
    ]
    ctx = WritingContext(
        project_id="test",
        references=refs,
        current_document="",
        literature_review="y" * 5_000,
    )

    svc = WritingAssistantService()
    prompt = svc._build_system_prompt(ctx)
    est_tokens = svc._estimate_tokens(prompt)
    assert est_tokens <= MAX_PROMPT_TOKENS, (
        f"prompt still over budget after truncation: {est_tokens} tokens"
    )


def test_build_system_prompt_drops_review_first_under_pressure():
    """When the prompt is over budget, the literature-review block
    is dropped before the reference block is shrunk."""
    from app.models.references import WritingContext
    from app.services.writing_assistant import WritingAssistantService

    refs = [
        _make_reference(
            _make_paper(f"Paper {i}", "z" * 200, id_suffix=str(i))
        )
        for i in range(50)
    ]
    long_review = "r" * 50_000
    ctx = WritingContext(
        project_id="test",
        references=refs,
        current_document="",
        literature_review=long_review,
    )
    svc = WritingAssistantService()
    prompt = svc._build_system_prompt(ctx)
    # The huge review should not be present verbatim after truncation.
    assert long_review not in prompt
    # Some references should remain.
    assert "Paper 0" in prompt or "Paper 49" in prompt


def test_build_system_prompt_keeps_references_when_no_review():
    """When there's no review block, the system prompt should keep
    the reference block intact (it fits the budget on its own)."""
    from app.models.references import WritingContext
    from app.services.writing_assistant import WritingAssistantService

    refs = [
        _make_reference(
            _make_paper(f"Paper {i}", "abstract " * 20, id_suffix=str(i))
        )
        for i in range(5)
    ]
    ctx = WritingContext(project_id="test", references=refs, current_document="")

    svc = WritingAssistantService()
    prompt = svc._build_system_prompt(ctx)
    assert "Paper 0" in prompt
    assert "Paper 4" in prompt


def test_estimate_tokens_is_roughly_chars_over_4():
    """The token estimate is chars / 4. Verify on a few inputs."""
    from app.services.writing_assistant import WritingAssistantService

    assert WritingAssistantService._estimate_tokens("") == 0
    assert WritingAssistantService._estimate_tokens("abcd") == 1
    assert WritingAssistantService._estimate_tokens("a" * 100) == 25
    assert WritingAssistantService._estimate_tokens("a" * 101) == 25  # floor(101/4)


def test_cap_prompt_block_truncates_long_input():
    """The helper used by ``generate_section`` / ``expand_content``
    should truncate a long string and append a marker."""
    from app.services.writing_assistant import WritingAssistantService

    long_block = "x" * 10_000
    out = WritingAssistantService._cap_prompt_block(long_block, max_tokens=10)
    # 4 chars/token * 10 = 40 chars + trailing '...' marker
    assert len(out) <= 50
    assert "..." in out


def test_cap_prompt_block_passes_through_short_input():
    from app.services.writing_assistant import WritingAssistantService

    short = "x" * 20
    assert WritingAssistantService._cap_prompt_block(short, max_tokens=100) == short
    # Empty input passes through unchanged.
    assert WritingAssistantService._cap_prompt_block("", max_tokens=0) == ""


def test_truncate_to_budget_returns_original_when_within_budget():
    """No truncation when the prompt is already within budget."""
    from app.services.writing_assistant import (
        WritingAssistantService,
        WRITING_ASSISTANT_SYSTEM_PROMPT,
    )

    prompt = WRITING_ASSISTANT_SYSTEM_PROMPT + " tiny refs"
    out, was_truncated = WritingAssistantService._truncate_to_budget(
        prompt, " tiny refs", "", ""
    )
    assert was_truncated is False
    assert out == prompt
