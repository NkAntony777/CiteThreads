"""
CTDP — CiteThreads Draft Pipeline.

Public surface for the multi-phase paper drafting pipeline. This package
implements concepts borrowed from opendraft (Scout → Scribe → Signal →
Architect → Formatter → Crafter → Refiner → Referee → FactCheck →
Compiler) without its Gemini dependency, with first-class awareness of
the citation graph, project scoping, and async execution.

Module map
----------
- context       : DraftContext Pydantic model + phase/status enums
- quality_gate  : 5-dimension scoring (text + graph health)
- checkpoint    : per-phase file persistence for resume (P0-2)
- progress      : asyncio event bus for phase progress (P0-2)
- phases/       : individual phase implementations
- prompts/      : bilingual prompt templates
- runner        : DraftRunner orchestrator (run_phase, resume_from,
                  run_all, get_status)
"""

from .checkpoint import (
    CHECKPOINT_VERSION,
    PHASE_OUTPUTS,
    delete_phase_checkpoint,
    has_phase_checkpoint,
    list_checkpoints,
    load_phase_checkpoint,
    restore_phase_outputs,
    save_phase_checkpoint,
    snapshot_phase_outputs,
)
from .context import (
    CitationStyle,
    DraftContext,
    PhaseName,
    PhaseResult,
    PhaseStatus,
)
from .progress import (
    ALL_EVENT_TYPES,
    EVT_DONE,
    EVT_ERROR,
    EVT_PHASE_END,
    EVT_PHASE_PROGRESS,
    EVT_PHASE_START,
    Event,
    ProgressBus,
    get_bus,
    get_bus_sync,
    reset_all as reset_all_buses,
    reset_bus,
)
from .quality_gate import QualityDecision, QualityGate, QualityScore
from .runner import DraftRunner

__all__ = [
    # Context + enums
    "CitationStyle",
    "DraftContext",
    "PhaseName",
    "PhaseResult",
    "PhaseStatus",
    # Quality scoring
    "QualityDecision",
    "QualityGate",
    "QualityScore",
    # Orchestrator
    "DraftRunner",
    # Checkpoint (P0-2)
    "CHECKPOINT_VERSION",
    "PHASE_OUTPUTS",
    "save_phase_checkpoint",
    "load_phase_checkpoint",
    "has_phase_checkpoint",
    "delete_phase_checkpoint",
    "list_checkpoints",
    "snapshot_phase_outputs",
    "restore_phase_outputs",
    # Progress bus (P0-2)
    "Event",
    "ProgressBus",
    "EVT_PHASE_START",
    "EVT_PHASE_PROGRESS",
    "EVT_PHASE_END",
    "EVT_ERROR",
    "EVT_DONE",
    "ALL_EVENT_TYPES",
    "get_bus",
    "get_bus_sync",
    "reset_bus",
    "reset_all_buses",
]
