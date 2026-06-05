# Draft Pipeline — Status (CTDP)

CTDP (CiteThreads Draft Pipeline) is a 5-phase paper-drafting pipeline
that takes a project (a curated set of papers + a topic) and produces
a structured, citation-grounded long-form draft. It is a faithful
port of opendraft's phase design (Scout → Scribe → Signal → Architect
→ Formatter → Crafter → Refiner → Referee → FactCheck → Compiler)
rebound to our async stack: Pydantic `DraftContext`, the unified
`paper_search_service`, the OpenAI-compatible `LLMFactory`, and the
project-scoped citation graph.

> **The pipeline is shipped.** All 5 user-facing phases are
> implemented and callable via Python or HTTP. See *How to use* below.

---

## What exists and is production-ready

| Component | Status | Coverage |
|---|---|---|
| `DraftContext` (Pydantic v2) — user inputs + phase outputs | ✅ Stable | 17+ unit tests in `tests/test_context.py` |
| `QualityGate` + `QualityScore` (5-dim, 125-point scale) | ✅ Stable | 32 unit tests in `tests/test_quality_gate.py` |
| `PhaseName` / `PhaseStatus` / `CitationStyle` / `PhaseResult` / `QualityDecision` enums | ✅ Stable | covered via context tests |
| `prompts.load_prompt(name, lang)` — bilingual loader with en/zh fallback | ✅ Stable | imports clean; no dedicated tests |
| Bilingual prompt `.md` content (12 prompts × 2 langs = 24 files) | ✅ scout / scribe / signal / architect / formatter / crafter / refiner / referee / factcheck / compiler / abstract_generator / citation_verify | manually reviewed |
| **Research phase** (`phases/research.py`): `scout` + `scribe` + `signal` + `run_research_phase` | ✅ Async; uses `paper_search_service`; falls back gracefully without LLM | 29 unit tests in `phases/tests/test_research.py` |
| **Structure phase** (`phases/structure.py`): `architect` + `formatter` + `run_structure_phase` | ✅ Async; heuristic fallback; supports APA / IEEE / Chicago / MLA / NALT | 23 unit tests in `phases/tests/test_structure.py` |
| **Compose phase** (`phases/compose.py`): `crafter` (6 section writers) + `refiner` + `run_compose_phase` | ✅ Async; 6 IMRaD sections; stub drafts without LLM; CTDP `[@paper_id]` citation markers | 32 unit tests in `phases/tests/test_compose.py` |
| **Refine phase** (`phases/refine.py`): `polish` + `voice` + `entropy` + `run_refine_phase` | ✅ Three sequential refinement passes on `ctx.section_drafts`; polish + voice are per-section LLM calls, entropy is one cross-section LLM call; LLM errors propagate to fail the phase, parse failures degrade to no-op for the affected section; pass names recorded on `PhaseResult.passes` | 24 unit tests in `phases/tests/test_refine.py` |
| **Validate phase** (`phases/validate.py`): `referee` + `factcheck` + `run_validate_phase` | ✅ Async; deterministic citation audit; optional LLM-augmented claim detection; bilingual QA report | covered in `phases/tests/test_validate_compile.py` |
| **Compile phase** (`phases/compile.py`): `compiler` + `abstract_writer` + `run_compile_phase` | ✅ Async; IMRaD body assembly; APA / IEEE / Chicago / MLA references; `QualityGate.score()` appended to `ctx.quality_history`; `ctx.final_draft` populated; `abstract_writer` uses `abstract_generator.md` prompt and writes `ctx.abstract` | covered in `phases/tests/test_validate_compile.py` + `phases/tests/test_enhance.py` |
| **Citation Verifier** (`phases/citation_verify.py`): `citation_verify` | ✅ Async; deterministic 3-bucket audit (verified / incomplete / unresolved); LLM-optional replacement suggestions for unresolved ids; result on `ctx.citation_audit` + appended to `ctx.qa_report` | covered in `phases/tests/test_enhance.py` |
| **Table/Figure hints** (`phases/table_figure_hints.py`): `suggest_table_figure_hints` + `apply_table_figure_hints` | ✅ Deterministic heuristic; numeric-density + comparison → TABLE; sequence / workflow language → FIGURE; honors `paper_summaries.key_findings` as a boost; bilingual (en/zh) block appended to `ctx.final_draft` | covered in `phases/tests/test_enhance.py` |
| **Orchestrator** (`runner.py`): `DraftRunner` — load/persist `DraftContext` to `data/projects/{id}/draft_state.json`; dispatch the 5 phase buckets (`run_phase`, `resume_from`, `run_all`, `get_status`) | ✅ Atomic JSON writes; LLM-less fallback per phase; per-phase checkpoints in `data/projects/{id}/checkpoints/{phase}.json` (atomic, version-stamped, stale-version ignored); `resume_from(phase)` skips already-done phases from on-disk checkpoints; publishes `phase-start` / `phase-progress` / `phase-end` / `error` / `done` events to an injectable `ProgressBus`; honors `ctx.cancellation_requested` between phases | 18 router tests + 13 checkpoint/resume tests in `tests/test_draft_checkpoint_resume.py` |
| **Draft router** (`routers/draft.py`): 7 endpoints under `/api/draft/projects/{id}/…` + SSE progress stream at `/stream` | ✅ Behind `BearerAuthDep`; 503 when no LLM key; 404 unknown project; 400 malformed id; LLM-free endpoints (`/status`, `/draft.md`, `/export.*`) work without a key; `/stream` serves `text/event-stream` with `: heartbeat` keepalive every 15s and `done` as the terminal event; multiple concurrent subscribers each receive every event | 18 router tests + 14 SSE / bus tests in `tests/test_draft_sse.py` |
| **Draft export** (`services/draft_pipeline/exporters.py` + 3 router endpoints): `to_pdf` (WeasyPrint), `to_docx` (python-docx), `to_latex` (article-class) | ✅ LLM-free; 404 when `ctx.final_draft` is None; PDF returns 503 on missing GTK3 runtime (Windows); DOCX/LaTeX run on any platform; 22 new tests | 25 tests in `tests/test_draft_export.py` (22 pass + 3 PDF skipped on Windows) |
| **Real LLM integration tests** (`tests/integration/test_real_llm.py`): Scout + Scribe + Signal against a live model | ✅ Opt-in via `INTEGRATION_LLM_KEY` env var; auto-skipped in CI without secrets | 3 tests; smoke test for prompt regressions |
| **DraftGenerator component** (`frontend/src/components/DraftGenerator/`): 3rd tab in `WritingAssistant` | ✅ Bilingual (en/zh); 4-stage UI; 11 component tests; i18n keys in both locales | 11 tests in `DraftGenerator.test.tsx` |
| **Per-section regenerate + cancel + friendly errors** (P1-2): `POST /sections/{name}/regenerate` re-crafts one section with optional `custom_instructions`; `POST /cancel` sets the runner's cancellation flag; the runner checks the flag at every phase + sub-section boundary; the frontend adds a cancel button (red, danger) next to the run buttons, a per-section "🔄 重写" button that opens a modal with a custom-instructions textarea, and translates raw API errors into friendly Chinese/English messages (auth/llm-key/network/5xx/garbage) | ✅ All 4 crafter_* writers accept `custom_instructions`; runner's `request_cancellation` / `clear_cancellation` round-trip; flag persists to `draft_state.json` and survives runner restarts; 13 backend tests + 5 new frontend tests | 13 new tests in `tests/test_draft_per_section.py`; 21 tests in `DraftGenerator.test.tsx` (16 prior + 5 new) |
| **Bearer auth** (`app/auth.py`): `require_bearer_token` + `is_auth_enabled` | ✅ Skips public paths; 401 on missing/wrong token; configurable | 8 tests in `tests/test_review_security_fixes.py` |
| **Per-user auth + rate limit + cost guard** (P2-1): `app/users.py` loads a per-user table from `CITETHREADS_USERS_JSON` (or `data/users.json`); `app/rate_limit.py` is a per-user sliding-window limiter (10 req/min default on LLM endpoints) returning 429 + `Retry-After`; `app/cost_guard.py` records every LLM call's tokens to `data/usage/{user_id}/{YYYY-MM}.jsonl` and enforces a per-user `monthly_token_budget` with 429 + `X-Reason: budget_exceeded`; the LLM usage is captured transparently by a `_UsageRecordingClient` wrapper around `AsyncOpenAI` so call sites don't change; `app/routers/admin.py` exposes `GET /api/admin/usage?user_id=X&month=YYYY-MM` and `GET /api/admin/usage/users`; `app/routers/draft.py` + `agent.py` get `Depends(make_llm_guard_dependency("phase"))` on the LLM endpoints only (free endpoints like `/status`, `/draft.md`, `/tools` stay un-throttled); `ProjectMetadata.user_id` plus `project_storage.get_project(user_id=...)` enforce cross-user isolation at the storage layer; single-secret mode is the dev default (any matching token → `ANONYMOUS_ADMIN`) | ✅ Two modes auto-detected at startup; per-user budget is enforced with a header-detailed 429; the JSONL ledger is append-only and never blocks the LLM call on write failure; the 4 pre-existing skipped tests are unaffected | 17 tests in `tests/test_per_user_auth.py` |
| **Structured JSON logging** (`app/logging_config.py`): `configure_json_logging()` replaces `logging.basicConfig`; `JsonFormatter` emits one-line JSON (`ts`/`level`/`logger`/`msg` + optional `request_id`/`user_id`/`phase`/`duration_ms`); `RequestIdMiddleware` binds `X-Request-ID` to a contextvar for the request lifetime and emits the access log line | ✅ Idempotent (re-calls clear prior handlers); uvicorn loggers rewired; survives schema-less extras | 13 tests in `tests/test_logging_config.py` |
| **Per-phase metrics** (`app/metrics.py`): in-memory `MetricsStore` with counters + histograms; module-level `metrics` singleton; `phase_timer` context manager wraps the runner's `run_phase`; `/api/metrics` returns JSON snapshot, `/metrics` returns Prometheus text exposition | ✅ No external deps (stdlib only); `record_llm_call(...)` API ready for per-call instrumentation; per-bucket histogram is +Inf only (we don't keep raw samples) | 8 tests in `tests/test_metrics.py` |
| **Health checks** (`app/health.py`): `/health` returns combined report (`status: ok\|degraded\|down` + per-check breakdown); three checks: `llm_configured` (informational), `data_writable` (atomic temp-write under `data/projects/`), `pipeline_loaded` (re-imports the runner); `/health/live` is a no-dep liveness probe; `/health/ready` is the readiness probe | ✅ All checks local; total cost < 50ms; HTTP 200 for `ok`/`degraded`, 503 for `down` | 10 tests in `tests/test_health.py` |
| **Docker** (`backend/Dockerfile`, `backend/docker-compose.yml`, `frontend/Dockerfile`, `frontend/nginx.conf`, `docker-compose.yml` at project root): backend is `python:3.11-slim` with weasyprint's libpango/libcairo/libgdk-pixbuf pre-installed, non-root `appuser` (uid 1000); frontend is multi-stage (`node:20-alpine` → `nginx:alpine`) with SPA fallback and `/api` reverse-proxy; project-root compose wires both with a backend healthcheck gate | ✅ Production-ready; the nginx config is hand-written (no template magic) | n/a (infra) |
| **CI** (`.github/workflows/ci.yml`, `.github/workflows/docker.yml`): backend job runs `pytest` on Python 3.11 with cached pip and weasyprint system deps; frontend job runs `tsc --noEmit` + `lint` + `test:run` + `build` on Node 20 with cached npm; coverage.xml is uploaded as an artifact; docker job builds + pushes to GHCR on tag push (`v*`) | ✅ Two parallel jobs; concurrency cancel-in-progress; coverage retention 14d | n/a (CI) |

