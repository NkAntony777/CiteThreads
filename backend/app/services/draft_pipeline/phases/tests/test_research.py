"""Tests for the research phase (Scout + Scribe + Signal + orchestrator).

These tests mock both the LLM client and the paper_search_service so
they run offline and deterministic.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List

import pytest

from app.models import Paper
from app.services.draft_pipeline import (
    CitationStyle,
    DraftContext,
    PhaseName,
    PhaseStatus,
)
from app.services.draft_pipeline.phases import (
    run_research_phase,
    scout,
    scribe,
    signal,
    CandidatePaper,
    PaperSummary,
    ResearchGap,
    ScoutResult,
    ScribeResult,
    SignalResult,
)
from app.services.draft_pipeline.phases.research import (
    _parse_rerank_json,
    _parse_scribe_json,
    _parse_signal_json,
    _format_candidates_for_prompt,
    _paper_to_candidate,
    _truncate,
    _already_known_ids,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockCompletions:
    def __init__(self, scripted: List[str]):
        self._scripted = list(scripted)
        self.calls: List[dict] = []

    async def create(self, *, model: str, messages, temperature, max_tokens):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if not self._scripted:
            raise RuntimeError("MockCompletions ran out of scripted responses")
        return _MockResponse(self._scripted.pop(0))


class _MockLLMClient:
    def __init__(self, scripted: List[str] | str):
        if isinstance(scripted, str):
            scripted = [scripted]
        self.chat = type("Chat", (), {"completions": _MockCompletions(scripted)})()


# ---- paper_search_service mock -------------------------------------------


class _MockSearchResult:
    def __init__(self, papers, errors=None, sources_searched=None):
        self.papers = papers
        self.errors = errors or {}
        self.sources_searched = sources_searched or ["openalex", "semantic_scholar", "arxiv"]
        self.total = len(papers)


def _make_paper(pid: str, *, title: str = "T", year: int = 2023, authors=None) -> Paper:
    return Paper(
        id=pid,
        title=f"{title} {pid}",
        authors=authors or ["Alice Smith", "Bob Jones"],
        year=year,
        venue="Test Journal",
        abstract=f"Abstract of {pid}",
        doi=f"10.1234/{pid}",
        citation_count=42,
    )


@pytest.fixture
def mock_paper_search(monkeypatch):
    """Replace paper_search_service.search with a fixture-driven mock."""

    async def _search(query, sources=None, filters=None, limit=20):
        papers = [
            _make_paper(f"openalex:W1{i}", title="Foundations of X", year=2023)
            for i in range(3)
        ] + [
            _make_paper(f"arxiv:{2401 + i:05d}", title="Recent advances", year=2024)
            for i in range(2)
        ] + [
            _make_paper(f"s2:hash{i}", title="Survey", year=2022)
            for i in range(2)
        ]
        return _MockSearchResult(papers, sources_searched=["openalex", "arxiv", "s2"])

    # Patch the symbol the phase module imported
    from app.services.draft_pipeline.phases import research as research_module
    monkeypatch.setattr(research_module.paper_search_service, "search", _search)
    return _search


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_truncate_under_limit():
    assert _truncate("short", 100) == "short"


def test_truncate_over_limit():
    out = _truncate("a" * 100, 10)
    assert out.endswith("...")
    assert len(out) == 10


def test_truncate_none_returns_empty():
    assert _truncate(None, 100) == ""


def test_paper_to_candidate_extracts_source_from_id():
    p = _make_paper("arxiv:2401.12345")
    c = _paper_to_candidate(p, source_api="arxiv")
    assert c.paper_id == "arxiv:2401.12345"
    assert c.source_api == "arxiv"
    assert c.year == 2023
    assert c.citation_count == 42


def test_paper_to_candidate_infers_source_when_empty():
    p = _make_paper("openalex:W123")
    c = _paper_to_candidate(p)
    assert c.source_api == "openalex"


def test_already_known_ids_unions_references_and_graph():
    ctx = DraftContext(
        project_id="p",
        topic="t",
        reference_ids=["ref1", "ref2"],
        graph_node_ids=["g1", "ref1"],
    )
    assert _already_known_ids(ctx) == {"ref1", "ref2", "g1"}


def test_format_candidates_for_prompt_is_valid_json():
    cands = [
        CandidatePaper(paper_id="x", title="X", authors=["A"], year=2023, abstract="abs"),
        CandidatePaper(paper_id="y", title="Y", authors=["B"], year=2024, abstract=""),
    ]
    s = _format_candidates_for_prompt(cands)
    parsed = json.loads(s)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "x"


# ---------------------------------------------------------------------------
# JSON parser unit tests
# ---------------------------------------------------------------------------


def test_parse_rerank_json_strips_fences():
    raw = '```json\n[{"id":"a","relevance_score":"High","why_relevant":"r"}]\n```'
    rows = _parse_rerank_json(raw)
    assert len(rows) == 1
    assert rows[0].paper_id == "a"
    assert rows[0].relevance_score == "High"


def test_parse_rerank_json_handles_surrounding_prose():
    raw = (
        "Here is the ranking you asked for:\n"
        '[{"id":"a","relevance_score":"Medium","why_relevant":"r1"}]\n'
        "Hope that helps!"
    )
    rows = _parse_rerank_json(raw)
    assert len(rows) == 1
    assert rows[0].relevance_score == "Medium"


def test_parse_rerank_json_normalizes_unknown_score():
    raw = '[{"id":"a","relevance_score":"very-high","why_relevant":"r"}]'
    rows = _parse_rerank_json(raw)
    assert rows[0].relevance_score == "Medium"  # falls back to default


def test_parse_rerank_json_empty_on_garbage():
    assert _parse_rerank_json("not json at all") == []
    assert _parse_rerank_json("") == []


def test_parse_scribe_json_basic():
    batch = [
        CandidatePaper(paper_id="a", title="A", authors=["X"], year=2023),
        CandidatePaper(paper_id="b", title="B", authors=["Y"], year=2024),
    ]
    raw = json.dumps(
        [
            {
                "paper_id": "a",
                "research_question": "Q?",
                "methodology": "M",
                "key_findings": ["f1", "f2"],
                "implications": "I",
                "limitations": ["L"],
                "relevance_score": 4,
                "relevance_reason": "R",
            },
            {
                "paper_id": "b",
                "research_question": "Q2?",
                "methodology": "M2",
                "key_findings": ["g1"],
                "implications": "I2",
                "limitations": [],
                "relevance_score": 5,
                "relevance_reason": "R2",
            },
        ]
    )
    summaries = _parse_scribe_json(raw, batch)
    assert len(summaries) == 2
    assert summaries[0].paper_id == "a"
    assert summaries[0].key_findings == ["f1", "f2"]
    assert summaries[1].relevance_score == 5


def test_parse_scribe_json_clamps_relevance_score():
    batch = [CandidatePaper(paper_id="a", title="A", authors=[], year=None)]
    raw = json.dumps([{"paper_id": "a", "relevance_score": 99}])
    summaries = _parse_scribe_json(raw, batch)
    assert summaries[0].relevance_score == 5  # clamped


def test_parse_signal_json_full():
    raw = json.dumps(
        {
            "gaps": [
                {
                    "title": "G1",
                    "description": "D1",
                    "gap_type": "methodological",
                    "difficulty": "High",
                    "impact": 5,
                    "suggested_approach": "Try X",
                }
            ],
            "emerging_trends": ["T1", "T2"],
            "novel_angles": ["A1"],
        }
    )
    parsed = _parse_signal_json(raw)
    assert len(parsed["gaps"]) == 1
    assert parsed["gaps"][0].title == "G1"
    assert parsed["gaps"][0].impact == 5
    assert parsed["emerging_trends"] == ["T1", "T2"]
    assert parsed["novel_angles"] == ["A1"]


def test_parse_signal_json_with_fences():
    raw = '```json\n{"gaps": [], "emerging_trends": [], "novel_angles": []}\n```'
    parsed = _parse_signal_json(raw)
    assert parsed == {"gaps": [], "emerging_trends": [], "novel_angles": []}


def test_parse_signal_json_garbage_returns_empty():
    parsed = _parse_signal_json("totally not json")
    assert parsed == {"gaps": [], "emerging_trends": [], "novel_angles": []}


# ---------------------------------------------------------------------------
# Scout tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scout_returns_candidates_from_search(mock_paper_search):
    ctx = DraftContext(project_id="p1", topic="Neural networks for X")
    result = await scout(ctx, llm_client=None, use_llm_rerank=False)
    assert isinstance(result, ScoutResult)
    assert len(result.candidates) == 7  # 3 + 2 + 2 from the mock
    # All candidates have basic metadata
    for c in result.candidates:
        assert c.title
        assert c.authors
        assert c.year is not None


@pytest.mark.asyncio
async def test_scout_excludes_known_paper_ids(mock_paper_search):
    ctx = DraftContext(
        project_id="p1",
        topic="X",
        reference_ids=["openalex:W10"],  # first one is in the mock
    )
    result = await scout(ctx, llm_client=None, use_llm_rerank=False)
    assert all(c.paper_id != "openalex:W10" for c in result.candidates)


@pytest.mark.asyncio
async def test_scout_gracefully_handles_search_exception(monkeypatch):
    from app.services.draft_pipeline.phases import research as research_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(research_module.paper_search_service, "search", _boom)
    ctx = DraftContext(project_id="p1", topic="X")
    result = await scout(ctx, llm_client=None, use_llm_rerank=False)
    assert result.candidates == []
    assert "search" in result.errors
    assert "network down" in result.errors["search"]


@pytest.mark.asyncio
async def test_scout_rerank_sorts_high_first(mock_paper_search):
    scripted = json.dumps(
        [
            {"id": "openalex:W11", "relevance_score": "Low", "why_relevant": "weak"},
            {"id": "openalex:W10", "relevance_score": "High", "why_relevant": "core"},
            {"id": "s2:hash0", "relevance_score": "Medium", "why_relevant": "ok"},
        ]
    )
    client = _MockLLMClient([scripted])
    ctx = DraftContext(project_id="p1", topic="X")
    result = await scout(ctx, llm_client=client, use_llm_rerank=True)
    # First three candidates in mock: W10, W11, W12 → after rerank, the
    # scores are Low/High/Low respectively, so the order in result
    # depends on mock ordering + sort. At minimum, the High one comes
    # before the Low ones.
    high_cands = [c for c in result.candidates if c.relevance_score == "High"]
    low_cands = [c for c in result.candidates if c.relevance_score == "Low"]
    assert high_cands and low_cands
    high_idx = result.candidates.index(high_cands[0])
    low_idx = result.candidates.index(low_cands[0])
    assert high_idx < low_idx


@pytest.mark.asyncio
async def test_scout_rerank_fallback_when_llm_returns_garbage(mock_paper_search):
    client = _MockLLMClient(["this is not json at all"])
    ctx = DraftContext(project_id="p1", topic="X")
    result = await scout(ctx, llm_client=client, use_llm_rerank=True)
    # Graceful degradation: no scores set, candidates returned as-is
    assert len(result.candidates) == 7
    for c in result.candidates:
        assert c.relevance_score == "Medium"


# ---------------------------------------------------------------------------
# Scribe tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scribe_returns_summaries_per_paper():
    candidates = [
        CandidatePaper(
            paper_id="a", title="Paper A", authors=["X"], year=2023, doi="10.1/a"
        ),
        CandidatePaper(
            paper_id="b", title="Paper B", authors=["Y"], year=2024, doi="10.1/b"
        ),
    ]
    scripted = json.dumps(
        [
            {
                "paper_id": "a",
                "research_question": "Q1?",
                "methodology": "M1",
                "key_findings": ["f1"],
                "implications": "I1",
                "limitations": ["L1"],
                "relevance_score": 4,
                "relevance_reason": "R1",
            },
            {
                "paper_id": "b",
                "research_question": "Q2?",
                "methodology": "M2",
                "key_findings": ["f2", "f3"],
                "implications": "I2",
                "limitations": [],
                "relevance_score": 5,
                "relevance_reason": "R2",
            },
        ]
    )
    client = _MockLLMClient([scripted])
    ctx = DraftContext(project_id="p", topic="T")
    result = await scribe(ctx, candidates, client, batch_size=2, max_batches=1)
    assert len(result.summaries) == 2
    assert result.summaries[0].paper_id == "a"
    assert result.summaries[1].key_findings == ["f2", "f3"]


@pytest.mark.asyncio
async def test_scribe_empty_candidates_returns_empty():
    client = _MockLLMClient([])
    ctx = DraftContext(project_id="p", topic="T")
    result = await scribe(ctx, [], client)
    assert result.summaries == []


@pytest.mark.asyncio
async def test_scribe_caps_at_max_batches():
    candidates = [
        CandidatePaper(paper_id=f"p{i}", title=f"T{i}", authors=[], year=2024)
        for i in range(20)
    ]
    # Scripted as TWO separate batches because the scribe function
    # awaits multiple _scribe_one_batch calls when max_batches > 1.
    batch1 = json.dumps(
        [
            {
                "paper_id": f"p{i}",
                "research_question": "Q",
                "methodology": "M",
                "key_findings": ["f"],
                "implications": "I",
                "limitations": [],
                "relevance_score": 3,
                "relevance_reason": "R",
            }
            for i in range(5)
        ]
    )
    batch2 = json.dumps(
        [
            {
                "paper_id": f"p{i}",
                "research_question": "Q",
                "methodology": "M",
                "key_findings": ["f"],
                "implications": "I",
                "limitations": [],
                "relevance_score": 3,
                "relevance_reason": "R",
            }
            for i in range(5, 10)
        ]
    )
    client = _MockLLMClient([batch1, batch2])
    ctx = DraftContext(project_id="p", topic="T")
    result = await scribe(ctx, candidates, client, batch_size=5, max_batches=2)
    assert len(result.summaries) == 10  # capped, not 20


# ---------------------------------------------------------------------------
# Signal tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_returns_gaps_and_trends():
    summaries = [
        PaperSummary(
            paper_id="a",
            title="A",
            authors=[],
            year=2023,
            research_question="Q",
            key_findings=["f"],
            limitations=["L"],
        ),
        PaperSummary(
            paper_id="b",
            title="B",
            authors=[],
            year=2024,
            research_question="Q2",
            key_findings=["g"],
            limitations=[],
        ),
    ]
    scripted = json.dumps(
        {
            "gaps": [
                {
                    "title": "G1",
                    "description": "D1",
                    "gap_type": "empirical",
                    "difficulty": "Medium",
                    "impact": 4,
                    "suggested_approach": "Try Y",
                }
            ],
            "emerging_trends": ["T1"],
            "novel_angles": ["A1", "A2"],
        }
    )
    client = _MockLLMClient([scripted])
    ctx = DraftContext(project_id="p", topic="T")
    result = await signal(ctx, summaries, client)
    assert len(result.gaps) == 1
    assert result.gaps[0].gap_type == "empirical"
    assert result.emerging_trends == ["T1"]
    assert result.novel_angles == ["A1", "A2"]


@pytest.mark.asyncio
async def test_signal_empty_summaries_returns_empty():
    client = _MockLLMClient([])
    ctx = DraftContext(project_id="p", topic="T")
    result = await signal(ctx, [], client)
    assert result.gaps == []
    assert result.emerging_trends == []
    assert result.novel_angles == []
    # No LLM call should have been made
    assert client.chat.completions.calls == []


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_research_phase_full_pipeline(mock_paper_search):
    rerank = json.dumps(
        [
            {"id": f"openalex:W1{i}", "relevance_score": "High", "why_relevant": "core"}
            for i in range(3)
        ]
        + [
            {"id": f"arxiv:{2401 + i:05d}", "relevance_score": "Medium", "why_relevant": "ok"}
            for i in range(2)
        ]
        + [
            {"id": f"s2:hash{i}", "relevance_score": "Low", "why_relevant": "weak"}
            for i in range(2)
        ]
    )
    scribe_payload = json.dumps(
        [
            {
                "paper_id": f"openalex:W1{i}",
                "research_question": "Q?",
                "methodology": "M",
                "key_findings": ["f"],
                "implications": "I",
                "limitations": [],
                "relevance_score": 4,
                "relevance_reason": "R",
            }
            for i in range(3)
        ]
    )
    scribe_payload_2 = json.dumps(
        [
            {
                "paper_id": "openalex:W11",
                "research_question": "Q?",
                "methodology": "M",
                "key_findings": ["f"],
                "implications": "I",
                "limitations": [],
                "relevance_score": 3,
                "relevance_reason": "R",
            }
        ]
    )
    signal_payload = json.dumps(
        {
            "gaps": [
                {
                    "title": "G1",
                    "description": "D1",
                    "gap_type": "methodological",
                    "difficulty": "High",
                    "impact": 4,
                    "suggested_approach": "Try Z",
                }
            ],
            "emerging_trends": ["T1"],
            "novel_angles": ["A1"],
        }
    )
    # 7 candidates → Scout rerank (1) + Scribe 2 batches (2) + Signal (1) = 4 calls
    client = _MockLLMClient([rerank, scribe_payload, scribe_payload_2, signal_payload])
    ctx = DraftContext(project_id="p1", topic="Test topic", target_word_count=5000)
    result_ctx = await run_research_phase(ctx, llm_client=client)

    # 1. Phase marked SUCCEEDED
    assert result_ctx.is_phase_done(PhaseName.RESEARCH) is True
    assert result_ctx.progress_pct() == 20.0  # 1 of 5 dispatchable phases (EXPORT is a placeholder)

    # 2. Scout results
    assert len(result_ctx.candidate_papers) == 7
    # 3. Scribe results (only first 3 candidates due to cap=3 for first batch)
    assert len(result_ctx.paper_summaries) >= 1
    # 4. Signal results
    assert len(result_ctx.research_gaps) == 1
    assert result_ctx.research_gaps[0]["title"] == "G1"


@pytest.mark.asyncio
async def test_run_research_phase_without_llm_skips_llm_steps(mock_paper_search):
    ctx = DraftContext(project_id="p1", topic="T")
    result_ctx = await run_research_phase(ctx, llm_client=None)

    # Phase still marked done (Scout doesn't need LLM)
    assert result_ctx.is_phase_done(PhaseName.RESEARCH) is True
    assert len(result_ctx.candidate_papers) == 7
    # But no summaries or gaps because no LLM
    assert result_ctx.paper_summaries == []
    assert result_ctx.research_gaps == []


@pytest.mark.asyncio
async def test_run_research_phase_records_failure(mock_paper_search):
    """If a sub-phase raises, the RESEARCH phase is marked FAILED and
    the exception is re-raised."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    # Force scribe to fail by giving it bad LLM
    class _FailClient:
        class chat:
            class completions:
                async def create(*a, **k):
                    raise RuntimeError("synthetic failure")

    ctx = DraftContext(project_id="p1", topic="T")
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await run_research_phase(ctx, llm_client=_FailClient())

    assert ctx.phase_results[PhaseName.RESEARCH].status is PhaseStatus.FAILED
    assert "synthetic failure" in ctx.phase_results[PhaseName.RESEARCH].error
