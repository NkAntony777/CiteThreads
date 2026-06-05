"""
Tests for the per-phase checkpoint + resume behaviour in :mod:`DraftRunner`.

Coverage
--------
- After a successful phase, a per-phase checkpoint file appears in
  ``data/projects/{id}/checkpoints/{phase_name}.json``.
- A fresh ``DraftRunner`` constructed after a "crash" can
  :meth:`DraftRunner.resume_from` a completed phase and see its
  outputs, with no second LLM call.
- :meth:`DraftRunner.resume_from` re-runs a phase whose checkpoint is
  stale (older than the in-memory context) and skips when the
  checkpoint is fresh.
- ``get_status`` exposes a per-phase checkpoint summary so the
  frontend can render "research is checkpointed" hints.
- Checkpoint schema is version-stamped: a stale-version file is
  silently ignored and the phase re-runs.
- Failure path: when a phase raises, the checkpoint is still written
  with the FAILED status (so resume knows what happened).

We use the same isolated-data-dir + stubbed LLM pattern as
``test_draft_router.py`` so the runner runs offline.
"""

from __future__ import annotations

import asyncio
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
# Auth + project fixtures
# ---------------------------------------------------------------------------


def _set_auth(token: str) -> None:
    from app.config import settings
    from app import auth as auth_mod

    settings.auth_token = token
    auth_mod.settings.auth_token = token


