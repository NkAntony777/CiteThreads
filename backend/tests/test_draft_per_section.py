"""
Tests for the per-section regenerate + cancel endpoints (P1-2).

Covers:
- `POST /sections/{name}/regenerate` happy path (mocked LLM)
- `POST /sections/{name}/regenerate` 400 for unknown section
- `POST /sections/{name}/regenerate` 404 for not-yet-drafted section
- `POST /sections/{name}/regenerate` 503 when no LLM
- `POST /sections/{name}/regenerate` honours custom_instructions
  (asserts the prompt contains the user-supplied guidance)
- `POST /sections/{name}/regenerate` empty custom instructions
  behaves identically to no custom instructions
- `POST /cancel` sets `ctx.cancellation_requested` and persists it
- `POST /cancel` is idempotent (second call still returns 200)
- The runner honours `cancellation_requested` between phases
  (subsequent phases are SKIPPED)
- The runner honours the in-phase cancellation flag inside compose
  (a cancelled compose stops after the current section)
- The DraftRunner exposes `request_cancellation` and clears the flag
  on the next explicit `run_phase` call
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ---------------------------------------------------------------------------
# Auth + project fixtures
# ---------------------------------------------------------------------------


def _set_auth(token: str) -> None:
    from app.config import settings
    from app import auth as auth_mod

    settings.auth_token = token
    auth_mod.settings.auth_token = token


@pytest.fixture
def auth_token():
    _set_auth("per-section-token")
    yield "per-section-token"
    _set_auth("")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import storage
    from app.services.storage import project_storage

    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(storage, "settings", settings, raising=False)
    project_storage.projects_dir = Path(settings.data_dir) / "projects"
    project_storage.projects_dir.mkdir(parents=True, exist_ok=True)

    proj = project_storage.create_project(
        seed_paper_id="seed:abc",
        name="Per-Section Test Project",
        depth=1,
        direction="both",
    )
    yield {"project_id": proj.id, "tmp_path": tmp_path}


@pytest.fixture
async def app_client():
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# LLM mocks
# ---------------------------------------------------------------------------


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockCompletions:
    """Captures every call so tests can inspect messages."""

    def __init__(self, scripted: List[str], on_call=None):
        self._scripted: List[str] = list(scripted)
        self.calls: List[dict] = []
        self._on_call = on_call

    async def create(self, *, model, messages, temperature, max_tokens):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self._on_call is not None:
            try:
                self._on_call(len(self.calls))
            except Exception:
                pass
        if not self._scripted:
            return _MockResponse("")
        return _MockResponse(self._scripted.pop(0))


class _MockLLMClient:
    def __init__(self, scripted: List[str], on_call=None):
        self.chat = type(
            "Chat", (), {"completions": _MockCompletions(scripted, on_call)}
        )()


def _full_phase_script() -> List[str]:
    """Canned responses for one full run: research (3) + structure (2) +
    compose (6 crafter calls). Returns the 3 + 2 + 6 = 11 strings."""
    reorder = json.dumps(
        [
            {"id": "openalex:W100", "relevance_score": "High", "why_relevant": "core"},
        ]
    )
    scribe = json.dumps(
        [
            {
                "paper_id": "openalex:W100",
                "research_question": "rq",
                "methodology": "m",
                "key_findings": ["k1"],
                "implications": "i",
                "limitations": ["l"],
                "relevance_score": 4,
                "relevance_reason": "ok",
            }
        ]
    )
    signal = json.dumps(
        {
            "gaps": [
                {
                    "title": "g1",
                    "description": "d",
                    "gap_type": "methodological",
                    "difficulty": "Low",
                    "impact": 3,
                    "suggested_approach": "sa",
                }
            ],
            "emerging_trends": [],
            "novel_angles": [],
        }
    )
    architect = json.dumps(
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
    formatter = json.dumps(
        {
            "format_name": "IMRaD",
            "target_venue": "Nature",
            "manuscript_spec": {"font": "Times"},
            "outline_markdown": "# Paper\n\n## Intro\nTarget: 600\n",
        }
    )
    crafter = (
        '```json\n{"body": "## Introduction\\n\\nThis is the intro '
        '[@openalex:W100].\\n", "headings": ["Introduction"], '
        '"paper_ids_cited": ["openalex:W100"], "tables": []}\n```'
    )
    return [reorder, scribe, signal, architect, formatter] + [crafter] * 6


def _regen_script() -> str:
    """Single response used by a per-section regenerate call."""
    return (
        '```json\n{"body": "## Introduction\\n\\nRegenerated intro '
        '[@openalex:W100].\\n", "headings": ["Introduction"], '
        '"paper_ids_cited": ["openalex:W100"], "tables": []}\n```'
    )


@pytest.fixture
def stubbed_llm(monkeypatch):
    """Patch the LLM factory and paper search so phases can run offline."""
    from app.services import llm_factory
    from app.routers import draft as draft_router_mod
    from app.services.paper_search_service import (
        SearchResult,
        UnifiedPaperSearchService,
    )
    from app.models import Paper

    state: dict = {"client": None}

    def _patch(scripted: List[str] | None = None) -> _MockLLMClient:
        if scripted is None:
            scripted = _full_phase_script()
        client = _MockLLMClient(scripted)
        state["client"] = client

        def _factory(api_key=None, base_url=None, timeout=30.0):
            return client

        monkeypatch.setattr(llm_factory, "create_llm_client", _factory)
        monkeypatch.setattr(
            draft_router_mod, "create_llm_client", _factory
        )

        async def _fake_search(self, query, sources=None, filters=None, limit=20):
            return SearchResult(
                papers=[
                    Paper(
                        id="openalex:W100",
                        title="Foundations",
                        authors=["Alice"],
                        year=2023,
                        abstract="abs",
                    )
                ],
                errors={},
                sources_searched=["openalex"],
            )

        monkeypatch.setattr(UnifiedPaperSearchService, "search", _fake_search)
        return client

    return _patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_full_pipeline(app_client, auth_token, pid) -> None:
    """Run research + structure + compose so a section has a draft to regen."""
    h = {"Authorization": f"Bearer {auth_token}"}
    for path in ("research", "structure", "compose"):
        r = await app_client.post(f"/api/draft/projects/{pid}/{path}", headers=h)
        assert r.status_code == 200, f"{path}: {r.text}"


# ---------------------------------------------------------------------------
# 1) Per-section regenerate: happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_section_happy_path(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    client = stubbed_llm(_full_phase_script() + [_regen_script()])
    pid = isolated_data_dir["project_id"]
    await _run_full_pipeline(app_client, auth_token, pid)

    resp = await app_client.post(
        f"/api/draft/projects/{pid}/sections/introduction/regenerate",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"custom_instructions": "Make it tighter."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["project_id"] == pid
    assert body["section"] == "introduction"
    assert "Regenerated intro" in body["body"]
    assert body["body_chars"] == len(body["body"])


@pytest.mark.asyncio
async def test_regenerate_section_404_when_not_yet_drafted(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]
    # Don't run the pipeline — section_drafts is empty.

    resp = await app_client.post(
        f"/api/draft/projects/{pid}/sections/introduction/regenerate",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404
    assert "compose" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_regenerate_section_400_for_unknown_section(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]
    await _run_full_pipeline(app_client, auth_token, pid)

    resp = await app_client.post(
        f"/api/draft/projects/{pid}/sections/not_a_section/regenerate",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400
    assert "unknown section" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_regenerate_section_503_when_no_llm(
    app_client, auth_token, isolated_data_dir, stubbed_llm, monkeypatch
):
    from app.services import llm_factory
    from app.routers import draft as draft_router_mod

    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]
    await _run_full_pipeline(app_client, auth_token, pid)

    def _none_factory(api_key=None, base_url=None, timeout=30.0):
        return None

    monkeypatch.setattr(llm_factory, "create_llm_client", _none_factory)
    monkeypatch.setattr(
        draft_router_mod, "create_llm_client", _none_factory
    )

    resp = await app_client.post(
        f"/api/draft/projects/{pid}/sections/introduction/regenerate",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_regenerate_section_honours_custom_instructions(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    """The custom instructions text should appear in the prompt sent
    to the LLM. We assert the captured user message contains it."""
    client = stubbed_llm(_full_phase_script() + [_regen_script()])
    pid = isolated_data_dir["project_id"]
    await _run_full_pipeline(app_client, auth_token, pid)

    custom_text = "USE MORE TECHNICAL VOCABULARY"
    resp = await app_client.post(
        f"/api/draft/projects/{pid}/sections/introduction/regenerate",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"custom_instructions": custom_text},
    )
    assert resp.status_code == 200, resp.text

    # Inspect the captured LLM call (the last one) for the custom text.
    assert client.chat.completions.calls, "LLM was not called"
    last_call = client.chat.completions.calls[-1]
    user_msg = last_call["messages"][-1]["content"]
    assert custom_text in user_msg


@pytest.mark.asyncio
async def test_regenerate_section_empty_custom_instructions_uses_default_prompt(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    """Empty / whitespace custom_instructions should NOT inject the
    'ADDITIONAL REWRITE GUIDANCE' block. We assert the prompt only
    carries the default rewrite instructions."""
    client = stubbed_llm(_full_phase_script() + [_regen_script()])
    pid = isolated_data_dir["project_id"]
    await _run_full_pipeline(app_client, auth_token, pid)

    resp = await app_client.post(
        f"/api/draft/projects/{pid}/sections/introduction/regenerate",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"custom_instructions": "   "},
    )
    assert resp.status_code == 200, resp.text
    last_call = client.chat.completions.calls[-1]
    user_msg = last_call["messages"][-1]["content"]
    assert "ADDITIONAL REWRITE GUIDANCE" not in user_msg


# ---------------------------------------------------------------------------
# 2) Cancel endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_endpoint_sets_flag(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]

    resp = await app_client.post(
        f"/api/draft/projects/{pid}/cancel",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cancelled"] is True
    assert body["project_id"] == pid


@pytest.mark.asyncio
async def test_cancel_endpoint_is_idempotent(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]

    r1 = await app_client.post(
        f"/api/draft/projects/{pid}/cancel",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    r2 = await app_client.post(
        f"/api/draft/projects/{pid}/cancel",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["cancelled"] is True
    assert r2.json()["cancelled"] is True


@pytest.mark.asyncio
async def test_cancel_endpoint_persists_flag(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    """After cancel, a fresh DraftRunner (new request) should still
    see the flag set on the persisted state."""
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]

    resp = await app_client.post(
        f"/api/draft/projects/{pid}/cancel",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200

    from app.services.draft_pipeline.runner import DraftRunner

    r = DraftRunner(project_id=pid, llm_client=None)
    assert r.ctx.cancellation_requested is True


@pytest.mark.asyncio
async def test_runner_marks_phase_skipped_when_flag_set(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    """When cancellation is set, calling run_phase should mark the
    phase SKIPPED and not invoke the phase function."""
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]
    # Mark cancelled first.
    await app_client.post(
        f"/api/draft/projects/{pid}/cancel",
        headers={"Authorization": f"Bearer {auth_token}"},
    )

    from app.services.draft_pipeline import PhaseName, PhaseStatus
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory

    runner = DraftRunner(
        project_id=pid,
        llm_client=llm_factory.create_llm_client(),
    )
    assert runner.ctx.cancellation_requested is True
    await runner.run_phase(PhaseName.RESEARCH)
    rec = runner.ctx.phase_results.get(PhaseName.RESEARCH)
    assert rec is not None
    assert rec.status == PhaseStatus.SKIPPED
    assert "cancel" in (rec.error or "").lower()


@pytest.mark.asyncio
async def test_runner_clears_flag_on_next_explicit_run(
    app_client, auth_token, isolated_data_dir, stubbed_llm
):
    """After cancellation, the runner's ``clear_cancellation()`` call
    resets the flag, and the next ``run_phase`` invocation runs the
    phase normally (rather than skipping it)."""
    stubbed_llm(_full_phase_script())
    pid = isolated_data_dir["project_id"]
    await app_client.post(
        f"/api/draft/projects/{pid}/cancel",
        headers={"Authorization": f"Bearer {auth_token}"},
    )

    from app.services.draft_pipeline import PhaseName, PhaseStatus
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory

    runner = DraftRunner(
        project_id=pid,
        llm_client=llm_factory.create_llm_client(),
    )
    assert runner.ctx.cancellation_requested is True

    # First run sees the flag and SKIPS.
    await runner.run_phase(PhaseName.RESEARCH)
    rec = runner.ctx.phase_results.get(PhaseName.RESEARCH)
    assert rec.status == PhaseStatus.SKIPPED

    # Operator explicitly clears the flag and runs again.
    runner.clear_cancellation()
    assert runner.ctx.cancellation_requested is False
    await runner.run_phase(PhaseName.RESEARCH)
    rec = runner.ctx.phase_results.get(PhaseName.RESEARCH)
    assert rec.status == PhaseStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_compose_phase_honours_inflight_cancellation(
    app_client, auth_token, isolated_data_dir, stubbed_llm, monkeypatch
):
    """If cancellation is set during a compose run, the next
    sub-section boundary raises and the phase is marked FAILED with
    the cancellation reason.

    We use the mock LLM's ``on_call`` hook to set the cancellation
    flag on the runner's context as soon as the first crafter call
    completes; the in-loop check between crafter calls then raises."""
    pid = isolated_data_dir["project_id"]
    h = {"Authorization": f"Bearer {auth_token}"}

    from app.services.draft_pipeline import PhaseName, PhaseStatus
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory as _llmf
    from app.routers import draft as draft_router_mod

    runner_holder: dict = {}

    def _on_call(n: int) -> None:
        # 3 research + 2 structure + 1 crafter = 6th call is the first
        # compose crafter. After that fires, set the flag so the next
        # sub-section boundary raises.
        if n == 6 and runner_holder.get("runner") is not None:
            if not runner_holder["runner"].ctx.cancellation_requested:
                runner_holder["runner"].ctx.cancellation_requested = True

    client = _MockLLMClient(_full_phase_script(), on_call=_on_call)

    def _factory(api_key=None, base_url=None, timeout=30.0):
        return client

    monkeypatch.setattr(_llmf, "create_llm_client", _factory)
    monkeypatch.setattr(
        draft_router_mod, "create_llm_client", _factory
    )

    # Run research + structure to populate context.
    await app_client.post(f"/api/draft/projects/{pid}/research", headers=h)
    await app_client.post(f"/api/draft/projects/{pid}/structure", headers=h)

    # Build a fresh runner with the same mock client.
    runner = DraftRunner(project_id=pid, llm_client=client)
    runner_holder["runner"] = runner

    # Run compose. After the first crafter call, the flag will be set;
    # the in-loop check between crafter calls raises.
    import pytest as _pytest
    with _pytest.raises(Exception) as excinfo:
        await runner.run_phase(PhaseName.COMPOSE)
    rec = runner.ctx.phase_results.get(PhaseName.COMPOSE)
    assert rec is not None
    assert rec.status == PhaseStatus.FAILED
    assert "cancel" in (rec.error or "").lower()
    assert "cancel" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def test_runner_request_cancellation_sets_flag(monkeypatch, isolated_data_dir):
    """Direct API: DraftRunner.request_cancellation() flips the flag
    on the loaded context."""
    from app.services.draft_pipeline.runner import DraftRunner

    pid = isolated_data_dir["project_id"]
    r = DraftRunner(project_id=pid, llm_client=None)
    assert r.ctx.cancellation_requested is False
    r.request_cancellation()
    assert r.ctx.cancellation_requested is True
    r.request_cancellation()  # idempotent
    assert r.ctx.cancellation_requested is True
    r.clear_cancellation()
    assert r.ctx.cancellation_requested is False
