"""
Validate phase — Referee + FactCheck.

Faithful port of opendraft's ``engine/phases/validate.py`` adapted to
our async stack:

- Async functions (opendraft is sync)
- Uses our ``LLMFactory`` AsyncOpenAI client (opendraft uses Gemini)
- Bilingual prompts (en/zh) loaded from ``prompts/{lang}/``
- Writes structured results into ``DraftContext.qa_report``
  (markdown string) plus typed return values for tests

Two independent sub-agents
---------------------------
``referee``   — narrative consistency / voice / argument flow
``factcheck`` — citation verification against the project's reference
                set, with optional LLM-augmented detection of
                unsupported factual claims

Plus an orchestrator ``run_validate_phase`` that runs both in
sequence, populates ``ctx.qa_report`` and marks
``PhaseName.VALIDATE`` done.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Set

from openai import AsyncOpenAI

from ..context import DraftContext, PhaseName, PhaseStatus
from ..prompts import load_prompt
from .research import _resolve_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RefereeFinding:
    """A single QA finding surfaced by the Referee agent."""

    category: str          # "narrative" | "voice" | "flow" | "citation" | "other"
    section: str           # section name, or "" for cross-cutting findings
    detail: str            # the finding text


@dataclass
class RefereeResult:
    """The Referee's structured output."""

    qa_markdown: str = ""             # full QA report (markdown)
    findings: List[RefereeFinding] = field(default_factory=list)
    verdict: str = "minor revisions"  # "publishable" | "minor" | "major"
    raw_llm_output: str = ""


@dataclass
class FactCheckClaim:
    """An unsupported factual claim (no `[@paper_id]` attached)."""

    section: str
    sentence: str
    issue: str = "no_citation"


@dataclass
class FactCheckResult:
    """The FactCheck agent's structured output."""

    verified: List[str] = field(default_factory=list)
    orphan: List[str] = field(default_factory=list)
    unsupported_claims: List[FactCheckClaim] = field(default_factory=list)
    summary: str = ""
    raw_llm_output: str = ""

    @property
    def passed(self) -> bool:
        """True if there are no orphan citations."""
        return not self.orphan


# ---------------------------------------------------------------------------
# Helpers — citation extraction
# ---------------------------------------------------------------------------


_CITATION_RE = re.compile(r"\[@([^\]]+)\]")


def _extract_citation_ids(text: str) -> List[str]:
    """Return all `[@paper_id]` IDs in ``text``, in order, deduped."""
    if not text:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for match in _CITATION_RE.finditer(text):
        pid = match.group(1).strip()
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _all_section_text(section_drafts: dict[str, str]) -> str:
    """Concatenate all section drafts in a stable order (sorted by
    name for determinism)."""
    return "\n\n".join(
        f"## {name}\n\n{body}" for name, body in sorted(section_drafts.items())
    )


def _known_paper_ids(ctx: DraftContext) -> Set[str]:
    """Union of every paper ID the project knows about."""
    known: Set[str] = set(ctx.reference_ids or [])
    known.update(ctx.graph_node_ids or [])
    for s in ctx.paper_summaries or []:
        if isinstance(s, dict):
            pid = s.get("paper_id") or s.get("id")
            if pid:
                known.add(pid)
    return known


# ---------------------------------------------------------------------------
# Deterministic fact-check (no LLM)
# ---------------------------------------------------------------------------


def _deterministic_factcheck(
    section_drafts: dict[str, str], known: Set[str]
) -> FactCheckResult:
    """Pure-Python citation audit. No LLM call — used as the
    authoritative source of truth for "is this citation real?" and as
    a fallback when the LLM-augmented step is skipped."""
    verified: Set[str] = set()
    orphan: Set[str] = set()
    for _section, body in section_drafts.items():
        for pid in _extract_citation_ids(body or ""):
            if pid in known:
                verified.add(pid)
            else:
                orphan.add(pid)
    return FactCheckResult(
        verified=sorted(verified),
        orphan=sorted(orphan),
        summary=f"{len(verified)} verified, {len(orphan)} orphan",
    )


