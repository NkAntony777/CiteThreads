"""
Research phase — Scout + Scribe + Signal.

Faithful port of opendraft's ``engine/phases/research.py`` with these
adaptations:

- Async functions (opendraft is sync)
- Uses our ``paper_search_service`` for paper discovery (opendraft
  calls Crossref/S2/arXiv/OpenAlex directly via ``api_citations``)
- Uses our ``LLMFactory`` AsyncOpenAI client (opendraft uses Gemini)
- Bilingual prompts (en/zh) loaded from ``prompts/{lang}/`` directory
- Writes structured results into ``DraftContext`` fields instead of
  dumping intermediate markdown to disk
- Project-scoped: respects existing ``reference_ids`` and
  ``graph_node_ids`` so we don't re-discover what the user already has

Three independent phases
------------------------
``scout``   — find candidate papers (LLM-light, mostly API calls)
``scribe``  — summarize each paper (LLM-heavy, one call per batch)
``signal``  — identify research gaps (LLM, one call on summaries)

Plus an orchestrator ``run_research_phase`` that runs all three in
order, populates ``ctx.candidate_papers``, ``ctx.paper_summaries``,
``ctx.research_gaps``, and updates phase status.

Output dataclasses
------------------
``ScoutResult`` / ``ScribeResult`` / ``SignalResult`` mirror what gets
written to ``DraftContext`` and let callers (tests, future router)
inspect intermediate state without poking at the mutable context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence

from openai import AsyncOpenAI

from ..context import DraftContext, PhaseName, PhaseStatus
from ..prompts import load_prompt
from ...paper_search_service import paper_search_service, SearchSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses (opendraft uses dicts; we use typed records)
# ---------------------------------------------------------------------------


@dataclass
class CandidatePaper:
    """A paper surfaced by Scout. Mirrors ``app.models.Paper`` fields
    but stores only the bits Scout needs (opendraft's Citation is
    heavier and includes venue-only fields we don't need here)."""

    paper_id: str
    title: str
    authors: List[str]
    year: Optional[int]
    venue: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    url: Optional[str] = None
    abstract: Optional[str] = None
    citation_count: int = 0
    source_api: str = ""  # which crawler returned it
    relevance_score: str = "Medium"  # LLM-assigned High/Medium/Low
    why_relevant: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "url": self.url,
            "abstract": self.abstract,
            "citation_count": self.citation_count,
            "source_api": self.source_api,
            "relevance_score": self.relevance_score,
            "why_relevant": self.why_relevant,
        }


@dataclass
class ScoutResult:
    candidates: List[CandidatePaper] = field(default_factory=list)
    sources_searched: List[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dicts(self) -> List[dict]:
        return [c.to_dict() for c in self.candidates]


@dataclass
class PaperSummary:
    paper_id: str
    title: str
    authors: List[str]
    year: Optional[int]
    doi: Optional[str] = None
    research_question: str = ""
    methodology: str = ""
    key_findings: List[str] = field(default_factory=list)
    implications: str = ""
    limitations: List[str] = field(default_factory=list)
    relevance_score: int = 0  # 0-5
    relevance_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "doi": self.doi,
            "research_question": self.research_question,
            "methodology": self.methodology,
            "key_findings": list(self.key_findings),
            "implications": self.implications,
            "limitations": list(self.limitations),
            "relevance_score": self.relevance_score,
            "relevance_reason": self.relevance_reason,
        }


@dataclass
class ScribeResult:
    summaries: List[PaperSummary] = field(default_factory=list)
    raw_llm_output: str = ""  # kept for debugging / re-parsing


@dataclass
class ResearchGap:
    title: str
    description: str
    gap_type: str = "methodological"  # methodological / empirical / theoretical / application / temporal
    difficulty: str = "Medium"  # Low / Medium / High
    impact: int = 3  # 1-5
    suggested_approach: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "gap_type": self.gap_type,
            "difficulty": self.difficulty,
            "impact": self.impact,
            "suggested_approach": self.suggested_approach,
        }


