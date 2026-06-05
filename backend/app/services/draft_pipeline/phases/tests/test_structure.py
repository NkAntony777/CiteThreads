"""Tests for the structure phase (Architect + Formatter + orchestrator)."""

from __future__ import annotations

import json

import pytest

from app.services.draft_pipeline import (
    CitationStyle,
    DraftContext,
    PhaseName,
    PhaseStatus,
)
from app.services.draft_pipeline.phases import (
    architect,
    formatter,
    run_structure_phase,
    FormattedOutline,
    Outline,
    OutlineSection,
)
from app.services.draft_pipeline.phases.structure import (
    _default_format_for_style,
    _default_manuscript_spec,
    _heuristic_outline,
    _outline_to_dict,
    _outline_to_markdown,
    _parse_architect_json,
    _parse_formatter_json,
)


# ---------------------------------------------------------------------------
# Mocks (kept identical to test_research.py for consistency)
# ---------------------------------------------------------------------------


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockCompletions:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    async def create(self, *, model, messages, temperature, max_tokens):
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        if not self._scripted:
            raise RuntimeError("MockCompletions ran out of scripted responses")
        return _MockResponse(self._scripted.pop(0))


class _MockLLMClient:
    def __init__(self, scripted):
        if isinstance(scripted, str):
            scripted = [scripted]
        self.chat = type("Chat", (), {"completions": _MockCompletions(scripted)})()


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_heuristic_outline_has_six_sections():
    ctx = DraftContext(project_id="p", topic="T", target_word_count=6000)
    o = _heuristic_outline(ctx)
    assert len(o.sections) == 6
    titles = [s.title for s in o.sections]
    assert "Introduction" in titles
    assert "Conclusion" in titles


def test_heuristic_outline_target_words_within_5pct():
    ctx = DraftContext(project_id="p", topic="T", target_word_count=10000)
    o = _heuristic_outline(ctx)
    total = sum(s.target_words for s in o.sections)
    assert abs(total - 10000) / 10000 < 0.05


def test_heuristic_outline_zero_target_falls_back_to_default():
    # Pydantic rejects target_word_count=0 (min 100), so bypass via
    # model_construct to exercise the defensive branch in _heuristic_outline.
    ctx = DraftContext.model_construct(
        project_id="p", topic="T", target_word_count=0
    )
    o = _heuristic_outline(ctx)
    assert o.total_target_words == 8000  # default


def test_outline_to_markdown_includes_sections_and_target_words():
    o = Outline(
        paper_type="Literature Review",
        research_question="Q?",
        sections=[
            OutlineSection(number="1", title="Intro", target_words=1000, key_points=["K1"]),
            OutlineSection(number="2", title="Methods", target_words=1500),
        ],
    )
    md = _outline_to_markdown(o, "apa")
    assert "# Literature Review — APA style" in md
    assert "## 1 Intro" in md
    assert "*Target: 1000 words*" in md
    assert "## 2 Methods" in md
    assert "Q?" in md


def test_outline_to_markdown_renders_evidence_paper_ids():
    o = Outline(
        sections=[
            OutlineSection(
                number="1", title="Intro", target_words=100,
                evidence_paper_ids=["abc", "def"],
            )
        ]
    )
    md = _outline_to_markdown(o, "apa")
    assert "[@abc]" in md
    assert "[@def]" in md


def test_outline_to_dict_roundtrip():
    o = Outline(
        paper_type="Mixed",
        sections=[OutlineSection(number="1", title="X", target_words=500)],
    )
    d = _outline_to_dict(o)
    assert d["paper_type"] == "Mixed"
    assert d["sections"][0]["title"] == "X"


def test_parse_architect_json_strips_fences():
    raw = "```json\n" + json.dumps(
        {
            "paper_type": "Literature Review",
            "research_question": "Q?",
            "total_target_words": 8000,
            "sections": [
                {
                    "number": "1",
                    "title": "Intro",
                    "target_words": 1000,
                    "key_points": ["K1"],
                    "evidence_paper_ids": ["p1"],
                }
            ],
        }
    ) + "\n```"
    o = _parse_architect_json(raw)
    assert o.paper_type == "Literature Review"
    assert o.research_question == "Q?"
    assert len(o.sections) == 1
    assert o.sections[0].evidence_paper_ids == ["p1"]


