"""Unit tests for draft_pipeline.context (DraftContext + enums)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.draft_pipeline import (
    CitationStyle,
    DraftContext,
    PhaseName,
    PhaseResult,
    PhaseStatus,
)


# --- Enum sanity ---------------------------------------------------------


def test_phase_name_has_six_buckets():
    assert len(PhaseName) == 6
    assert {p.value for p in PhaseName} == {
        "research", "structure", "compose", "validate", "compile", "export",
    }


def test_phase_status_values():
    assert {s.value for s in PhaseStatus} == {
        "pending", "running", "succeeded", "failed", "skipped",
    }


def test_citation_style_values():
    assert {c.value for c in CitationStyle} == {"apa", "ieee", "chicago", "mla", "nalt"}


# --- Construction --------------------------------------------------------


def _ctx(**overrides) -> DraftContext:
    base = {"project_id": "p1", "topic": "Neural networks for protein folding"}
    base.update(overrides)
    return DraftContext(**base)


def test_minimal_construction_uses_defaults():
    ctx = _ctx()
    assert ctx.project_id == "p1"
    assert ctx.topic.startswith("Neural")
    assert ctx.language == "en"
    assert ctx.citation_style is CitationStyle.APA
    assert ctx.target_word_count == 8000
    assert ctx.reference_ids == []
    assert ctx.graph_node_ids == []
    assert ctx.phase_results == {}
    assert ctx.candidate_papers == []
    assert ctx.paper_summaries == []
    assert ctx.research_gaps == []
    assert ctx.section_drafts == {}
    assert ctx.outline is None
    assert ctx.formatted_outline is None
    assert ctx.final_draft is None
    assert ctx.qa_report is None
    assert ctx.cancellation_requested is False
    assert ctx.quality_history == []


def test_missing_project_id_raises():
    with pytest.raises(ValidationError):
        DraftContext(topic="t")  # type: ignore[call-arg]


def test_missing_topic_raises():
    with pytest.raises(ValidationError):
        DraftContext(project_id="p1")  # type: ignore[call-arg]


def test_target_word_count_bounds():
    with pytest.raises(ValidationError):
        _ctx(target_word_count=0)
    with pytest.raises(ValidationError):
        _ctx(target_word_count=10**7)


def test_citation_style_accepts_enum_and_string():
    a = _ctx(citation_style=CitationStyle.IEEE)
    assert a.citation_style is CitationStyle.IEEE
    b = _ctx(citation_style="chicago")
    assert b.citation_style is CitationStyle.CHICAGO


def test_chinese_language_is_accepted():
    ctx = _ctx(language="zh")
    assert ctx.language == "zh"


# --- Phase tracking ------------------------------------------------------


def test_is_phase_done_initially_false():
    ctx = _ctx()
    for p in PhaseName:
        assert ctx.is_phase_done(p) is False


def test_mark_phase_running_sets_started_at():
    ctx = _ctx()
    r = ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.RUNNING)
    assert r.status is PhaseStatus.RUNNING
    assert r.started_at is not None
    assert r.finished_at is None
    # idempotent re-mark does not overwrite started_at
    r2 = ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.RUNNING)
    assert r2.started_at == r.started_at


def test_mark_phase_succeeded_sets_finished_at():
    ctx = _ctx()
    ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.RUNNING)
    r = ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.SUCCEEDED)
    assert r.status is PhaseStatus.SUCCEEDED
    assert r.finished_at is not None


def test_is_phase_done_true_after_succeeded():
    ctx = _ctx()
    ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.SUCCEEDED)
    assert ctx.is_phase_done(PhaseName.RESEARCH) is True


def test_is_phase_done_false_after_failed():
    ctx = _ctx()
    ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.RUNNING)
    ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.FAILED, error="boom")
    assert ctx.is_phase_done(PhaseName.RESEARCH) is False
    assert ctx.phase_results[PhaseName.RESEARCH].error == "boom"


def test_progress_pct_starts_at_zero():
    ctx = _ctx()
    assert ctx.progress_pct() == 0.0


def test_progress_pct_partial():
    ctx = _ctx()
    ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.SUCCEEDED)
    ctx.mark_phase(PhaseName.STRUCTURE, PhaseStatus.SUCCEEDED)
    # 2 of 5 dispatchable phases = 40.0
    assert ctx.progress_pct() == 40.0


def test_progress_pct_full():
    ctx = _ctx()
    for p in PhaseName:
        ctx.mark_phase(p, PhaseStatus.SUCCEEDED)
    assert ctx.progress_pct() == 100.0


def test_section_drafts_accepts_arbitrary_keys():
    ctx = _ctx()
    ctx.section_drafts = {
        "Introduction": "# Intro\nbody",
        "Methods": "## Methods\nbody",
        "Custom Section": "body",
    }
    assert len(ctx.section_drafts) == 3


def test_phase_result_is_a_basemodel():
    r = PhaseResult(phase=PhaseName.RESEARCH)
    assert r.phase is PhaseName.RESEARCH
    assert r.status is PhaseStatus.PENDING


# --- Outline type contract --------------------------------------------------


def test_outline_accepts_structured_dict():
    """Architect writes a structured dict to ctx.outline; the type must
    allow it (regression for the previous ``Optional[str]`` lie)."""
    ctx = _ctx()
    ctx.outline = {
        "paper_type": "Literature Review",
        "target_venue": "Nature Machine Intelligence",
        "research_question": "How do transformers learn structure?",
        "draft_statement": "We argue...",
        "total_target_words": 8000,
        "sections": [
            {
                "number": "1",
                "title": "Introduction",
                "target_words": 1200,
                "key_points": ["Context", "Gap"],
                "evidence_paper_ids": ["p1", "p2"],
            },
        ],
    }
    assert ctx.outline["paper_type"] == "Literature Review"
    assert ctx.outline["sections"][0]["evidence_paper_ids"] == ["p1", "p2"]


def test_outline_default_is_none():
    ctx = _ctx()
    assert ctx.outline is None


def test_formatted_outline_remains_string():
    """The Formatter writes a markdown string to formatted_outline. Keep
    this as ``str`` so downstream LLM prompts can ``_truncate`` it."""
    ctx = _ctx()
    ctx.formatted_outline = "# 1. Introduction\n*Target: 1000 words*\n- Point A"
    assert isinstance(ctx.formatted_outline, str)
    assert "Introduction" in ctx.formatted_outline
