"""Unit tests for draft_pipeline.quality_gate (QualityGate + QualityScore)."""

from __future__ import annotations

import pytest

from app.services.draft_pipeline import (
    CitationStyle,
    DraftContext,
    PhaseName,
    PhaseStatus,
    QualityDecision,
    QualityGate,
    QualityScore,
)


# --- Helpers -------------------------------------------------------------


def _ctx(**overrides) -> DraftContext:
    base = {"project_id": "p1", "topic": "Test topic", "target_word_count": 8000}
    base.update(overrides)
    return DraftContext(**base)


def _word_block(n_words: int) -> str:
    return " ".join(["word"] * n_words)


def _section_drafts_imrad(target: int = 8000) -> dict[str, str]:
    """An IMRaD-shaped draft with ``target`` total words and ~8 cites/k."""
    per = max(target // 6, 100)
    body = _word_block(per)
    # 8 citations per 1000 words: 1 cite per 125 words → every 125th word replace.
    # Simpler: sprinkle ``[@Key]`` tokens into the body.
    cite_block = " [@K1] [@K2] [@K3] [@K4] [@K5] [@K6] [@K7] [@K8]" * (per // 100)
    return {
        "Introduction": body + cite_block,
        "Literature Review": body + cite_block,
        "Methodology": body + cite_block,
        "Results": body + cite_block,
        "Discussion": body + cite_block,
        "Conclusion": body + cite_block,
    }


# --- Empty / boundary cases ---------------------------------------------


def test_empty_ctx_scores_zero_total_and_fails():
    ctx = _ctx()
    s = QualityGate().score(ctx)
    assert s.total == 0
    assert s.decision is QualityDecision.FAIL
    assert s.passed is False


def test_passed_false_for_fail_decision():
    s = QualityScore(total=10, decision=QualityDecision.FAIL)
    assert s.passed is False


def test_passed_true_for_warn_decision():
    s = QualityScore(total=80, decision=QualityDecision.WARN)
    assert s.passed is True


def test_passed_true_for_pass_decision():
    s = QualityScore(total=120, decision=QualityDecision.PASS)
    assert s.passed is True


def test_to_dict_is_json_friendly():
    s = QualityScore(
        total=100,
        word_count=25,
        citation_density=25,
        completeness=25,
        structure=25,
        graph_health=0,
        issues=["x"],
        decision=QualityDecision.PASS,
    )
    d = s.to_dict()
    assert d["total"] == 100
    assert d["decision"] == "pass"
    assert d["issues"] == ["x"]
    assert d["word_count"] == 25


# --- Per-dimension scoring -----------------------------------------------


def test_word_count_full_at_target():
    ctx = _ctx(target_word_count=1000)
    ctx.section_drafts = {"Introduction": _word_block(1000)}
    s = QualityGate().score(ctx)
    assert s.word_count == 25


def test_word_count_half_at_half_target():
    ctx = _ctx(target_word_count=1000)
    ctx.section_drafts = {"Introduction": _word_block(500)}
    s = QualityGate().score(ctx)
    assert s.word_count == 12  # int(0.5 * 25)


def test_word_count_zero_target_returns_max():
    """Defensive: QualityGate handles target <= 0 even if Pydantic
    usually blocks it. Construct via ``model_construct`` to bypass the
    ``ge=100`` validator (mimics what a deserialized checkpoint might
    deliver in Task 2)."""
    ctx = DraftContext.model_construct(
        project_id="p1", topic="t", target_word_count=0
    )
    s = QualityGate().score(ctx)
    assert s.word_count == 25


def test_citation_density_eight_per_thousand_gives_full_marks():
    ctx = _ctx()
    # 992 plain words + 8 citation tokens = 1000 tokens, 8 cites.
    body = _word_block(992) + " [@K1] [@K2] [@K3] [@K4] [@K5] [@K6] [@K7] [@K8]"
    ctx.section_drafts = {"Introduction": body}
    s = QualityGate().score(ctx)
    assert s.citation_density == 25


def test_citation_density_zero_when_no_drafts():
    s = QualityGate().score(_ctx())
    assert s.citation_density == 0


def test_citation_density_zero_when_no_words():
    """Empty body → 0 tokens → guards against div-by-zero."""
    ctx = _ctx()
    ctx.section_drafts = {"Introduction": ""}
    s = QualityGate().score(ctx)
    assert s.citation_density == 0


def test_completeness_full_when_all_six_sections_present():
    ctx = _ctx()
    ctx.section_drafts = {
        "Introduction": "x",
        "Literature Review": "x",
        "Methodology": "x",
        "Results": "x",
        "Discussion": "x",
        "Conclusion": "x",
    }
    s = QualityGate().score(ctx)
    assert s.completeness == 25


def test_completeness_zero_when_no_sections():
    s = QualityGate().score(_ctx())
    assert s.completeness == 0


def test_completeness_partial_matches_substring():
    """Section keys are matched case-insensitively against IMRaD
    needles via ``in`` (the section name is a substring of itself)."""
    ctx = _ctx()
    ctx.section_drafts = {
        "Introduction": "x",       # → introduction
        "Methodology": "x",        # → methodology
        "Results": "x",            # → results
    }
    s = QualityGate().score(ctx)
    # 3 / 6 = 12.5 → 12
    assert s.completeness == 12


def test_structure_full_when_heading_hierarchy_present():
    ctx = _ctx()
    # 1 h1, 6 h2, 10 h3 → 8 + 9 + 8 = 25
    h2_blocks = []
    for letter in "ABCDEFGHIJK":
        h2_blocks.append(f"\n## Section {letter}\n### Sub {letter}a\nbody")
    body = "\n# Main Title\n" + "\n".join(h2_blocks[:6]) + "".join(
        f"\n### Sub extra {i}\nbody" for i in range(4)
    )
    ctx.section_drafts = {"Paper": body}
    s = QualityGate().score(ctx)
    assert s.structure == 25


def test_structure_partial_when_only_h1():
    ctx = _ctx()
    ctx.section_drafts = {"Paper": "\n# Only h1\nbody"}
    s = QualityGate().score(ctx)
    assert s.structure == 8


def test_structure_zero_when_no_headings():
    ctx = _ctx()
    ctx.section_drafts = {"Paper": "no headings at all"}
    s = QualityGate().score(ctx)
    assert s.structure == 0


def test_graph_health_full_when_all_signals_present():
    ctx = _ctx()
    ctx.graph_node_ids = [f"p{i}" for i in range(10)]
    ctx.candidate_papers = [
        {"id": f"c{i}", "year": 2021 + (i % 3)} for i in range(12)
    ]
    ctx.research_gaps = [{"title": "gap1", "paper_id": "gap1", "description": "x"}]
    ctx.reference_ids = ["gap1", "other"]
    ctx.quality_history = [QualityScore(total=80, decision=QualityDecision.WARN)]
    s = QualityGate().score(ctx)
    # 5 + 5 + 5 + 5 + 5 = 25
    assert s.graph_health == 25


def test_graph_health_zero_when_no_signals():
    s = QualityGate().score(_ctx())
    assert s.graph_health == 0


def test_graph_health_recency_below_threshold_no_credit():
    ctx = _ctx()
    ctx.candidate_papers = [{"year": 2010}, {"year": 2011}]
    s = QualityGate().score(ctx)
    # 0 (graph_node_ids<5) + 0 (candidate_papers<10) + 0 + 0 (recency) + 0 = 0
    assert s.graph_health == 0


def test_graph_health_non_int_year_ignored_for_recency():
    ctx = _ctx()
    ctx.candidate_papers = [
        {"year": "2024"},     # not int, ignored
        {"year": None},
        {"year": 2019},       # not >= 2020
    ] * 5  # 15 total
    s = QualityGate().score(ctx)
    # 5 (>=10 candidates) + 0 (no recent >=2020) = 5
    assert s.graph_health == 5


# --- Decision thresholds -------------------------------------------------


def test_decision_pass_at_threshold_100():
    s = QualityGate()._decide(100)
    assert s is QualityDecision.PASS


def test_decision_warn_just_below_pass():
    s = QualityGate()._decide(99)
    assert s is QualityDecision.WARN


def test_decision_warn_at_threshold_75():
    s = QualityGate()._decide(75)
    assert s is QualityDecision.WARN


def test_decision_fail_just_below_warn():
    s = QualityGate()._decide(74)
    assert s is QualityDecision.FAIL


def test_custom_thresholds_via_subclass():
    class StrictGate(QualityGate):
        PASS_THRESHOLD = 110
        WARN_THRESHOLD = 90

    g = StrictGate()
    s = g._decide(109)
    assert s is QualityDecision.WARN
    s2 = g._decide(90)
    assert s2 is QualityDecision.WARN
    s3 = g._decide(89)
    assert s3 is QualityDecision.FAIL


# --- Issue reporting -----------------------------------------------------


def test_issues_list_populated_for_weak_draft():
    ctx = _ctx()
    ctx.section_drafts = {"Intro": "tiny"}
    s = QualityGate().score(ctx)
    # every dimension should be weak → 5 issues
    assert len(s.issues) == 5
    joined = " ".join(s.issues)
    assert "字数" in joined
    assert "引用" in joined
    assert "章节" in joined
    assert "标题" in joined
    assert "图谱" in joined


def test_issues_list_empty_for_perfect_draft():
    """All dimensions at max → no issues, decision = pass."""
    # Build a draft that maxes every dimension.
    head = "\n# Main Title\n"
    h2_block = ""
    for letter in "ABCDEF":
        h2_block += f"\n## Section {letter}\n"
        h2_block += f"### Sub {letter}a\nbody\n"
        h2_block += f"### Sub {letter}b\nbody\n"
    h2_block += "\n### Sub extra 1\nbody"
    h2_block += "\n### Sub extra 2\nbody"
    cite_pad = " [@K1] [@K2] [@K3] [@K4] [@K5] [@K6] [@K7] [@K8]"
    intro_body = head + h2_block + _word_block(150) + cite_pad
    # Each remaining section just needs to count toward completeness.
    stub = _word_block(50) + cite_pad
    ctx = _ctx(target_word_count=400)
    ctx.section_drafts = {
        "Introduction": intro_body,
        "Literature Review": stub,
        "Methodology": stub,
        "Results": stub,
        "Discussion": stub,
        "Conclusion": stub,
    }
    ctx.graph_node_ids = [f"p{i}" for i in range(10)]
    ctx.candidate_papers = [{"year": 2024} for _ in range(15)]
    ctx.research_gaps = [{"title": "g1", "paper_id": "g1"}]
    ctx.reference_ids = ["g1"]
    # The graph_health dimension's 5th point requires a prior score
    # in history; populate one to unlock the final dimension.
    ctx.quality_history = [
        QualityScore(total=100, decision=QualityDecision.PASS)
    ]
    s = QualityGate().score(ctx)
    assert s.total == 125, s.to_dict()
    assert s.issues == []
    assert s.decision is QualityDecision.PASS


# --- Integration with DraftContext ---------------------------------------


def test_quality_score_uses_real_phase_results():
    """The graph_health dimension rewards a good quality_history."""
    ctx = _ctx()
    # Manually populate quality_history
    ctx.quality_history = [QualityScore(total=80, decision=QualityDecision.WARN)]
    s = QualityGate().score(ctx)
    # No other signal present, but history ≥ 75 → +5
    assert s.graph_health == 5


def test_total_equals_sum_of_dimensions():
    ctx = _ctx()
    s = QualityGate().score(ctx)
    assert s.total == (
        s.word_count
        + s.citation_density
        + s.completeness
        + s.structure
        + s.graph_health
    )
