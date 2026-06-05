"""
Real LLM integration tests for the draft pipeline.

Why this file exists
--------------------
Every other test in the suite mocks the LLM. That keeps CI fast and
deterministic, but it also means a prompt tweak can ship without anyone
noticing that the real model now hallucinates, returns malformed JSON,
or refuses the request outright. These tests are the canary.

When do they run?
- **CI without secrets** (no ``INTEGRATION_LLM_KEY`` env var): the
  whole file is skipped via ``pytestmark`` so the test run stays green.
- **On a developer machine** (or CI with a secret): they hit the real
  SiliconFlow / DeepSeek endpoint and assert the pipeline still
  produces the right shape of output.

The point isn't exhaustive coverage; it's "if you change a prompt, run
a real model and confirm it still works".

How to run locally
------------------
::

    export INTEGRATION_LLM_KEY=sk-...
    # Optional overrides (defaults to project settings):
    # export INTEGRATION_LLM_BASE_URL=https://api.siliconflow.cn/v1
    # export INTEGRATION_LLM_MODEL=deepseek-ai/DeepSeek-V3
    cd backend
    .venv/Scripts/python -m pytest tests/integration/test_real_llm.py -v
"""

from __future__ import annotations

import os
from typing import List, Optional

import pytest


# ---------------------------------------------------------------------------
# Skip gate: no env var = no real LLM = skip everything
# ---------------------------------------------------------------------------

INTEGRATION_KEY = os.environ.get("INTEGRATION_LLM_KEY", "").strip()
INTEGRATION_BASE_URL = os.environ.get(
    "INTEGRATION_LLM_BASE_URL", "https://api.siliconflow.cn/v1"
)
INTEGRATION_MODEL = os.environ.get(
    "INTEGRATION_LLM_MODEL", "deepseek-ai/DeepSeek-V3"
)

pytestmark = pytest.mark.integration

if not INTEGRATION_KEY:
    pytest.skip(
        "INTEGRATION_LLM_KEY not set; skipping real LLM integration tests",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test topic
# ---------------------------------------------------------------------------
# A single topic keeps the run focused and the cost predictable. Picked
# to give Scout enough results across OpenAlex/arXiv/Semantic Scholar
# while staying well within a single LLM context window.
TOPIC = "transformer neural networks for protein folding"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client():
    """Build an AsyncOpenAI client from the integration env vars.

    We don't reuse ``app.services.llm_factory.create_llm_client`` because
    it pulls from ``settings.siliconflow_api_key`` and the whole point
    of ``INTEGRATION_LLM_KEY`` is to be an explicit opt-in that doesn't
    depend on the developer's local config.
    """
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=INTEGRATION_KEY,
        base_url=INTEGRATION_BASE_URL,
        timeout=60.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scout_returns_at_least_three_candidates():
    """Scout discovers enough candidates from real paper APIs to feed
    Scribe. We don't require any LLM call here; Scout's job is
    API-only."""
    from app.services.draft_pipeline import DraftContext
    from app.services.draft_pipeline.phases import scout

    ctx = DraftContext(project_id="integration-test", topic=TOPIC, language="en")
    result = await scout(ctx, llm_client=None, use_llm_rerank=False, limit_per_source=20)

    assert len(result.candidates) >= 3, (
        f"Scout returned only {len(result.candidates)} candidates for "
        f"topic={TOPIC!r}; need at least 3 to feed Scribe. "
        f"Errors: {result.errors!r}"
    )
    # Every candidate has the minimum metadata Scribe relies on
    for c in result.candidates:
        assert c.paper_id, "candidate missing paper_id"
        assert c.title, "candidate missing title"


@pytest.mark.asyncio
async def test_scribe_produces_summaries_with_research_question():
    """Scribe (LLM-backed) turns a handful of Scout's candidates into
    structured summaries whose ``research_question`` is non-empty.

    This is the canary: if you tweak a prompt and the model now refuses
    or returns prose instead of JSON, this test fails."""
    from app.services.draft_pipeline import DraftContext
    from app.services.draft_pipeline.phases import scout, scribe, CandidatePaper

    client = _make_client()
    ctx = DraftContext(project_id="integration-test", topic=TOPIC, language="en")

    # Step 1: Scout (no LLM)
    scout_result = await scout(ctx, llm_client=None, use_llm_rerank=False, limit_per_source=20)
    assert scout_result.candidates, "Scout produced no candidates; Scribe has nothing to do"

    # Step 2: Scribe (LLM). Cap at 3 to keep token cost predictable and
    # the test fast. The real pipeline uses 5-20; we only need a smoke
    # check that the prompt still works.
    take = min(3, len(scout_result.candidates))
    selected: List[CandidatePaper] = scout_result.candidates[:take]
    scribe_result = await scribe(ctx, selected, client, batch_size=take, max_batches=1)

    assert len(scribe_result.summaries) >= 1, (
        "Scribe returned no summaries. The LLM likely refused or "
        "returned non-JSON. raw_llm_output:\n"
        f"{scribe_result.raw_llm_output[:500]!r}"
    )
    for s in scribe_result.summaries:
        assert s.paper_id, f"summary missing paper_id: {s!r}"
        assert s.research_question.strip(), (
            f"summary {s.paper_id!r} has empty research_question. "
            "Either the prompt regressed or the model is refusing."
        )


@pytest.mark.asyncio
async def test_run_research_phase_end_to_end_with_real_llm():
    """End-to-end: Scout -> Scribe -> Signal with a real model.

    The same contract the production ``/api/draft/projects/{id}/research``
    endpoint calls. If this test breaks, the whole draft pipeline is
    broken for real users."""
    from app.services.draft_pipeline import DraftContext, PhaseName, PhaseStatus
    from app.services.draft_pipeline.phases import run_research_phase

    client = _make_client()
    ctx = DraftContext(
        project_id="integration-test",
        topic=TOPIC,
        language="en",
        target_word_count=5000,
    )

    # Use a small Scribe budget so the test stays under ~30s. The
    # production pipeline configures batch_size=5, max_batches=4.
    result_ctx = await run_research_phase(ctx, llm_client=client)

    # 1. Phase must have completed (or failed gracefully — we want the
    #    FAILED marker to surface, not a silent crash).
    assert result_ctx.phase_results[PhaseName.RESEARCH].status in {
        PhaseStatus.SUCCEEDED,
        PhaseStatus.FAILED,
    }, "research phase did not record a terminal status"

    # 2. Scout produced enough candidates
    assert len(result_ctx.candidate_papers) >= 3, (
        f"Scout returned {len(result_ctx.candidate_papers)} candidates; "
        f"need >= 3. errors: {ctx.phase_results[PhaseName.RESEARCH].error!r}"
    )

    # 3. Scribe produced at least one summary
    assert len(result_ctx.paper_summaries) >= 1, (
        "Scribe produced no summaries. Possible prompt regression or "
        "model refusal."
    )

    # 4. Every summary has a non-empty research_question (the field
    #    the structure phase relies on for the research_question
    #    section of the outline).
    for s in result_ctx.paper_summaries:
        assert s.get("research_question", "").strip(), (
            f"summary {s.get('paper_id')!r} has empty research_question"
        )

    # 5. Signal produced at least one gap, OR the model returned an
    #    empty gaps list (also valid — some topics have no obvious
    #    gaps). We don't fail on zero gaps; we just verify the
    #    field is a list.
    assert isinstance(result_ctx.research_gaps, list)
    for g in result_ctx.research_gaps:
        assert g.get("title"), f"gap missing title: {g!r}"