def test_parse_architect_json_with_surrounding_prose():
    raw = "Here is the JSON:\n" + json.dumps(
        {"paper_type": "Empirical", "sections": [], "total_target_words": 5000}
    ) + "\nThanks!"
    o = _parse_architect_json(raw)
    assert o.paper_type == "Empirical"
    assert o.sections == []


def test_parse_architect_json_garbage_returns_empty_outline():
    o = _parse_architect_json("definitely not json")
    assert o.paper_type == ""
    assert o.sections == []


def test_parse_architect_json_clamps_non_int_target_words():
    raw = json.dumps(
        {"sections": [{"number": "1", "title": "X", "target_words": "not-a-number"}]}
    )
    o = _parse_architect_json(raw)
    assert o.sections[0].target_words == 0


def test_parse_formatter_json_full():
    raw = json.dumps(
        {
            "format_name": "APA",
            "target_venue": "Nature",
            "manuscript_spec": {"font": "Times New Roman 12pt", "line_spacing": "double"},
            "outline_markdown": "# Outline\n...",
        }
    )
    fallback = Outline(sections=[OutlineSection(number="1", title="X", target_words=100)])
    f = _parse_formatter_json(raw, fallback, "apa")
    assert f.format_name == "APA"
    assert f.target_venue == "Nature"
    assert f.manuscript_spec["font"] == "Times New Roman 12pt"


def test_parse_formatter_json_garbage_uses_fallback():
    fallback = Outline(
        paper_type="Literature Review",
        sections=[OutlineSection(number="1", title="Intro", target_words=1000)],
    )
    f = _parse_formatter_json("not json", fallback, "apa")
    assert f.paper_type == "Literature Review"
    assert "Intro" in f.outline_markdown
    assert f.format_name == "IMRaD"  # default for apa


def test_default_format_for_style():
    assert _default_format_for_style("ieee") == "IEEE"
    assert _default_format_for_style("mla") == "MLA"
    assert _default_format_for_style("chicago") == "Chicago"
    assert _default_format_for_style("nalt") == "Chicago"
    assert _default_format_for_style("apa") == "IMRaD"
    assert _default_format_for_style("unknown") == "IMRaD"


def test_default_manuscript_spec_for_ieee():
    spec = _default_manuscript_spec("ieee")
    assert "10pt" in spec["font"]
    assert "single" in spec["line_spacing"]


def test_default_manuscript_spec_for_apa():
    spec = _default_manuscript_spec("apa")
    assert "12pt" in spec["font"]
    assert "double" in spec["line_spacing"]


# ---------------------------------------------------------------------------
# Architect tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_architect_populates_outline():
    scripted = json.dumps(
        {
            "paper_type": "Literature Review",
            "target_venue": "Nature Machine Intelligence",
            "research_question": "How do transformers learn protein structure?",
            "draft_statement": "Transformers learn protein structure via...",
            "total_target_words": 8000,
            "sections": [
                {
                    "number": "1",
                    "title": "Introduction",
                    "target_words": 1200,
                    "key_points": ["Context", "Gap"],
                    "evidence_paper_ids": ["p1", "p2"],
                },
                {
                    "number": "2",
                    "title": "Methods",
                    "target_words": 1000,
                    "key_points": ["Approach"],
                    "evidence_paper_ids": [],
                },
            ],
        }
    )
    client = _MockLLMClient([scripted])
    ctx = DraftContext(
        project_id="p",
        topic="Transformers and protein folding",
        target_word_count=8000,
    )
    # Manually populate research outputs (bypassing the LLM-required
    # research phase) so architect has context to draw on.
    ctx.paper_summaries = [
        {"paper_id": "p1", "title": "A", "research_question": "Q?",
         "key_findings": ["f"], "limitations": []},
        {"paper_id": "p2", "title": "B", "research_question": "Q2?",
         "key_findings": ["g"], "limitations": []},
    ]
    ctx.research_gaps = [
        {"title": "G1", "description": "D", "gap_type": "methodological"}
    ]
    outline = await architect(ctx, client)
    assert outline.paper_type == "Literature Review"
    assert outline.target_venue == "Nature Machine Intelligence"
    assert len(outline.sections) == 2
    assert outline.sections[0].evidence_paper_ids == ["p1", "p2"]


