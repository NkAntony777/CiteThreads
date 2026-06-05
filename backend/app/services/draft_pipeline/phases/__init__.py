"""
CTDP phase implementations.

This package mirrors opendraft's ``engine/phases/`` but uses our
async-native LLM factory, paper search service, and Pydantic
``DraftContext`` instead of Gemini + dataclass + filesystem.

Sub-packages
------------
- research  : Scout + Scribe + Signal (the literature-discovery stage)
- structure : Architect + Formatter
- compose   : Crafter (per section) + Refiner
- validate  : Referee + FactCheck (QA gate)
- compile   : Compiler (final assembly + quality scoring)
- refine    : Polish + Voice + Entropy (post-draft refinement passes)
"""

from .research import (
    run_research_phase,
    scout,
    scribe,
    signal,
    ScoutResult,
    ScribeResult,
    SignalResult,
    CandidatePaper,
    PaperSummary,
    ResearchGap,
)
from .structure import (
    run_structure_phase,
    architect,
    formatter,
    Outline,
    OutlineSection,
    FormattedOutline,
)
from .compose import (
    run_compose_phase,
    crafter,
    crafter_introduction,
    crafter_literature_review,
    crafter_methodology,
    crafter_results,
    crafter_discussion,
    crafter_conclusion,
    refiner,
    SectionDraft,
    CrafterResult,
    RefinerResult,
    ComposeResult,
    SECTION_NAMES,
    split_word_budget,
    count_citations,
    count_words,
    citation_density_ok,
)
from .validate import (
    run_validate_phase,
    referee,
    factcheck,
    RefereeResult,
    RefereeFinding,
    FactCheckResult,
    FactCheckClaim,
)
from .compile import (
    run_compile_phase,
    compiler,
    abstract_writer,
    CompilerResult,
)
from .citation_verify import (
    citation_verify,
    CitationAudit,
)
from .table_figure_hints import (
    apply_table_figure_hints,
    suggest_table_figure_hints,
    TableFigureHints,
)
from .refine import (
    run_refine_phase,
    polish,
    voice,
    entropy,
    RefinePassResult,
    ALL_PASSES,
    ENTROPY_SECTION_KEYS,
    PASS_POLISH,
    PASS_VOICE,
    PASS_ENTROPY,
)

__all__ = [
    "run_research_phase",
    "scout",
    "scribe",
    "signal",
    "ScoutResult",
    "ScribeResult",
    "SignalResult",
    "CandidatePaper",
    "PaperSummary",
    "ResearchGap",
    "run_structure_phase",
    "architect",
    "formatter",
    "Outline",
    "OutlineSection",
    "FormattedOutline",
    # Compose phase (Task #5)
    "run_compose_phase",
    "crafter",
    "crafter_introduction",
    "crafter_literature_review",
    "crafter_methodology",
    "crafter_results",
    "crafter_discussion",
    "crafter_conclusion",
    "refiner",
    "SectionDraft",
    "CrafterResult",
    "RefinerResult",
    "ComposeResult",
    "SECTION_NAMES",
    "split_word_budget",
    "count_citations",
    "count_words",
    "citation_density_ok",
    # validate (Task #6)
    "run_validate_phase",
    "referee",
    "factcheck",
    "RefereeResult",
    "RefereeFinding",
    "FactCheckResult",
    "FactCheckClaim",
    # compile (Task #6)
    "run_compile_phase",
    "compiler",
    "abstract_writer",
    "CompilerResult",
    # citation verifier + table/figure hints (Task #30)
    "citation_verify",
    "CitationAudit",
    "apply_table_figure_hints",
    "suggest_table_figure_hints",
    "TableFigureHints",
    # multi-round refinement passes (Task #28)
    "run_refine_phase",
    "polish",
    "voice",
    "entropy",
    "RefinePassResult",
    "ALL_PASSES",
    "ENTROPY_SECTION_KEYS",
    "PASS_POLISH",
    "PASS_VOICE",
    "PASS_ENTROPY",
]
