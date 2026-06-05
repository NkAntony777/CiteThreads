"""Tests for the post-compile enhancement phases.

Covers:
- Citation Verifier: extracts [@paper_id] tags, classifies into
  verified / incomplete / unresolved, populates ctx.citation_audit,
  appends an audit block to ctx.qa_report, gracefully handles no-LLM.
- Abstract Generator: LLM call writes to ctx.abstract; fallback path
  works without LLM and produces a non-empty string; the LLM-with-
  fallback path prefers the LLM output when it's a real string.
- Table/Figure hints: numeric-density + comparison → TABLE_SUGGESTION;
  sequence markers → FIGURE_SUGGESTION; without either → no hints;
  paper_summaries key_findings contribute a boost.

Target: 10+ tests, all deterministic (mocked LLM).
"""

from __future__ import annotations

import json

import pytest

from app.services.draft_pipeline import (
    CitationStyle,
    DraftContext,
)
from app.services.draft_pipeline.phases import (
    CitationAudit,
    TableFigureHints,
    abstract_writer,
    apply_table_figure_hints,
    citation_verify,
    suggest_table_figure_hints,
)
from app.services.draft_pipeline.phases.citation_verify import (
    _audit_citations,
    _candidate_index,
    _format_audit_block,
    _is_complete,
    _parse_replacements_json,
)
from app.services.draft_pipeline.phases.table_figure_hints import (
    _count_numbers,
    _has_comparison,
    _has_sequence,
    _list_item_count,
    _summaries_findings_boost,
)


