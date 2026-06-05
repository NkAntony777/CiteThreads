"""
DraftContext — the mutable state object threaded through every pipeline
phase.

Design intent
-------------
opendraft's ``DraftContext`` is a loose dataclass where any phase can
read or write any attribute. We trade that flexibility for type safety
and a stable contract: every field below is documented, typed, and
written by a specific phase family.

Pydantic v2 is used (consistent with ``app.models.schemas.Paper``) so
the context can be (de)serialized for checkpoint/resume in Task 2.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PhaseName(str, Enum):
    """
    The 6 user-visible phase buckets. Each bucket is implemented by one
    or more sub-phases in opendraft terminology (e.g. RESEARCH = Scout
    + Scribe + Signal). Using a string enum keeps the field JSON-
    serializable and lets the frontend iterate ``Object.values(PhaseName)``
    to render a progress tracker.
    """

    RESEARCH = "research"
    STRUCTURE = "structure"
    COMPOSE = "compose"
    VALIDATE = "validate"
    COMPILE = "compile"
    EXPORT = "export"


class PhaseStatus(str, Enum):
    """Lifecycle status of a single phase execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class CitationStyle(str, Enum):
    """Supported output citation styles. Matches opendraft's set."""

    APA = "apa"
    IEEE = "ieee"
    CHICAGO = "chicago"
    MLA = "mla"
    NALT = "nalt"


class PhaseResult(BaseModel):
    """Execution record for a single phase."""

    phase: PhaseName
    status: PhaseStatus = PhaseStatus.PENDING
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    output_ref: Optional[str] = None
    passes: List[str] = Field(
        default_factory=list,
        description=(
            "Sub-step labels applied during this phase. Used by the "
            "Compose phase to record which refinement passes "
            "(polish/voice/entropy) were applied. Empty for phases "
            "that have no sub-step concept."
        ),
    )


# Forward reference: QualityScore lives in quality_gate. Pydantic v2
# handles forward refs as long as the type is resolvable at validation
# time. We import lazily inside the model to avoid an import cycle
# (quality_gate TYPE_CHECKING-imports DraftContext).
_QUALITY_SCORE_TYPE = "QualityScore"


