# CiteThreads Architecture

This document describes the high-level architecture of CiteThreads,
with a focus on the CTDP (CiteThreads Draft Pipeline) end-to-end
paper-generation subsystem. For a narrative tour of the existing
codebase (graph + writing assistant), see `AGENTS.md`. For the
shipped-state, limitations, and roadmap of CTDP, see
`backend/app/services/draft_pipeline/STATUS.md`.

---

## Top-level layout

CiteThreads is a standard React + FastAPI full-stack app with two
co-equal product surfaces:

1. **Citation graph + writing assistant** (the original product).
   Frontend: `frontend/src/components/GraphCanvas`,
   `SearchBar`, `WritingAssistant`, `GraphFilters`, etc.
   Backend: `backend/app/routers/papers.py`,
   `projects.py`, `writing.py`, `ai.py`, `agent.py`.

2. **CTDP long-form draft pipeline** (the add-on). Frontend: the
   `DraftGenerator` tab inside `WritingAssistant`. Backend:
   `backend/app/routers/draft.py` + the
   `backend/app/services/draft_pipeline/` package.

Both surfaces share:
- the same FastAPI process and bearer-token auth (`app/auth.py`),
- the same `AsyncOpenAI` LLM client (`app/services/llm_factory.py`),
- the same project storage (`app/services/storage.py` → JSON files
  under `data/projects/<project_id>/`),
- the same bilingual React/i18n setup.

---

## CTDP pipeline diagram

