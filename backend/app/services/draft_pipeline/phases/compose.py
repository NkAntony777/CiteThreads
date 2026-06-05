"""
Compose phase — Crafter (per section) + Refiner + orchestrator.

Faithful port of opendraft's ``engine/phases/compose.py`` adapted to our
async stack, ``DraftContext``, and CTDP citation style (``[@paper_id]``
inline markers, as opposed to opendraft's ``{cite_XXX}`` template
tokens). The 6 user-visible sections match the opendraft IMRaD layout
minus the thesis appendices bundle, which is collapsed into the
Conclusion phase per our pipeline design.

Six section writers
-------------------
- ``crafter_introduction``      — hooks + research question + roadmap
- ``crafter_literature_review`` — surveys cited literature, gaps
- ``crafter_methodology``       — methods/approach from cited sources
- ``crafter_results``           — synthesised findings, tables
- ``crafter_discussion``        — interpretation, limitations, future work
- ``crafter_conclusion``        — recap of contribution, impact

All Crafter functions share the same system prompt
(``prompts/{en,zh}/crafter.md``) but receive a section-specific user
message that frames the section's purpose, target word count, and the
relevant context window from earlier phases.

The ``refiner`` function takes a single section draft + a refinement
instruction and returns the improved text. The orchestrator may invoke
it once at the end of the phase, but it is opt-in (off by default) to
keep the LLM call count predictable.

Orchestrator
-----------
``run_compose_phase`` marks ``PhaseName.COMPOSE`` RUNNING, iterates
the six sections in order, populates ``ctx.section_drafts`` (the
canonical handoff to the downstream Validate + Compile phases), and
marks the phase SUCCEEDED or FAILED.

Graceful degradation
--------------------
With no LLM client, the orchestrator produces six stub drafts whose
content is the topic + a brief scaffolding paragraph. This keeps the
pipeline unblocked for tests and for the front-end preview path
without requiring live LLM calls.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from openai import AsyncOpenAI

from ..context import DraftContext, PhaseName, PhaseStatus
from ..prompts import load_prompt
from .research import _resolve_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants and result dataclasses
# ---------------------------------------------------------------------------


# The 6 user-visible sections written by Crafter. The order matters:
# the orchestrator iterates over this list, and downstream phases
# (Validate, Compile) read ``ctx.section_drafts`` using these keys.
SECTION_NAMES: List[str] = [
    "introduction",
    "literature_review",
    "methodology",
    "results",
    "discussion",
    "conclusion",
]

# Default IMRaD-style weight distribution. Matches the heuristic
# outline in structure.py so the Compose phase's stub output
# is consistent with what Architect would have produced.
_DEFAULT_SECTION_WEIGHTS = {
    "introduction": 0.15,
    "literature_review": 0.25,
    "methodology": 0.15,
    "results": 0.20,
    "discussion": 0.15,
    "conclusion": 0.10,
}

# Minimum 1 inline citation per N words (per the design contract
# documented in the task spec). We count ``[@paper_id]`` markers as
# the canonical CTDP inline citation form.
_CITATION_DENSITY_WINDOW_WORDS = 200


@dataclass
class SectionDraft:
    """A single section's draft as written by the Crafter (and
    optionally refined). Used as the in-memory return value of each
    ``crafter_*`` function and as the value type stored in
    ``ctx.section_drafts``."""

    section_name: str
    body: str
    target_words: int
    actual_words: int = 0
    citation_count: int = 0
    citation_density_per_200: float = 0.0
    raw_llm_output: str = ""
    refined: bool = False

    def to_dict(self) -> dict:
        return {
            "section_name": self.section_name,
            "body": self.body,
            "target_words": self.target_words,
            "actual_words": self.actual_words,
            "citation_count": self.citation_count,
            "citation_density_per_200": self.citation_density_per_200,
            "refined": self.refined,
        }


@dataclass
class CrafterResult:
    """The result returned by each ``crafter_*`` function. Wraps a
    ``SectionDraft`` plus any structured metadata the LLM produced."""

    draft: SectionDraft
    paper_ids_cited: List[str] = field(default_factory=list)
    tables: int = 0
    headings: int = 0


@dataclass
class RefinerResult:
    """The result returned by ``refiner``. The original draft is
    preserved for comparison and for unit tests."""

    original: SectionDraft
    refined: SectionDraft


@dataclass
class ComposeResult:
    """Aggregate result of one full ``run_compose_phase`` call."""

    drafts: List[SectionDraft] = field(default_factory=list)
    refined: List[str] = field(default_factory=list)
    used_llm: bool = False
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def split_word_budget(total_words: int, section_names: Iterable[str]) -> dict:
    """Split ``total_words`` across the given ``section_names`` using
    the default IMRaD weight distribution. Unknown section names are
    treated as 0% (they should not appear at the call site)."""
    if total_words <= 0:
        total_words = 8000  # match DraftContext default

    # Re-normalise against the names we were actually given.
    weights = {
        name: _DEFAULT_SECTION_WEIGHTS.get(name, 0.0)
        for name in section_names
    }
    weight_sum = sum(weights.values()) or 1.0
    return {
        name: max(1, int(round(total_words * (w / weight_sum))))
        for name, w in weights.items()
    }


def count_citations(text: str) -> int:
    """Count CTDP-style inline citations ``[@paper_id]``. We use a
    simple bracket pattern; the alternative opendraft-style
    ``{cite_XXX}`` is not used here."""
    if not text:
        return 0
    return len(re.findall(r"\[@[^\]\s]+\]", text))


def count_words(text: str) -> int:
    """Approximate word count by splitting on whitespace. We strip
    markdown table syntax lightly so tables don't inflate the count
    with cell separators."""
    if not text:
        return 0
    # Drop table pipes and headings markers so they don't count as
    # extra "words".
    cleaned = re.sub(r"[|`*#>\-]+", " ", text)
    return len([w for w in cleaned.split() if w])


def citation_density_ok(text: str, min_per_200: float = 1.0) -> tuple[bool, float]:
    """Return ``(is_ok, density_per_200)`` for the given text.

    Density is computed as ``citations / words * 200``. We require
    ``density >= min_per_200`` AND at least 1 citation present
    whenever the text is longer than the density window.
    """
    words = count_words(text)
    cites = count_citations(text)
    if words == 0:
        return False, 0.0
    density = cites * _CITATION_DENSITY_WINDOW_WORDS / words
    if words < _CITATION_DENSITY_WINDOW_WORDS:
        # Short section: require at least 1 citation
        ok = cites >= 1
    else:
        ok = density >= min_per_200
    return ok, round(density, 2)


def _language_instruction(language: str) -> str:
    """Return the language instruction line that opendraft appends to
    every Crafter user message. Keeping the exact phrasing makes the
    behaviour 1:1 with opendraft for downstream consistency."""
    if (language or "").lower().startswith("zh"):
        return "\n\n请用中文撰写整个章节（包括所有标题和段落）。"
    return "\n\nPlease write the entire section in English."


def _format_custom_instructions(custom: Optional[str]) -> str:
    """Render the optional user-supplied rewrite guidance as a block
    appended to the crafter's user message.

    Empty / whitespace-only strings collapse to "" so callers can pass
    an unset form field without a noisy placeholder.
    """
    text = (custom or "").strip()
    if not text:
        return ""
    return (
        "\n\n**ADDITIONAL REWRITE GUIDANCE (from the user — must be "
        "honoured in this draft):**\n" + text
    )


def _format_paper_summaries_for_prompt(ctx: DraftContext, max_chars: int = 4000) -> str:
    """Render the Scribe output as a compact JSON block for inclusion
    in Crafter's user message."""
    summaries = ctx.paper_summaries or []
    if not summaries:
        return "(no paper summaries available)"
    payload = []
    for s in summaries[:30]:
        if not isinstance(s, dict):
            continue
        payload.append(
            {
                "paper_id": s.get("paper_id", ""),
                "title": s.get("title", ""),
                "research_question": (s.get("research_question") or "")[:200],
                "methodology": (s.get("methodology") or "")[:200],
                "key_findings": (s.get("key_findings") or [])[:4],
            }
        )
    blob = json.dumps(payload, ensure_ascii=False, indent=1)
    if len(blob) > max_chars:
        blob = blob[: max_chars - 3] + "..."
    return blob


