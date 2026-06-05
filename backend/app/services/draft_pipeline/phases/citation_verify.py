"""
Citation Verifier phase — post-validate citation audit.

Runs after ``run_validate_phase`` to categorise every ``[@paper_id]``
in the section drafts into one of three buckets:

- **verified**    : the paper_id exists in the project's candidate
                    set AND has both a DOI and a venue.
- **incomplete**  : the paper_id exists in the candidate set but is
                    missing the DOI and/or the venue. Worth surfacing
                    to the user so the reference list can be enriched.
- **unresolved**  : the paper_id is cited but does not exist in the
                    candidate set. Likely a typo or a paper the user
                    added out-of-band; an LLM (when available) can
                    suggest near-matches from the candidate set.

Results are written to ``ctx.citation_audit`` and appended to
``ctx.qa_report`` as a "Citation Audit" section so the user sees the
audit alongside the existing FactCheck / Referee output.

Async only; LLM-optional; deterministic without it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from openai import AsyncOpenAI

from ..context import DraftContext
from ..prompts import load_prompt
from .research import _resolve_model
from .validate import _extract_citation_ids

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CitationAudit:
    """Structured output of the citation verifier."""

    verified: List[str] = field(default_factory=list)
    incomplete: List[str] = field(default_factory=list)
    unresolved: List[str] = field(default_factory=list)
    replacements: Dict[str, List[str]] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "verified": list(self.verified),
            "incomplete": list(self.incomplete),
            "unresolved": list(self.unresolved),
            "replacements": {k: list(v) for k, v in self.replacements.items()},
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_index(ctx: DraftContext) -> Dict[str, dict]:
    """Build a paper_id -> candidate dict index for O(1) lookup.

    Sources are merged with candidate_papers first, then paper_summaries
    (summaries override candidates only when they have a non-empty DOI or
    venue, since the candidate record is usually richer)."""
    by_id: Dict[str, dict] = {}
    for c in ctx.candidate_papers or []:
        if not isinstance(c, dict):
            continue
        pid = c.get("id") or c.get("paper_id")
        if not pid:
            continue
        by_id[pid] = c
    for s in ctx.paper_summaries or []:
        if not isinstance(s, dict):
            continue
        pid = s.get("paper_id") or s.get("id")
        if not pid:
            continue
        existing = by_id.get(pid, {})
        merged = dict(existing)
        merged.update({k: v for k, v in s.items() if v})
        by_id[pid] = merged
    return by_id


def _is_complete(meta: dict) -> bool:
    """A candidate record is 'complete' if it has both a DOI and a venue
    (or an arXiv id, which substitutes for venue in some fields)."""
    if not isinstance(meta, dict):
        return False
    doi = (meta.get("doi") or "").strip()
    venue = (meta.get("venue") or "").strip()
    arxiv_id = (meta.get("arxiv_id") or "").strip()
    if doi and (venue or arxiv_id):
        return True
    if venue and arxiv_id:
        return True
    return False


def _audit_citations(
    section_drafts: Dict[str, str],
    candidate_index: Dict[str, dict],
) -> CitationAudit:
    """Deterministic 3-bucket audit. Pure-Python; no LLM."""
    verified: Set[str] = set()
    incomplete: Set[str] = set()
    unresolved: Set[str] = set()
    seen: Set[str] = set()
    for body in section_drafts.values():
        for pid in _extract_citation_ids(body or ""):
            if pid in seen:
                continue
            seen.add(pid)
            meta = candidate_index.get(pid)
            if meta is None:
                unresolved.add(pid)
            elif _is_complete(meta):
                verified.add(pid)
            else:
                incomplete.add(pid)
    audit = CitationAudit(
        verified=sorted(verified),
        incomplete=sorted(incomplete),
        unresolved=sorted(unresolved),
        summary=(
            f"{len(verified)} verified, "
            f"{len(incomplete)} incomplete, "
            f"{len(unresolved)} unresolved"
        ),
    )
    return audit


def _format_audit_block(audit: CitationAudit, lang: str = "en") -> str:
    """Render the CitationAudit as a markdown block to append to
    ``ctx.qa_report``. Matches the style of the existing FactCheck block."""
    if lang == "zh":
        header = "## Citation Audit — 引用完整性核查"
    else:
        header = "## Citation Audit — Completeness Check"
    lines: List[str] = [header, ""]
    if audit.summary:
        if lang == "zh":
            lines.append(f"**摘要:** {audit.summary}")
        else:
            lines.append(f"**Summary:** {audit.summary}")
        lines.append("")

    def _section(title_en: str, title_zh: str, ids: List[str]) -> None:
        heading = title_zh if lang == "zh" else title_en
        if not ids:
            return
        lines.append(f"**{heading}:**")
        lines.extend(f"- `[@{pid}]`" for pid in ids)
        lines.append("")

    _section(
        "Verified (DOI + venue present)",
        "已核验(有 DOI 与期刊)",
        audit.verified,
    )
    _section(
        "Incomplete (missing DOI or venue)",
        "元数据不全(缺 DOI 或期刊)",
        audit.incomplete,
    )
    _section(
        "Unresolved (not in candidate set)",
        "无法解析(不在候选集内)",
        audit.unresolved,
    )

    if audit.replacements:
        title = "建议替换(LLM 推荐)" if lang == "zh" else "Suggested replacements (LLM)"
        lines.append(f"**{title}:**")
        for old, suggestions in audit.replacements.items():
            sugg_str = ", ".join(f"`[@{s}]`" for s in suggestions)
            lines.append(f"- `[@{old}]` → {sugg_str}")
        lines.append("")

    if not (audit.verified or audit.incomplete or audit.unresolved):
        if lang == "zh":
            lines.append("_无引用需要核查。_")
        else:
            lines.append("_No citations to audit._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM-augmented replacement suggestions
# ---------------------------------------------------------------------------


_REPLACEMENT_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def _llm_suggest_replacements(
    ctx: DraftContext,
    llm: AsyncOpenAI,
    audit: CitationAudit,
) -> Dict[str, List[str]]:
    """Ask the LLM for up to 3 candidate-id suggestions per unresolved
    citation, based on the known candidate set. Returns
    ``{old_id: [suggested_ids]}``.

    Designed to be best-effort: any failure returns ``{}`` so the
    deterministic audit still ships."""
    if not audit.unresolved:
        return {}
    try:
        prompt_body = load_prompt("citation_verify", lang=ctx.language)
    except FileNotFoundError:
        # No prompt shipped yet — skip silently. The deterministic
        # audit is still authoritative.
        logger.info("citation_verify: no prompt for lang=%r, skipping LLM step", ctx.language)
        return {}

    candidate_index = _candidate_index(ctx)
    candidates_brief: List[dict] = []
    for pid, meta in candidate_index.items():
        candidates_brief.append(
            {
                "id": pid,
                "title": (meta.get("title") or "").strip()[:200],
                "year": meta.get("year"),
            }
        )
    unresolved_brief = [
        {"id": pid, "context": _citation_context(ctx, pid)}
        for pid in audit.unresolved
    ]

    user_msg = (
        f"Topic: {ctx.topic}\n\n"
        f"# Unresolved citations (cited but not in candidate set)\n"
        f"{json.dumps(unresolved_brief, ensure_ascii=False)}\n\n"
        f"# Known candidate set\n"
        f"{json.dumps(candidates_brief, ensure_ascii=False)}\n\n"
        "Return a JSON object (no prose, no fences) with field "
        "`replacements` — a mapping from each unresolved id to an array "
        "of up to 3 candidate ids that could plausibly replace it "
        "(based on title/topic similarity). Use empty array if no good "
        "match exists. Do not invent ids that are not in the candidate set."
    )
    try:
        response = await llm.chat.completions.create(
            model=_resolve_model(llm),
            messages=[
                {"role": "system", "content": prompt_body},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=800,
        )
    except Exception as e:
        logger.warning("citation_verify: LLM call failed: %s", e)
        return {}

    raw = (response.choices[0].message.content or "").strip()
    return _parse_replacements_json(raw, valid_ids=set(candidate_index.keys()))


def _citation_context(ctx: DraftContext, pid: str, window: int = 200) -> str:
    """Return up to ``window`` chars of surrounding text for ``pid``
    from the section drafts, so the LLM has the local context."""
    for body in ctx.section_drafts.values():
        if not body:
            continue
        idx = body.find(f"[@{pid}]")
        if idx < 0:
            continue
        start = max(0, idx - window // 2)
        end = min(len(body), idx + window // 2)
        return body[start:end].replace("\n", " ").strip()
    return ""


def _parse_replacements_json(
    text: str, *, valid_ids: Set[str]
) -> Dict[str, List[str]]:
    """Tolerate prose + fence wrappers around the LLM JSON output."""
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL)
    match = _REPLACEMENT_JSON_RE.search(cleaned)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    raw_map = data.get("replacements", {})
    if not isinstance(raw_map, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for old, suggestions in raw_map.items():
        if not isinstance(suggestions, list):
            continue
        cleaned_suggestions: List[str] = []
        for s in suggestions:
            if not isinstance(s, str):
                continue
            s = s.strip()
            if s and s in valid_ids and s != old and s not in cleaned_suggestions:
                cleaned_suggestions.append(s)
            if len(cleaned_suggestions) >= 3:
                break
        if cleaned_suggestions:
            out[str(old)] = cleaned_suggestions
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def citation_verify(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    use_llm: bool = True,
) -> CitationAudit:
    """Audit every ``[@paper_id]`` in ``ctx.section_drafts`` against
    ``ctx.candidate_papers`` (with ``ctx.paper_summaries`` as a richer
    fallback for DOI/venue).

    Writes the result to ``ctx.citation_audit`` and appends a
    "Citation Audit" block to ``ctx.qa_report``.

    With ``use_llm=True`` and a non-None ``llm_client``, also asks the
    LLM to suggest replacement candidates for unresolved citations.
    """
    candidate_index = _candidate_index(ctx)
    audit = _audit_citations(ctx.section_drafts, candidate_index)
    logger.info("citation_verify: %s", audit.summary)

    if use_llm and llm_client is not None and audit.unresolved:
        audit.replacements = await _llm_suggest_replacements(
            ctx, llm_client, audit
        )

    ctx.citation_audit = audit.to_dict()

    # Append to the existing qa_report so the user sees a single
    # combined audit (Referee + FactCheck + Citation Audit).
    block = _format_audit_block(audit, lang=ctx.language or "en")
    if ctx.qa_report:
        if block not in ctx.qa_report:
            ctx.qa_report = ctx.qa_report.rstrip() + "\n\n" + block
    else:
        ctx.qa_report = block

    return audit


__all__ = ["citation_verify", "CitationAudit", "_audit_citations", "_format_audit_block"]