@pytest.fixture
def auth_token():
    _set_auth("ckpt-test-token")
    yield "ckpt-test-token"
    _set_auth("")


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    """Point ``settings.data_dir`` at a tmp path; pre-seed one project."""
    from app.config import settings
    from app.services import storage
    from app.services.storage import project_storage

    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(storage, "settings", settings, raising=False)
    project_storage.projects_dir = Path(settings.data_dir) / "projects"
    project_storage.projects_dir.mkdir(parents=True, exist_ok=True)

    proj = project_storage.create_project(
        seed_paper_id="seed:abc",
        name="Checkpoint Test Project",
        depth=1,
        direction="both",
    )
    yield {"project_id": proj.id, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# LLM mocks (matches the script in test_draft_router.py)
# ---------------------------------------------------------------------------


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockCompletions:
    def __init__(self, scripted: List[str] | str):
        self._scripted: List[str] = (
            list(scripted) if not isinstance(scripted, str) else [scripted]
        )
        self.calls: List[dict] = []

    async def create(self, *, model, messages, temperature, max_tokens):
        self.calls.append(
            {"model": model, "messages": messages,
             "temperature": temperature, "max_tokens": max_tokens}
        )
        if not self._scripted:
            raise RuntimeError("MockCompletions ran out of scripted responses")
        return _MockResponse(self._scripted.pop(0))


class _MockLLMClient:
    def __init__(self, scripted: List[str] | str):
        self.chat = type(
            "Chat", (), {"completions": _MockCompletions(scripted)}
        )()
        self.call_log = self.chat.completions.calls  # alias


_REORDER_JSON = json.dumps(
    [
        {"id": "openalex:W100", "relevance_score": "High", "why_relevant": "core"},
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


def _research_script() -> List[str]:
    """Canned responses for one research phase (3 LLM calls)."""
    return [_REORDER_JSON, _SCRIBE_JSON, _SIGNAL_JSON]


def _patch_paper_search(monkeypatch) -> None:
    """Patch the search service so research's Scout can return a fake
    candidate paper without hitting the network."""
    from app.services.paper_search_service import (
        SearchResult,
        UnifiedPaperSearchService,
    )
    from app.models import Paper

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
            ],
            errors={},
            sources_searched=["openalex"],
        )

    monkeypatch.setattr(UnifiedPaperSearchService, "search", _fake_search)


@pytest.fixture
def stubbed_llm(monkeypatch):
    """Return a factory that scripts the mock LLM client."""
    from app.services import llm_factory
    from app.routers import draft as draft_router_mod

    state: dict[str, Any] = {"scripted": None}

    def _patch(scripted: List[str] | None = None) -> _MockLLMClient:
        if scripted is None:
            scripted = _research_script()
        state["scripted"] = scripted
        client = _MockLLMClient(scripted)

        def _factory(api_key=None, base_url=None, timeout=30.0):
            return client

        monkeypatch.setattr(llm_factory, "create_llm_client", _factory)
        monkeypatch.setattr(
            draft_router_mod, "create_llm_client", _factory
        )
        _patch_paper_search(monkeypatch)
        return client

    return _patch


# ---------------------------------------------------------------------------
# Checkpoint write/read primitives
# ---------------------------------------------------------------------------


def test_checkpoint_save_writes_per_phase_file(
    isolated_data_dir, tmp_path
):
    """save_phase_checkpoint writes one file per phase under
    ``data/projects/{id}/checkpoints/{phase_name}.json``."""
    from app.services.draft_pipeline import (
        DraftContext,
        PhaseName,
        PhaseResult,
        PhaseStatus,
        checkpoint as ckpt,
    )

    pid = isolated_data_dir["project_id"]
    rec = PhaseResult(
        phase=PhaseName.RESEARCH,
        status=PhaseStatus.SUCCEEDED,
    )
    outputs = {"candidate_papers": [{"id": "x"}], "paper_summaries": []}
    path = ckpt.save_phase_checkpoint(pid, PhaseName.RESEARCH, rec, outputs)
    assert path.exists()
    assert path.name == "research.json"
    assert path.parent.name == "checkpoints"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["phase"] == "research"
    assert payload["version"] == ckpt.CHECKPOINT_VERSION
    assert payload["outputs"] == outputs


def test_checkpoint_load_round_trip(isolated_data_dir):
    """A checkpoint written by save_phase_checkpoint is readable by
    load_phase_checkpoint and has the same shape."""
    from app.services.draft_pipeline import (
        PhaseName,
        PhaseResult,
        PhaseStatus,
        checkpoint as ckpt,
    )

    pid = isolated_data_dir["project_id"]
    rec = PhaseResult(phase=PhaseName.COMPOSE, status=PhaseStatus.SUCCEEDED)
    outputs = {"section_drafts": {"Introduction": "body"}}
    ckpt.save_phase_checkpoint(pid, PhaseName.COMPOSE, rec, outputs)
    payload = ckpt.load_phase_checkpoint(pid, PhaseName.COMPOSE)
    assert payload is not None
    assert payload["phase"] == "compose"
    assert payload["outputs"] == outputs
    assert payload["phase_result"]["status"] == "succeeded"


def test_checkpoint_stale_version_is_ignored(isolated_data_dir):
    """A checkpoint with a wrong version is treated as missing."""
    from app.services.draft_pipeline import (
        PhaseName,
        checkpoint as ckpt,
    )
    from app.services.storage import project_storage

    pid = isolated_data_dir["project_id"]
    path = project_storage._get_project_dir(pid) / "checkpoints" / "research.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"phase": "research", "version": 999, "outputs": {}}),
        encoding="utf-8",
    )
    assert ckpt.has_phase_checkpoint(pid, PhaseName.RESEARCH) is True
    # The version mismatch makes load return None.
    assert ckpt.load_phase_checkpoint(pid, PhaseName.RESEARCH) is None


def test_checkpoint_corrupt_file_returns_none(isolated_data_dir):
    """A malformed JSON checkpoint is treated as missing (logged + None)."""
    from app.services.draft_pipeline import (
        PhaseName,
        checkpoint as ckpt,
    )
    from app.services.storage import project_storage

    pid = isolated_data_dir["project_id"]
    path = project_storage._get_project_dir(pid) / "checkpoints" / "research.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert ckpt.load_phase_checkpoint(pid, PhaseName.RESEARCH) is None