# ---------------------------------------------------------------------------
# Mocks (shared pattern across the test suite)
# ---------------------------------------------------------------------------


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockCompletions:
    def __init__(self, scripted):
        self._scripted = list(scripted) if not isinstance(scripted, str) else [scripted]
        self.calls: list[dict] = []

    async def create(self, *, model, messages, temperature, max_tokens):
        self.calls.append(
            {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        )
        if not self._scripted:
            raise RuntimeError("MockCompletions ran out of scripted responses")
        return _MockResponse(self._scripted.pop(0))


class _MockLLMClient:
    def __init__(self, scripted):
        self.chat = type("Chat", (), {"completions": _MockCompletions(scripted)})()


# ---------------------------------------------------------------------------
# Citation Verifier — deterministic helpers
# ---------------------------------------------------------------------------


def _ctx_with_papers() -> DraftContext:
    """5 cited paper ids: 2 verified (DOI+venue), 1 incomplete (no DOI),
    2 unresolved (not in candidate set). Mirrors the brief's spec."""
    return DraftContext(
        project_id="p1",
        topic="Neural networks for drug discovery",
        language="en",
        citation_style=CitationStyle.APA,
        reference_ids=["ref1", "ref2"],
        graph_node_ids=["g1", "g2", "g3"],
        candidate_papers=[
            {
                "id": "doi:10.1/verified_a",
                "title": "Verified Paper A",
                "year": 2023,
                "venue": "Nature MI",
                "doi": "10.1/verified_a",
            },
            {
                "id": "doi:10.2/verified_b",
                "title": "Verified Paper B",
                "year": 2024,
                "venue": "arXiv",
                "doi": "10.2/verified_b",
                "arxiv_id": "2401.00002",
            },
            {
                "id": "incomplete:1",
                "title": "Incomplete Paper — no DOI",
                "year": 2022,
                "venue": "JMLR",
                "doi": "",
            },
        ],
        paper_summaries=[
            {
                "paper_id": "doi:10.1/verified_a",
                "title": "Verified Paper A",
                "year": 2023,
                "venue": "Nature MI",
                "doi": "10.1/verified_a",
            },
        ],
        section_drafts={
            "introduction": (
                "We cite [@doi:10.1/verified_a] and [@incomplete:1] "
                "and also [@typo:wrong] which is unresolved."
            ),
            "results": (
                "[@doi:10.2/verified_b] is verified. "
                "[@another_typo] is not in the corpus either."
            ),
        },
    )


def test_is_complete_requires_doi_and_venue():
    assert _is_complete({"doi": "10.1/x", "venue": "V", "arxiv_id": ""}) is True
    assert _is_complete({"doi": "", "venue": "V", "arxiv_id": "2401.x"}) is True
    assert _is_complete({"doi": "10.1/x", "venue": "", "arxiv_id": ""}) is False
    assert _is_complete({"doi": "", "venue": "", "arxiv_id": ""}) is False
    assert _is_complete({}) is False
    assert _is_complete(None) is False


def test_candidate_index_merges_summaries_over_candidates():
    ctx = _ctx_with_papers()
    idx = _candidate_index(ctx)
    # Verified papers present
    assert "doi:10.1/verified_a" in idx
    assert "doi:10.2/verified_b" in idx
    # Incomplete paper present (no DOI)
    assert "incomplete:1" in idx
    assert idx["incomplete:1"]["doi"] == ""


def test_audit_citations_classifies_into_three_buckets():
    ctx = _ctx_with_papers()
    idx = _candidate_index(ctx)
    audit = _audit_citations(ctx.section_drafts, idx)
    assert set(audit.verified) == {"doi:10.1/verified_a", "doi:10.2/verified_b"}
    assert audit.incomplete == ["incomplete:1"]
    assert set(audit.unresolved) == {"typo:wrong", "another_typo"}
    assert "2 verified" in audit.summary
    assert "1 incomplete" in audit.summary
    assert "2 unresolved" in audit.summary


# ---------------------------------------------------------------------------
# Citation Verifier — agent (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_citation_verify_writes_audit_and_appends_report():
    ctx = _ctx_with_papers()
    audit = await citation_verify(ctx, llm_client=None, use_llm=False)
    assert isinstance(audit, CitationAudit)
    assert ctx.citation_audit is not None
    assert set(ctx.citation_audit["verified"]) == {"doi:10.1/verified_a", "doi:10.2/verified_b"}
    assert ctx.citation_audit["incomplete"] == ["incomplete:1"]
    assert set(ctx.citation_audit["unresolved"]) == {"typo:wrong", "another_typo"}
    # The audit block is appended to ctx.qa_report
    assert "Citation Audit" in ctx.qa_report
    assert "[@incomplete:1]" in ctx.qa_report
    assert "[@typo:wrong]" in ctx.qa_report


@pytest.mark.asyncio
async def test_citation_verify_deterministic_without_llm_marks_two_unresolved():
    """The brief: 5 citations, finds 3, marks 2 as unresolved.
    Here we have 4 distinct cited ids; 2 verified, 1 incomplete, 2 unresolved."""
    ctx = _ctx_with_papers()
    audit = await citation_verify(ctx, llm_client=None, use_llm=False)
    # The brief's spec: 5 citations, 3 found, 2 unresolved. Our fixture
    # has 5 distinct ids (verified_a, verified_b, incomplete:1, typo:wrong,
    # another_typo). 3 are in the candidate set (verified_a, verified_b,
    # incomplete:1) and 2 are unresolved.
    assert len(audit.verified) + len(audit.incomplete) + len(audit.unresolved) == 5
    assert len(audit.unresolved) == 2
    assert len(audit.verified) == 2
    assert len(audit.incomplete) == 1


@pytest.mark.asyncio
async def test_citation_verify_missing_doi_marks_incomplete():
    """A paper with no DOI in the candidate set must end up in
    the 'incomplete' bucket, not 'verified'."""
    ctx = _ctx_with_papers()
    # confirmed: the fixture has incomplete:1 with no DOI
    audit = await citation_verify(ctx, llm_client=None, use_llm=False)
    assert "incomplete:1" not in audit.verified
    assert "incomplete:1" in audit.incomplete
    assert "incomplete:1" not in audit.unresolved


@pytest.mark.asyncio
async def test_citation_verify_with_llm_adds_replacement_suggestions():
    llm_response = json.dumps(
        {
            "replacements": {
                "typo:wrong": ["doi:10.1/verified_a", "doi:10.2/verified_b"],
                "another_typo": [],
            }
        }
    )
    client = _MockLLMClient(llm_response)
    ctx = _ctx_with_papers()
    audit = await citation_verify(ctx, llm_client=client, use_llm=True)
    assert audit.replacements.get("typo:wrong") == [
        "doi:10.1/verified_a", "doi:10.2/verified_b"
    ]
    # another_typo was empty in the LLM response, so it must be omitted
    assert "another_typo" not in audit.replacements
    # Persisted on ctx
    assert ctx.citation_audit["replacements"]["typo:wrong"] == [
        "doi:10.1/verified_a", "doi:10.2/verified_b"
    ]


@pytest.mark.asyncio
async def test_citation_verify_with_llm_filters_invalid_candidate_ids():
    """The LLM may return ids that are not actually in the candidate
    set; those must be filtered out."""
    llm_response = json.dumps(
        {
            "replacements": {
                "typo:wrong": ["doi:10.1/verified_a", "fake_id_not_in_set"],
            }
        }
    )
    client = _MockLLMClient(llm_response)
    ctx = _ctx_with_papers()
    audit = await citation_verify(ctx, llm_client=client, use_llm=True)
    assert "fake_id_not_in_set" not in audit.replacements.get("typo:wrong", [])


def test_parse_replacements_json_handles_fenced_output():
    raw = "```json\n" + json.dumps(
        {"replacements": {"a": ["b"]}}
    ) + "\n```"
    out = _parse_replacements_json(raw, valid_ids={"a", "b"})
    assert out == {"a": ["b"]}


def test_format_audit_block_bilingual():
    audit = CitationAudit(
        verified=["v1"],
        incomplete=["i1"],
        unresolved=["u1"],
        replacements={"u1": ["v1"]},
        summary="1 verified, 1 incomplete, 1 unresolved",
    )
    en = _format_audit_block(audit, lang="en")
    zh = _format_audit_block(audit, lang="zh")
    assert "Citation Audit" in en
    assert "已核验" in zh
    assert "[@u1]" in en
    assert "[@u1]" in zh


# ---------------------------------------------------------------------------
# Abstract Generator
# ---------------------------------------------------------------------------


def _ctx_with_full_sections() -> DraftContext:
    return DraftContext(
        project_id="p1",
        topic="ML for drug discovery",
        language="en",
        citation_style=CitationStyle.APA,
        section_drafts={
            "introduction": "Drug discovery is expensive. We argue ML helps.",
            "literature_review": "Many surveys exist. The most recent is [@a].",
            "methodology": "We followed PRISMA. We screened 1,234 papers.",
            "results": "We found 12 relevant papers. Accuracy improved 42%.",
            "discussion": "Limitations include dataset bias. Future work: graph models.",
            "conclusion": "We recommend future work focus on graph-based methods.",
        },
    )


@pytest.mark.asyncio
async def test_abstract_writer_with_llm_writes_to_ctx_abstract():
    payload = json.dumps({"abstract": "We present a 250-word review of ML for drug discovery."})
    client = _MockLLMClient(payload)
    ctx = _ctx_with_full_sections()
    abstract = await abstract_writer(ctx, client)
    assert abstract
    assert "drug discovery" in abstract.lower()
    # Also persisted on ctx
    assert ctx.abstract == abstract


@pytest.mark.asyncio
async def test_abstract_writer_without_llm_uses_fallback():
    ctx = _ctx_with_full_sections()
    abstract = await abstract_writer(ctx, llm_client=None)
    assert abstract
    assert len(abstract) > 0
    # The fallback pulls first sentence from each section, so it should
    # be a multi-sentence string.
    assert abstract.endswith(".")
    assert ctx.abstract == abstract


@pytest.mark.asyncio
async def test_abstract_writer_fallback_produces_reasonable_length():
    ctx = _ctx_with_full_sections()
    abstract = await abstract_writer(ctx, llm_client=None, target_words=200)
    words = abstract.split()
    # The fallback trims to target_words (200) — so we expect <= 200.
    assert 0 < len(words) <= 200
    assert abstract.endswith(".")


@pytest.mark.asyncio
async def test_abstract_writer_llm_failure_falls_back_to_heuristic():
    class _BoomClient:
        class chat:
            class completions:
                async def create(*a, **k):
                    raise RuntimeError("network down")

    ctx = _ctx_with_full_sections()
    abstract = await abstract_writer(ctx, _BoomClient())
    # Heuristic abstract: first sentence of each section, joined
    assert abstract
    assert abstract.endswith(".")
    assert ctx.abstract == abstract


@pytest.mark.asyncio
async def test_abstract_writer_empty_sections_returns_empty_and_clears_ctx():
    ctx = DraftContext(project_id="p", topic="t", section_drafts={})
    ctx.abstract = "stale"  # should be cleared
    abstract = await abstract_writer(ctx, llm_client=None)
    assert abstract == ""
    assert ctx.abstract == ""


@pytest.mark.asyncio
async def test_abstract_writer_write_ctx_false_does_not_persist():
    payload = json.dumps({"abstract": "Quick abstract."})
    client = _MockLLMClient(payload)
    ctx = _ctx_with_full_sections()
    ctx.abstract = "old"  # must not be overwritten
    abstract = await abstract_writer(ctx, client, write_ctx=False)
    assert abstract == "Quick abstract."
    assert ctx.abstract == "old"


# ---------------------------------------------------------------------------
# Table/Figure hints — heuristic unit tests
# ---------------------------------------------------------------------------


def test_count_numbers_basic():
    text = "We found 12 papers with 42% accuracy in 2023 ($1.2M budget)."
    n = _count_numbers(text)
    assert n >= 4  # 12, 42, 2023, 1.2


def test_count_numbers_handles_overlap():
    # "2023" matches both the year pattern and the bare-number pattern;
    # we must dedupe overlapping matches.
    n = _count_numbers("Published in 2023.")
    assert n == 1


def test_has_sequence_english_and_chinese():
    assert _has_sequence("First, we do X. Second, we do Y.")
    assert _has_sequence("Step 1: do X. Step 2: do Y.")
    assert _has_sequence("The workflow has 3 stages.")
    assert _has_sequence("整体流程分为三个阶段: 1) 数据采集 2) 训练 3) 评估")
    assert not _has_sequence("We just have prose here with no sequence.")


def test_has_comparison_english_and_chinese():
    assert _has_comparison("Method A is better than Method B.")
    assert _has_comparison("Compared to the baseline, our model improves F1.")
    assert _has_comparison("该方法相比基线模型提升 12%")
    assert not _has_comparison("We trained a model. It worked.")


def test_list_item_count_handles_bullets_and_numbers():
    text = (
        "Some prose.\n\n"
        "- First item\n"
        "- Second item\n"
        "- Third item\n"
    )
    assert _list_item_count(text) == 3


# ---------------------------------------------------------------------------
# Table/Figure hints — per-section + integration
# ---------------------------------------------------------------------------


def test_section_suggestions_table_triggered_by_numbers():
    body = (
        "We compared 12 baselines. Method A scored 78.3% accuracy. "
        "Method B scored 75.1% accuracy. Method C scored 82.0% accuracy. "
        "On the 2023 benchmark, our model reached 91.4% accuracy, "
        "outperforming the previous SOTA by 8.2%."
    )
    hints = suggest_table_figure_hints({"results": body})
    table_hints = [h for h in hints if h.kind == "table"]
    assert table_hints, "Expected at least one TABLE_SUGGESTION"
    assert any("Comparison" in h.caption or "results" in h.caption.lower() for h in table_hints)


def test_section_suggestions_figure_triggered_by_sequence():
    body = (
        "Our pipeline has 4 stages. First, we collect data. "
        "Second, we preprocess. Third, we train. Finally, we evaluate."
    )
    hints = suggest_table_figure_hints({"methodology": body})
    figure_hints = [h for h in hints if h.kind == "figure"]
    assert figure_hints, "Expected at least one FIGURE_SUGGESTION"


def test_section_suggestions_no_hint_for_prose_only():
    body = "We discuss the limitations of prior work. Future directions are explored."
    hints = suggest_table_figure_hints({"discussion": body})
    assert hints == []


def test_summaries_findings_boost_increases_table_threshold():
    body = (
        "Prior work is summarized. We outline open questions."
    )
    # Without summaries, no table
    no_summary_hints = suggest_table_figure_hints({"results": body})
    no_summary_tables = [h for h in no_summary_hints if h.kind == "table"]
    # With summaries that have numeric key_findings cited in the body,
    # the boost may push us over the table threshold.
    summaries = [
        {
            "paper_id": "p1",
            "title": "P1",
            "key_findings": [
                "Achieved 95% accuracy on the test set",
                "Reduced error by 30% over baseline",
            ],
        },
        {
            "paper_id": "p2",
            "title": "P2",
            "key_findings": "Improved 12 patients out of 50 in the trial.",
        },
    ]
    # We need to put [@p1] in the body for the boost to apply
    body_with_cites = (
        "Prior work is summarized. [@p1] and [@p2] are compared. "
        "We outline open questions."
    )
    with_summary_hints = suggest_table_figure_hints(
        {"results": body_with_cites}, paper_summaries=summaries
    )
    with_summary_tables = [h for h in with_summary_hints if h.kind == "table"]
    assert len(with_summary_tables) >= 1
    # The 'no summary' baseline (no body cites either) should have no
    # table hint; with summary boost we have one.
    assert len(no_summary_tables) <= len(with_summary_tables)


def test_apply_table_figure_hints_appends_to_final_draft():
    ctx = _ctx_with_full_sections()
    # Inject a methodology section with a sequence
    ctx.section_drafts["methodology"] = (
        "First, we collect data. Second, we train. Third, we evaluate."
    )
    ctx.final_draft = "## 1 Introduction\n\nSome intro text.\n"
    hints = apply_table_figure_hints(ctx, target="final_draft")
    assert isinstance(hints, list)
    # The block is appended
    assert "Suggested Tables" in ctx.final_draft
    assert "[FIGURE_SUGGESTION" in ctx.final_draft or "[TABLE_SUGGESTION" in ctx.final_draft


def test_apply_table_figure_hints_creates_final_draft_if_missing():
    ctx = _ctx_with_full_sections()
    # Inject a methodology section with a sequence so hints are produced
    ctx.section_drafts["methodology"] = (
        "First, we collect data. Second, we preprocess. Third, we train. "
        "Finally, we evaluate on the held-out test set."
    )
    ctx.final_draft = None
    apply_table_figure_hints(ctx, target="final_draft")
    assert ctx.final_draft is not None
    assert "Suggested Tables" in ctx.final_draft


def test_apply_table_figure_hints_no_hints_noop():
    ctx = _ctx_with_full_sections()
    ctx.section_drafts = {"discussion": "Just discussion prose."}
    ctx.final_draft = "## Discussion\n\nProse.\n"
    apply_table_figure_hints(ctx, target="final_draft")
    # Nothing should be appended
    assert "Suggested Tables" not in ctx.final_draft