def _format_citation_list(ctx: DraftContext) -> str:
    """Build a "Available citations" list from the project's
    ``reference_ids``. Crafter is told to use ``[@paper_id]`` markers
    pointing at one of these IDs."""
    ids = list(ctx.reference_ids or [])
    if not ids:
        # Fall back to paper_summaries paper_ids so we have at least
        # *something* the Crafter can cite.
        for s in ctx.paper_summaries or []:
            if isinstance(s, dict) and s.get("paper_id"):
                ids.append(s["paper_id"])
    if not ids:
        return "(no citation database available — use placeholders)"
    return "\n".join(f"- [@{pid}]" for pid in ids)


def _strip_metadata_sections(text: str) -> str:
    """Best-effort cleanup of the metadata sections Crafter is told
    never to produce (e.g. ``## Citations Used``). Removes any
    trailing block of those headings. Defensive only."""
    if not text:
        return text
    pattern = re.compile(
        r"\n##\s*(Citations Used|Notes for Revision|Word Count Breakdown)\b.*$",
        flags=re.DOTALL | re.IGNORECASE,
    )
    return pattern.sub("", text).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Crafter section writers
# ---------------------------------------------------------------------------


async def _crafter_one_section(
    ctx: DraftContext,
    section_name: str,
    llm_client: AsyncOpenAI,
    *,
    user_message: str,
) -> CrafterResult:
    """Common implementation shared by all 6 Crafter section functions.

    Calls the LLM with the crafter system prompt + the section-specific
    user message and parses the response into a ``CrafterResult``. The
    caller is responsible for writing the result back into
    ``ctx.section_drafts``."""
    prompt_body = load_prompt("crafter", lang=ctx.language)

    response = await llm_client.chat.completions.create(
        model=_resolve_model(llm_client),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_message},
        ],
        temperature=0.4,
        max_tokens=4000,
    )
    raw = (response.choices[0].message.content or "").strip()
    body = _strip_metadata_sections(raw)

    # Extract citation IDs actually used.
    cited_ids = re.findall(r"\[@([^\]\s]+)\]", body)

    # Cheap structural counts.
    tables = body.count("\n|")  # rough markdown table row count
    headings = len(re.findall(r"^#{1,4}\s", body, flags=re.MULTILINE))

    target = _word_target_for(ctx, section_name)
    return CrafterResult(
        draft=SectionDraft(
            section_name=section_name,
            body=body,
            target_words=target,
            actual_words=count_words(body),
            citation_count=count_citations(body),
            citation_density_per_200=citation_density_ok(body)[1],
            raw_llm_output=raw,
        ),
        paper_ids_cited=sorted(set(cited_ids)),
        tables=tables,
        headings=headings,
    )


