"""
Table/Figure hints phase — heuristic suggestion of tables and figures.

Reads ``ctx.section_drafts`` and (optionally) ``ctx.paper_summaries``
to suggest where the author should drop a comparative table or an
illustrative figure. Heuristics only — the goal is to give the author
a starting checklist, not to design the visualization.

Two marker types are emitted in a markdown hint block:

- ``[TABLE_SUGGESTION: <caption>]``
- ``[FIGURE_SUGGESTION: <caption>]``

The block is appended to ``ctx.final_draft`` if present (creating it
otherwise) and also returned by ``suggest_table_figure_hints()`` for
tests / callers that want the raw string.

Async, deterministic, no LLM dependency.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..context import DraftContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class TableFigureHints:
    """A single heuristic suggestion."""

    section: str
    kind: str  # "table" | "figure"
    caption: str
    rationale: str = ""


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


# Loose number / percent / date detector. Captures the obvious
# empirical-claim patterns: "12 patients", "42%", "2023", "$1.2M".
_NUMBER_PATTERNS = [
    re.compile(r"\b\d+(?:\.\d+)?\s*%"),                # 42%
    re.compile(r"\$\s?\d+(?:[\.,]\d+)?\s?[kKmMbB]?"),  # $1.2M
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),             # 1,234,567
    re.compile(r"\b(?:19|20)\d{2}\b"),                # years
    re.compile(r"\bp\s*[=<>]\s*0?\.\d+"),             # p-values
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|sec|min|hr|kg|lb|km|mi|Hz|MHz|GHz|GB|MB|KB)\b"),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:×|x)\b"),       # 2x speedup
    re.compile(r"\b\d+\s+(?:patients|subjects|samples|participants|users|studies|papers|trials)\b"),
    re.compile(r"\b\d+(?:\.\d+)?\b"),                 # bare numbers (last resort, high recall)
]

# Words that often introduce a sequence / process / list of items.
_SEQUENCE_MARKERS = [
    re.compile(r"\b(?:first|second|third|fourth|fifth|finally|next|then|step\s+\d+|stage\s+\d+)\b", re.IGNORECASE),
    re.compile(r"\b(?:①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩)"),
    re.compile(r"^\s*\d+[\.\)]\s+", re.MULTILINE),  # numbered list items
    re.compile(r"\bworkflow\b", re.IGNORECASE),
    re.compile(r"\bpipeline\b", re.IGNORECASE),
    re.compile(r"\barchitecture\b", re.IGNORECASE),
    re.compile(r"\b步骤|阶段|流程|工作流|流水线|架构|示意图|时序|流程图", re.IGNORECASE),
]

# Words that often introduce a comparison.
# Split into English (with \b boundaries) and Chinese (no \b — CJK
# characters are all word characters, so \b never matches between them).
_COMPARISON_MARKERS_EN = re.compile(
    r"\b(?:versus|vs\.?|compared\s+(?:to|with)|in\s+contrast|on\s+the\s+other\s+hand|"
    r"whereas|while|better\s+than|worse\s+than|outperform|faster\s+than|slower\s+than|"
    r"baseline|state[-\s]of[-\s]the[-\s]art|SOTA)\b",
    re.IGNORECASE,
)
_COMPARISON_MARKERS_CN = re.compile(
    r"相比|对比|优于|差于|比.{0,8}更快|基线(?:模型)?"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_numbers(text: str) -> int:
    """Count numeric tokens in ``text`` (deduplicated per pattern match)."""
    seen_spans: List[Tuple[int, int]] = []
    for pat in _NUMBER_PATTERNS:
        for m in pat.finditer(text):
            seen_spans.append((m.start(), m.end()))
    # Drop overlapping matches so e.g. "2023" inside a longer date isn't
    # double-counted.
    seen_spans.sort()
    pruned: List[Tuple[int, int]] = []
    for span in seen_spans:
        if pruned and span[0] < pruned[-1][1]:
            continue
        pruned.append(span)
    return len(pruned)


def _has_sequence(text: str) -> bool:
    return any(p.search(text) for p in _SEQUENCE_MARKERS)


def _has_comparison(text: str) -> bool:
    return bool(_COMPARISON_MARKERS_EN.search(text) or _COMPARISON_MARKERS_CN.search(text))


def _humanize_section(name: str) -> str:
    return name.strip().replace("_", " ").title()


# ---------------------------------------------------------------------------
# Per-section heuristic
# ---------------------------------------------------------------------------


# Tunable thresholds. Tested in ``test_enhance.py``; tweak with care.
_TABLE_NUMBERS_THRESHOLD = 4          # ≥ this many numeric tokens → table
_TABLE_COMPARISON_BONUS = 0           # add when comparison marker present
_FIGURE_SEQUENCE_BONUS = 1            # presence of sequence marker → figure


def _section_suggestions(
    section_name: str,
    body: str,
    findings_boost: int = 0,
) -> List[TableFigureHints]:
    """Apply the heuristics to one section's body. Returns 0..N hints.

    ``findings_boost`` is an additional numeric count contributed by
    ``ctx.paper_summaries`` — papers cited in this section whose
    ``key_findings`` (or similar) entries are rich enough to merit a
    comparative table even if the prose alone wouldn't trigger one.
    """
    if not body or not body.strip():
        return []
    text = body.strip()
    hints: List[TableFigureHints] = []

    n_numbers = _count_numbers(text) + findings_boost
    has_compare = _has_comparison(text)
    has_seq = _has_sequence(text)

    human = _humanize_section(section_name)

    # Table: many numbers → worth a comparative table
    if n_numbers >= _TABLE_NUMBERS_THRESHOLD or findings_boost >= 2:
        if has_compare:
            caption = f"Comparison of key findings in the {human}"
            rationale = (
                f"{n_numbers} numeric findings with explicit comparison "
                f"language — a table would let the reader scan the deltas."
            )
        else:
            caption = f"Summary of numeric results reported in the {human}"
            rationale = (
                f"{n_numbers} numeric findings — a table would compress "
                f"them into a single scannable view."
            )
        if findings_boost >= 2 and n_numbers < _TABLE_NUMBERS_THRESHOLD:
            # Boosts over the threshold on summary key_findings alone.
            rationale = (
                f"{findings_boost} papers cited in this section have "
                f"structured key_findings entries — a table would "
                f"let the reader compare them side-by-side."
            )
        hints.append(
            TableFigureHints(
                section=section_name,
                kind="table",
                caption=caption,
                rationale=rationale,
            )
        )

    # Figure: many distinct items, sequence, workflow, or architecture
    # language → an illustrative figure helps.
    if has_seq or _list_item_count(text) >= 3:
        if re.search(r"\bworkflow\b|\bpipeline\b|\barchitecture\b|流程|流水线|架构|时序|流程图|示意图", text, re.IGNORECASE):
            caption = f"Workflow / architecture diagram for the {human}"
        elif re.search(r"\bsteps?\b|\bstage\b|步骤|阶段", text, re.IGNORECASE):
            caption = f"Process / step diagram for the {human}"
        else:
            caption = f"Illustrative figure for the {human}"
        hints.append(
            TableFigureHints(
                section=section_name,
                kind="figure",
                caption=caption,
                rationale=(
                    "Sequence / step / workflow language detected — "
                    "an illustrative figure would clarify the order."
                ),
            )
        )

    return hints


def _list_item_count(text: str) -> int:
    """Crude count of bullet / numbered list items in the section."""
    return len(re.findall(r"^\s*(?:[-*]|\d+[.)])\s+\S+", text, re.MULTILINE))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def suggest_table_figure_hints(
    section_drafts: Dict[str, str],
    paper_summaries: Optional[List[dict]] = None,
) -> List[TableFigureHints]:
    """Run the per-section heuristics over every section. Returns a flat
    list of ``TableFigureHints`` ordered by section name for determinism.

    ``paper_summaries`` is optional. When provided, each paper's
    ``key_findings`` / ``key_methods`` / ``results`` field is scanned
    for numeric content; the count of papers with rich findings
    contributes a boost to the table threshold for the sections where
    the paper is cited (heuristic: any section whose body mentions the
    paper_id).
    """
    findings_boost_by_section = _summaries_findings_boost(
        section_drafts, paper_summaries
    )
    hints: List[TableFigureHints] = []
    for name in sorted(section_drafts.keys()):
        boost = findings_boost_by_section.get(name, 0)
        hints.extend(_section_suggestions(name, section_drafts[name], boost))
    return hints


def _summaries_findings_boost(
    section_drafts: Dict[str, str],
    paper_summaries: Optional[List[dict]],
) -> Dict[str, int]:
    """For each section, count how many ``paper_summaries`` entries
    have numeric key_findings AND are cited in the section's body.
    """
    if not paper_summaries:
        return {}
    boost: Dict[str, int] = {}
    for summary in paper_summaries:
        if not isinstance(summary, dict):
            continue
        pid = summary.get("paper_id") or summary.get("id")
        if not pid:
            continue
        rich = _summary_has_numeric_findings(summary)
        if not rich:
            continue
        token = f"[@{pid}]"
        for name, body in section_drafts.items():
            if body and token in body:
                boost[name] = boost.get(name, 0) + 1
    return boost


def _summary_has_numeric_findings(summary: dict) -> bool:
    """A summary counts as 'rich in findings' if any of these fields
    contains ≥ 1 numeric token."""
    candidates = []
    for key in ("key_findings", "key_methods", "results", "main_results"):
        val = summary.get(key)
        if isinstance(val, str):
            candidates.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    candidates.append(" ".join(str(v) for v in item.values()))
    if not candidates:
        return False
    return _count_numbers(" ".join(candidates)) >= 1


def _format_hints_markdown(
    hints: List[TableFigureHints], lang: str = "en"
) -> str:
    """Render a list of hints as a single markdown block."""
    if not hints:
        return ""
    if lang == "zh":
        title = "## 图表建议(自动)"
        intro = (
            "以下图表建议由启发式规则自动生成,供作者参考;实际取"
            "舍请根据内容决定。"
        )
    else:
        title = "## Suggested Tables & Figures (heuristic)"
        intro = (
            "The following table/figure suggestions are generated by "
            "heuristic rules. Pick the ones that fit the narrative; "
            "discard the rest."
        )
    parts: List[str] = [title, "", intro, ""]
    for h in hints:
        marker = "[TABLE_SUGGESTION" if h.kind == "table" else "[FIGURE_SUGGESTION"
        parts.append(f"- {marker}: {h.caption}]")
        if h.rationale:
            parts.append(f"  - _{h.rationale}_")
    return "\n".join(parts)


def apply_table_figure_hints(
    ctx: DraftContext,
    *,
    target: str = "final_draft",
    append_separator: bool = True,
) -> List[TableFigureHints]:
    """Compute the hints from ``ctx.section_drafts`` and append a
    markdown hint block to ``ctx.final_draft`` (or another target
    field). Returns the underlying hints so tests / callers can
    inspect them.

    The block is also stored on ``ctx.citation_audit`` is NOT touched;
    this phase is intentionally non-overlapping with citation audit.
    """
    hints = suggest_table_figure_hints(
        ctx.section_drafts or {},
        paper_summaries=ctx.paper_summaries or None,
    )
    if not hints:
        logger.info("table_figure_hints: no suggestions generated")
        return hints

    block = _format_hints_markdown(hints, lang=ctx.language or "en")
    logger.info(
        "table_figure_hints: %d suggestions (%d tables, %d figures)",
        len(hints),
        sum(1 for h in hints if h.kind == "table"),
        sum(1 for h in hints if h.kind == "figure"),
    )

    separator = "\n\n" if append_separator else ""
    existing = (getattr(ctx, target, None) or "")
    if target == "final_draft":
        ctx.final_draft = (existing.rstrip() + separator + block + "\n").lstrip()
    else:
        # Generic setattr so callers can target any Optional[str] field.
        setattr(ctx, target, (existing.rstrip() + separator + block + "\n").lstrip())
    return hints


__all__ = [
    "TableFigureHints",
    "suggest_table_figure_hints",
    "apply_table_figure_hints",
    "_count_numbers",
    "_has_sequence",
    "_has_comparison",
    "_format_hints_markdown",
    "_section_suggestions",
]