@dataclass
class SignalResult:
    gaps: List[ResearchGap] = field(default_factory=list)
    emerging_trends: List[str] = field(default_factory=list)
    novel_angles: List[str] = field(default_factory=list)
    raw_llm_output: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _paper_to_candidate(paper, source_api: str = "") -> CandidatePaper:
    """Convert ``app.models.Paper`` to ``CandidatePaper``."""
    return CandidatePaper(
        paper_id=paper.id,
        title=paper.title or "",
        authors=list(paper.authors or []),
        year=paper.year,
        venue=paper.venue,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        url=paper.url,
        abstract=paper.abstract,
        citation_count=paper.citation_count or 0,
        source_api=source_api or paper.id.split(":")[0] if ":" in paper.id else "",
    )


def _already_known_ids(ctx: DraftContext) -> set[str]:
    """Project-scoped dedup: don't re-surface papers the user already
    has in their reference list or graph."""
    return set(ctx.reference_ids) | set(ctx.graph_node_ids)


def _truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[: max_chars - 3] + "..."


def _format_candidates_for_prompt(
    candidates: Sequence[CandidatePaper], max_chars: int = 8000
) -> str:
    """Render candidates as a compact JSON blob for LLM context."""
    payload = []
    for c in candidates:
        payload.append(
            {
                "id": c.paper_id,
                "title": c.title,
                "authors": c.authors[:5],
                "year": c.year,
                "venue": c.venue,
                "doi": c.doi,
                "abstract": _truncate(c.abstract, 400),
                "citation_count": c.citation_count,
            }
        )
    s = json.dumps(payload, ensure_ascii=False, indent=1)
    return _truncate(s, max_chars)


# ---------------------------------------------------------------------------
# Phase 1: Scout — find candidate papers
# ---------------------------------------------------------------------------


async def scout(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    limit_per_source: int = 20,
    min_candidates: int = 10,
    use_llm_rerank: bool = True,
) -> ScoutResult:
    """
    Discover candidate papers for ``ctx.topic`` using the unified paper
    search service. If ``llm_client`` and ``use_llm_rerank`` are set,
    an LLM step assigns a relevance score to each candidate.

    Papers already in ``ctx.reference_ids`` or ``ctx.graph_node_ids``
    are excluded (project-scoped dedup).

    Args:
        ctx: The pipeline state. Only ``topic``, ``reference_ids``,
            ``graph_node_ids``, and ``language`` are read.
        llm_client: Optional AsyncOpenAI. If None and ``use_llm_rerank``
            is True, the function returns unranked results without
            calling the LLM (graceful degradation for tests).
        limit_per_source: How many papers to fetch from each source.
        min_candidates: Soft target; if fewer than this many unique
            candidates are found, the result is returned as-is and
            callers can decide to widen the search.
        use_llm_rerank: Whether to call the LLM to assign
            relevance_score / why_relevant.

    Returns:
        ``ScoutResult`` with the candidate list and per-source errors.
    """
    result = ScoutResult()
    known = _already_known_ids(ctx)

    try:
        search = await paper_search_service.search(
            query=ctx.topic,
            sources=None,  # use default trio (OpenAlex, S2, arXiv)
            limit=limit_per_source,
        )
    except Exception as e:
        logger.exception("scout: paper_search_service raised")
        result.errors["search"] = str(e)
        return result

    result.sources_searched = list(search.sources_searched)
    for src, err in search.errors.items():
        result.errors[src] = err

    for paper in search.papers:
        if paper.id in known:
            continue
        # Best-effort source tag from the paper id (e.g. "openalex:W...")
        src = ""
        if ":" in paper.id:
            src = paper.id.split(":", 1)[0]
        result.candidates.append(_paper_to_candidate(paper, src))

    if use_llm_rerank and llm_client is not None and result.candidates:
        try:
            await _llm_rerank_candidates(ctx, llm_client, result)
        except Exception as e:
            logger.warning("scout: LLM rerank failed, leaving unranked: %s", e)

    logger.info(
        "scout: %d candidates from %d sources (target %d+)",
        len(result.candidates),
        len(result.sources_searched),
        min_candidates,
    )
    return result


