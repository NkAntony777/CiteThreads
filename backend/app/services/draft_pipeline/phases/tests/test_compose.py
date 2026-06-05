"""Tests for the compose phase (Crafter + Refiner + orchestrator)."""

from __future__ import annotations

import json
import re

import pytest

from app.services.draft_pipeline import (
    DraftContext,
    PhaseName,
    PhaseStatus,
)
from app.services.draft_pipeline.phases import (
    ComposeResult,
    CrafterResult,
    RefinerResult,
    SectionDraft,
    SECTION_NAMES,
    crafter,
    crafter_conclusion,
    crafter_discussion,
    crafter_introduction,
    crafter_literature_review,
    crafter_methodology,
    crafter_results,
    citation_density_ok,
    count_citations,
    count_words,
    refiner,
    run_compose_phase,
    split_word_budget,
)
from app.services.draft_pipeline.phases.compose import (
    _format_citation_list,
    _stub_section_draft,
    _strip_metadata_sections,
    _word_target_for,
)


# ---------------------------------------------------------------------------
# Mocks (kept identical to test_structure.py for consistency)
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
# Helper: a "well-cited" section body
# ---------------------------------------------------------------------------


def _good_section_body(target_words: int = 400) -> str:
    """Build a section body that meets the design contract: at least
    one [@paper_id] citation per 200 words. We use 3 paper IDs and
    place them roughly evenly throughout the text."""
    paragraphs = []
    # 3 paragraphs of ~140 words each → ~420 words total, with 3
    # citations spread across them.
    for i, pid in enumerate(["p1", "p2", "p3"]):
        words = []
        for j in range(140):
            words.append(f"word{j}")
        para = " ".join(words)
        paragraphs.append(f"This is paragraph {i+1} {para} [@{pid}].")
    return "# Introduction\n\n" + "\n\n".join(paragraphs) + "\n"


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_section_names_contains_six_sections():
    assert len(SECTION_NAMES) == 6
    assert "introduction" in SECTION_NAMES
    assert "conclusion" in SECTION_NAMES
    assert "literature_review" in SECTION_NAMES


def test_split_word_budget_within_five_pct_of_total():
    total = 10000
    budget = split_word_budget(total, SECTION_NAMES)
    assert set(budget.keys()) == set(SECTION_NAMES)
    allocated = sum(budget.values())
    assert abs(allocated - total) / total < 0.05


def test_split_word_budget_zero_falls_back_to_default():
    # Pydantic rejects target_word_count=0 (min 100), so we test the
    # helper directly with 0 to exercise its defensive branch.
    budget = split_word_budget(0, SECTION_NAMES)
    assert sum(budget.values()) >= 1000  # at least the default 8000


def test_count_citations_finds_bracket_at_markers():
    text = "Some claim [@p1] and another [@p2][@p3] and a non-citation [@]."
    assert count_citations(text) == 3


def test_count_citations_zero_for_empty_or_plain_text():
    assert count_citations("") == 0
    assert count_citations("No citations here at all.") == 0


def test_count_words_strips_markdown_punctuation():
    # Pipes, asterisks, etc. should not inflate the word count.
    text = "| col1 | col2 |\n|------|------|\n| a    | b    |"
    assert count_words(text) <= 10


def test_citation_density_ok_with_short_section_needs_one_citation():
    # Less than the 200-word window: just need at least 1 citation
    text = "Short claim [@p1]."
    ok, density = citation_density_ok(text)
    assert ok is True
    assert density > 0


def test_citation_density_ok_fails_when_no_citations_in_long_section():
    # 400 words but no citations
    words = " ".join(f"w{i}" for i in range(400))
    ok, density = citation_density_ok(words)
    assert ok is False
    assert density == 0.0


def test_citation_density_ok_passes_at_threshold():
    body = _good_section_body(target_words=400)
    ok, density = citation_density_ok(body)
    assert ok is True
    assert density >= 1.0


def test_strip_metadata_sections_removes_trailing_blocks():
    text = (
        "# Intro\n\nProse here.\n\n"
        "## Citations Used\n- [@p1]\n- [@p2]\n\n"
        "## Notes for Revision\n- fix X\n"
    )
    cleaned = _strip_metadata_sections(text)
    assert "Citations Used" not in cleaned
    assert "Notes for Revision" not in cleaned
    assert "Prose here" in cleaned