```text
                  ┌──────────────────────────────────────────────────┐
                  │  Frontend  (React 18 + Ant Design 5 + i18n)      │
                  │  ┌────────────────────────────────────────────┐  │
                  │  │  WritingAssistant  (3 tabs)                │  │
                  │  │  ├─ AI Assistant                          │  │
                  │  │  ├─ Canvas  (real-LLM streaming editor)   │  │
                  │  │  └─ Long-Form Draft  ←─ DraftGenerator    │  │
                  │  └────────────────────────────────────────────┘  │
                  └────────────────────────┬─────────────────────────┘
                                           │ axios + Bearer
                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  routers/draft.py  (7 endpoints, BearerAuthDep)                    │  │
│  │  POST /api/draft/projects/{id}/{research|structure|compose|        │  │
│  │                              validate|compile|run-all}              │  │
│  │  GET  /api/draft/projects/{id}/status                              │  │
│  │  GET  /api/draft/projects/{id}/draft.md                            │  │
│  └──────────────────────────────┬─────────────────────────────────────┘  │
│                                 │                                        │
│                                 ▼                                        │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  DraftRunner  ·  draft_pipeline/runner.py                          │  │
│  │  · load/persist DraftContext ↔ data/projects/{id}/draft_state.json │  │
│  │  · dispatch 5 phase buckets                                        │  │
│  │  · get_status() snapshot                                           │  │
│  └──────────────────────────────┬─────────────────────────────────────┘  │
│                                 │                                        │
│                                 ▼                                        │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Phase implementations  ·  draft_pipeline/phases/                  │  │
│  │  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌──────────┐  ┌────┐  │  │
│  │  │ research │→ │ structure │→ │ compose  │→ │ validate │→ │ …  │  │  │
│  │  │          │  │           │  │          │  │          │  │    │  │  │
│  │  │ scout    │  │ architect │  │ crafter  │  │ referee  │  │com-│  │  │
│  │  │ scribe   │  │ formatter │  │  (×6)    │  │ factcheck│  │pile│  │  │
│  │  │ signal   │  │           │  │ refiner  │  │          │  │    │  │  │
│  │  └──────────┘  └───────────┘  └──────────┘  └──────────┘  └────┘  │  │
│  └──────────────────────────────┬─────────────────────────────────────┘  │
│                                 │                                        │
│                                 ▼                                        │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Shared infrastructure                                             │  │
│  │  · DraftContext  (Pydantic v2) — typed state container             │  │
│  │  · QualityGate  (5-dim, 125-point) — appended to quality_history  │  │
│  │  · prompts/{en,zh}/*.md  (10 prompts × 2 languages)                │  │
│  │  · LLMFactory  (AsyncOpenAI)                                       │  │
│  │  · paper_search_service  (OpenAlex / S2 / arXiv / DBLP / PubMed)    │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## File map

| Path | Role |
|---|---|
| `backend/app/services/draft_pipeline/__init__.py` | Public surface: re-exports `DraftContext`, `QualityGate`, `DraftRunner`, enums, and the 5 phase orchestrators. |
| `backend/app/services/draft_pipeline/context.py` | `DraftContext` Pydantic v2 model + `PhaseName` / `PhaseStatus` / `CitationStyle` enums. Owns `mark_phase()`, `is_phase_done()`, `progress_pct()`. |
| `backend/app/services/draft_pipeline/quality_gate.py` | `QualityScore` dataclass + `QualityGate` class. 5-dim scoring (word_count / citation_density / completeness / structure / graph_health) on a 125-point scale. |
| `backend/app/services/draft_pipeline/prompts/__init__.py` | `load_prompt(name, lang)` with `en`/`zh` fallback; `list_loaded_prompts(lang)` helper. |
| `backend/app/services/draft_pipeline/prompts/{en,zh}/*.md` | 10 bilingual prompt templates (scout, scribe, signal, architect, formatter, crafter, refiner, referee, factcheck, compiler). |
| `backend/app/services/draft_pipeline/phases/research.py` | `scout`, `scribe`, `signal` + `run_research_phase` orchestrator. Uses `paper_search_service` + AsyncOpenAI. |
| `backend/app/services/draft_pipeline/phases/structure.py` | `architect`, `formatter` + `run_structure_phase`. Heuristic fallback without LLM. |
| `backend/app/services/draft_pipeline/phases/compose.py` | `crafter` (6 IMRaD section writers) + `refiner` + `run_compose_phase`. CTDP `[@paper_id]` citation markers. |
| `backend/app/services/draft_pipeline/phases/validate.py` | `referee`, `factcheck` + `run_validate_phase`. Citation audit against the project's known paper set. |
| `backend/app/services/draft_pipeline/phases/compile.py` | `compiler`, `abstract_writer` + `run_compile_phase`. IMRaD body assembly + APA / IEEE / Chicago / MLA reference rendering. |
| `backend/app/services/draft_pipeline/phases/tests/` | ~100 unit tests, one file per phase family. |
| `backend/app/services/draft_pipeline/runner.py` | `DraftRunner`: load/persist `DraftContext`, dispatch the 5 phase buckets, atomic JSON writes to `data/projects/{id}/draft_state.json`. |
| `backend/app/services/draft_pipeline/STATUS.md` | Full status, limitations, roadmap. |
| `backend/app/routers/draft.py` | 7 HTTP endpoints (see the table below). Behind `BearerAuthDep`. |
| `backend/app/main.py` | Registers `draft_router` under `/api` with the bearer auth dependency. |
| `backend/tests/test_draft_router.py` | 18 tests for the router + runner (auth, 503, 404, 400, happy paths, persistence, end-to-end). |
| `backend/tests/integration/test_real_llm.py` | 3 real-LLM smoke tests. Opt-in via `INTEGRATION_LLM_KEY` env var. |
| `frontend/src/components/DraftGenerator/DraftGenerator.tsx` | The 3rd tab in `WritingAssistant`. 4-button UI (research → structure → compose → compile); status polling. |
| `frontend/src/components/DraftGenerator/DraftGenerator.test.tsx` | 11 component tests. |
| `frontend/src/services/draftApi.ts` | Typed TS client for the 5 contract endpoints. |

---

## Phase flow

```text
                  ┌──────────────────────────────────────────┐
                  │  DraftContext (Pydantic v2)               │
                  │  ──────────────────────────────────────   │
                  │  Inputs (immutable):                       │
                  │   project_id, topic, language,             │
                  │   citation_style, target_word_count,       │
                  │   reference_ids, graph_node_ids            │
                  │                                           │
                  │  Phase outputs (mutated in place):         │
                  │   candidate_papers   ← research            │
                  │   paper_summaries    ← research            │
                  │   research_gaps      ← research            │
                  │   outline            ← structure           │
                  │   formatted_outline  ← structure           │
                  │   section_drafts     ← compose             │
                  │   qa_report          ← validate            │
                  │   final_draft        ← compile             │
                  │   quality_history[]  ← compile             │
                  └──────────────────────────────────────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            ▼                     ▼                     ▼
   ┌─────────────┐         ┌──────────────┐       ┌──────────────┐
   │   Phase 1   │         │   Phase 2    │       │   Phase 3    │
   │  RESEARCH   │ ──────▶ │  STRUCTURE   │ ────▶ │   COMPOSE    │
   │             │         │              │       │              │
   │  scout      │         │  architect   │       │  crafter     │
   │  scribe     │         │  formatter   │       │   ×6 IMRaD   │
   │  signal     │         │              │       │  refiner     │
   └─────────────┘         └──────────────┘       └──────┬───────┘
                                                        │
                                                        ▼
                                                 ┌──────────────┐
                                                 │   Phase 4    │
                                                 │  VALIDATE    │
                                                 │              │
                                                 │  referee     │
                                                 │  factcheck   │
                                                 └──────┬───────┘
                                                        │
                                                        ▼
                                                 ┌──────────────┐
                                                 │   Phase 5    │
                                                 │  COMPILE     │
                                                 │              │
                                                 │  compiler    │
                                                 │  abstract_w  │
                                                 └──────┬───────┘
                                                        │
                                                        ▼
                                                ctx.final_draft
                                                ctx.quality_history[-1]
```

Each phase reads what earlier phases wrote, mutates its own
output fields, and marks `PhaseResult.status` to `SUCCEEDED` /
`FAILED` / `SKIPPED`. The runner persists `DraftContext` after
every phase so the user can resume mid-pipeline.

---

## Common operations

### Start a draft run

```bash
# From the CLI:
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/draft/projects/{id}/run-all
```

```python
# From Python:
from app.services.draft_pipeline.runner import DraftRunner
runner = DraftRunner(project_id=project_id, llm_client=client)
await runner.run_all()
```

```tsx
// From the UI: click "Compile" in the DraftGenerator tab.
```

### Monitor progress

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/draft/projects/{id}/status | jq
# → { "project_id": "...", "progress_pct": 60.0, "phases": { ... } }
```

The `DraftGenerator` component polls this endpoint every 3 s while
any phase is running.

### Cancel an in-flight run

There is no cancel button in the UI yet (P1-2). In the meantime,
the runner will time out the underlying LLM call after 60 s (the
`AsyncOpenAI` timeout set in `_make_client` for the integration
tests; production uses the LLMFactory default of 30 s) and mark
the phase FAILED. A subsequent call to `/run-all` will retry from
the failed phase — the runner reads the persisted `DraftContext`
and skips already-SUCCEEDED phases.

### Export the final draft

The compiled draft is exposed as `text/markdown` at
`GET /api/draft/projects/{id}/draft.md`. A subsequent commit will
add PDF / DOCX / LaTeX export via the (currently placeholder)
`EXPORT` phase (P0-1).

### Debug a single phase

```python
import asyncio
from app.services.draft_pipeline import DraftContext, PhaseName
from app.services.draft_pipeline.phases import run_research_phase

async def debug():
    ctx = DraftContext(project_id="p1", topic="…")
    await run_research_phase(ctx, llm_client=client)
    print(f"candidates: {len(ctx.candidate_papers)}")
    print(f"summaries : {len(ctx.paper_summaries)}")
    print(f"gaps      : {len(ctx.research_gaps)}")
    print(f"phase     : {ctx.phase_results[PhaseName.RESEARCH].status.value}")

asyncio.run(debug())
```

### Run the test suite

```bash
# Default (332 mocked tests, ~6 s):
cd backend && pytest -q

# Just the draft pipeline:
cd backend && pytest app/services/draft_pipeline/ -q

# Real LLM smoke (requires secret):
export INTEGRATION_LLM_KEY=sk-...
cd backend && pytest -m integration -v
```

---

## Why a Pydantic context object?

`DraftContext` is the single source of truth for everything the
pipeline has done. Two reasons we made it Pydantic:

1. **Serialization** — `runner.py` writes it to JSON after every
   phase. A plain dict would work, but Pydantic v2 gives us
   validation on reload and a stable contract for future
   `Checkpoint` / `Resume` work (P0-2).
2. **Field ownership documentation** — each field's docstring
   states which phase writes it. A reviewer or new contributor can
   `grep` for `paper_summaries` and find both the producer (Scribe)
   and the consumers (Signal, Architect, Crafter).

The trade-off: Pydantic v2 is not free. For each phase run we
re-validate on assignment. The cost is negligible (≪1 ms) for the
field count we have, and the safety it buys is worth it.

---

## Why an in-house pipeline and not LangGraph / LlamaIndex / etc.?

Three reasons, in order of weight:

1. **Project-scoped state.** The user's existing
   `reference_ids` + `graph_node_ids` shape the entire run. A
   general-purpose graph framework has no concept of "the user's
   library" and would push the dedup logic to the caller.
2. **Citation grounding.** Every artifact (candidates, summaries,
   gaps, outline, drafts) carries a `paper_id` or `[@paper_id]`
   marker. The framework doesn't help here; we'd still write the
   same code.
3. **Audit + QA.** The `QualityGate` and the validate phase need
   to read every field on the context. Pydantic with field-level
   ownership is a better fit than a graph's free-form state dict.

The cost: ~2,000 lines of in-house phase code that we'd otherwise
get for free from a framework. We pay that cost once and own the
output. See `backend/app/services/draft_pipeline/STATUS.md` for
the list of features we still need to add.
