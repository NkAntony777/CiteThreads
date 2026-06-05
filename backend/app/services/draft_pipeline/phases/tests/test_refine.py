"""Tests for the refine phase (polish + voice + entropy + orchestrator)."""

from __future__ import annotations

import json

import pytest

from app.services.draft_pipeline import (
    DraftContext,
    PhaseName,
    PhaseStatus,
)
from app.services.draft_pipeline.phases import (
    ALL_PASSES,
    ENTROPY_SECTION_KEYS,
    PASS_ENTROPY,
    PASS_POLISH,
    PASS_VOICE,
    RefinePassResult,
    SECTION_NAMES,
    entropy,
    polish,
    run_refine_phase,
    voice,
)


# ---------------------------------------------------------------------------
# Mocks — same shape as test_compose.py for consistency
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
        self.calls.append(
            {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        )
        if not self._scripted:
            raise RuntimeError("MockCompletions ran out of scripted responses")
        return _MockResponse(self._scripted.pop(0))


class _MockLLMClient:
    def __init__(self, scripted):
        if isinstance(scripted, str):
            scripted = [scripted]
        self.chat = type("Chat", (), {"completions": _MockCompletions(scripted)})()


# ---------------------------------------------------------------------------
# Sample drafts
# ---------------------------------------------------------------------------


def _good_section_body(target_words: int = 200) -> str:
    """A minimally-cited section body that satisfies the Crafter
    contract (~1 cite per 200 words)."""
    para = " ".join(f"word{i}" for i in range(target_words))
    return f"# Section\n\n{para} [@p1].\n"


def _sample_drafts() -> dict[str, str]:
    """A full IMRaD-shaped drafts dict, each section ~150 words with
    one citation. Reused across tests."""
    return {
        "introduction": _good_section_body(150) + " [@p1].",
        "literature_review": _good_section_body(150) + " [@p2].",
        "methodology": _good_section_body(150) + " [@p3].",
        "results": _good_section_body(150) + " [@p1].",
        "discussion": _good_section_body(150) + " [@p2].",
        "conclusion": _good_section_body(150) + " [@p3].",
    }


def _ctx(**overrides) -> DraftContext:
    base = {
        "project_id": "p1",
        "topic": "Test topic",
        "target_word_count": 8000,
        "reference_ids": ["p1", "p2", "p3"],
    }
    base.update(overrides)
    return DraftContext(**base)


# ---------------------------------------------------------------------------
# Module-level sanity
# ---------------------------------------------------------------------------


def test_all_passes_list_contains_three_passes():
    assert set(ALL_PASSES) == {PASS_POLISH, PASS_VOICE, PASS_ENTROPY}
    assert len(ALL_PASSES) == 3


def test_entropy_section_keys_match_crafter_section_names():
    assert ENTROPY_SECTION_KEYS == SECTION_NAMES
    assert len(ENTROPY_SECTION_KEYS) == 6


# ---------------------------------------------------------------------------
# Polish tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polish_no_llm_returns_input_unchanged():
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await polish(ctx, drafts, llm_client=None)
    assert isinstance(result, RefinePassResult)
    assert result.drafts == drafts
    assert result.changed == []
    assert result.used_llm is False


@pytest.mark.asyncio
async def test_polish_empty_drafts_returns_empty():
    ctx = _ctx()
    result = await polish(ctx, {}, llm_client=_MockLLMClient([]))
    assert result.drafts == {}
    assert result.changed == []


@pytest.mark.asyncio
async def test_polish_with_llm_replaces_each_section():
    polished = {k: f"# {k}\n\nPolished content. [@p1]" for k in _sample_drafts()}
    scripted_responses = [
        json.dumps({"section_name": k, "polished": polished[k]}) for k in _sample_drafts()
    ]
    client = _MockLLMClient(scripted_responses)
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await polish(ctx, drafts, llm_client=client)
    assert result.used_llm is True
    # The returned body has a trailing newline appended by
    # _strip_metadata_sections; compare on the substantive content.
    for k in _sample_drafts():
        assert result.drafts[k].rstrip() == polished[k].rstrip()
    # All 6 sections changed.
    assert set(result.changed) == set(drafts.keys())
    # One LLM call per section.
    assert len(client.chat.completions.calls) == 6


@pytest.mark.asyncio
async def test_polish_falls_back_to_input_on_garbage():
    # LLM returns nonsense (no JSON, no "polished" key).
    client = _MockLLMClient(["not json at all"] * 6)
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await polish(ctx, drafts, llm_client=client)
    # Garbage per section → every section keeps the input.
    assert result.drafts == drafts
    assert result.changed == []
    assert result.used_llm is True  # LLM was called, even if it failed


@pytest.mark.asyncio
async def test_polish_partial_garbage_keeps_bad_sections_input():
    # First 2 sections return valid JSON, rest return garbage.
    polished_a = "# a\n\nPolished a [@p1]"
    polished_b = "# b\n\nPolished b [@p1]"
    good = [
        json.dumps({"section_name": "introduction", "polished": polished_a}),
        json.dumps({"section_name": "literature_review", "polished": polished_b}),
    ]
    bad = ["garbage"] * 4
    client = _MockLLMClient(good + bad)
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await polish(ctx, drafts, llm_client=client)
    # First 2 sections updated (rstrip to ignore _strip_metadata_sections'
    # trailing newline), last 4 kept input verbatim.
    assert result.drafts["introduction"].rstrip() == polished_a.rstrip()
    assert result.drafts["literature_review"].rstrip() == polished_b.rstrip()
    for k in ("methodology", "results", "discussion", "conclusion"):
        assert result.drafts[k] == drafts[k]
    assert set(result.changed) == {"introduction", "literature_review"}


@pytest.mark.asyncio
async def test_polish_handles_code_fenced_json():
    fenced = "```json\n" + json.dumps(
        {"polished": "# x\n\nFenced [@p1]"}
    ) + "\n```"
    client = _MockLLMClient([fenced] * 6)
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await polish(ctx, drafts, llm_client=client)
    assert "Fenced" in result.drafts["introduction"]


# ---------------------------------------------------------------------------
# Voice tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_no_llm_returns_input_unchanged():
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await voice(ctx, drafts, llm_client=None)
    assert result.drafts == drafts
    assert result.changed == []
    assert result.used_llm is False


@pytest.mark.asyncio
async def test_voice_with_llm_uses_sibling_excerpt_as_reference():
    voiced = {k: f"# {k}\n\nVoiced. [@p1]" for k in _sample_drafts()}
    scripted_responses = [
        json.dumps({"voiced": voiced[k]}) for k in _sample_drafts()
    ]
    client = _MockLLMClient(scripted_responses)
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await voice(ctx, drafts, llm_client=client)
    for k in _sample_drafts():
        assert result.drafts[k].rstrip() == voiced[k].rstrip()
    assert set(result.changed) == set(drafts.keys())
    # Verify the user message actually contains the sibling excerpt
    # for at least the first call.
    first_user = client.chat.completions.calls[0]["messages"][1]["content"]
    assert "Voice reference" in first_user
    assert "excerpt" in first_user.lower()


@pytest.mark.asyncio
async def test_voice_empty_drafts_returns_empty():
    ctx = _ctx()
    result = await voice(ctx, {}, llm_client=_MockLLMClient([]))
    assert result.drafts == {}


@pytest.mark.asyncio
async def test_voice_garbage_response_keeps_input():
    client = _MockLLMClient(["nope"] * 6)
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await voice(ctx, drafts, llm_client=client)
    assert result.drafts == drafts
    assert result.changed == []


# ---------------------------------------------------------------------------
# Entropy tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entropy_no_llm_returns_input_unchanged():
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await entropy(ctx, drafts, llm_client=None)
    assert result.drafts == drafts
    assert result.changed == []
    assert result.used_llm is False


@pytest.mark.asyncio
async def test_entropy_empty_drafts_returns_empty():
    ctx = _ctx()
    result = await entropy(ctx, {}, llm_client=_MockLLMClient([]))
    assert result.drafts == {}


@pytest.mark.asyncio
async def test_entropy_with_llm_updates_all_six_sections():
    new_sections = {
        k: f"# {k}\n\nResolved across sections. [@p1]" for k in ENTROPY_SECTION_KEYS
    }
    payload = {
        "sections": new_sections,
        "issues_found": [
            {
                "kind": "term",
                "sections": ["methodology", "results"],
                "description": "Term mismatch",
                "resolution": "canonicalised",
                "rationale": "Methodology introduced the term first",
            }
        ],
    }
    client = _MockLLMClient([json.dumps(payload)])
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await entropy(ctx, drafts, llm_client=client)
    for k in ENTROPY_SECTION_KEYS:
        assert result.drafts[k].rstrip() == new_sections[k].rstrip()
    assert set(result.changed) == set(drafts.keys())
    # Issues propagated.
    assert len(result.issues) == 1
    assert result.issues[0]["kind"] == "term"


@pytest.mark.asyncio
async def test_entropy_partial_response_keeps_missing_sections_input():
    # LLM only returns 2 sections; the other 4 should be untouched.
    partial = {
        "introduction": "# introduction\n\nResolved [@p1]",
        "literature_review": "# literature_review\n\nResolved [@p1]",
    }
    payload = {"sections": partial, "issues_found": []}
    client = _MockLLMClient([json.dumps(payload)])
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await entropy(ctx, drafts, llm_client=client)
    # Updated keys come from the response (rstrip for trailing newline).
    assert result.drafts["introduction"].rstrip() == partial["introduction"].rstrip()
    assert result.drafts["literature_review"].rstrip() == partial["literature_review"].rstrip()
    # Missing keys retain the input.
    for k in ("methodology", "results", "discussion", "conclusion"):
        assert result.drafts[k] == drafts[k]


@pytest.mark.asyncio
async def test_entropy_garbage_response_keeps_input_with_warning(caplog):
    client = _MockLLMClient(["this is not json at all"])
    ctx = _ctx()
    drafts = _sample_drafts()
    result = await entropy(ctx, drafts, llm_client=client)
    assert result.drafts == drafts
    assert result.changed == []
    # raw_llm_output retained for debugging.
    assert "not json" in result.raw_llm_output


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_refine_phase_runs_all_three_passes_sequentially():
    """When LLM is absent, run_refine_phase applies all 3 passes
    as no-ops and records the pass names on the COMPOSE PhaseResult."""
    ctx = _ctx()
    ctx.section_drafts = _sample_drafts()
    out = await run_refine_phase(ctx, llm_client=None)
    assert out.section_drafts == _sample_drafts()
    # COMPOSE phase marked SUCCEEDED with the 3 passes recorded.
    pr = out.phase_results[PhaseName.COMPOSE]
    assert pr.status is PhaseStatus.SUCCEEDED
    assert set(pr.passes) == set(ALL_PASSES)


@pytest.mark.asyncio
async def test_run_refine_phase_can_select_specific_passes():
    ctx = _ctx()
    ctx.section_drafts = _sample_drafts()
    out = await run_refine_phase(ctx, llm_client=None, passes=[PASS_POLISH])
    pr = out.phase_results[PhaseName.COMPOSE]
    assert pr.passes == [PASS_POLISH]


@pytest.mark.asyncio
async def test_run_refine_phase_unknown_pass_falls_back_to_all():
    ctx = _ctx()
    ctx.section_drafts = _sample_drafts()
    # "bogus" is not a real pass; the orchestrator should log a
    # warning and fall back to all three real passes.
    out = await run_refine_phase(ctx, llm_client=None, passes=["bogus"])
    pr = out.phase_results[PhaseName.COMPOSE]
    assert set(pr.passes) == set(ALL_PASSES)


@pytest.mark.asyncio
async def test_run_refine_phase_with_llm_passes_inputs_through_passes():
    """The output of polish becomes the input to voice, which becomes
    the input to entropy. We verify this by giving each pass a
    deterministic response that tags its own output."""
    drafts = _sample_drafts()

    # Polish response: tag each section as "polished".
    polished = {k: f"# {k}\n\nPOLISHED [@p1]" for k in drafts}
    polish_scripted = [
        json.dumps({"polished": polished[k]}) for k in SECTION_NAMES
    ]
    # Voice response: same shape, tag as "voiced".
    voiced = {k: f"# {k}\n\nVOICED [@p1]" for k in drafts}
    voice_scripted = [
        json.dumps({"voiced": voiced[k]}) for k in SECTION_NAMES
    ]
    # Entropy response: a single payload covering all six sections.
    entropy_payload = {
        "sections": {k: f"# {k}\n\nENTROPY [@p1]" for k in SECTION_NAMES},
        "issues_found": [],
    }
    entropy_scripted = [json.dumps(entropy_payload)]
    scripted = polish_scripted + voice_scripted + entropy_scripted
    client = _MockLLMClient(scripted)

    ctx = _ctx()
    ctx.section_drafts = dict(drafts)
    out = await run_refine_phase(ctx, llm_client=client)

    # 6 polish + 6 voice + 1 entropy = 13 LLM calls
    assert len(client.chat.completions.calls) == 13
    # Final section_drafts should reflect the entropy pass's output
    # (rstrip to normalise the trailing newline added by
    # _strip_metadata_sections).
    for k in SECTION_NAMES:
        assert out.section_drafts[k].rstrip() == entropy_payload["sections"][k].rstrip()


@pytest.mark.asyncio
async def test_run_refine_phase_does_not_clobber_existing_passes_list():
    """Calling run_refine_phase twice (e.g. once with polish, once
    with voice) should accumulate both pass names on the COMPOSE
    PhaseResult rather than overwriting."""
    ctx = _ctx()
    ctx.section_drafts = _sample_drafts()
    await run_refine_phase(ctx, llm_client=None, passes=[PASS_POLISH])
    await run_refine_phase(ctx, llm_client=None, passes=[PASS_VOICE])
    pr = ctx.phase_results[PhaseName.COMPOSE]
    assert set(pr.passes) == {PASS_POLISH, PASS_VOICE}


@pytest.mark.asyncio
async def test_run_refine_phase_marks_failed_on_llm_error():
    class _FailClient:
        class chat:
            class completions:
                async def create(*a, **k):
                    raise RuntimeError("refine-boom")

    ctx = _ctx()
    ctx.section_drafts = _sample_drafts()
    with pytest.raises(RuntimeError, match="refine-boom"):
        await run_refine_phase(ctx, llm_client=_FailClient(), passes=[PASS_POLISH])
    assert ctx.phase_results[PhaseName.COMPOSE].status is PhaseStatus.FAILED
    assert "refine-boom" in ctx.phase_results[PhaseName.COMPOSE].error


@pytest.mark.asyncio
async def test_run_refine_phase_on_empty_section_drafts_is_safe():
    ctx = _ctx()
    # Don't populate section_drafts at all.
    out = await run_refine_phase(ctx, llm_client=None)
    assert out.section_drafts == {}
    pr = out.phase_results[PhaseName.COMPOSE]
    assert pr.status is PhaseStatus.SUCCEEDED
    assert set(pr.passes) == set(ALL_PASSES)
