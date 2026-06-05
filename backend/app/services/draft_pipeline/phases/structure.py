"""
Structure phase — Architect + Formatter.

Faithful port of opendraft's ``engine/phases/structure.py`` adapted to
our async stack:

- ``architect`` produces a paper outline from research-phase outputs
  (summaries + gaps). Single LLM call.
- ``formatter`` takes the outline + style + citation style and
  produces a submission-ready formatting spec. Single LLM call.
- ``run_structure_phase`` orchestrates both, populates
  ``ctx.outline`` and ``ctx.formatted_outline``, marks the
  ``PhaseName.STRUCTURE`` bucket.

Inputs from ``DraftContext`` (set by the prior research phase):
- ``topic`` — paper topic
- ``candidate_papers`` — papers surfaced by Scout
- ``paper_summaries`` — per-paper Scribe output
- ``research_gaps`` — Signal output
- ``target_word_count`` — drives section length budget
- ``citation_style`` — APA / IEEE / Chicago / MLA / NALT

Outputs to ``DraftContext``:
- ``outline`` — the Architect's structured outline
- ``formatted_outline`` — the Formatter's submission-ready outline
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from openai import AsyncOpenAI

from ..context import DraftContext, PhaseName, PhaseStatus
from ..prompts import load_prompt
from .research import _resolve_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OutlineSection:
    """A single section of the paper outline."""

    number: str           # e.g. "3" or "3.2"
    title: str
    target_words: int = 0
    key_points: List[str] = field(default_factory=list)
    evidence_paper_ids: List[str] = field(default_factory=list)


@dataclass
class Outline:
    paper_type: str = ""              # e.g. "Literature Review"
    target_venue: str = ""
    research_question: str = ""
    draft_statement: str = ""         # one-sentence main claim
    sections: List[OutlineSection] = field(default_factory=list)
    total_target_words: int = 0
    raw_llm_output: str = ""


@dataclass
class FormattedOutline:
    """The Formatter's output: an outline plus the style spec to apply
    when downstream phases write the actual prose."""

    paper_type: str = ""
    target_venue: str = ""
    citation_style: str = "APA"
    format_name: str = ""             # "IMRaD" | "IEEE" | "APA" | "Chicago"
    manuscript_spec: dict = field(default_factory=dict)
    outline_markdown: str = ""
    raw_llm_output: str = ""


# ---------------------------------------------------------------------------
# Phase 1: Architect
# ---------------------------------------------------------------------------


async def architect(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
) -> Outline:
    """Generate a paper outline from research-phase outputs.

    Falls back to a heuristic outline (no LLM) if ``llm_client`` is
    None, so callers can run this phase before the LLM is configured.
    """

    # Build the input payload from ctx
    payload = _build_architect_payload(ctx)
    prompt_body = load_prompt("architect", lang=ctx.language)

    user_msg = (
        f"Topic: {ctx.topic}\n"
        f"Target word count: {ctx.target_word_count}\n"
        f"Citation style: {ctx.citation_style.value if hasattr(ctx.citation_style, 'value') else ctx.citation_style}\n"
        f"\n# Research context\n{payload}\n\n"
        "Return a JSON object (no prose, no fences) with fields: "
        "`paper_type` (Literature Review | Empirical | Theoretical | Mixed), "
        "`target_venue` (string or empty), `research_question` (string), "
        "`draft_statement` (1-2 sentences), `total_target_words` (int), "
        "`sections` (array of objects with `number`, `title`, "
        "`target_words` (int), `key_points` (array of 1-3 strings), "
        "`evidence_paper_ids` (array of paper ids to cite))."
    )

    response = await llm_client.chat.completions.create(
        model=_resolve_model(llm_client),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
        max_tokens=3500,
    )
    raw = (response.choices[0].message.content or "").strip()
    parsed = _parse_architect_json(raw)
    parsed.raw_llm_output = raw
    if not parsed.paper_type:
        # LLM produced no parseable JSON: fall back to heuristic
        logger.warning("architect: LLM output not parseable, using heuristic outline")
        return _heuristic_outline(ctx)
    return parsed


def _build_architect_payload(ctx: DraftContext) -> str:
    """Render research-phase outputs as a compact JSON payload for the
    LLM. Hard-capped to keep token usage predictable."""
    summaries = []
    for s in ctx.paper_summaries[:30]:
        if isinstance(s, dict):
            summaries.append(
                {
                    "paper_id": s.get("paper_id", ""),
                    "title": s.get("title", ""),
                    "research_question": (s.get("research_question") or "")[:300],
                    "key_findings": (s.get("key_findings") or [])[:5],
                    "limitations": (s.get("limitations") or [])[:3],
                    "relevance_score": s.get("relevance_score", 0),
                }
            )
    gaps = ctx.research_gaps[:10] if isinstance(ctx.research_gaps, list) else []
    candidates = [
        {
            "id": c.get("id", ""),
            "title": c.get("title", ""),
            "year": c.get("year"),
            "venue": c.get("venue"),
        }
        for c in (ctx.candidate_papers or [])[:30]
        if isinstance(c, dict)
    ]
    payload = {
        "topic": ctx.topic,
        "candidates": candidates,
        "summaries": summaries,
        "gaps": gaps,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)[:7000]


def _parse_architect_json(text: str) -> Outline:
    """Tolerate code fences and prose around the JSON object."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return Outline()
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return Outline()
    if not isinstance(data, dict):
        return Outline()

    sections: List[OutlineSection] = []
    for s in data.get("sections", []):
        if not isinstance(s, dict):
            continue
        try:
            target = int(s.get("target_words", 0) or 0)
        except (TypeError, ValueError):
            target = 0
        sections.append(
            OutlineSection(
                number=str(s.get("number", "")).strip(),
                title=str(s.get("title", "")).strip(),
                target_words=target,
                key_points=[str(x).strip() for x in s.get("key_points", []) if x],
                evidence_paper_ids=[
                    str(x).strip() for x in s.get("evidence_paper_ids", []) if x
                ],
            )
        )

    return Outline(
        paper_type=str(data.get("paper_type", "")).strip(),
        target_venue=str(data.get("target_venue", "")).strip(),
        research_question=str(data.get("research_question", "")).strip(),
        draft_statement=str(data.get("draft_statement", "")).strip(),
        sections=sections,
        total_target_words=int(data.get("total_target_words", 0) or 0),
    )


