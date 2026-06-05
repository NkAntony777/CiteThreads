"""
Quality gate — 5-dimension scoring for draft pipeline outputs.

Why 5 dimensions, not 4 (opendraft's count)?
--------------------------------------------
opendraft scores 4 axes × 25 points = 100 max. We add a 5th axis,
``graph_health``, that uses the citation graph already built for the
project (community coverage, intent distribution, gap closure, recency).
This is the "stronger than original" differentiator in the foundation
layer. Other axes are re-implemented with cleaner contracts:

  - word_count        : actual / target, capped at 25
  - citation_density  : [@Key] occurrences per 1000 words, 8/k = full
  - completeness      : IMRaD section coverage, 6 / 6 = full
  - structure         : Markdown heading hierarchy, 1×h1 + 6×h2 + 10×h3 = full
  - graph_health      : see _score_graph_health, 25 points total

Decision thresholds (125-point scale):
  - total >= 100 : pass (continue)
  - 75 <= total < 100 : warn (continue with logged issues)
  - total < 75  : fail (block / trigger retry in Task 2)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .context import DraftContext


class QualityDecision(str, Enum):
    """Outcome of a quality evaluation."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


# Map threshold name -> (int value) for clarity in callers.
DECISION_THRESHOLDS = {
    QualityDecision.PASS: 100,
    QualityDecision.WARN: 75,
}


@dataclass
class QualityScore:
    """A single quality evaluation. Pure data; no side effects."""

    total: int = 0
    word_count: int = 0
    citation_density: int = 0
    completeness: int = 0
    structure: int = 0
    graph_health: int = 0
    issues: List[str] = field(default_factory=list)
    decision: QualityDecision = QualityDecision.FAIL

    @property
    def passed(self) -> bool:
        """True unless the gate decided to fail."""
        return self.decision is not QualityDecision.FAIL

    def to_dict(self) -> dict:
        """Stable JSON-friendly snapshot."""
        d = asdict(self)
        d["decision"] = self.decision.value
        return d