def _word_target_for(ctx: DraftContext, section_name: str) -> int:
    """Return the per-section target word count. If the architect
    already filled in a 6-section outline, use that; otherwise fall
    back to the default weight split."""
    outline = ctx.outline if isinstance(ctx.outline, dict) else None
    sections = (outline or {}).get("sections", []) or []
    title_lookup = {
        "introduction": "introduction",
        "literature_review": "literature review",
        "methodology": "methodology",
        "results": "results",
        "discussion": "discussion",
        "conclusion": "conclusion",
    }
    needle = title_lookup.get(section_name, section_name)
    for s in sections:
        if not isinstance(s, dict):
            continue
        title = (s.get("title") or "").strip().lower()
        if title == needle:
            try:
                tw = int(s.get("target_words") or 0)
                if tw > 0:
                    return tw
            except (TypeError, ValueError):
                pass
    return split_word_budget(ctx.target_word_count, SECTION_NAMES).get(
        section_name, 0
    )


def _stub_section_draft(ctx: DraftContext, section_name: str) -> SectionDraft:
    """Produce a short, deterministic stub draft for a single section.
    Used when no LLM is configured so downstream phases still have
    something concrete to operate on."""
    target = _word_target_for(ctx, section_name)
    titles = {
        "introduction": "Introduction",
        "literature_review": "Literature Review",
        "methodology": "Methodology",
        "results": "Results",
        "discussion": "Discussion",
        "conclusion": "Conclusion",
    }
    title = titles.get(section_name, section_name.replace("_", " ").title())
    # Cite the first reference id (if any) to satisfy citation density
    # checks in the stub path.
    cite_pid = ""
    if ctx.reference_ids:
        cite_pid = ctx.reference_ids[0]
    elif ctx.paper_summaries and isinstance(ctx.paper_summaries[0], dict):
        cite_pid = ctx.paper_summaries[0].get("paper_id", "")
    cite_token = f" [@{cite_pid}]" if cite_pid else ""

    # ~80 words of stub prose so downstream stages don't get empty
    # strings and word counts remain roughly in the right order of
    # magnitude.
    stub_body = (
        f"# {title}\n\n"
        f"This is a stub draft for the {title.lower()} section of the "
        f"paper on \"{ctx.topic}\". The full prose will be generated by "
        f"the Crafter agent once a language model is configured.{cite_token} "
        "This stub preserves the section heading, a short scaffold "
        "paragraph, and a single inline citation so that downstream "
        "validation and compilation phases can be exercised without a "
        "live LLM call.\n"
    )
    return SectionDraft(
        section_name=section_name,
        body=stub_body,
        target_words=target,
        actual_words=count_words(stub_body),
        citation_count=count_citations(stub_body),
        citation_density_per_200=citation_density_ok(stub_body)[1],
    )