def _heuristic_outline(ctx: DraftContext) -> Outline:
    """A 6-section IMRaD-style outline produced without an LLM. Used
    when the LLM is not configured or returns unparseable output.
    Keeps the pipeline unblocked for downstream phases."""
    total = ctx.target_word_count or 8000
    # Standard IMRaD weight distribution
    weights = {
        "Introduction": 0.15,
        "Literature Review": 0.25,
        "Methodology": 0.15,
        "Results": 0.20,
        "Discussion": 0.15,
        "Conclusion": 0.10,
    }
    sections = [
        OutlineSection(
            number=str(i + 1),
            title=title,
            target_words=int(total * w),
        )
        for i, (title, w) in enumerate(weights.items())
    ]
    return Outline(
        paper_type="Literature Review",
        target_venue="",
        research_question=ctx.topic,
        draft_statement="",
        sections=sections,
        total_target_words=total,
    )


# ---------------------------------------------------------------------------
# Phase 2: Formatter
# ---------------------------------------------------------------------------


async def formatter(
    ctx: DraftContext,
    outline: Outline,
    llm_client: AsyncOpenAI,
) -> FormattedOutline:
    """Apply academic style and citation conventions to the outline."""

    prompt_body = load_prompt("formatter", lang=ctx.language)
    style = _citation_style_str(ctx.citation_style)

    user_msg = (
        f"Topic: {ctx.topic}\n"
        f"Citation style: {style}\n"
        f"Target word count: {ctx.target_word_count}\n\n"
        f"# Paper outline (JSON)\n{json.dumps(_outline_to_dict(outline), ensure_ascii=False, indent=1)[:5000]}\n\n"
        "Return a JSON object (no prose, no fences) with fields: "
        "`format_name` (IMRaD | IEEE | APA | Chicago), "
        "`target_venue` (string), "
        "`manuscript_spec` (object with any of `font`, `line_spacing`, "
        "`margins`, `page_numbers`, `headings`), "
        "`outline_markdown` (the outline rewritten with section numbers, "
        "headings, target word counts, and inline citation style notes)."
    )

    response = await llm_client.chat.completions.create(
        model=_resolve_model(llm_client),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=3500,
    )
    raw = (response.choices[0].message.content or "").strip()
    parsed = _parse_formatter_json(raw, fallback=outline, style=style)
    parsed.raw_llm_output = raw
    return parsed


def _parse_formatter_json(
    text: str, fallback: Outline, style: str
) -> FormattedOutline:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        # No parseable JSON: return a deterministic formatter output
        return FormattedOutline(
            paper_type=fallback.paper_type,
            target_venue=fallback.target_venue,
            citation_style=style,
            format_name=_default_format_for_style(style),
            manuscript_spec=_default_manuscript_spec(style),
            outline_markdown=_outline_to_markdown(fallback, style),
        )
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return FormattedOutline(
            paper_type=fallback.paper_type,
            target_venue=fallback.target_venue,
            citation_style=style,
            format_name=_default_format_for_style(style),
            manuscript_spec=_default_manuscript_spec(style),
            outline_markdown=_outline_to_markdown(fallback, style),
        )
    if not isinstance(data, dict):
        return FormattedOutline(
            paper_type=fallback.paper_type,
            citation_style=style,
            format_name=_default_format_for_style(style),
            manuscript_spec=_default_manuscript_spec(style),
            outline_markdown=_outline_to_markdown(fallback, style),
        )
    return FormattedOutline(
        paper_type=str(data.get("paper_type") or fallback.paper_type).strip(),
        target_venue=str(data.get("target_venue") or fallback.target_venue).strip(),
        citation_style=style,
        format_name=str(data.get("format_name") or _default_format_for_style(style)).strip(),
        manuscript_spec=dict(data.get("manuscript_spec") or _default_manuscript_spec(style)),
        outline_markdown=str(data.get("outline_markdown") or _outline_to_markdown(fallback, style)),
    )