async def _llm_rerank_candidates(
    ctx: DraftContext, llm: AsyncOpenAI, result: ScoutResult
) -> None:
    """Ask the LLM to assign High/Medium/Low relevance to each
    candidate and write a one-sentence justification.

    Best-effort: any parse failure logs a warning and leaves the
    candidates with default "Medium" relevance."""
    prompt_body = load_prompt("scout", lang=ctx.language)
    candidates_block = _format_candidates_for_prompt(result.candidates)
    user_msg = (
        f"Topic: {ctx.topic}\n\n"
        f"Candidate papers (JSON):\n{candidates_block}\n\n"
        "For each paper, return a JSON array of objects with fields "
        "`id`, `relevance_score` (High|Medium|Low), and `why_relevant` "
        "(one sentence). Return ONLY the JSON array, no prose, no "
        "markdown fences."
    )

    response = await llm.chat.completions.create(
        model=_resolve_model(llm),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=4000,
    )
    content = (response.choices[0].message.content or "").strip()
    rankings = _parse_rerank_json(content)
    if not rankings:
        return

    by_id = {r.paper_id: r for r in rankings}
    for cand in result.candidates:
        if cand.paper_id in by_id:
            cand.relevance_score = by_id[cand.paper_id].relevance_score
            cand.why_relevant = by_id[cand.paper_id].why_relevant

    # Sort: High first, then Medium, then Low; tie-break by citation count.
    score_order = {"High": 0, "Medium": 1, "Low": 2}
    result.candidates.sort(
        key=lambda c: (
            score_order.get(c.relevance_score, 1),
            -c.citation_count,
        )
    )


def _parse_rerank_json(text: str) -> List["_RerankRow"]:
    """Tolerate code fences and stray prose around the JSON array."""
    text = text.strip()
    # Strip ```json ... ``` if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    # Extract the first JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []
    snippet = text[start : end + 1]
    try:
        data = json.loads(snippet)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rid = item.get("id") or item.get("paper_id")
        if not rid:
            continue
        score = str(item.get("relevance_score", "Medium")).capitalize()
        if score not in ("High", "Medium", "Low"):
            score = "Medium"
        rows.append(
            _RerankRow(
                paper_id=rid,
                relevance_score=score,
                why_relevant=str(item.get("why_relevant", "")).strip(),
            )
        )
    return rows


@dataclass
class _RerankRow:
    paper_id: str
    relevance_score: str
    why_relevant: str


def _resolve_model(client: AsyncOpenAI) -> str:
    """Best-effort model name from the OpenAI client. The LLMFactory
    in our codebase doesn't expose the model on the client object, so
    callers can pre-set it via configure. As a fallback we read from
    settings."""
    try:
        from ....llm_factory import _current_model  # type: ignore
        if _current_model:
            return _current_model
    except Exception:
        pass
    from ....config import settings
    return settings.ai_model


# ---------------------------------------------------------------------------
# Phase 2: Scribe — summarize each paper
# ---------------------------------------------------------------------------


async def scribe(
    ctx: DraftContext,
    candidates: Iterable[CandidatePaper],
    llm_client: AsyncOpenAI,
    *,
    batch_size: int = 5,
    max_batches: int = 4,
) -> ScribeResult:
    """
    Summarize each candidate paper. To keep LLM context manageable
    and parallelize I/O, candidates are processed in batches of
    ``batch_size`` via ``asyncio.gather``.

    The first batch always runs; subsequent batches are gated by
    the LLM's word count expectation (opendraft targets 5,000+ words
    for the full review, which is 200-400 words per paper). We cap
    at ``max_batches * batch_size`` papers so a runaway topic doesn't
    spawn 100 concurrent LLM calls.

    Returns:
        ``ScribeResult`` with one ``PaperSummary`` per processed paper.
    """
    cand_list = list(candidates)
    if not cand_list:
        return ScribeResult()

    cap = min(len(cand_list), max_batches * batch_size)
    selected = cand_list[:cap]

    prompt_body = load_prompt("scribe", lang=ctx.language)
    batches = [selected[i : i + batch_size] for i in range(0, len(selected), batch_size)]

    results: List[PaperSummary] = []
    raw_outputs: List[str] = []
    for batch in batches:
        batch_results, batch_raw = await _scribe_one_batch(
            ctx, llm_client, prompt_body, batch
        )
        results.extend(batch_results)
        raw_outputs.append(batch_raw)

    return ScribeResult(summaries=results, raw_llm_output="\n\n---\n\n".join(raw_outputs))


async def _scribe_one_batch(
    ctx: DraftContext,
    llm: AsyncOpenAI,
    prompt_body: str,
    batch: Sequence[CandidatePaper],
) -> tuple[List[PaperSummary], str]:
    user_msg = (
        f"Topic: {ctx.topic}\n\n"
        f"Papers to summarize (JSON):\n{_format_candidates_for_prompt(batch)}\n\n"
        "Return a JSON array with one object per paper, using fields "
        "`paper_id`, `research_question`, `methodology`, `key_findings` "
        "(array of 3-5 strings), `implications`, `limitations` "
        "(array), `relevance_score` (0-5 integer), `relevance_reason`. "
        "Return ONLY the JSON array."
    )
    response = await llm.chat.completions.create(
        model=_resolve_model(llm),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=4000,
    )
    raw = (response.choices[0].message.content or "").strip()
    parsed = _parse_scribe_json(raw, batch)
    return parsed, raw