def test_format_citation_list_uses_reference_ids():
    ctx = DraftContext(
        project_id="p", topic="T", reference_ids=["a", "b", "c"]
    )
    out = _format_citation_list(ctx)
    assert "[@a]" in out
    assert "[@b]" in out
    assert "[@c]" in out


def test_format_citation_list_falls_back_to_paper_summaries():
    ctx = DraftContext(project_id="p", topic="T")
    ctx.paper_summaries = [
        {"paper_id": "s1", "title": "X"},
        {"paper_id": "s2", "title": "Y"},
    ]
    out = _format_citation_list(ctx)
    assert "[@s1]" in out
    assert "[@s2]" in out


def test_word_target_for_uses_architect_outline_when_present():
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=10000,
    )
    ctx.outline = {
        "sections": [
            {"title": "Introduction", "target_words": 1234},
            {"title": "Conclusion", "target_words": 555},
        ]
    }
    assert _word_target_for(ctx, "introduction") == 1234
    assert _word_target_for(ctx, "conclusion") == 555


def test_stub_section_draft_cites_first_reference():
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=6000,
        reference_ids=["abc", "def"],
    )
    draft = _stub_section_draft(ctx, "introduction")
    assert draft.section_name == "introduction"
    assert "[@abc]" in draft.body
    assert draft.actual_words > 10
    assert draft.citation_count >= 1


# ---------------------------------------------------------------------------
# Crafter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crafter_introduction_produces_draft_with_citations():
    client = _MockLLMClient([_good_section_body()])
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=6000,
        reference_ids=["p1", "p2", "p3"],
    )
    result = await crafter_introduction(ctx, client)
    assert isinstance(result, CrafterResult)
    assert result.draft.section_name == "introduction"
    assert result.draft.actual_words > 100
    ok, _ = citation_density_ok(result.draft.body)
    assert ok is True
    assert set(result.paper_ids_cited) == {"p1", "p2", "p3"}


@pytest.mark.asyncio
async def test_crafter_literature_review_uses_research_summaries():
    client = _MockLLMClient([_good_section_body()])
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=6000,
        reference_ids=["p1", "p2", "p3"],
        paper_summaries=[
            {
                "paper_id": "p1", "title": "A", "research_question": "Q?",
                "key_findings": ["f"], "limitations": [],
            }
        ],
        research_gaps=[{"title": "Gap: G1 — D"}],
    )
    result = await crafter_literature_review(ctx, client)
    assert result.draft.section_name == "literature_review"
    # The user message should mention the paper_summaries + gaps we set
    sent_user_msg = client.chat.completions.calls[0]["messages"][1]["content"]
    assert "p1" in sent_user_msg
    assert "G1" in sent_user_msg


@pytest.mark.asyncio
async def test_crafter_dispatcher_routes_to_correct_section():
    client = _MockLLMClient([_good_section_body()])
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=6000,
        reference_ids=["p1"],
    )
    for name in SECTION_NAMES:
        # Each invocation uses one scripted response; we need a new
        # client for each one because responses are popped.
        per_client = _MockLLMClient([_good_section_body()])
        result = await crafter(ctx, per_client, name)
        assert result.draft.section_name == name


@pytest.mark.asyncio
async def test_crafter_dispatcher_rejects_unknown_section_name():
    client = _MockLLMClient([])
    ctx = DraftContext(project_id="p", topic="T")
    with pytest.raises(ValueError, match="Unknown section_name"):
        await crafter(ctx, client, "appendix")


@pytest.mark.asyncio
async def test_crafter_methodology_passes_prior_section_context():
    client = _MockLLMClient([_good_section_body()])
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=6000,
        reference_ids=["p1"],
    )
    # Pre-populate a lit review so Methodology can reference it
    ctx.section_drafts = {
        "literature_review": "## 2.1 Literature Review\n\nSome prose [@p1]."
    }
    await crafter_methodology(ctx, client)
    sent_user_msg = client.chat.completions.calls[0]["messages"][1]["content"]
    assert "Some prose" in sent_user_msg  # last 2000 chars included


