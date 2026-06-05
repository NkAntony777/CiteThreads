"""
Compile phase — Compiler (final assembly).

Faithful port of opendraft's ``engine/phases/compile.py`` adapted to
our async stack:

- Async functions (opendraft is sync)
- Uses our ``LLMFactory`` AsyncOpenAI client (opendraft uses Gemini)
- Bilingual prompts (en/zh) loaded from ``prompts/{lang}/``
- Writes the final markdown into ``DraftContext.final_draft``
- Calls ``QualityGate.score(ctx)`` to attach a quality score to
  ``ctx.quality_history`` so callers can inspect the result

Sub-agents
----------
``compiler``        — assemble the full draft (with optional title +
                      abstract + references)
``abstract_writer`` — small helper that synthesizes an abstract
                      from the section drafts
``run_compile_phase`` — orchestrator, marks ``PhaseName.COMPILE``
                        done, writes ``ctx.final_draft`` and appends
                        a ``QualityScore`` snapshot to
                        ``ctx.quality_history``
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from openai import AsyncOpenAI

from ..context import CitationStyle, DraftContext, PhaseName, PhaseStatus
from ..prompts import load_prompt
from ..quality_gate import QualityGate, QualityScore
from .research import _resolve_model
from .validate import _extract_citation_ids

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CompilerResult:
    """The Compiler's structured output."""

    title: str = ""
    abstract: str = ""
    body_markdown: str = ""
    references_markdown: str = ""
    qa_summary: str = ""
    final_draft: str = ""          # convenience: title + abstract + body + refs
    raw_llm_output: str = ""


# ---------------------------------------------------------------------------
# Helpers — section ordering + references rendering
# ---------------------------------------------------------------------------


# Conventional IMRaD ordering. Sections are matched loosely (substring)
# so both the snake_case names used by the Crafter
# ("literature_review") and the title-case names used by Architect
# ("Literature Review") are handled by the same rule.
_IMRAD_ORDER: List[Tuple[str, int]] = [
    ("introduction", 1),
    ("literature", 2),       # covers "literature review" and "literature_review"
    ("methodology", 3),
    ("method", 3),
    ("results", 4),
    ("discussion", 5),
    ("conclusion", 6),
]


def _ordered_section_names(section_drafts: dict[str, str]) -> List[str]:
    """Return section names sorted by canonical IMRaD position.

    Sections we don't recognize come at the end in their original
    (alphabetical) order. This keeps the assembly deterministic
    without hard-failing on unfamiliar section names.
    """
    name_to_idx: List[Tuple[int, str]] = []
    for name in section_drafts.keys():
        lc = name.lower().strip()
        position = 99
        for needle, idx in _IMRAD_ORDER:
            if needle in lc:
                position = min(position, idx)
        name_to_idx.append((position, name))
    name_to_idx.sort(key=lambda x: (x[0], x[1]))
    return [n for _, n in name_to_idx]


def _format_qa_header(qa_report: Optional[str], lang: str) -> str:
    """Render the optional QA report as a blockquote at the very top
    of the final draft. Extracts the first non-empty paragraph from
    the report so the blockquote stays short."""
    if not qa_report:
        return ""
    first_para = ""
    for line in qa_report.splitlines():
        s = line.strip()
        if not s:
            if first_para:
                break
            continue
        if s.startswith("#"):
            continue
        first_para += (" " if first_para else "") + s
        if len(first_para) > 200:
            break
    if not first_para:
        return ""
    label = "QA 判定" if lang == "zh" else "QA verdict"
    snippet = first_para[:200].rstrip()
    return f"> **{label}:** {snippet}\n\n"


def _collect_paper_metadata(ctx: DraftContext) -> List[dict]:
    """Collect metadata for every paper referenced in the draft.

    Preference order: ``paper_summaries`` (richest), then candidates,
    then any user-supplied references. Each item has at least an
    ``id``; missing fields are empty strings."""
    by_id: dict[str, dict] = {}

    for s in ctx.paper_summaries or []:
        if not isinstance(s, dict):
            continue
        pid = s.get("paper_id") or s.get("id")
        if not pid:
            continue
        by_id[pid] = {
            "id": pid,
            "title": s.get("title", ""),
            "authors": list(s.get("authors", []) or []),
            "year": s.get("year"),
            "venue": s.get("venue", ""),
            "doi": s.get("doi", ""),
        }

    for c in ctx.candidate_papers or []:
        if not isinstance(c, dict):
            continue
        pid = c.get("id") or c.get("paper_id")
        if not pid or pid in by_id:
            continue
        by_id[pid] = {
            "id": pid,
            "title": c.get("title", ""),
            "authors": list(c.get("authors", []) or []),
            "year": c.get("year"),
            "venue": c.get("venue", ""),
            "doi": c.get("doi", ""),
        }

    return [by_id[pid] for pid in sorted(by_id.keys())]