async def crafter_introduction(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
    *,
    custom_instructions: Optional[str] = None,
) -> CrafterResult:
    """Write the Introduction section. Anchors the paper with a hook,
    context, gap, and a roadmap of the sections that follow."""
    target = _word_target_for(ctx, "introduction")
    custom_block = _format_custom_instructions(custom_instructions)
    user_msg = (
        f"Write the Introduction section.\n\n"
        f"Topic: {ctx.topic}\n\n"
        f"Outline (excerpt):\n"
        f"{(ctx.formatted_outline or '')[:2000]}\n\n"
        f"Available citations (use [@paper_id] inline):\n"
        f"{_format_citation_list(ctx)}\n\n"
        f"**CRITICAL REQUIREMENTS:**\n"
        f"1. Write at least {target} words.\n"
        f"2. Open with a hook, provide context, identify a gap, "
        f"state the paper's approach, and preview the sections that follow.\n"
        f"3. Use **flowing prose** (not bullets) for the main argument.\n"
        f"4. Include at least one inline citation per 200 words, "
        f"using the [@paper_id] format from the citation list above.\n"
        f"5. Use markdown headings: # for the section title, ## for "
        f"sub-sections as needed.\n"
        f"6. Output ONLY the section content (no metadata blocks).{_language_instruction(ctx.language)}"
        f"{custom_block}"
    )
    return await _crafter_one_section(
        ctx, "introduction", llm_client, user_message=user_msg
    )