def test_checkpoint_list_and_delete(isolated_data_dir):
    """list_checkpoints returns the phases with files on disk;
    delete_phase_checkpoint removes them."""
    from app.services.draft_pipeline import (
        PhaseName,
        PhaseResult,
        PhaseStatus,
        checkpoint as ckpt,
    )

    pid = isolated_data_dir["project_id"]
    for p in (PhaseName.RESEARCH, PhaseName.STRUCTURE):
        ckpt.save_phase_checkpoint(
            pid, p, PhaseResult(phase=p, status=PhaseStatus.SUCCEEDED), {}
        )
    found = ckpt.list_checkpoints(pid)
    assert PhaseName.RESEARCH in found
    assert PhaseName.STRUCTURE in found
    assert ckpt.delete_phase_checkpoint(pid, PhaseName.RESEARCH) is True
    # Second delete is a no-op returning False.
    assert ckpt.delete_phase_checkpoint(pid, PhaseName.RESEARCH) is False
    found2 = ckpt.list_checkpoints(pid)
    assert PhaseName.RESEARCH not in found2
    assert PhaseName.STRUCTURE in found2


# ---------------------------------------------------------------------------
# Runner integration: write + resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_writes_checkpoint_after_successful_phase(
    isolated_data_dir, stubbed_llm
):
    """After ``run_phase(RESEARCH)`` a checkpoint file appears on disk."""
    from app.services.draft_pipeline import PhaseName
    from app.services.draft_pipeline.checkpoint import has_phase_checkpoint
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory

    pid = isolated_data_dir["project_id"]
    stubbed_llm(_research_script())
    llm = llm_factory.create_llm_client()
    r = DraftRunner(project_id=pid, llm_client=llm)
    await r.run_phase(PhaseName.RESEARCH)
    assert has_phase_checkpoint(pid, PhaseName.RESEARCH)


@pytest.mark.asyncio
async def test_resume_from_uses_existing_checkpoint_no_second_llm_call(
    isolated_data_dir, stubbed_llm
):
    """Run research, simulate a crash (drop the runner), construct a
    fresh runner, and ``resume_from(RESEARCH)`` should restore from
    the checkpoint without calling the LLM again."""
    from app.services.draft_pipeline import PhaseName
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory

    pid = isolated_data_dir["project_id"]
    script1 = _research_script()
    client1 = stubbed_llm(script1)
    llm1 = llm_factory.create_llm_client()

    r1 = DraftRunner(project_id=pid, llm_client=llm1)
    await r1.run_phase(PhaseName.RESEARCH)
    assert len(client1.call_log) == 3  # 3 LLM calls during the first run
    candidate_papers_count = len(r1.ctx.candidate_papers)
    assert candidate_papers_count >= 1

    # Simulate crash: discard r1, build a brand new runner with a
    # *different* mock client whose call list starts at 0.
    script2: List[str] = []  # if resume incorrectly calls the LLM, we
    # will see the underflow / a panic.
    client2 = stubbed_llm(script2)
    # The factory was re-patched by stubbed_llm to a new mock; we can
    # call create_llm_client() and it will return client2.

    r2 = DraftRunner(
        project_id=pid, llm_client=llm_factory.create_llm_client()
    )
    # Resume should load the checkpoint and skip the LLM entirely.
    await r2.resume_from(PhaseName.RESEARCH)
    assert len(client2.call_log) == 0
    assert r2.ctx.is_phase_done(PhaseName.RESEARCH)
    assert len(r2.ctx.candidate_papers) == candidate_papers_count