def _referenced_paper_ids(ctx: DraftContext) -> List[str]:
    """All paper IDs that actually appear as `[@paper_id]` in the
    section drafts, in order, deduped."""
    seen: Set[str] = set()
    out: List[str] = []
    for body in ctx.section_drafts.values():
        for pid in _extract_citation_ids(body or ""):
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def _render_references_markdown(
    paper_ids: Sequence[str],
    paper_index: dict[str, dict],
    style: CitationStyle,
) -> str:
    """Render a `## References` markdown block for ``paper_ids``."""
    if not paper_ids:
        return ""
    lines: List[str] = []
    label = "参考文献" if str(style.value).lower() in ("chicago",) and False else "References"
    # English-first label; bilingual variant handled below if needed.
    lines.append(f"## {label}")
    lines.append("")
    for pid in paper_ids:
        meta = paper_index.get(pid, {})
        line = _format_reference_line(pid, meta, style)
        lines.append(f"- {line}")
    return "\n".join(lines)


def _format_reference_line(pid: str, meta: dict, style: CitationStyle) -> str:
    """Format a single reference line in the requested style.

    Each style uses a slightly different punctuation. The `[@id]`
    prefix is preserved in every style so downstream parsers (and
    the existing review_generator) can still match.
    """
    authors = meta.get("authors") or []
    year = meta.get("year")
    title = meta.get("title") or ""
    venue = meta.get("venue") or ""
    doi = meta.get("doi") or ""

    author_str = _format_authors(authors)

    if style == CitationStyle.APA:
        # APA-ish: Author, A. A. (Year). Title. Venue. https://doi.org/...
        year_part = f"({year})." if year else "(n.d.)."
        venue_part = f" {venue}." if venue else ""
        doi_part = f" https://doi.org/{doi}" if doi else ""
        return f"[@{pid}] {author_str} {year_part} {title}.{venue_part}{doi_part}".strip()
    if style == CitationStyle.IEEE:
        # IEEE-ish: Author, A., "Title," Venue, Year. doi:...
        year_part = f", {year}" if year else ""
        venue_part = f", {venue}" if venue else ""
        doi_part = f", doi: {doi}" if doi else ""
        return f"[@{pid}] {author_str}, \"{title},\"{venue_part}{year_part}.{doi_part}".strip()
    if style == CitationStyle.MLA:
        # MLA-ish: Author. "Title." Venue, Year.
        venue_part = f" {venue}," if venue else ""
        year_part = f" {year}." if year else ""
        return f"[@{pid}] {author_str} \"{title}.\"{venue_part}{year_part}".strip()
    if style == CitationStyle.CHICAGO or style == CitationStyle.NALT:
        year_part = f" ({year})." if year else ""
        venue_part = f" {venue}." if venue else ""
        doi_part = f" https://doi.org/{doi}" if doi else ""
        return f"[@{pid}] {author_str}{year_part} {title}.{venue_part}{doi_part}".strip()
    # Fallback
    return f"[@{pid}] {author_str} ({year or 'n.d.'}) {title}. {venue}".strip()


def _format_authors(authors: Sequence[str]) -> str:
    """Best-effort "Smith, J., Jones, B." rendering."""
    if not authors:
        return "Unknown author"
    formatted: List[str] = []
    for name in authors[:6]:
        if "," in name:
            formatted.append(name.strip())
            continue
        parts = name.strip().split()
        if len(parts) == 1:
            formatted.append(parts[0])
        else:
            last = parts[-1]
            initials = " ".join(p[0].upper() + "." for p in parts[:-1] if p)
            formatted.append(f"{last}, {initials}")
    if len(authors) > 6:
        formatted.append("et al.")
    return ", ".join(formatted)


# ---------------------------------------------------------------------------
# Compiler agent
# ---------------------------------------------------------------------------