# ---------------------------------------------------------------------------
# Referee agent
# ---------------------------------------------------------------------------


async def referee(
    ctx: DraftContext,
    llm_client: AsyncOpenAI,
) -> RefereeResult:
    """
    Run the Referee agent over the assembled section drafts.

    The Referee produces a markdown QA report (`qa_markdown`) and a
    structured list of findings (`findings`). If the LLM output is
    unparseable for the verdict, we default to "minor revisions" —
    the report is still useful even without a verdict line.
    """
    prompt_body = load_prompt("referee", lang=ctx.language)
    # ctx.outline is a dict (structured); ctx.formatted_outline is the
    # rendered markdown form. Use the markdown if present, otherwise
    # fall back to a compact JSON dump of the structured outline.
    if ctx.formatted_outline:
        outline = ctx.formatted_outline
    elif isinstance(ctx.outline, dict):
        import json
        outline = json.dumps(ctx.outline, ensure_ascii=False, indent=1)
    else:
        outline = ""
    body = _all_section_text(ctx.section_drafts)

    user_msg = (
        f"Topic: {ctx.topic}\n\n"
        f"# Formatted outline\n{_truncate(outline, 4000)}\n\n"
        f"# Section drafts (concatenated)\n{_truncate(body, 12000)}\n\n"
        "Produce the QA report in markdown following the structure in "
        "the system prompt. Be specific and concise."
    )

    response = await llm_client.chat.completions.create(
        model=_resolve_model(llm_client),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=3500,
    )
    raw = (response.choices[0].message.content or "").strip()
    findings, verdict = _parse_referee_markdown(raw)
    return RefereeResult(
        qa_markdown=raw,
        findings=findings,
        verdict=verdict,
        raw_llm_output=raw,
    )


# Regex used to extract the "Overall assessment" verdict line.
_VERDICT_RES = [
    (re.compile(r"publishable", re.IGNORECASE), "publishable"),
    (re.compile(r"major revisions", re.IGNORECASE), "major revisions"),
    (re.compile(r"minor revisions", re.IGNORECASE), "minor revisions"),
]


def _parse_referee_markdown(text: str) -> tuple[List[RefereeFinding], str]:
    """Heuristically extract findings + verdict from a referee report.

    The prompt prescribes a fixed section structure, so we walk
    headings and capture the bullets that follow each one. Anything
    we can't classify falls into a generic "other" category.
    """
    findings: List[RefereeFinding] = []
    if not text:
        return findings, "minor revisions"

    verdict = "minor revisions"
    # Look for an "Overall assessment" line and pick the strongest
    # verdict keyword we can find in it.
    for line in text.splitlines():
        if "overall assessment" in line.lower() or "总体评价" in line:
            # The verdict usually lives on the next non-empty lines.
            tail = text[text.lower().find("overall assessment"):]
            tail = tail[:400]
            for pattern, label in _VERDICT_RES:
                if pattern.search(tail):
                    verdict = label
                    break
            break

    # Walk headings → capture bullet text under each.
    section_to_category = {
        "narrative consistency": "narrative",
        "voice and tone": "voice",
        "argument flow": "flow",
        "citation usage": "citation",
        "narrative": "narrative",
        "voice": "voice",
        "flow": "flow",
        "citation": "citation",
    }
    lines = text.splitlines()
    current_heading = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip().lower()
            continue
        if not stripped.startswith(("-", "*", "1.", "2.", "3.")):
            continue
        # Strip the bullet / numbered prefix
        body = re.sub(r"^([-*]|\d+\.)\s*", "", stripped).strip()
        if not body:
            continue
        # Only attribute categories we recognize.
        category = "other"
        for needle, cat in section_to_category.items():
            if needle in current_heading:
                category = cat
                break
        # Try to extract a section name from a leading "Section X:" / "第X节"
        section_name = _extract_section_name(body)
        findings.append(RefereeFinding(category=category, section=section_name, detail=body))
    return findings, verdict