---

## How to use

The pipeline has three entry points, listed from lowest-level
(greatest control) to highest-level (greatest convenience).

### 1. Run a single phase (Python)

```python
import asyncio
from app.services.draft_pipeline import (
    DraftContext,
    CitationStyle,
)
from app.services.draft_pipeline.phases import (
    run_research_phase,
    run_structure_phase,
    run_compose_phase,
    run_refine_phase,
    run_validate_phase,
    run_compile_phase,
)
from app.services.llm_factory import create_llm_client

client = create_llm_client()  # reads settings.siliconflow_api_key

ctx = DraftContext(
    project_id="my-project",
    topic="transformer neural networks for protein folding",
    language="en",
    citation_style=CitationStyle.APA,
    target_word_count=8000,
)

# Run phases in order. Each call mutates `ctx` in place and marks its
# PhaseResult. Skip the LLM-less ones; they will warn-log and continue.
await run_research_phase(ctx, llm_client=client)
await run_structure_phase(ctx, llm_client=client)
await run_compose_phase(ctx, llm_client=client)
# Optional: polish → voice → entropy pass on the section drafts.
# Recorded on PhaseName.COMPOSE.passes. Without an LLM, no-op.
await run_refine_phase(ctx, llm_client=client)
await run_validate_phase(ctx, llm_client=client)
await run_compile_phase(ctx, llm_client=client)

# Final draft is on ctx.final_draft; quality history on ctx.quality_history
print(ctx.final_draft[:500])
print(ctx.progress_pct())  # 100.0
```