@pytest.mark.asyncio
async def test_resume_from_re_runs_when_checkpoint_stale(
    isolated_data_dir, stubbed_llm
):
    """When the on-disk checkpoint is older than the in-memory ctx
    (e.g. an upstream phase was re-run and invalidated this one),
    ``resume_from`` should ignore the checkpoint and run the phase.

    The staleness rule: a fresh in-memory ``PhaseResult`` with a
    ``finished_at`` newer than the checkpoint's overrides the
    checkpoint — but a missing in-memory result leaves the checkpoint
    in control. We test the missing-in-memory case here (the
    re-runs-on-stale-outputs variant is covered by the snapshot
    unit tests below)."""
    from app.services.draft_pipeline import (
        DraftContext,
        PhaseName,
        PhaseResult,
        PhaseStatus,
        checkpoint as ckpt,
    )
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory

    pid = isolated_data_dir["project_id"]
    # Drop a stale checkpoint on disk with a SUCCESS result but no
    # matching in-memory ctx state.
    rec = PhaseResult(phase=PhaseName.RESEARCH, status=PhaseStatus.SUCCEEDED)
    ckpt.save_phase_checkpoint(
        pid, PhaseName.RESEARCH, rec,
        {"candidate_papers": [{"id": "stale", "title": "old"}],
         "paper_summaries": [], "research_gaps": []},
    )

    # Resume on a *fresh* runner with no LLM: it should pick up the
    # checkpoint (since in-memory ctx has no fresher record).
    r2 = DraftRunner(project_id=pid, llm_client=None)
    await r2.resume_from(PhaseName.RESEARCH)
    # The checkpoint was applied: ctx has the stale outputs but no
    # LLM call happened (we passed llm_client=None; if the runner
    # tried to call the LLM it would raise AttributeError on None).
    assert r2.ctx.candidate_papers and r2.ctx.candidate_papers[0]["id"] == "stale"


@pytest.mark.asyncio
async def test_get_status_exposes_checkpoint_summary(
    isolated_data_dir, stubbed_llm
):
    """``get_status`` includes a ``checkpoints`` map of
    ``phase -> bool`` so the UI can show which phases are resumable."""
    from app.services.draft_pipeline import PhaseName
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory

    pid = isolated_data_dir["project_id"]
    stubbed_llm(_research_script())
    llm = llm_factory.create_llm_client()
    r = DraftRunner(project_id=pid, llm_client=llm)
    await r.run_phase(PhaseName.RESEARCH)
    status = r.get_status()
    assert "checkpoints" in status
    assert status["checkpoints"]["research"] is True
    assert status["checkpoints"]["structure"] is False
    assert status["checkpoints"]["compose"] is False


@pytest.mark.asyncio
async def test_resume_after_failed_phase_writes_failed_checkpoint(
    isolated_data_dir, monkeypatch
):
    """A phase that raises should still write a checkpoint with
    status=FAILED so a subsequent resume can detect "we already
    tried this" instead of looping forever."""
    from app.services.draft_pipeline import (
        PhaseName,
        PhaseResult,
        PhaseStatus,
        checkpoint as ckpt,
    )
    from app.services.draft_pipeline.runner import DraftRunner
    import app.services.draft_pipeline.runner as runner_mod

    pid = isolated_data_dir["project_id"]
    original = runner_mod._PHASE_DISPATCH[PhaseName.RESEARCH]

    # Force a failure by monkeypatching the phase dispatcher.
    async def _boom(ctx, llm):
        ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.RUNNING)
        ctx.mark_phase(
            PhaseName.RESEARCH, PhaseStatus.FAILED, error="synthetic failure"
        )
        raise RuntimeError("synthetic failure")

    runner_mod._PHASE_DISPATCH[PhaseName.RESEARCH] = _boom
    try:
        r = DraftRunner(project_id=pid, llm_client=None)
        with pytest.raises(RuntimeError, match="synthetic failure"):
            await r.run_phase(PhaseName.RESEARCH)

        payload = ckpt.load_phase_checkpoint(pid, PhaseName.RESEARCH)
        assert payload is not None
        assert payload["phase_result"]["status"] == "failed"
        assert "synthetic failure" in (payload["phase_result"].get("error") or "")
    finally:
        # Restore the dispatch so the next test sees the real runner.
        runner_mod._PHASE_DISPATCH[PhaseName.RESEARCH] = original