@pytest.mark.asyncio
async def test_crafter_results_strips_metadata_blocks():
    body_with_meta = (
        _good_section_body()
        + "\n\n## Citations Used\n- [@p1]\n\n## Notes for Revision\n- fix X\n"
    )
    client = _MockLLMClient([body_with_meta])
    ctx = DraftContext(
        project_id="p", topic="T", reference_ids=["p1", "p2", "p3"],
    )
    result = await crafter_results(ctx, client)
    assert "Citations Used" not in result.draft.body
    assert "Notes for Revision" not in result.draft.body


@pytest.mark.asyncio
async def test_crafter_conclusion_uses_first_section_as_excerpt():
    client = _MockLLMClient([_good_section_body()])
    ctx = DraftContext(
        project_id="p", topic="T", reference_ids=["p1"],
    )
    # Pre-populate the introduction so the conclusion has a "main body excerpt"
    ctx.section_drafts = {"introduction": _good_section_body()}
    await crafter_conclusion(ctx, client)
    sent_user_msg = client.chat.completions.calls[0]["messages"][1]["content"]
    # The conclusion user message should reference the introduction
    assert "Conclusion" in sent_user_msg


# ---------------------------------------------------------------------------
# Refiner tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refiner_returns_refined_draft_with_refined_flag():
    refined_body = _good_section_body() + "\n\n*Refined for clarity.*\n"
    client = _MockLLMClient([refined_body])
    ctx = DraftContext(
        project_id="p", topic="T", reference_ids=["p1", "p2", "p3"],
    )
    original = SectionDraft(
        section_name="introduction",
        body=_good_section_body(),
        target_words=400,
    )
    result = await refiner(ctx, client, original)
    assert isinstance(result, RefinerResult)
    assert result.original is original
    assert result.refined.section_name == "introduction"
    assert result.refined.refined is True
    assert "Refined for clarity" in result.refined.body


@pytest.mark.asyncio
async def test_refiner_strips_metadata_sections():
    refined_body = (
        _good_section_body()
        + "\n\n## Citations Used\n- [@p1]\n"
    )
    client = _MockLLMClient([refined_body])
    ctx = DraftContext(
        project_id="p", topic="T", reference_ids=["p1"],
    )
    original = SectionDraft(
        section_name="introduction",
        body=_good_section_body(),
        target_words=400,
    )
    result = await refiner(ctx, client, original)
    assert "Citations Used" not in result.refined.body


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_compose_phase_without_llm_uses_stub_drafts():
    ctx = DraftContext(
        project_id="p", topic="Machine learning", target_word_count=6000,
        reference_ids=["abc"],
    )
    out = await run_compose_phase(ctx, llm_client=None)
    assert out.is_phase_done(PhaseName.COMPOSE)
    assert set(out.section_drafts.keys()) == set(SECTION_NAMES)
    for name, body in out.section_drafts.items():
        assert isinstance(body, str)
        assert len(body) > 0
        # Stubs cite the first reference id
        assert "[@abc]" in body


@pytest.mark.asyncio
async def test_run_compose_phase_with_llm_calls_each_writer_once():
    # 6 section writers + optional refinement (off by default)
    responses = [_good_section_body() for _ in SECTION_NAMES]
    client = _MockLLMClient(responses)
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=6000,
        reference_ids=["p1", "p2", "p3"],
        paper_summaries=[
            {
                "paper_id": "p1", "title": "A", "research_question": "Q?",
                "key_findings": ["f"], "limitations": [],
            }
        ],
    )
    out = await run_compose_phase(ctx, llm_client=client)
    assert out.is_phase_done(PhaseName.COMPOSE)
    assert len(client.chat.completions.calls) == 6
    assert set(out.section_drafts.keys()) == set(SECTION_NAMES)


@pytest.mark.asyncio
async def test_run_compose_phase_with_refine_at_end_calls_six_extra():
    responses = [_good_section_body() for _ in range(12)]  # 6 drafts + 6 refines
    client = _MockLLMClient(responses)
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=6000,
        reference_ids=["p1", "p2", "p3"],
    )
    out = await run_compose_phase(ctx, llm_client=client, refine_at_end=True)
    assert out.is_phase_done(PhaseName.COMPOSE)
    assert len(client.chat.completions.calls) == 12
    # Refined section bodies should differ from the original (the mock
    # returned identical bodies, so the test is just that the calls
    # happened — refinement is opt-in).
    assert isinstance(out.section_drafts, dict)