async def compiler(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    generate_abstract: bool = True,
    generate_title: bool = True,
) -> CompilerResult:
    """
    Assemble the final draft. Two modes:

    1. **With LLM**: ask the compiler model to (a) suggest a title
       and (b) write a 150-250 word abstract. The body is always
       concatenated from the section drafts (we never let the LLM
       rewrite the actual content).

    2. **Without LLM**: title and abstract are heuristic — title is
       the topic; abstract is the first paragraph of the first
       section, trimmed to 250 words.

    The function never raises on a missing/empty section_drafts: it
    returns a CompilerResult whose ``final_draft`` is "" and lets
    the orchestrator decide what to do.
    """
    if not ctx.section_drafts:
        return CompilerResult()

    style = ctx.citation_style

    # Pre-compute deterministic pieces.
    qa_header = _format_qa_header(ctx.qa_report, ctx.language)
    body = _assemble_body(ctx)
    paper_index = {p["id"]: p for p in _collect_paper_metadata(ctx)}
    referenced = _referenced_paper_ids(ctx)
    references_md = _render_references_markdown(
        referenced, paper_index, style
    )

    # Optional LLM pieces.
    title = ctx.topic
    abstract = ""
    qa_summary = _derive_qa_summary(ctx)
    if generate_title or generate_abstract:
        if llm_client is not None:
            try:
                title, abstract = await _llm_title_and_abstract(
                    ctx, llm_client, body, generate_title=generate_title,
                    generate_abstract=generate_abstract,
                )
            except Exception as e:
                logger.warning("compiler: LLM title/abstract failed: %s", e)
        # Deterministic fallback for abstract when no LLM is configured
        # or the LLM returned nothing — produce a short abstract from
        # the first non-empty section.
        if generate_abstract and not abstract:
            abstract = _heuristic_abstract(ctx, body, target_words=200)

    if not title:
        title = ctx.topic

    final = _compose_final(
        title=title,
        abstract=abstract,
        body=body,
        references=references_md,
        qa_header=qa_header,
        qa_summary=qa_summary,
    )

    return CompilerResult(
        title=title,
        abstract=abstract,
        body_markdown=body,
        references_markdown=references_md,
        qa_summary=qa_summary,
        final_draft=final,
    )


def _assemble_body(ctx: DraftContext) -> str:
    """Concatenate section drafts in canonical IMRaD order, with
    `## <number>. <title>` headings extracted from the outline if
    available.

    The match between ``ctx.section_drafts`` keys and the outline's
    ``title`` fields is case-insensitive and ignores underscores vs
    spaces, so the snake_case keys the Crafter writes
    (``"literature_review"``) are reconciled with the title-case
    outline entries (``"Literature Review"``).
    """
    # If we have a structured outline (dict) with sections, use its
    # numbers and titles. Otherwise, just emit "## <section name>".
    outline = ctx.outline if isinstance(ctx.outline, dict) else None
    num_by_norm: dict[str, str] = {}
    title_by_norm: dict[str, str] = {}
    if outline:
        for s in outline.get("sections", []) or []:
            if not isinstance(s, dict):
                continue
            title = str(s.get("title", "")).strip()
            if not title:
                continue
            num_by_norm[_normalize_title(title)] = str(
                s.get("number", "")
            ).strip()
            title_by_norm[_normalize_title(title)] = title

    ordered = _ordered_section_names(ctx.section_drafts)
    parts: List[str] = []
    for name in ordered:
        body = ctx.section_drafts.get(name, "")
        if not body.strip():
            continue
        key = _normalize_title(name)
        number = num_by_norm.get(key, "")
        display_title = title_by_norm.get(key, _humanize_name(name))
        heading = (
            f"## {number} {display_title}".strip()
            if number
            else f"## {display_title}"
        )
        parts.append(heading)
        parts.append("")
        parts.append(body.rstrip())
        parts.append("")
    return "\n".join(parts).rstrip()


def _normalize_title(s: str) -> str:
    """Loose title matcher: lowercases, strips whitespace, and treats
    ``_`` the same as a space."""
    return re.sub(r"\s+", " ", s.strip().lower().replace("_", " "))


def _humanize_name(s: str) -> str:
    """Turn ``literature_review`` into ``Literature Review`` for
    human-readable headings."""
    return s.strip().replace("_", " ").title()