async def crafter_literature_review(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
    *,
    custom_instructions: Optional[str] = None,
) -> CrafterResult:
    """Write the Literature Review section. Synthesises cited
    literature and surfaces the research gaps the paper will address."""
    target = _word_target_for(ctx, "literature_review")
    custom_block = _format_custom_instructions(custom_instructions)
    user_msg = (
        f"Write the Literature Review section.\n\n"
        f"Topic: {ctx.topic}\n\n"
        f"Research summaries (JSON):\n"
        f"{_format_paper_summaries_for_prompt(ctx)}\n\n"
        f"Identified research gaps:\n"
        f"{json.dumps(ctx.research_gaps or [], ensure_ascii=False)[:1500]}\n\n"
        f"Available citations (use [@paper_id] inline):\n"
        f"{_format_citation_list(ctx)}\n\n"
        f"**CRITICAL REQUIREMENTS:**\n"
        f"1. Write at least {target} words.\n"
        f"2. Organise the review thematically (theoretical framework, "
        f"empirical studies, methodological comparison, evolution of "
        f"the field) and close with the research gaps the paper will "
        f"address.\n"
        f"3. Use **flowing prose** paragraphs. Avoid heavy bullet lists.\n"
        f"4. Include at least one comparison table (markdown, ≤5 "
        f"columns, ≤300 chars per cell).\n"
        f"5. Cite every non-trivial claim with [@paper_id].\n"
        f"6. Use 4 levels of headings where appropriate (##, ###, "
        f"####, #####).{_language_instruction(ctx.language)}"
        f"{custom_block}"
    )
    return await _crafter_one_section(
        ctx, "literature_review", llm_client, user_message=user_msg
    )


async def crafter_methodology(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
    *,
    custom_instructions: Optional[str] = None,
) -> CrafterResult:
    """Write the Methodology section. Describes the approach using
    cited literature — never claims to have conducted new empirical
    work (the paper is a literature-based review)."""
    target = _word_target_for(ctx, "methodology")
    lit_review_excerpt = ""
    if isinstance(ctx.section_drafts, dict):
        lit_review_excerpt = (ctx.section_drafts.get("literature_review") or "")[-2000:]
    custom_block = _format_custom_instructions(custom_instructions)
    user_msg = (
        f"Write the Methodology section.\n\n"
        f"Topic: {ctx.topic}\n\n"
        f"Literature Review context (what was identified):\n"
        f"{lit_review_excerpt}\n\n"
        f"Identified research gaps (Signal output):\n"
        f"{json.dumps(ctx.research_gaps or [], ensure_ascii=False)[:1500]}\n\n"
        f"Available citations (use [@paper_id] inline):\n"
        f"{_format_citation_list(ctx)}\n\n"
        f"**CRITICAL REQUIREMENTS:**\n"
        f"1. Write at least {target} words.\n"
        f"2. Use headings ## Methodology with ### sub-sections "
        f"(research design, data sources, analysis approach, rationale).\n"
        f"3. Describe methods *from cited literature* — do not claim "
        f"to have conducted new experiments or collected new data.\n"
        f"4. Use language like \"Previous research [@paper_id]\", "
        f"\"A potential approach could follow [@paper_id]\", "
        f"\"As described in [@paper_id]\" rather than \"we conducted\".\n"
        f"5. Include at least one methodology summary table.\n"
        f"6. Cite every methodological claim with [@paper_id].{_language_instruction(ctx.language)}"
        f"{custom_block}"
    )
    return await _crafter_one_section(
        ctx, "methodology", llm_client, user_message=user_msg
    )