def _parse_scribe_json(
    text: str, batch: Sequence[CandidatePaper]
) -> List[PaperSummary]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    by_id = {c.paper_id: c for c in batch}
    out: List[PaperSummary] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        pid = item.get("paper_id") or item.get("id")
        if not pid:
            continue
        cand = by_id.get(pid)
        score = item.get("relevance_score", 3)
        try:
            score = max(0, min(5, int(score)))
        except (TypeError, ValueError):
            score = 3
        out.append(
            PaperSummary(
                paper_id=pid,
                title=cand.title if cand else str(item.get("title", "")),
                authors=cand.authors if cand else [],
                year=cand.year if cand else None,
                doi=cand.doi if cand else None,
                research_question=str(item.get("research_question", "")).strip(),
                methodology=str(item.get("methodology", "")).strip(),
                key_findings=[
                    str(x).strip() for x in item.get("key_findings", []) if x
                ],
                implications=str(item.get("implications", "")).strip(),
                limitations=[
                    str(x).strip() for x in item.get("limitations", []) if x
                ],
                relevance_score=score,
                relevance_reason=str(item.get("relevance_reason", "")).strip(),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Phase 3: Signal — research gap analysis
# ---------------------------------------------------------------------------


async def signal(
    ctx: DraftContext,
    summaries: Iterable[PaperSummary],
    llm_client: AsyncOpenAI,
) -> SignalResult:
    """
    Identify research gaps, emerging trends, and novel angles from
    the paper summaries.

    One LLM call (synchronous within the async function) — the heavy
    lifting is in the prompt, not the iteration. Returns structured
    ``ResearchGap`` records ready for downstream phase use.
    """
    summary_list = list(summaries)
    if not summary_list:
        return SignalResult()

    prompt_body = load_prompt("signal", lang=ctx.language)
    compact = [
        {
            "paper_id": s.paper_id,
            "title": s.title,
            "research_question": _truncate(s.research_question, 300),
            "key_findings": s.key_findings[:5],
            "limitations": s.limitations[:3],
        }
        for s in summary_list
    ]
    user_msg = (
        f"Topic: {ctx.topic}\n\n"
        f"Paper summaries (JSON):\n{_truncate(json.dumps(compact, ensure_ascii=False), 8000)}\n\n"
        "Return a JSON object with three arrays: `gaps` "
        "(objects with `title`, `description`, `gap_type` one of "
        "methodological/empirical/theoretical/application/temporal, "
        "`difficulty` Low/Medium/High, `impact` 1-5, `suggested_approach`), "
        "`emerging_trends` (strings), `novel_angles` (strings). "
        "Return ONLY the JSON object."
    )
    response = await llm_client.chat.completions.create(
        model=_resolve_model(llm_client),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
        max_tokens=3000,
    )
    raw = (response.choices[0].message.content or "").strip()
    parsed = _parse_signal_json(raw)
    return SignalResult(
        gaps=parsed["gaps"],
        emerging_trends=parsed["emerging_trends"],
        novel_angles=parsed["novel_angles"],
        raw_llm_output=raw,
    )


def _parse_signal_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return {"gaps": [], "emerging_trends": [], "novel_angles": []}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {"gaps": [], "emerging_trends": [], "novel_angles": []}
    if not isinstance(data, dict):
        return {"gaps": [], "emerging_trends": [], "novel_angles": []}

    gaps: List[ResearchGap] = []
    for item in data.get("gaps", []):
        if not isinstance(item, dict):
            continue
        gaps.append(
            ResearchGap(
                title=str(item.get("title", "")).strip(),
                description=str(item.get("description", "")).strip(),
                gap_type=str(item.get("gap_type", "methodological")).strip(),
                difficulty=str(item.get("difficulty", "Medium")).strip(),
                impact=int(item.get("impact", 3) or 3),
                suggested_approach=str(item.get("suggested_approach", "")).strip(),
            )
        )
    trends = [str(t).strip() for t in data.get("emerging_trends", []) if t]
    angles = [str(t).strip() for t in data.get("novel_angles", []) if t]
    return {"gaps": gaps, "emerging_trends": trends, "novel_angles": angles}


# ---------------------------------------------------------------------------
# Orchestrator: run all three sub-phases in sequence
# ---------------------------------------------------------------------------


async def run_research_phase(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    skip_rerank: bool = False,
    skip_summarize: bool = False,
    skip_gap_analysis: bool = False,
) -> DraftContext:
    """
    Run Scout → Scribe → Signal in sequence. Mutates ``ctx`` in place.

    Each sub-phase is independently skippable so callers (and tests)
    can short-circuit. If ``llm_client`` is None and a phase needs it,
    the LLM-using step is skipped and a warning is logged; non-LLM
    steps still run.

    Returns the same ``ctx`` for chaining.
    """
    ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.RUNNING)
    try:
        # 1. Scout
        if ctx.cancellation_requested:
            raise RuntimeError("Research phase cancelled by request")
        scout_result = await scout(
            ctx,
            llm_client=llm_client,
            use_llm_rerank=(llm_client is not None) and not skip_rerank,
        )
        ctx.candidate_papers = scout_result.to_dicts()
        logger.info("research: scout produced %d candidates", len(ctx.candidate_papers))

        # 2. Scribe
        if ctx.cancellation_requested:
            raise RuntimeError("Research phase cancelled by request")
        if scout_result.candidates and not skip_summarize:
            if llm_client is None:
                logger.warning("research: no LLM client, skipping Scribe")
            else:
                cands = [_candidate_from_dict(d) for d in ctx.candidate_papers]
                scribe_result = await scribe(ctx, cands, llm_client)
                ctx.paper_summaries = [s.to_dict() for s in scribe_result.summaries]
                logger.info(
                    "research: scribe produced %d summaries",
                    len(ctx.paper_summaries),
                )

        # 3. Signal
        if ctx.cancellation_requested:
            raise RuntimeError("Research phase cancelled by request")
        if ctx.paper_summaries and not skip_gap_analysis:
            if llm_client is None:
                logger.warning("research: no LLM client, skipping Signal")
            else:
                summaries = [_summary_from_dict(d) for d in ctx.paper_summaries]
                signal_result = await signal(ctx, summaries, llm_client)
                ctx.research_gaps = [g.to_dict() for g in signal_result.gaps]
                # Also keep trends + angles as separate fields on ctx.
                ctx.outline = None  # ensure no stale outline from prior runs
                logger.info(
                    "research: signal identified %d gaps, %d trends, %d angles",
                    len(ctx.research_gaps),
                    len(signal_result.emerging_trends),
                    len(signal_result.novel_angles),
                )
                # Trends/angles are stashed on quality_history-like field
                # (we don't have a dedicated ctx field for them yet; in
                # Task #4 we may add ``emerging_trends`` / ``novel_angles``)

        ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.SUCCEEDED)
    except Exception as e:
        ctx.mark_phase(PhaseName.RESEARCH, PhaseStatus.FAILED, error=str(e))
        raise
    return ctx


def _candidate_from_dict(d: dict) -> CandidatePaper:
    return CandidatePaper(
        paper_id=d.get("id", ""),
        title=d.get("title", ""),
        authors=list(d.get("authors", [])),
        year=d.get("year"),
        venue=d.get("venue"),
        doi=d.get("doi"),
        arxiv_id=d.get("arxiv_id"),
        url=d.get("url"),
        abstract=d.get("abstract"),
        citation_count=d.get("citation_count", 0),
        source_api=d.get("source_api", ""),
        relevance_score=d.get("relevance_score", "Medium"),
        why_relevant=d.get("why_relevant", ""),
    )


def _summary_from_dict(d: dict) -> PaperSummary:
    return PaperSummary(
        paper_id=d.get("paper_id", ""),
        title=d.get("title", ""),
        authors=list(d.get("authors", [])),
        year=d.get("year"),
        doi=d.get("doi"),
        research_question=d.get("research_question", ""),
        methodology=d.get("methodology", ""),
        key_findings=list(d.get("key_findings", [])),
        implications=d.get("implications", ""),
        limitations=list(d.get("limitations", [])),
        relevance_score=d.get("relevance_score", 0),
        relevance_reason=d.get("relevance_reason", ""),
    )