def _compose_final(
    *,
    title: str,
    abstract: str,
    body: str,
    references: str,
    qa_header: str,
    qa_summary: str,
) -> str:
    """Glue the pieces together into the final markdown document."""
    parts: List[str] = []
    if qa_header:
        parts.append(qa_header.rstrip())
    parts.append(f"# {title}".rstrip())
    parts.append("")
    if abstract:
        parts.append("## Abstract")
        parts.append("")
        parts.append(abstract.strip())
        parts.append("")
    if body:
        parts.append(body.rstrip())
        parts.append("")
    if qa_summary:
        parts.append(f"> **QA summary:** {qa_summary}")
        parts.append("")
    if references:
        parts.append(references)
    return "\n".join(parts).rstrip() + "\n"


def _derive_qa_summary(ctx: DraftContext) -> str:
    """Look at the most recent validate-phase entry in
    ``quality_history`` and produce a one-liner. Falls back to a
    generic message if nothing is recorded yet."""
    if not ctx.quality_history:
        return ""
    last = ctx.quality_history[-1]
    if not isinstance(last, dict):
        return ""
    if last.get("phase") != PhaseName.VALIDATE.value:
        return ""
    verdict = last.get("verdict", "minor revisions")
    return f"{verdict} — {last.get('verified_count', 0)} verified, {last.get('orphan_count', 0)} orphan"


async def _llm_title_and_abstract(
    ctx: DraftContext,
    llm: AsyncOpenAI,
    body: str,
    *,
    generate_title: bool,
    generate_abstract: bool,
) -> Tuple[str, str]:
    prompt_body = load_prompt("compiler", lang=ctx.language)
    user_msg = (
        f"Topic: {ctx.topic}\n"
        f"Citation style: {ctx.citation_style.value if hasattr(ctx.citation_style, 'value') else ctx.citation_style}\n"
        f"QA report (truncated): {_truncate(ctx.qa_report or '(none)', 1500)}\n\n"
        f"# Section drafts (concatenated)\n{_truncate(body, 12000)}\n\n"
        "Return a JSON object (no prose, no fences) with fields: "
        "`title` (string, ≤ 200 chars), `abstract` (string, 150-250 words "
        "synthesized from the sections), `body_markdown` (echo the input "
        "body unchanged), `references_markdown` (echo a ## References "
        "block listing every paper-id cited in the body), `qa_summary` "
        "(one short sentence)."
    )
    response = await llm.chat.completions.create(
        model=_resolve_model(llm),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=3500,
    )
    raw = (response.choices[0].message.content or "").strip()
    parsed = _parse_compiler_json(raw)
    title = parsed.get("title", "") if generate_title else ""
    abstract = parsed.get("abstract", "") if generate_abstract else ""
    return title.strip(), abstract.strip()


def _parse_compiler_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "title": str(data.get("title", "") or ""),
        "abstract": str(data.get("abstract", "") or ""),
        "body_markdown": str(data.get("body_markdown", "") or ""),
        "references_markdown": str(data.get("references_markdown", "") or ""),
        "qa_summary": str(data.get("qa_summary", "") or ""),
    }


# ---------------------------------------------------------------------------
# Abstract writer (standalone helper, used by router/tests)
# ---------------------------------------------------------------------------


async def abstract_writer(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    target_words: int = 200,
    write_ctx: bool = True,
) -> str:
    """Standalone helper that produces only an abstract.

    Provided for callers (the future router) that want to refresh
    just the abstract without re-running the full compiler.

    Three modes:

    1. **With LLM** (``llm_client`` provided): calls
       ``prompts/{lang}/abstract_generator.md`` to synthesize a
       200-300 word abstract from the section drafts and the
       formatted outline.
    2. **Without LLM**: deterministic fallback that takes the first
       non-heading sentence from each section in IMRaD order and
       concatenates them into a paragraph.
    3. **LLM failure**: logged, then falls back to the deterministic
       mode.

    When ``write_ctx=True`` (the default) the result is also written
    to ``ctx.abstract`` so callers don't have to remember to.
    """
    if not ctx.section_drafts:
        if write_ctx:
            ctx.abstract = ""
        return ""
    body = _assemble_body(ctx)
    abstract = ""
    if llm_client is not None:
        try:
            abstract = await _llm_abstract_only(
                ctx, llm_client, body, target_words
            )
        except Exception as e:
            logger.warning("abstract_writer: LLM failed (%s), using heuristic", e)
    if not abstract:
        abstract = _heuristic_abstract(ctx, body, target_words)
    abstract = (abstract or "").strip()
    if write_ctx:
        ctx.abstract = abstract
    return abstract