async def crafter_results(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
    *,
    custom_instructions: Optional[str] = None,
) -> CrafterResult:
    """Write the Results section. Synthesises findings from cited
    sources; never invents new data."""
    target = _word_target_for(ctx, "results")
    methodology_excerpt = ""
    lit_review_excerpt = ""
    if isinstance(ctx.section_drafts, dict):
        methodology_excerpt = (ctx.section_drafts.get("methodology") or "")[-1500:]
        lit_review_excerpt = (ctx.section_drafts.get("literature_review") or "")[:1500]
    custom_block = _format_custom_instructions(custom_instructions)
    user_msg = (
        f"Write the Results / Analysis section.\n\n"
        f"Topic: {ctx.topic}\n\n"
        f"Methodology (previous section):\n"
        f"{methodology_excerpt}\n\n"
        f"Literature Review (theoretical framework):\n"
        f"{lit_review_excerpt}\n\n"
        f"Research summaries (JSON):\n"
        f"{_format_paper_summaries_for_prompt(ctx)}\n\n"
        f"Available citations (use [@paper_id] inline):\n"
        f"{_format_citation_list(ctx)}\n\n"
        f"**CRITICAL REQUIREMENTS:**\n"
        f"1. Write at least {target} words.\n"
        f"2. Synthesise findings FROM CITED LITERATURE; do not "
        f"present new empirical results of your own.\n"
        f"3. Use language like \"Studies have shown\", \"Research "
        f"indicates\", \"Findings suggest\" — never \"we found\" or "
        f"\"our analysis\".\n"
        f"4. Include 2-3 data / comparison tables.\n"
        f"5. Cite every finding with [@paper_id].\n"
        f"6. Use 4 levels of headings where appropriate.{_language_instruction(ctx.language)}"
        f"{custom_block}"
    )
    return await _crafter_one_section(
        ctx, "results", llm_client, user_message=user_msg
    )


async def crafter_discussion(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
    *,
    custom_instructions: Optional[str] = None,
) -> CrafterResult:
    """Write the Discussion section. Interprets findings, compares to
    prior work, and surfaces limitations + future research."""
    target = _word_target_for(ctx, "discussion")
    results_excerpt = ""
    lit_review_excerpt = ""
    if isinstance(ctx.section_drafts, dict):
        results_excerpt = (ctx.section_drafts.get("results") or "")[-2000:]
        lit_review_excerpt = (ctx.section_drafts.get("literature_review") or "")[:1500]
    custom_block = _format_custom_instructions(custom_instructions)
    user_msg = (
        f"Write the Discussion section.\n\n"
        f"Topic: {ctx.topic}\n\n"
        f"Results (previous section):\n"
        f"{results_excerpt}\n\n"
        f"Literature Review (theoretical framework, for comparison):\n"
        f"{lit_review_excerpt}\n\n"
        f"Identified research gaps (Signal output):\n"
        f"{json.dumps(ctx.research_gaps or [], ensure_ascii=False)[:1000]}\n\n"
        f"Available citations (use [@paper_id] inline):\n"
        f"{_format_citation_list(ctx)}\n\n"
        f"**CRITICAL REQUIREMENTS:**\n"
        f"1. Write at least {target} words.\n"
        f"2. Discuss findings FROM CITED LITERATURE (synthesised in "
        f"the Results section). Compare with the theoretical "
        f"framework established in the Literature Review.\n"
        f"3. Cover: interpretation, comparison with prior work, how "
        f"the literature addresses the research gaps, theoretical & "
        f"practical implications, limitations discussed in the "
        f"literature, future research directions.\n"
        f"4. Include at least one summary / implications table.\n"
        f"5. Cite every claim with [@paper_id].\n"
        f"6. Reference the earlier sections explicitly (e.g. \"As "
        f"discussed in the Literature Review\", \"The findings "
        f"synthesised in the previous section\").{_language_instruction(ctx.language)}"
        f"{custom_block}"
    )
    return await _crafter_one_section(
        ctx, "discussion", llm_client, user_message=user_msg
    )