To run a single phase in isolation (e.g. during development):

```python
ctx = DraftContext(project_id="p1", topic="…", target_word_count=3000)
await run_research_phase(ctx, llm_client=client)
print(len(ctx.candidate_papers), "candidates")
print(len(ctx.paper_summaries), "summaries")
```

Phases are **idempotent w.r.t. inputs but cumulative in outputs** —
running research twice keeps the inputs, replaces the candidates /
summaries / gaps. Markers like `ctx.is_phase_done(PhaseName.RESEARCH)`
let you check state without re-running.

### 2. Run the full pipeline via `DraftRunner` (Python)

`DraftRunner` adds **persistence** (state survives a process restart)
and a single `run_all()` entry point. Use it when you need resume
across HTTP requests.

```python
from app.services.draft_pipeline import DraftRunner
from app.services.llm_factory import create_llm_client

client = create_llm_client()
runner = DraftRunner(project_id="my-project", llm_client=client)

# Run all 5 phases in order.
ctx = await runner.run_all()

# Or one at a time — state is persisted after each call, so the next
# call (or a fresh runner) can pick up where this left off.
await runner.run_phase(PhaseName.RESEA); await runner.run_phase(PhaseName.STRUCTURE)
# …

# Snapshot for the /status endpoint:
status = runner.get_status()
# → {"project_id": "my-project", "progress_pct": 100.0, "phases": {...}}
```