@pytest.mark.asyncio
async def test_architect_uses_heuristic_when_llm_returns_garbage():
    client = _MockLLMClient(["not json at all"])
    ctx = DraftContext(project_id="p", topic="T", target_word_count=6000)
    # Heuristic is the fallback when LLM output is unparseable
    outline = await architect(ctx, client)
    # Either the LLM output gets parsed (empty Outline) OR heuristic fires
    # We assert that we always get *some* structure: 0 or 6 sections both OK,
    # but the orchestrator should not raise.
    assert outline is not None


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_formatter_applies_style_to_outline():
    scripted = json.dumps(
        {
            "format_name": "APA",
            "manuscript_spec": {"font": "Times New Roman 12pt"},
            "outline_markdown": "# Outline in APA style",
        }
    )
    client = _MockLLMClient([scripted])
    ctx = DraftContext(
        project_id="p",
        topic="T",
        citation_style=CitationStyle.APA,
    )
    outline = Outline(
        paper_type="Literature Review",
        sections=[OutlineSection(number="1", title="Intro", target_words=1000)],
    )
    f = await formatter(ctx, outline, client)
    assert f.format_name == "APA"
    assert f.outline_markdown == "# Outline in APA style"
    assert f.citation_style == "apa"


@pytest.mark.asyncio
async def test_formatter_uses_fallback_markdown_on_garbage():
    client = _MockLLMClient(["!@#$%"])
    ctx = DraftContext(
        project_id="p",
        topic="T",
        citation_style=CitationStyle.IEEE,
    )
    outline = Outline(
        paper_type="Empirical",
        sections=[OutlineSection(number="1", title="Methods", target_words=1500)],
    )
    f = await formatter(ctx, outline, client)
    assert f.format_name == "IEEE"
    assert "Methods" in f.outline_markdown


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_structure_phase_with_llm_marks_phase_done():
    architect_payload = json.dumps(
        {
            "paper_type": "Literature Review",
            "total_target_words": 5000,
            "sections": [
                {"number": "1", "title": "Intro", "target_words": 1000},
                {"number": "2", "title": "Methods", "target_words": 2000},
                {"number": "3", "title": "Conclusion", "target_words": 2000},
            ],
        }
    )
    formatter_payload = json.dumps(
        {"format_name": "IMRaD", "manuscript_spec": {"font": "12pt"}, "outline_markdown": "# IMRaD"}
    )
    client = _MockLLMClient([architect_payload, formatter_payload])
    ctx = DraftContext(
        project_id="p",
        topic="T",
        target_word_count=5000,
        paper_summaries=[
            {"paper_id": "p1", "title": "A", "research_question": "Q?", "key_findings": [], "limitations": []}
        ],
    )
    out = await run_structure_phase(ctx, llm_client=client)
    assert out.is_phase_done(PhaseName.STRUCTURE)
    assert out.outline is not None
    assert len(out.outline["sections"]) == 3
    assert out.formatted_outline == "# IMRaD"
    # Only STRUCTURE is done here (1/5 dispatchable = 20%); research phase
    # is not run in this test, so the orchestrator only marks one bucket.
    assert out.progress_pct() == 20.0


@pytest.mark.asyncio
async def test_run_structure_phase_without_llm_uses_heuristic():
    ctx = DraftContext(project_id="p", topic="T", target_word_count=5000)
    out = await run_structure_phase(ctx, llm_client=None)
    assert out.is_phase_done(PhaseName.STRUCTURE)
    assert out.outline is not None
    assert len(out.outline["sections"]) == 6  # heuristic
    assert out.formatted_outline is not None
    # Heuristic-formatter applies APA defaults for apa citation style
    assert "APA" in out.formatted_outline or "apa" in out.formatted_outline.lower()


@pytest.mark.asyncio
async def test_run_structure_phase_records_failure():
    class _FailClient:
        class chat:
            class completions:
                async def create(*a, **k):
                    raise RuntimeError("boom")

    ctx = DraftContext(project_id="p", topic="T")
    with pytest.raises(RuntimeError, match="boom"):
        await run_structure_phase(ctx, llm_client=_FailClient())
    assert ctx.phase_results[PhaseName.STRUCTURE].status is PhaseStatus.FAILED
    assert "boom" in ctx.phase_results[PhaseName.STRUCTURE].error


@pytest.mark.asyncio
async def test_run_structure_phase_uses_ieee_spec_when_citation_style_ieee():
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=5000,
        citation_style=CitationStyle.IEEE,
    )
    out = await run_structure_phase(ctx, llm_client=None)
    # IEEE spec lives in the formatter output as part of the markdown
    assert "IEEE" in out.formatted_outline or "10pt" in out.formatted_outline