async def crafter_conclusion(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
    *,
    custom_instructions: Optional[str] = None,
) -> CrafterResult:
    """Write the Conclusion section. Recaps the contribution and the
    impact of the work."""
    target = _word_target_for(ctx, "conclusion")
    body_excerpt = ""
    if isinstance(ctx.section_drafts, dict):
        for name in (
            "introduction",
            "literature_review",
            "methodology",
            "results",
            "discussion",
        ):
            text = ctx.section_drafts.get(name) or ""
            if text:
                body_excerpt = (body_excerpt + "\n\n" + text)[-2500:]
                break  # we only need a small excerpt
    custom_block = _format_custom_instructions(custom_instructions)
    user_msg = (
        f"Write the Conclusion section.\n\n"
        f"Topic: {ctx.topic}\n\n"
        f"Main body excerpt (findings + discussion):\n"
        f"{body_excerpt}\n\n"
        f"Available citations (use [@paper_id] inline):\n"
        f"{_format_citation_list(ctx)}\n\n"
        f"**CRITICAL REQUIREMENTS:**\n"
        f"1. Write at least {target} words.\n"
        f"2. Recap the problem, summarise the key findings, and "
        f"emphasise the contribution and impact.\n"
        f"3. Include a short summary table if appropriate.\n"
        f"4. Cite every non-trivial claim with [@paper_id].\n"
        f"5. Output ONLY the section content (no metadata blocks).{_language_instruction(ctx.language)}"
        f"{custom_block}"
    )
    return await _crafter_one_section(
        ctx, "conclusion", llm_client, user_message=user_msg
    )


# ---------------------------------------------------------------------------
# Refiner
# ---------------------------------------------------------------------------


async def refiner(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
    draft: SectionDraft,
    *,
    instruction: str = "Improve language, fix passive voice, ensure "
    "citations are present in [@paper_id] format, and remove "
    "repetition. Do not invent new claims or citations.",
) -> RefinerResult:
    """Refine a single section draft. Used by the orchestrator's
    optional end-of-phase refinement pass.

    The original draft is preserved on the returned ``RefinerResult``
    so callers (and tests) can compare before / after."""
    prompt_body = load_prompt("refiner", lang=ctx.language)
    user_msg = (
        f"Topic: {ctx.topic}\n\n"
        f"Section: {draft.section_name}\n\n"
        f"Refinement instruction:\n{instruction}\n\n"
        f"Draft to refine:\n```\n{draft.body}\n```\n\n"
        "Return the refined section markdown. Keep the same overall "
        "structure and heading hierarchy. Do not add metadata blocks. "
        "Preserve all [@paper_id] inline citations; you may add more "
        "if natural but must not invent new paper_ids beyond those in "
        f"the citation list below.\n\n"
        f"Available citations:\n{_format_citation_list(ctx)}{_language_instruction(ctx.language)}"
    )

    response = await llm_client.chat.completions.create(
        model=_resolve_model(llm_client),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=4000,
    )
    raw = (response.choices[0].message.content or "").strip()
    body = _strip_metadata_sections(raw)

    refined = SectionDraft(
        section_name=draft.section_name,
        body=body,
        target_words=draft.target_words,
        actual_words=count_words(body),
        citation_count=count_citations(body),
        citation_density_per_200=citation_density_ok(body)[1],
        raw_llm_output=raw,
        refined=True,
    )
    return RefinerResult(original=draft, refined=refined)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


# Per-section writers in execution order. The orchestrator iterates
# over this list. Each entry is a callable that takes (ctx, llm) and
# returns a CrafterResult.
_SECTION_WRITERS = [
    ("introduction", crafter_introduction),
    ("literature_review", crafter_literature_review),
    ("methodology", crafter_methodology),
    ("results", crafter_results),
    ("discussion", crafter_discussion),
    ("conclusion", crafter_conclusion),
]

# Map of section name → writer, used by the public ``crafter``
# dispatcher below.
_SECTION_WRITER_BY_NAME = dict(_SECTION_WRITERS)