State is written atomically to
`backend/data/projects/{id}/draft_state.json` after every phase
(tempfile + `os.replace`), so a crash mid-write never leaves a
half-written file the next request will read.

### 3. Call the HTTP endpoints

All endpoints are mounted under `/api/draft/projects/{id}/…` and
require `Authorization: Bearer <token>` (see `app/auth.py`).

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/draft/projects/{id}/research` | (none) | `200 {ok, candidates, summaries, gaps}` |
| POST | `/api/draft/projects/{id}/structure` | (none) | `200 {ok, outline, formatted_outline}` |
| POST | `/api/draft/projects/{id}/compose` | (none) | `200 {ok, section_drafts}` |
| POST | `/api/draft/projects/{id}/validate` | (none) | `200 {ok, qa_report}` |
| POST | `/api/draft/projects/{id}/compile` | (none) | `200 {ok, final_draft_preview}` |
| POST | `/api/draft/projects/{id}/run-all` | (none) | `200 {ok}` (runs all 5 in order) |
| GET  | `/api/draft/projects/{id}/status` | — | `200 {project_id, progress_pct, phases}` |
| GET  | `/api/draft/projects/{id}/draft.md` | — | `200 text/markdown` after compile; `404` before |
| GET  | `/api/draft/projects/{id}/export.pdf` | — | `200 application/pdf` (WeasyPrint); `503` on missing GTK3 runtime; `404` before compile |
| GET  | `/api/draft/projects/{id}/export.docx` | — | `200 application/vnd.openxmlformats-officedocument.wordprocessingml.document`; `404` before compile |
| GET  | `/api/draft/projects/{id}/export.tex` | — | `200 application/x-tex` (article-class skeleton with `\\cite` keys); `404` before compile |

```bash
TOKEN=...  # CITETHREADS_AUTH_TOKEN

curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/draft/projects/my-project/research

curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/draft/projects/my-project/status | jq
```

The 5 phase endpoints return `503` if no LLM key is configured
(server-side only — the key never leaves the server). The `/status`,
`/draft.md`, and the three `/export.*` endpoints are LLM-free and
work without a key. The PDF endpoint additionally returns `503` when
WeasyPrint's native GTK3 dependencies are not installed (notably on
stock Windows); DOCX and LaTeX export work everywhere.

### 4. Use the frontend UI

Open the project in the React app, switch to the **Writing Assistant**
tab, then the **Long-Form Draft** sub-tab. The 4 buttons drive
research → structure → compose → compile in order; the progress bar
polls `/status` every 3 s. See
`frontend/src/components/DraftGenerator/DraftGenerator.tsx`.

### 5. Check the result

```python
# After compile, ctx.final_draft holds the full markdown body
# and ctx.quality_history has the QualityScore snapshots.