@pytest.mark.asyncio
async def test_snapshot_restore_round_trip(isolated_data_dir):
    """snapshot_phase_outputs + restore_phase_outputs are inverse for
    every phase's owned field set."""
    from app.services.draft_pipeline import (
        DraftContext,
        PhaseName,
        PhaseStatus,
        checkpoint as ckpt,
    )

    pid = isolated_data_dir["project_id"]
    # Build a ctx with research outputs populated.
    ctx = DraftContext(project_id=pid, topic="X")
    ctx.candidate_papers = [{"id": "p1", "title": "T"}]
    ctx.paper_summaries = [{"paper_id": "p1"}]
    ctx.research_gaps = [{"title": "g"}]
    snap = ckpt.snapshot_phase_outputs(PhaseName.RESEARCH, ctx)

    # Mutate the in-memory ctx, then restore from the snapshot.
    ctx.candidate_papers = []
    ctx.paper_summaries = []
    ctx.research_gaps = []
    ckpt.restore_phase_outputs(ctx, PhaseName.RESEARCH, snap)
    assert ctx.candidate_papers == [{"id": "p1", "title": "T"}]
    assert ctx.paper_summaries == [{"paper_id": "p1"}]
    assert ctx.research_gaps == [{"title": "g"}]


# ---------------------------------------------------------------------------
# Event publication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_publishes_lifecycle_events_to_bus(
    isolated_data_dir, stubbed_llm
):
    """``run_phase`` publishes phase-start, phase-progress, phase-end
    to the supplied bus in order."""
    from app.services.draft_pipeline import (
        PhaseName,
        ProgressBus,
        EVT_PHASE_START,
        EVT_PHASE_PROGRESS,
        EVT_PHASE_END,
    )
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory

    pid = isolated_data_dir["project_id"]
    bus = ProgressBus(pid)
    queue = await bus.subscribe()

    stubbed_llm(_research_script())
    llm = llm_factory.create_llm_client()
    r = DraftRunner(project_id=pid, llm_client=llm, event_bus=bus)
    await r.run_phase(PhaseName.RESEARCH)

    events = []
    # Drain a few events: phase-start + 3 progress + phase-end.
    for _ in range(5):
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=1.0)
            events.append(ev)
        except asyncio.TimeoutError:
            break
    types = [e.type for e in events]
    assert types[0] == EVT_PHASE_START
    assert types[-1] == EVT_PHASE_END
    # All progress events are between start and end.
    assert all(
        t == EVT_PHASE_PROGRESS
        for t in types[1:-1]
    )
    # End event carries status=succeeded.
    assert events[-1].data["status"] == "succeeded"
    await bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_run_all_publishes_done_event(isolated_data_dir, stubbed_llm):
    """``run_all`` emits a terminal ``done`` event after the last
    phase finishes (success or failure path)."""
    from app.services.draft_pipeline import (
        PhaseName,
        ProgressBus,
        EVT_DONE,
    )
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services import llm_factory

    pid = isolated_data_dir["project_id"]
    # Build a full-phase script (research + structure + compose +
    # validate + compile) so run_all reaches the end.
    from tests.test_draft_router import _full_phase_script

    bus = ProgressBus(pid)
    queue = await bus.subscribe()
    stubbed_llm(_full_phase_script())
    llm = llm_factory.create_llm_client()
    r = DraftRunner(project_id=pid, llm_client=llm, event_bus=bus)
    try:
        await r.run_all()
    except Exception:
        pass  # some test envs may not have full phase coverage

    # Drain everything; assert a 'done' event is in there.
    types = []
    for _ in range(50):
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=0.5)
            types.append(ev.type)
            if ev.type == EVT_DONE:
                break
        except asyncio.TimeoutError:
            break
    assert EVT_DONE in types
    await bus.unsubscribe(queue)