class DraftContext(BaseModel):
    """
    The pipeline state object. Initialized with user inputs and a few
    reference IDs from the existing Project. Each phase reads from and
    writes to specific fields documented below.

    Field ownership
    ---------------
    - User inputs       : set once at init, never mutated
    - Reference scoping : set at init from Project, never mutated
    - Internal state    : written by specific phases
    """

    model_config = {"arbitrary_types_allowed": True}

    # --- User inputs (immutable) ----------------------------------------
    project_id: str = Field(..., min_length=1, description="Owning project id")
    topic: str = Field(..., min_length=1, description="The paper topic")
    language: str = Field("en", description="Output language: 'en' or 'zh'")
    citation_style: CitationStyle = CitationStyle.APA
    target_word_count: int = Field(8000, ge=100, le=200_000)
    author_name: Optional[str] = None
    institution: Optional[str] = None

    # --- Reference scoping (immutable, from Project) --------------------
    reference_ids: List[str] = Field(
        default_factory=list,
        description="Paper IDs the user has already added to the project",
    )
    graph_node_ids: List[str] = Field(
        default_factory=list,
        description="Paper IDs present in the project's citation graph",
    )

    # --- Internal state (phase-owned) -----------------------------------
    phase_results: Dict[PhaseName, PhaseResult] = Field(default_factory=dict)
    quality_history: List[Any] = Field(
        default_factory=list,
        description="Append-only list of QualityScore snapshots",
    )

    # Phase outputs (see field docstrings for owners)
    candidate_papers: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Scout output: candidate paper dicts (Paper.model_dump compatible)",
    )
    paper_summaries: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Scribe output: per-paper summary dicts",
    )
    research_gaps: List[dict] = Field(
        default_factory=list,
        description=(
            "Signal output: list of research-gap dicts. Schema per "
            "element: {title, description, gap_type, difficulty, impact, "
            "suggested_approach}. The Signal phase writes dicts (not "
            "strings) so downstream phases can render structured UI "
            "or pass them to LLM prompts."
        ),
    )
    outline: Optional[dict] = Field(
        default=None,
        description=(
            "Architect output: structured outline (paper_type, sections, "
            "target_venue, etc.). Schema: {paper_type, target_venue, "
            "research_question, draft_statement, total_target_words, "
            "sections: [{number, title, target_words, key_points, "
            "evidence_paper_ids}]}. Use the structured fields directly; "
            "use formatted_outline for the markdown rendering."
        ),
    )
    formatted_outline: Optional[str] = Field(
        default=None, description="Formatter output: outline with style applied"
    )
    section_drafts: Dict[str, str] = Field(
        default_factory=dict,
        description="Crafter output: section_name -> markdown body",
    )
    final_draft: Optional[str] = Field(
        default=None, description="Compiler output: assembled full draft"
    )
    qa_report: Optional[str] = Field(
        default=None, description="Referee + FactCheck output: QA findings"
    )
    abstract: Optional[str] = Field(
        default=None,
        description=(
            "AbstractWriter output: 200-300 word paper abstract, stored "
            "separately from final_draft so callers can refresh it "
            "without re-running the Compiler."
        ),
    )
    citation_audit: Optional[dict] = Field(
        default=None,
        description=(
            "CitationVerifier output: structured audit of every "
            "[@paper_id] in the section drafts. Schema: "
            "{verified: [ids], incomplete: [ids], unresolved: [ids], "
            "summary: str, replacements: {old_id: [suggested_ids]}}. "
            "Each entry in verified/incomplete/unresolved is a paper_id "
            "string. LLM-suggested replacement IDs are only present when "
            "the phase ran with an LLM client."
        ),
    )

    # --- Control flags -------------------------------------------------
    cancellation_requested: bool = False

    # --- Helpers -------------------------------------------------------

    def is_phase_done(self, phase: PhaseName) -> bool:
        """True only if the phase has a SUCCEEDED record."""
        result = self.phase_results.get(phase)
        return result is not None and result.status == PhaseStatus.SUCCEEDED

    def progress_pct(self) -> float:
        """Percentage (0–100) of dispatchable buckets that have succeeded.

        The ``PhaseName`` enum carries 6 values, but only 5 are
        actually dispatchable by ``DraftRunner`` (RESEARCH, STRUCTURE,
        COMPOSE, VALIDATE, COMPILE). EXPORT is a placeholder for a
        future phase. We divide by the dispatchable count so the bar
        reaches 100% at end-to-end run completion, matching user
        expectations.
        """
        dispatchable = {PhaseName.RESEARCH, PhaseName.STRUCTURE, PhaseName.COMPOSE,
                         PhaseName.VALIDATE, PhaseName.COMPILE}
        total = len(dispatchable)
        done = sum(1 for p in dispatchable if self.is_phase_done(p))
        return round(done / total * 100, 1)

    def mark_phase(self, phase: PhaseName, status: PhaseStatus,
                   *, error: Optional[str] = None,
                   output_ref: Optional[str] = None) -> PhaseResult:
        """Update or create the PhaseResult for ``phase``."""
        now = datetime.utcnow()
        existing = self.phase_results.get(phase)
        if existing is None:
            existing = PhaseResult(phase=phase, status=status)
        else:
            existing.status = status
        if status == PhaseStatus.RUNNING and existing.started_at is None:
            existing.started_at = now
        if status in (PhaseStatus.SUCCEEDED, PhaseStatus.FAILED, PhaseStatus.SKIPPED):
            existing.finished_at = now
        if error is not None:
            existing.error = error
        if output_ref is not None:
            existing.output_ref = output_ref
        self.phase_results[phase] = existing
        return existing


# Re-bind the forward reference so quality_history annotations work in
# Pydantic v2 (no-op for our purposes since quality_history is List[Any],
# but keeps the door open for List[QualityScore] in Task 2+).
DraftContext.model_rebuild()