from app.services.draft_pipeline import PhaseName
print(ctx.final_draft)              # full markdown
print(ctx.quality_history[-1].total)  # 0-125 score
print(ctx.qa_report)                # Referee + FactCheck output
```

For HTTP, `GET /api/draft/projects/{id}/draft.md` returns the
compiled markdown body (`Content-Type: text/markdown`).

---

## Limitations

CTDP deliberately does **less** than opendraft in a few places —
features the original has that we don't, plus new ones we don't yet
have.

### Features opendraft has that we don't

- **Per-user auth + rate limit + cost guard.** opendraft has a
  multi-tenant story; we have a single shared bearer token. (P2-1.)
- **Multi-round refiner** (polish + voice + entropy passes). We have
  a single opt-in `refiner`; opendraft runs it 2-3× with different
  objectives. (P1-1.)
- **Per-section regenerate / cancel buttons** in the UI. (P1-2.)
- **Token-level streaming of LLM output.** The runner publishes
  phase-level progress via SSE, but the prose the LLM generates
  within a single phase is not streamed token-by-token. (out of
  scope: the composing Crafter writes a section at a time, so
  token-level streaming would be visible only inside one section.)

### Known CTDP-specific limitations

- **5 of 6 `PhaseName` values are dispatchable.** `EXPORT` is a
  placeholder. `progress_pct` divides by 5 (dispatchable count), so
  the bar reaches 100% at end-to-end — but the enum still carries 6
  members for forward compatibility.
- **No streaming in HTTP responses.** Each phase endpoint returns one
  final JSON; for a 5-phase run with 3 LLM calls per phase, the
  client sees one late response instead of incremental progress.
- **No automatic retry / circuit breaker.** A transient LLM error
  fails the phase. The runner marks it FAILED and persists; the next
  call retries from scratch.
- **Compose phase has fixed 6 IMRaD sections.** Custom section
  structures (e.g. a thesis appendix bundle) require editing
  `SECTION_NAMES` in `phases/compose.py`.
- **Bilingual prompts are translation pairs, not locale-aware
  variants.** The `language` field on `DraftContext` picks `en/` or
  `zh/` for the prompt directory, but a true locale-aware
  adaptation (e.g. citation style conventions differing between
  Chinese and English APA) is not implemented.
- **`paper_search_service.search` is hit on every Scout invocation.**
  No cache beyond what the crawlers themselves keep. For repeated
  runs on the same topic, the cost / latency profile is not great.
- **The CTDP `[@paper_id]` citation marker is not rendered.** The
  body has the markers; the references list has the full citations;
  a renderer that turns `[@paper_id]` into `(Author, Year)` style
  inline citations is planned but not shipped.

---

## Roadmap

The remaining work is captured in the task queue (`TaskList` in the
project tracking). Grouped by priority:

### P0 — Required for a real submission flow

- **P0-1: PDF / DOCX / LaTeX export** (✅ done). Three GET endpoints
  (`/export.pdf`, `/export.docx`, `/export.tex`) backed by WeasyPrint,
  python-docx, and an inline LaTeX article-class skeleton. PDF returns
  503 when GTK3 is missing; DOCX/LaTeX run everywhere.
- **P0-2: Checkpoint + Resume + SSE progress** (✅ done). Per-phase
  checkpoint files in `data/projects/{id}/checkpoints/{phase}.json`
  written after every phase transition (atomic, version-stamped).
  `DraftRunner.resume_from(phase)` consults the on-disk checkpoint
  first and skips the LLM call when one is found. New SSE endpoint
  `GET /api/draft/projects/{id}/stream` serves `text/event-stream`
  with `phase-start` / `phase-progress` / `phase-end` / `error` /
  `done` events, plus `: heartbeat` keepalives every 15s for
  long-running compose phases. 27 new tests in
  `tests/test_draft_checkpoint_resume.py` (13) and
  `tests/test_draft_sse.py` (14).

### P1 — Quality of life

- **P1-1: Multi-round refiner.** Polish + voice + entropy passes
  invoked at the end of compose.
- **P1-2: Per-section regenerate + cancel + error UX.** Buttons in
  the DraftGenerator component to rerun a single section or abort an
  in-flight phase; better error toasts.

### P2 — Operability

- **P2-1: Per-user auth + rate limit + cost guard.** Move from a
  shared bearer token to per-user keys; track per-user LLM spend.
- **P2-2: Logging + metrics + Dockerfile + CI.** Structured logs
  per phase, Prometheus metrics, container image, CI pipeline that
  runs the integration tests on every PR with a secret.

### P3 — Polish

- **P3-1: Citation Verifier + Abstract Generator + Table/Figure.**
  A standalone verifier that audits every `[@paper_id]` in the final
  draft, a separate abstract generator that writes the abstract from
  the body, and table/figure rendering from the results section.
- **P3-2 (✅ done): Real LLM integration tests + this README rewrite.**

---

## Why the package is shipped as a coherent unit

1. **`DraftContext` is the load-bearing type for every phase.** All
   5 phases read and write the same Pydantic model; tests cover the
   shape and the field ownership contract.
2. **The `QualityGate` 5-dim scoring is shared by Validate and
   Compile.** A single quality number drives both the QA report and
   the final quality snapshot appended to `quality_history`.
3. **`DraftRunner` makes the HTTP layer trivial.** The router
   resolves an LLM client, hands it to the runner, and returns the
   status snapshot. Persistence is the runner's job, not the
   router's.
4. **The integration test catches prompt regressions before they
   ship.** Mocked tests prove the code paths; the real-LLM test
   proves the model still cooperates.

---

Last updated: 2026-06-05 (P2-2 session: Logging + metrics + Dockerfile + CI — `app/logging_config.py` (JSON formatter + `RequestIdMiddleware`), `app/metrics.py` (in-memory store + `phase_timer` + Prometheus text), `app/health.py` (combined `/health` + `/health/live` + `/health/ready` with `llm_configured` / `data_writable` / `pipeline_loaded` checks), `backend/Dockerfile` + `backend/docker-compose.yml`, `frontend/Dockerfile` (multi-stage) + `frontend/nginx.conf`, project-root `docker-compose.yml`, `.github/workflows/ci.yml` (backend + frontend parallel jobs) + `.github/workflows/docker.yml` (GHCR publish on tag), wired into `main.py`; 35 new tests, 478 pass / 4 skip / 1 pre-existing SSE-bus test failure unrelated to this work).

P1-2 session: per-section regenerate + cancel + friendly error UX —
`backend/app/routers/draft.py` (new `POST /sections/{name}/regenerate`
and `POST /cancel` endpoints, both behind `BearerAuthDep`),
`backend/app/services/draft_pipeline/runner.py` (`run_phase` checks
`ctx.cancellation_requested` first; new `request_cancellation` /
`clear_cancellation` methods; `run_all` short-circuits at the next
phase boundary when the flag is set),
`backend/app/services/draft_pipeline/phases/compose.py` (crafter
dispatcher + 6 crafter_* writers accept `custom_instructions`,
appended as an `ADDITIONAL REWRITE GUIDANCE` block to the prompt;
`_format_custom_instructions` collapses empty / whitespace input),
`backend/app/services/draft_pipeline/phases/{research,structure,validate,compile}.py`
(check `ctx.cancellation_requested` between sub-operations),
`frontend/src/services/draftApi.ts` (new `regenerateSection` /
`cancelDraft` API methods + types),
`frontend/src/components/DraftGenerator/DraftGenerator.tsx` (cancel
button visible during running phases; per-section regen modal with
custom-instructions textarea and side-by-side diff toggle; new
`errorKey` mapping to friendly zh/en messages for auth/llm-key/
5xx/network/garbage),
`frontend/src/components/DraftGenerator/DraftGenerator.css` (styles
for cancel button + regen modal diff grid),
`frontend/src/locales/{en-US,zh-CN}.json` (new i18n keys under
`draftGenerator.buttons.cancel*`, `draftGenerator.regen.*`,
`draftGenerator.error.{authRequiredFriendly,noLLMKeyFriendly,
llmServerError,networkError,garbageOutput,sessionExpired}` and
`draftGenerator.status.cancelRequested/cancelled`),
`backend/tests/test_draft_per_section.py` (13 new tests:
regenerate happy path / 400 / 404 / 503 / custom-instructions
honoured / empty custom-instructions skip the prompt block;
cancel sets / idempotent / persists; runner marks phase SKIPPED
when flag is set; runner clears the flag on next explicit run;
in-flight cancellation in compose; direct
`request_cancellation` API round-trip),
`frontend/src/components/DraftGenerator/DraftGenerator.test.tsx`
(5 new tests: cancel button visibility + idle/during states,
cancel button click calls API, per-section regen modal+submit,
friendly 401 message, friendly 5xx message).
Test count: 246 pass / 4 skip / 1 pre-existing per-user-auth test
failure (unrelated to this work).