def _default_format_for_style(style: str) -> str:
    return {
        "ieee": "IEEE",
        "mla": "MLA",
        "chicago": "Chicago",
        "nalt": "Chicago",
    }.get(style.lower(), "IMRaD")


def _default_manuscript_spec(style: str) -> dict:
    """Fallback spec used when the LLM doesn't return one."""
    if style.lower() == "ieee":
        return {
            "font": "Times New Roman 10pt",
            "line_spacing": "single",
            "margins": "0.75 inch all sides",
            "headings": "numbered, Roman numeral top level",
        }
    return {
        "font": "Times New Roman 12pt",
        "line_spacing": "double",
        "margins": "1 inch all sides",
        "headings": "Level 1 bold centered, Level 2 bold left, Level 3 bold italic left",
    }


def _citation_style_str(style) -> str:
    """Coerce enum or string to a lowercase string token."""
    if hasattr(style, "value"):
        return str(style.value)
    return str(style).lower()


def _outline_to_dict(outline: Outline) -> dict:
    return {
        "paper_type": outline.paper_type,
        "target_venue": outline.target_venue,
        "research_question": outline.research_question,
        "draft_statement": outline.draft_statement,
        "total_target_words": outline.total_target_words,
        "sections": [
            {
                "number": s.number,
                "title": s.title,
                "target_words": s.target_words,
                "key_points": list(s.key_points),
                "evidence_paper_ids": list(s.evidence_paper_ids),
            }
            for s in outline.sections
        ],
    }


def _outline_to_markdown(outline: Outline, style: str) -> str:
    """Deterministic markdown rendering of the outline (used as
    fallback when the LLM's output is unusable)."""
    lines: List[str] = [
        f"# {outline.paper_type or 'Paper'} — {style.upper()} style",
        "",
        f"**Target venue:** {outline.target_venue or 'TBD'}  ",
        f"**Total target words:** {outline.total_target_words}  ",
        f"**Citation style:** {style}",
        "",
    ]
    if outline.research_question:
        lines += ["**Research question:**", outline.research_question, ""]
    if outline.draft_statement:
        lines += ["**Draft statement:**", outline.draft_statement, ""]
    for s in outline.sections:
        lines.append(f"## {s.number} {s.title}".rstrip())
        lines.append(f"*Target: {s.target_words} words*")
        if s.key_points:
            lines.append("")
            for kp in s.key_points:
                lines.append(f"- {kp}")
        if s.evidence_paper_ids:
            lines.append("")
            lines.append(
                "Evidence: " + ", ".join(f"[@{pid}]" for pid in s.evidence_paper_ids)
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_structure_phase(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
) -> DraftContext:
    """Run Architect → Formatter, populating ctx.outline and
    ctx.formatted_outline. Marks ``PhaseName.STRUCTURE`` done.

    With no LLM client, Architect uses a heuristic 6-section IMRaD
    outline and Formatter produces deterministic markdown + spec.
    """
    ctx.mark_phase(PhaseName.STRUCTURE, PhaseStatus.RUNNING)
    try:
        if ctx.cancellation_requested:
            raise RuntimeError("Structure phase cancelled by request")
        if llm_client is not None:
            outline = await architect(ctx, llm_client)
        else:
            logger.info("structure: no LLM client, using heuristic outline")
            outline = _heuristic_outline(ctx)

        ctx.outline = _outline_to_dict(outline)
        logger.info(
            "structure: architect produced %d sections, %d total target words",
            len(outline.sections),
            outline.total_target_words or sum(s.target_words for s in outline.sections),
        )

        if ctx.cancellation_requested:
            raise RuntimeError("Structure phase cancelled by request")
        if llm_client is not None:
            formatted = await formatter(ctx, outline, llm_client)
        else:
            style = _citation_style_str(ctx.citation_style)
            formatted = FormattedOutline(
                paper_type=outline.paper_type,
                target_venue=outline.target_venue,
                citation_style=style,
                format_name=_default_format_for_style(style),
                manuscript_spec=_default_manuscript_spec(style),
                outline_markdown=_outline_to_markdown(outline, style),
            )

        ctx.formatted_outline = formatted.outline_markdown
        ctx.mark_phase(PhaseName.STRUCTURE, PhaseStatus.SUCCEEDED)
        logger.info(
            "structure: formatter produced %s format, manuscript spec keys=%s",
            formatted.format_name,
            list(formatted.manuscript_spec.keys()),
        )
    except Exception as e:
        ctx.mark_phase(PhaseName.STRUCTURE, PhaseStatus.FAILED, error=str(e))
        raise
    return ctx
