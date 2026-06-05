"""
Tests for the CTDP draft router + orchestrator.

Covers:
- Auth (401 without bearer, 200 with bearer)
- 503 when no LLM is configured
- 404 for unknown project
- 400 for malformed project id
- Happy-path for each phase endpoint (mocked LLM + mocked search)
- ``/status`` reflects phase progress
- ``/draft.md`` returns 404 before compile, 200 after
- Persistence: state written to disk survives a fresh ``DraftRunner``
- ``run-all`` end-to-end with every phase mocked
- DraftRunner unit tests (no router in the loop)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, List

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ---------------------------------------------------------------------------
# Auth fixtures (mirrored from test_review_security_fixes.py so the test
# is self-contained if the user only runs this file).
# ---------------------------------------------------------------------------


def _set_auth(token: str) -> None:
    from app.config import settings
    from app import auth as auth_mod

    settings.auth_token = token
    auth_mod.settings.auth_token = token


@pytest.fixture
def auth_token():
    _set_auth("draft-test-token")
    yield "draft-test-token"
    _set_auth("")


@pytest.fixture
def no_auth():
    _set_auth("")
    yield
    _set_auth("")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# LLM mocks (the LLM gets called from every phase; we script one canned
# JSON answer per call so the pipeline can run end-to-end).
# ---------------------------------------------------------------------------


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockCompletions:
    """A scripted AsyncOpenAI chat completions mock.

    Each call pops the next response off ``scripted``. ``echo_user`` may
    be set to True for phases that don't actually inspect the response
    text (e.g. heuristic fallbacks)."""

    def __init__(self, scripted: List[str] | str, echo_user: bool = False):
        self._scripted: List[str] = (
            list(scripted) if not isinstance(scripted, str) else [scripted]
        )
        self._echo_user = echo_user
        self.calls: List[dict] = []

    async def create(self, *, model, messages, temperature, max_tokens):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self._echo_user:
            # Use the user message as the response so the parser sees
            # something but downstream code is free to fall back.
            for m in reversed(messages):
                if m.get("role") == "user":
                    return _MockResponse(m.get("content", ""))
            return _MockResponse("")
        if not self._scripted:
            raise RuntimeError(
                f"MockCompletions ran out of scripted responses "
                f"after {len(self.calls)} calls"
            )
        return _MockResponse(self._scripted.pop(0))


class _MockLLMClient:
    """Drop-in ``AsyncOpenAI`` stand-in. Its ``chat.completions.create``
    follows the same signature the real client uses, so the phase code
    doesn't need to know it's a mock."""

    def __init__(self, scripted: List[str] | str, echo_user: bool = False):
        self.chat = type(
            "Chat", (), {"completions": _MockCompletions(scripted, echo_user)}
        )()


# Canned JSON for phases that need it. Each phase only inspects a few
# fields, so a tiny payload is enough.
_REORDER_JSON = json.dumps(
    [
        {"id": "openalex:W100", "relevance_score": "High", "why_relevant": "core"},
        {"id": "openalex:W101", "relevance_score": "Medium", "why_relevant": "support"},
    ]
)
_SCRIBE_JSON = json.dumps(
    [
        {
            "paper_id": "openalex:W100",
            "research_question": "rq",
            "methodology": "method",
            "key_findings": ["k1", "k2"],
            "implications": "imp",
            "limitations": ["l1"],
            "relevance_score": 4,
            "relevance_reason": "strong",
        },
        {
            "paper_id": "openalex:W101",
            "research_question": "rq2",
            "methodology": "method2",
            "key_findings": ["k3"],
            "implications": "imp2",
            "limitations": ["l2"],
            "relevance_score": 3,
            "relevance_reason": "ok",
        },
    ]
)
_SIGNAL_JSON = json.dumps(
    {
        "gaps": [
            {
                "title": "gap1",
                "description": "d1",
                "gap_type": "methodological",
                "difficulty": "Medium",
                "impact": 4,
                "suggested_approach": "sa1",
            }
        ],
        "emerging_trends": ["t1"],
        "novel_angles": ["a1"],
    }
)
_ARCHITECT_JSON = json.dumps(
    {
        "paper_type": "Literature Review",
        "target_venue": "Nature",
        "research_question": "rq",
        "draft_statement": "claim",
        "total_target_words": 6000,
        "sections": [
            {"number": "1", "title": "Intro", "target_words": 600, "key_points": ["a"]},
            {"number": "2", "title": "Lit", "target_words": 1500, "key_points": ["b"]},
            {"number": "3", "title": "Method", "target_words": 900, "key_points": ["c"]},
            {"number": "4", "title": "Results", "target_words": 1200, "key_points": ["d"]},
            {"number": "5", "title": "Discussion", "target_words": 900, "key_points": ["e"]},
            {"number": "6", "title": "Conclusion", "target_words": 600, "key_points": ["f"]},
        ],
    }
)
_FORMATTER_JSON = json.dumps(
    {
        "format_name": "IMRaD",
        "target_venue": "Nature",
        "manuscript_spec": {"font": "Times New Roman 12pt"},
        "outline_markdown": "# Paper\n\n## 1 Intro\nTarget: 600 words\n",
    }
)
_CRAFTER_SECTIONS = (
    '```json\n{"body": "## Introduction\\n\\nThis is the intro [@openalex:W100].\\n", '
    '"headings": ["Introduction"], "paper_ids_cited": ["openalex:W100"], '
    '"tables": []}\n```'
)
_REFINER_JSON = json.dumps(
    {
        "body": "## Introduction\n\nRefined intro [@openalex:W100].\n",
        "headings": ["Introduction"],
        "paper_ids_cited": ["openalex:W100"],
        "tables": [],
    }
)
_REFEREE_JSON = (
    "## Referee Review\n\n"
    "### Section: Introduction\n"
    "- [clarity] Could be clearer\n"
    "\n**Overall verdict:** minor revisions\n"
)


def _full_phase_script() -> List[str]:
    """Canned responses for a single full end-to-end run.

    The number of LLM calls per phase (with this dataset):

    * research:  1 rerank + 1 scribe + 1 signal = 3
    * structure: 1 architect + 1 formatter      = 2
    * compose:   1 per section (6 sections)     = 6
    * validate:  1 referee + 1 factcheck        = 2
    * compile:   1 compiler + 1 abstract        = 2
    """
    return [
        # research (3)
        _REORDER_JSON,
        _SCRIBE_JSON,
        _SIGNAL_JSON,
        # structure (2)
        _ARCHITECT_JSON,
        _FORMATTER_JSON,
        # compose (6 — crafter per section)
        _CRAFTER_SECTIONS,
        _CRAFTER_SECTIONS,
        _CRAFTER_SECTIONS,
        _CRAFTER_SECTIONS,
        _CRAFTER_SECTIONS,
        _CRAFTER_SECTIONS,
        # validate (2)
        _REFEREE_JSON,
        "[]",  # factcheck finds no extra unsupported claims
        # compile (2)
        "```json\n{\"title\": \"T\", \"abstract\": \"A\"}\n```",
        "Abstract body here.",
    ]


# ---------------------------------------------------------------------------
# Project fixture: create a real project on disk for each test, isolated
# under a tmp dir via ``settings.data_dir``.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    """Point ``settings.data_dir`` at a tmp path and pre-seed one project.

    The router + DraftRunner both go through ``project_storage`` which
    reads ``settings.data_dir`` at call time, so monkeypatching it
    scopes the test cleanly without polluting the on-disk store."""
    from app.config import settings
    from app.services import storage
    from app.services.storage import project_storage

    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(storage, "settings", settings, raising=False)
    # The singleton instance computed ``projects_dir`` at import time, so
    # rebuild it against the new data_dir.
    project_storage.projects_dir = Path(settings.data_dir) / "projects"
    project_storage.projects_dir.mkdir(parents=True, exist_ok=True)

    proj = project_storage.create_project(
        seed_paper_id="seed:abc",
        name="Test Project",
        depth=1,
        direction="both",
    )
    yield {"project_id": proj.id, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# Async client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_client():
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helper: configure a fake LLM client + fake paper search.
# ---------------------------------------------------------------------------


@pytest.fixture
def stubbed_llm(monkeypatch):
    """Return a factory that scripts the mock LLM and patches the
    search service so the research phase can run offline.

    Usage::

        llm = stubbed_llm(["answer1", "answer2"])
        # llm.calls is now populated as the pipeline runs
    """
    from app.services import llm_factory
    from app.services.paper_search_service import (
        SearchResult,
        UnifiedPaperSearchService,
    )
    from app.models import Paper
    pss_module = UnifiedPaperSearchService  # alias kept for the closures below

    # Each test gets its own scripted list; default to the full e2e set
    # but allow callers to override.
    state: dict[str, Any] = {"scripted": None}

    def _patch(scripted: List[str] | str | None = None) -> _MockLLMClient:
        if scripted is None:
            scripted = _full_phase_script()
        state["scripted"] = scripted
        client = _MockLLMClient(scripted)

        def _factory(api_key=None, base_url=None, timeout=30.0):
            return client

        monkeypatch.setattr(llm_factory, "create_llm_client", _factory)
        # Also patch the symbol the router imports directly
        from app.routers import draft as draft_router_mod

        monkeypatch.setattr(
            draft_router_mod, "create_llm_client", _factory
        )
        # And the one the runner uses indirectly via the phase modules
        # (none — runner takes the client as a constructor arg).

        # Stub the paper search service so research's Scout can return
        # a few candidate papers without hitting the network. The
        # research phase imports ``paper_search_service`` directly from
        # the paper_search_service module, so we patch the *callable*
        # the phase actually invokes: ``UnifiedPaperSearchService.search``.
        async def _fake_search(self, query, sources=None, filters=None, limit=20):
            return SearchResult(
                papers=[
                    Paper(
                        id="openalex:W100",
                        title="Foundations",
                        authors=["Alice"],
                        year=2023,
                        abstract="abs",
                    ),
                    Paper(
                        id="openalex:W101",
                        title="Recent work",
                        authors=["Bob"],
                        year=2024,
                        abstract="abs",
                    ),
                ],
                errors={},
                sources_searched=["openalex"],
            )

        monkeypatch.setattr(UnifiedPaperSearchService, "search", _fake_search)
        return client

    return _patch


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_endpoint_requires_auth(app_client, auth_token, isolated_data_dir):
    """With auth configured, a request with no Authorization header
    must 401 — the LLM check happens after auth, so 401 wins."""
    pid = isolated_data_dir["project_id"]
    resp = await app_client.post(f"/api/draft/projects/{pid}/research")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_phase_endpoint_rejects_wrong_token(
    app_client, auth_token, isolated_data_dir
):
    pid = isolated_data_dir["project_id"]
    resp = await app_client.post(
        f"/api/draft/projects/{pid}/research",
        headers={"Authorization": "Bearer not-the-right-one"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_status_endpoint_requires_auth(app_client, auth_token, isolated_data_dir):
    pid = isolated_data_dir["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/status",
        headers={"Authorization": "Bearer not-the-right-one"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_status_endpoint_with_auth(
    app_client, auth_token, isolated_data_dir
):
    pid = isolated_data_dir["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/status",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == pid
    assert body["progress_pct"] == 0
    for phase in ("research", "structure", "compose", "validate", "compile"):
        assert phase in body["phases"]
        assert body["phases"][phase]["status"] == "pending"


# ---------------------------------------------------------------------------
# 503 when no LLM is configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_endpoint_returns_503_when_no_llm(
    app_client, auth_token, isolated_data_dir, monkeypatch
):
    from app.services import llm_factory
    from app.routers import draft as draft_router_mod

    pid = isolated_data_dir["project_id"]

    def _none_factory(api_key=None, base_url=None, timeout=30.0):
        return None

    monkeypatch.setattr(llm_factory, "create_llm_client", _none_factory)
    monkeypatch.setattr(
        draft_router_mod, "create_llm_client", _none_factory
    )

    resp = await app_client.post(
        f"/api/draft/projects/{pid}/research",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 503
    assert "LLM" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_status_endpoint_works_without_llm(
    app_client, auth_token, isolated_data_dir, monkeypatch
):
    """``/status`` is LLM-free and should return 200 even with no key."""
    from app.services import llm_factory
    from app.routers import draft as draft_router_mod

    pid = isolated_data_dir["project_id"]

    def _none_factory(api_key=None, base_url=None, timeout=30.0):
        return None

    monkeypatch.setattr(llm_factory, "create_llm_client", _none_factory)
    monkeypatch.setattr(
        draft_router_mod, "create_llm_client", _none_factory
    )

    resp = await app_client.get(
        f"/api/draft/projects/{pid}/status",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_draft_md_returns_404_before_compile(
    app_client, auth_token, isolated_data_dir
):
    pid = isolated_data_dir["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/draft.md",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 400 / 404 path handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_project_returns_404(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    stubbed_llm(_full_phase_script())
    resp = await app_client.post(
        "/api/draft/projects/does-not-exist/research",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_malformed_project_id_returns_400(
    app_client, auth_token, stubbed_llm
):
    stubbed_llm(_full_phase_script())
    resp = await app_client.post(
        "/api/draft/projects/has..bad..chars/research",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Happy path: each phase endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_endpoint_runs_and_returns_200(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    stubbed_llm(_full_phase_script()[:3])
    pid = isolated_data_dir["project_id"]
    resp = await app_client.post(
        f"/api/draft/projects/{pid}/research",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["phase"] == "research"
    assert body["status"] == "succeeded"
    assert body["summary"]["candidate_papers"] >= 1
    assert body["summary"]["paper_summaries"] >= 1


@pytest.mark.asyncio
async def test_structure_endpoint_runs(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    """Structure requires the research phase to have populated
    paper_summaries; run research first, then structure."""
    stubbed_llm(_full_phase_script()[:5])  # 3 research + 2 structure
    pid = isolated_data_dir["project_id"]
    r1 = await app_client.post(
        f"/api/draft/projects/{pid}/research",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert r1.status_code == 200, r1.text
    r2 = await app_client.post(
        f"/api/draft/projects/{pid}/structure",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["phase"] == "structure"
    assert body["status"] == "succeeded"
    assert body["summary"]["section_count"] >= 1


@pytest.mark.asyncio
async def test_compile_endpoint_produces_final_draft(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    """Compile requires all earlier phases; run them all first, then
    compile. The final draft should then be retrievable via draft.md."""
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]
    h = {"Authorization": f"Bearer {auth_token}"}
    for path in ("research", "structure", "compose", "validate", "compile"):
        r = await app_client.post(f"/api/draft/projects/{pid}/{path}", headers=h)
        assert r.status_code == 200, f"{path}: {r.text}"

    draft_resp = await app_client.get(f"/api/draft/projects/{pid}/draft.md", headers=h)
    assert draft_resp.status_code == 200, draft_resp.text
    assert len(draft_resp.text) > 0


@pytest.mark.asyncio
async def test_run_all_endpoint_runs_every_phase(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]
    resp = await app_client.post(
        f"/api/draft/projects/{pid}/run-all",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["phase"] == "run-all"
    # 5/5 dispatchable phases (EXPORT is a placeholder, not counted)
    assert body["progress_pct"] == 100.0
    # final_draft should exist
    assert body["summary"]["final_draft_chars"] > 0


# ---------------------------------------------------------------------------
# Status endpoint reflects progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_reflects_phase_progress(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    stubbed_llm(_full_phase_script()[:3])  # just research
    pid = isolated_data_dir["project_id"]
    h = {"Authorization": f"Bearer {auth_token}"}

    # Before: nothing done
    s0 = await app_client.get(f"/api/draft/projects/{pid}/status", headers=h)
    assert s0.json()["progress_pct"] == 0

    # After research: 1/5 dispatchable phases done (EXPORT is a
    # placeholder in the enum, not counted in progress_pct).
    await app_client.post(f"/api/draft/projects/{pid}/research", headers=h)
    s1 = await app_client.get(f"/api/draft/projects/{pid}/status", headers=h)
    assert s1.json()["progress_pct"] == 20.0
    assert s1.json()["phases"]["research"]["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Persistence: runner writes state, fresh runner reads it back
# ---------------------------------------------------------------------------


def test_runner_persists_state_to_disk(
    monkeypatch, isolated_data_dir, stubbed_llm
):
    from app.services.draft_pipeline import DraftContext, PhaseName
    from app.services.draft_pipeline.runner import DraftRunner

    pid = isolated_data_dir["project_id"]
    stubbed_llm(_full_phase_script()[:3])  # 3 research calls

    # First runner runs research and writes state
    from app.services import llm_factory
    from app.services.draft_pipeline.runner import DraftRunner as DR

    llm = llm_factory.create_llm_client()
    r1 = DR(project_id=pid, llm_client=llm)
    import asyncio
    asyncio.run(r1.run_phase(PhaseName.RESEARCH))
    assert r1.ctx.is_phase_done(PhaseName.RESEARCH)

    # Second runner loads from disk and sees the same state
    r2 = DR(project_id=pid, llm_client=None)
    assert r2.ctx.is_phase_done(PhaseName.RESEARCH)
    assert len(r2.ctx.candidate_papers) >= 1
    assert len(r2.ctx.paper_summaries) >= 1


def test_runner_handles_missing_state_file(monkeypatch, isolated_data_dir):
    """A fresh project with no draft_state.json produces a context
    seeded from the project metadata."""
    from app.services.draft_pipeline.runner import DraftRunner

    pid = isolated_data_dir["project_id"]
    r = DraftRunner(project_id=pid, llm_client=None)
    assert r.ctx.project_id == pid
    # The fixture project is freshly created with no papers in the
    # graph; the runner still seeds the topic from the project's
    # configured seed_paper_id and sets an empty list of node ids.
    assert r.ctx.topic  # something non-empty
    assert r.ctx.graph_node_ids == []
    assert r.ctx.phase_results == {}


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------


def test_runner_status_reports_last_error(monkeypatch, isolated_data_dir):
    """A failed phase should surface its error message via get_status()."""
    from app.services.draft_pipeline import PhaseName, PhaseStatus
    from app.services.draft_pipeline.runner import DraftRunner

    pid = isolated_data_dir["project_id"]
    r = DraftRunner(project_id=pid, llm_client=None)
    # Manually mark research as failed
    r.ctx.mark_phase(
        PhaseName.RESEARCH,
        PhaseStatus.FAILED,
        error="scout: paper search blew up",
    )
    status = r.get_status()
    assert "scout" in (status["last_error"] or "")
    assert status["phases"]["research"]["status"] == "failed"
    assert status["progress_pct"] == 0.0


def test_runner_status_after_successful_run(
    monkeypatch, isolated_data_dir, stubbed_llm
):
    """After a successful research run, status reports SUCCEEDED + 16.7%."""
    from app.services.draft_pipeline import PhaseName
    from app.services.draft_pipeline.runner import DraftRunner

    pid = isolated_data_dir["project_id"]
    stubbed_llm(_full_phase_script()[:3])
    from app.services import llm_factory

    r = DraftRunner(project_id=pid, llm_client=llm_factory.create_llm_client())
    import asyncio
    asyncio.run(r.run_phase(PhaseName.RESEARCH))

    status = r.get_status()
    assert status["progress_pct"] == 20.0  # 1/5 dispatchable phases
    assert status["phases"]["research"]["status"] == "succeeded"
    assert status["phases"]["structure"]["status"] == "pending"
    assert status["last_error"] is None