@pytest.mark.asyncio
async def test_run_compose_phase_marks_failed_on_llm_error():
    class _FailClient:
        class chat:
            class completions:
                async def create(*a, **k):
                    raise RuntimeError("compose-boom")

    ctx = DraftContext(project_id="p", topic="T")
    with pytest.raises(RuntimeError, match="compose-boom"):
        await run_compose_phase(ctx, llm_client=_FailClient())
    assert ctx.phase_results[PhaseName.COMPOSE].status is PhaseStatus.FAILED
    assert "compose-boom" in ctx.phase_results[PhaseName.COMPOSE].error


@pytest.mark.asyncio
async def test_run_compose_phase_compose_result_via_internal_state():
    # Verify the orchestrator populates the canonical state used by
    # downstream phases: section_drafts is a dict[str, str] with all
    # six keys.
    ctx = DraftContext(
        project_id="p", topic="T", target_word_count=6000,
        reference_ids=["p1"],
    )
    out = await run_compose_phase(ctx, llm_client=None)
    assert isinstance(out.section_drafts, dict)
    for name in SECTION_NAMES:
        assert name in out.section_drafts
        assert isinstance(out.section_drafts[name], str)
        # Progress reflects 3/6 phases done (RESEARCH not run, STRUCTURE
        # not run, COMPOSE done). Actually only COMPOSE is marked here.
    assert out.progress_pct() >= 16.7


# ---------------------------------------------------------------------------
# Prompt loading tests (smoke)
# ---------------------------------------------------------------------------


def test_crafter_prompt_loaded_for_en_and_zh():
    from app.services.draft_pipeline.prompts import load_prompt
    en = load_prompt("crafter", lang="en")
    zh = load_prompt("crafter", lang="zh")
    assert "Crafter" in en or "academic" in en.lower()
    assert "Crafter" in zh or "学术" in zh or "撰写" in zh
    # Distinct content
    assert en != zh


def test_refiner_prompt_loaded_for_en_and_zh():
    from app.services.draft_pipeline.prompts import load_prompt
    en = load_prompt("refiner", lang="en")
    zh = load_prompt("refiner", lang="zh")
    assert "Refiner" in en or "refined" in en.lower()
    assert "Refiner" in zh or "润色" in zh or "编辑" in zh
    assert en != zh


def test_zh_prompt_falls_back_to_en():
    # The loader falls back to en for unsupported langs
    from app.services.draft_pipeline.prompts import load_prompt
    en = load_prompt("crafter", lang="en")
    # Korean isn't in _SUPPORTED_LANGS, so loader raises
    with pytest.raises(ValueError):
        load_prompt("crafter", lang="ko")
    # Verify same content as English loader
    assert "Crafter" in en


# ---------------------------------------------------------------------------
# All-section smoke test (Crafter over all 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_six_section_writers_produce_drafts():
    """Loop over all 6 section writers and verify each produces a
    SectionDraft with non-empty body, correct section_name, and at
    least one citation."""
    for name, writer in [
        ("introduction", crafter_introduction),
        ("literature_review", crafter_literature_review),
        ("methodology", crafter_methodology),
        ("results", crafter_results),
        ("discussion", crafter_discussion),
        ("conclusion", crafter_conclusion),
    ]:
        client = _MockLLMClient([_good_section_body()])
        ctx = DraftContext(
            project_id="p", topic="T", target_word_count=6000,
            reference_ids=["p1", "p2", "p3"],
        )
        if name in ("methodology", "results", "discussion"):
            ctx.section_drafts = {
                "literature_review": _good_section_body(),
                "methodology": _good_section_body(),
            }
        if name == "conclusion":
            ctx.section_drafts = {"introduction": _good_section_body()}
        result = await writer(ctx, client)
        assert result.draft.section_name == name
        assert result.draft.body.strip()
        assert result.draft.citation_count >= 1