class QualityGate:
    """
    5-dimension scorer. Pure function over a DraftContext snapshot —
    no I/O, no LLM, deterministic. Safe to call in tests and during
    fast feedback loops.

    The class is a thin namespace; the constants below are class-level
    so callers (and tests) can override them for custom thresholds
    (e.g. stricter gating for journal-style output).
    """

    # Default thresholds on the 125-point scale.
    PASS_THRESHOLD: int = 100
    WARN_THRESHOLD: int = 75

    # Per-dimension cap.
    DIMENSION_MAX: int = 25

    # IMRaD section substrings (lowercased, matched against section
    # names in DraftContext.section_drafts).
    EXPECTED_SECTIONS: List[str] = [
        "introduction",
        "literature review",
        "methodology",
        "results",
        "discussion",
        "conclusion",
    ]

    def score(self, ctx: "DraftContext") -> QualityScore:
        """Compute the 5-dimension score and return a fresh QualityScore."""
        s = QualityScore()
        s.word_count = self._score_word_count(ctx)
        s.citation_density = self._score_citation_density(ctx)
        s.completeness = self._score_completeness(ctx)
        s.structure = self._score_structure(ctx)
        s.graph_health = self._score_graph_health(ctx)
        s.total = (
            s.word_count
            + s.citation_density
            + s.completeness
            + s.structure
            + s.graph_health
        )
        s.issues = self._collect_issues(s)
        s.decision = self._decide(s.total)
        return s

    # --- per-dimension helpers -----------------------------------------

    def _score_word_count(self, ctx: "DraftContext") -> int:
        actual = sum(len(s.split()) for s in ctx.section_drafts.values())
        target = ctx.target_word_count
        if target <= 0:
            return self.DIMENSION_MAX
        ratio = min(actual / target, 1.0)
        return int(ratio * self.DIMENSION_MAX)

    def _score_citation_density(self, ctx: "DraftContext") -> int:
        """Citations per 1000 words; 8/k = full marks (linear)."""
        if not ctx.section_drafts:
            return 0
        total_words = sum(len(s.split()) for s in ctx.section_drafts.values())
        if total_words == 0:
            return 0
        total_cites = sum(s.count("[@") for s in ctx.section_drafts.values())
        density = total_cites / total_words * 1000
        score = int(density / 8 * self.DIMENSION_MAX)
        return min(score, self.DIMENSION_MAX)

    def _score_completeness(self, ctx: "DraftContext") -> int:
        if not self.EXPECTED_SECTIONS:
            return self.DIMENSION_MAX
        sections_lc = {s.lower() for s in ctx.section_drafts.keys()}
        present = sum(
            1
            for needle in self.EXPECTED_SECTIONS
            if any(needle in name for name in sections_lc)
        )
        return int(present / len(self.EXPECTED_SECTIONS) * self.DIMENSION_MAX)

    def _score_structure(self, ctx: "DraftContext") -> int:
        all_md = "\n".join(ctx.section_drafts.values())
        h1 = all_md.count("\n# ")
        h2 = all_md.count("\n## ")
        h3 = all_md.count("\n### ")
        score = 0
        if h1 >= 1:
            score += 8
        if h2 >= 6:
            score += 9
        if h3 >= 10:
            score += 8
        return min(score, self.DIMENSION_MAX)

    def _score_graph_health(self, ctx: "DraftContext") -> int:
        """
        Graph-aware dimension. Uses the project's GraphData fields that
        DraftContext already carries.

        Heuristic contributions (each capped):
        - 5 pts : >= 5 papers in the graph
        - 5 pts : >= 10 candidate papers surfaced by Scout
        - 5 pts : at least one research_gap referenced in the draft
        - 5 pts : >= 50% candidate papers from year >= 2020
        - 5 pts : previous quality score in history >= 75
        """
        score = 0
        if len(ctx.graph_node_ids) >= 5:
            score += 5
        if len(ctx.candidate_papers) >= 10:
            score += 5
        # ``research_gaps`` are stored as dicts by the Signal phase; a
        # gap "counts as referenced" if any of its string values
        # appears in the project's reference list.
        if ctx.research_gaps and any(
            isinstance(g, dict)
            and any(isinstance(v, str) and v in ctx.reference_ids for v in g.values())
            for g in ctx.research_gaps
        ):
            score += 5
        if ctx.candidate_papers:
            recent = sum(
                1
                for p in ctx.candidate_papers
                if isinstance(p.get("year"), int) and p["year"] >= 2020
            )
            if recent / len(ctx.candidate_papers) >= 0.5:
                score += 5
        # ``quality_history`` is heterogeneous: the validate phase
        # stashes a verdict dict, while compile appends a
        # ``QualityScore``. Find the most recent QualityScore so the
        # "previous run scored well" check survives.
        if ctx.quality_history:
            for entry in reversed(ctx.quality_history):
                if isinstance(entry, QualityScore):
                    if entry.total >= self.WARN_THRESHOLD:
                        score += 5
                    break
        return min(score, self.DIMENSION_MAX)

    # --- aggregation ----------------------------------------------------

    @staticmethod
    def _collect_issues(s: QualityScore) -> List[str]:
        issues: List[str] = []
        if s.word_count < 15:
            issues.append(f"字数不足（word_count={s.word_count}/25）")
        if s.citation_density < 10:
            issues.append(f"引用密度低（citation_density={s.citation_density}/25）")
        if s.completeness < 15:
            issues.append(f"章节缺失（completeness={s.completeness}/25）")
        if s.structure < 10:
            issues.append(f"标题层级不够（structure={s.structure}/25）")
        if s.graph_health < 10:
            issues.append(f"图谱健康度低（graph_health={s.graph_health}/25）")
        return issues

    def _decide(self, total: int) -> QualityDecision:
        if total >= self.PASS_THRESHOLD:
            return QualityDecision.PASS
        if total >= self.WARN_THRESHOLD:
            return QualityDecision.WARN
        return QualityDecision.FAIL