def _extract_section_name(bullet: str) -> str:
    """Best-effort extraction of "Section Foo" / "第X节" prefix from
    a referee finding. Returns "" if no section can be detected."""
    m = re.match(r"^(?:Section|section|Sec\.?)\s+([A-Za-z0-9][\w \-/]*)", bullet)
    if m:
        return m.group(1).strip()
    if bullet.startswith("第") and "节" in bullet:
        # Chinese: 第3节: ... → return text up to colon
        head = bullet.split(":", 1)[0]
        if "节" in head:
            return head
    return ""


# ---------------------------------------------------------------------------
# FactCheck agent
# ---------------------------------------------------------------------------


async def factcheck(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    use_llm: bool = True,
) -> FactCheckResult:
    """
    Audit every `[@paper_id]` in ``ctx.section_drafts`` against the
    project's reference set.

    Always runs the deterministic pass first (so the orphan list is
    authoritative even without an LLM). When ``llm_client`` is
    provided and ``use_llm`` is True, the LLM step is also run to
    surface unsupported factual claims.
    """
    known = _known_paper_ids(ctx)
    base = _deterministic_factcheck(ctx.section_drafts, known)

    if not use_llm or llm_client is None:
        logger.info(
            "factcheck: deterministic pass — %s",
            base.summary,
        )
        return base

    # LLM step: detect unsupported factual claims. Verified / orphan
    # lists are always taken from the deterministic pass so they
    # remain grounded in real data.
    try:
        claims = await _llm_detect_unsupported_claims(ctx, llm_client)
    except Exception as e:
        logger.warning("factcheck: LLM claim detection failed: %s", e)
        claims = []

    base.unsupported_claims = claims
    base.summary = (
        f"{len(base.verified)} verified, "
        f"{len(base.orphan)} orphan, "
        f"{len(base.unsupported_claims)} unsupported claims"
    )
    return base


async def _llm_detect_unsupported_claims(
    ctx: DraftContext, llm: AsyncOpenAI
) -> List[FactCheckClaim]:
    prompt_body = load_prompt("factcheck", lang=ctx.language)
    body = _all_section_text(ctx.section_drafts)
    known = sorted(_known_paper_ids(ctx))
    user_msg = (
        f"Topic: {ctx.topic}\n\n"
        f"# Known paper IDs (the project's reference set)\n"
        f"{json.dumps(known, ensure_ascii=False)}\n\n"
        f"# Section drafts\n{_truncate(body, 12000)}\n\n"
        "Return a JSON object (no prose, no fences) with fields "
        "`verified` (array of paper-id strings from the drafts that "
        "are in the known set), `orphan` (paper-id strings cited but "
        "not in the known set), `unsupported_claims` (array of "
        "{section, sentence, issue=no_citation} objects — limit to 10), "
        "and `summary` (string)."
    )
    response = await llm.chat.completions.create(
        model=_resolve_model(llm),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        max_tokens=2500,
    )
    raw = (response.choices[0].message.content or "").strip()
    return _parse_factcheck_claims(raw)