async def crafter(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
    section_name: str,
    *,
    custom_instructions: Optional[str] = None,
) -> CrafterResult:
    """Public dispatcher that writes a single named section. Mirrors
    the opendraft Crafter API: callers pass ``section_name`` and the
    matching writer runs.

    Args:
        ctx: The pipeline state. ``formatted_outline`` and
            ``paper_summaries`` are the primary inputs.
        llm_client: An ``AsyncOpenAI`` instance used for the LLM call.
        section_name: One of ``SECTION_NAMES`` (e.g. ``"introduction"``).
        custom_instructions: Optional free-form guidance appended to
            the section-specific user message. Used by the
            per-section regenerate endpoint to let users steer the
            rewrite (e.g. "make it more technical", "shorten by 30%").
            Ignored when None or empty.

    Returns:
        A :class:`CrafterResult` containing the section draft and
        structural metadata (tables, headings, paper_ids cited).

    Raises:
        ValueError: if ``section_name`` is not in ``SECTION_NAMES``.
    """
    if section_name not in _SECTION_WRITER_BY_NAME:
        raise ValueError(
            f"Unknown section_name {section_name!r}. "
            f"Expected one of {SECTION_NAMES}."
        )
    return await _SECTION_WRITER_BY_NAME[section_name](
        ctx, llm_client, custom_instructions=custom_instructions
    )


async def run_compose_phase(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    refine_at_end: bool = False,
) -> DraftContext:
    """Run the full Compose phase: write all 6 sections, optionally
    refine them at the end, and populate ``ctx.section_drafts``.

    Behaviour
    ---------
    * Marks ``PhaseName.COMPOSE`` RUNNING → SUCCEEDED / FAILED.
    * With ``llm_client``: each ``crafter_*`` function calls the LLM
      once per section; the response is parsed into a ``SectionDraft``
      and written to ``ctx.section_drafts[section_name]``.
    * Without ``llm_client``: each section is filled with a short
      deterministic stub so downstream phases can still be exercised.
    * If ``refine_at_end`` is true, the Refiner is called once per
      section after drafting completes. The refined version replaces
      the original in ``ctx.section_drafts`` and the refined section
      names are added to the returned ``ComposeResult.refined`` list.
    * If any LLM call raises, the phase is marked FAILED and the
      exception is re-raised (matching the structure-phase contract).

    Returns the same ``ctx`` for chaining.
    """
    ctx.mark_phase(PhaseName.COMPOSE, PhaseStatus.RUNNING)
    compose_result = ComposeResult(used_llm=llm_client is not None)

    try:
        if llm_client is None:
            logger.info(
                "compose: no LLM client, producing stub drafts for %d sections",
                len(SECTION_NAMES),
            )
            for section_name in SECTION_NAMES:
                draft = _stub_section_draft(ctx, section_name)
                if not isinstance(ctx.section_drafts, dict):
                    ctx.section_drafts = {}
                ctx.section_drafts[section_name] = draft.body
                compose_result.drafts.append(draft)
        else:
            for section_name, writer in _SECTION_WRITERS:
                logger.info("compose: writing section %s", section_name)
                result = await writer(ctx, llm_client)
                if not isinstance(ctx.section_drafts, dict):
                    ctx.section_drafts = {}
                ctx.section_drafts[section_name] = result.draft.body
                compose_result.drafts.append(result.draft)

                if ctx.cancellation_requested:
                    raise RuntimeError("Compose phase cancelled by request")

            if refine_at_end:
                for section_name, writer in _SECTION_WRITERS:
                    draft = compose_result.drafts[
                        next(
                            i
                            for i, d in enumerate(compose_result.drafts)
                            if d.section_name == section_name
                        )
                    ]
                    refine_result = await refiner(ctx, llm_client, draft)
                    ctx.section_drafts[section_name] = refine_result.refined.body
                    compose_result.drafts[
                        next(
                            i
                            for i, d in enumerate(compose_result.drafts)
                            if d.section_name == section_name
                        )
                    ] = refine_result.refined
                    compose_result.refined.append(section_name)

        ctx.mark_phase(PhaseName.COMPOSE, PhaseStatus.SUCCEEDED)
        logger.info(
            "compose: wrote %d sections, refined=%s",
            len(compose_result.drafts),
            len(compose_result.refined),
        )
    except Exception as e:
        compose_result.errors.append(str(e))
        ctx.mark_phase(PhaseName.COMPOSE, PhaseStatus.FAILED, error=str(e))
        logger.exception("compose: phase failed")
        raise

    return ctx