async def _llm_abstract_only(
    ctx: DraftContext, llm: AsyncOpenAI, body: str, target_words: int
) -> str:
    # Prefer the dedicated abstract_generator prompt; fall back to the
    # compiler prompt if it isn't present (older deployments).
    try:
        prompt_body = load_prompt("abstract_generator", lang=ctx.language)
    except FileNotFoundError:
        prompt_body = load_prompt("compiler", lang=ctx.language)
    user_msg = (
        f"Topic: {ctx.topic}\n"
        f"Target abstract length: ~{target_words} words (200-300 ideal)\n\n"
        f"# Formatted outline (truncated)\n{_truncate(ctx.formatted_outline or '', 2000)}\n\n"
        f"# Section drafts\n{_truncate(body, 10000)}\n\n"
        "Return a JSON object with a single field `abstract` "
        "(string). No prose, no fences."
    )
    response = await llm.chat.completions.create(
        model=_resolve_model(llm),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=600,
    )
    raw = (response.choices[0].message.content or "").strip()
    parsed = _parse_compiler_json(raw)
    return (parsed.get("abstract") or "").strip()


def _heuristic_abstract(ctx: DraftContext, body: str, target_words: int) -> str:
    """Fallback abstract without LLM.

    Two flavors, in order of preference:

    1. If only one section has content, use the first non-heading
       paragraph (trimmed to ``target_words``) — same behavior as
       before, for backward compat.
    2. Otherwise, take the first non-heading sentence from each
       section in IMRaD order and concatenate into a single
       paragraph. The result is joined by spaces, then trimmed to
       ``target_words`` words.
    """
    non_empty = [
        (name, b) for name, b in ctx.section_drafts.items() if b and b.strip()
    ]
    if len(non_empty) <= 1:
        first = (non_empty[0][1] if non_empty else body) or ""
        for para in first.split("\n\n"):
            para = para.strip()
            if para and not para.startswith("#"):
                words = para.split()
                return " ".join(words[:target_words]).rstrip(",;:.") + "."
        return ""

    ordered = _ordered_section_names(ctx.section_drafts)
    sentences: List[str] = []
    for name in ordered:
        text = ctx.section_drafts.get(name) or ""
        for para in text.split("\n\n"):
            para = para.strip()
            if not para or para.startswith("#"):
                continue
            for sent in re.split(r"(?<=[.!?。!?])\s+", para):
                sent = sent.strip()
                if sent:
                    sentences.append(sent)
                    break
            if sentences and len(sentences) >= len(ordered):
                break
        if len(sentences) >= len(ordered):
            break

    if not sentences:
        return ""
    joined = " ".join(sentences)
    words = joined.split()
    return " ".join(words[:target_words]).rstrip(",;:.") + "."


def _truncate(text: Optional[str], max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_compile_phase(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    quality_gate: Optional[QualityGate] = None,
) -> DraftContext:
    """
    Run the Compiler. Populates ``ctx.final_draft`` and marks
    ``PhaseName.COMPILE`` done. Attaches a ``QualityScore`` to
    ``ctx.quality_history`` so callers can inspect the result
    without re-scoring.
    """
    ctx.mark_phase(PhaseName.COMPILE, PhaseStatus.RUNNING)
    try:
        if ctx.cancellation_requested:
            raise RuntimeError("Compile phase cancelled by request")
        result = await compiler(ctx, llm_client=llm_client)
        ctx.final_draft = result.final_draft
        # Persist the abstract on ctx.abstract too, so callers can read
        # it without re-parsing the final_draft.
        if not ctx.abstract and result.abstract:
            ctx.abstract = result.abstract
        logger.info(
            "compile: assembled %d chars, %d references",
            len(result.final_draft or ""),
            len(_referenced_paper_ids(ctx)),
        )

        if ctx.cancellation_requested:
            raise RuntimeError("Compile phase cancelled by request")
        # Attach a quality score to the history.
        gate = quality_gate or QualityGate()
        score: QualityScore = gate.score(ctx)
        ctx.quality_history.append(score)
        logger.info(
            "compile: quality score = %d (%s)",
            score.total,
            score.decision.value,
        )

        ctx.mark_phase(
            PhaseName.COMPILE,
            PhaseStatus.SUCCEEDED,
        )
    except Exception as e:
        ctx.mark_phase(PhaseName.COMPILE, PhaseStatus.FAILED, error=str(e))
        raise
    return ctx