def _parse_factcheck_claims(text: str) -> List[FactCheckClaim]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    claims = []
    for item in data.get("unsupported_claims", []) or []:
        if not isinstance(item, dict):
            continue
        claims.append(
            FactCheckClaim(
                section=str(item.get("section", "")).strip(),
                sentence=str(item.get("sentence", "")).strip(),
                issue=str(item.get("issue", "no_citation")).strip() or "no_citation",
            )
        )
    return claims


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truncate(text: Optional[str], max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_validate_phase(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    skip_llm: bool = False,
) -> DraftContext:
    """
    Run Referee → FactCheck in sequence. Populates
    ``ctx.qa_report`` and marks ``PhaseName.VALIDATE`` done.

    With no LLM client, the Referee step is skipped (a placeholder
    note is appended) and the FactCheck step runs in deterministic
    mode. The phase is considered successful in both cases — having
    an empty QA report is preferable to blocking the pipeline.
    """
    ctx.mark_phase(PhaseName.VALIDATE, PhaseStatus.RUNNING)
    try:
        if ctx.cancellation_requested:
            raise RuntimeError("Validate phase cancelled by request")
        # 1. Referee (LLM-optional but produces a real report when LLM is on)
        referee_markdown = ""
        verdict = "minor revisions"
        if skip_llm or llm_client is None:
            logger.info("validate: no LLM client, skipping Referee")
            referee_markdown = (
                "## Referee\n\n_Run skipped: no LLM client configured._\n"
            )
        else:
            try:
                ref = await referee(ctx, llm_client)
                referee_markdown = ref.qa_markdown or ""
                verdict = ref.verdict
            except Exception as e:
                logger.warning("validate: Referee failed: %s", e)
                referee_markdown = f"## Referee\n\n_Run failed: {e}_\n"
                verdict = "major revisions"

        # 2. FactCheck (deterministic pass always runs)
        if ctx.cancellation_requested:
            raise RuntimeError("Validate phase cancelled by request")
        use_llm = (not skip_llm) and (llm_client is not None)
        fc = await factcheck(ctx, llm_client=llm_client, use_llm=use_llm)
        factcheck_block = _format_factcheck_block(fc, lang=ctx.language)

        # 3. Combine into ctx.qa_report
        parts: List[str] = []
        if referee_markdown:
            parts.append(referee_markdown.rstrip())
        parts.append("")
        parts.append(factcheck_block)
        ctx.qa_report = "\n".join(parts).strip()

        # Record the verdict in the metadata for downstream phases.
        ctx.quality_history.append(
            {
                "phase": PhaseName.VALIDATE.value,
                "verdict": verdict,
                "verified_count": len(fc.verified),
                "orphan_count": len(fc.orphan),
                "unsupported_count": len(fc.unsupported_claims),
            }
        )

        ctx.mark_phase(PhaseName.VALIDATE, PhaseStatus.SUCCEEDED)
        logger.info(
            "validate: %s — %s",
            verdict,
            fc.summary,
        )
    except Exception as e:
        ctx.mark_phase(PhaseName.VALIDATE, PhaseStatus.FAILED, error=str(e))
        raise
    return ctx


def _format_factcheck_block(fc: FactCheckResult, lang: str = "en") -> str:
    """Render the FactCheck result as a markdown block for inclusion
    in ``ctx.qa_report``."""
    if lang == "zh":
        header = "## FactCheck — 引用核查"
    else:
        header = "## FactCheck — Citation Verification"
    lines: List[str] = [header, ""]
    if fc.summary:
        lines.append(f"**Summary:** {fc.summary}")
        lines.append("")
    if fc.verified:
        if lang == "zh":
            lines.append("**已核验(verified):**")
        else:
            lines.append("**Verified:**")
        lines.extend(f"- `[@{pid}]`" for pid in fc.verified)
        lines.append("")
    if fc.orphan:
        if lang == "zh":
            lines.append("**孤儿引用(orphan) — 阻塞性问题:**")
        else:
            lines.append("**Orphan citations — blocking issues:**")
        lines.extend(f"- `[@{pid}]`" for pid in fc.orphan)
        lines.append("")
    if fc.unsupported_claims:
        if lang == "zh":
            lines.append("**未支持论断(unsupported):**")
        else:
            lines.append("**Unsupported claims (no citation):**")
        for c in fc.unsupported_claims:
            sec = c.section or "?"
            lines.append(f"- [{sec}] {c.sentence}")
        lines.append("")
    if not (fc.verified or fc.orphan or fc.unsupported_claims):
        if lang == "zh":
            lines.append("_无引用需要核查。_")
        else:
            lines.append("_No citations to verify._")
    return "\n".join(lines)
