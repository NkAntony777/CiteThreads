# CiteThreads - Project Guide

学术引用脉络可视化与智能写作助手 (CTDP — CiteThreads Draft Pipeline 在 `backend/app/services/draft_pipeline/`)。

Key rules
- Keep the repo in a working state. Prefer small, verifiable changes.
- Do not auto-commit by default. Any git commits are a separate, explicit decision.
- Follow the project constraints in `AGENTS.md`.

Repo quick commands

Backend (FastAPI)
- Create venv: `cd backend; python -m venv .venv`
- Install: `cd backend; .\.venv\Scripts\python -m pip install -r requirements.txt`
- Dev server: `cd backend; .\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- Tests: `cd backend; .\.venv\Scripts\python -m pytest`

Frontend (React/Vite/TS)
- Install: `cd frontend; npm install`
- Dev server: `cd frontend; npm run dev` (http://localhost:5173)
- Typecheck: `cd frontend; npx tsc --noEmit`
- Lint: `cd frontend; npm run lint`
- Build: `cd frontend; npm run build`
- Tests: `cd frontend; npm run test:run`

Where things live
- `backend/app/services/` — service layer (crawlers, LLM factory, graph builder, draft pipeline)
- `backend/app/services/draft_pipeline/` — CTDP: DraftContext + QualityGate + research phase + 6 prompts
- `backend/app/routers/` — FastAPI routers
- `backend/app/agent_runtime/` — OpenAI-compatible tool-calling agent
- `backend/app/crawlers/` — OpenAlex / Semantic Scholar / arXiv / DBLP / PubMed / Crossref
- `backend/tests/` — pytest
- `data/claude-progress.txt` — running session log (append-only)
- `data/opendraft-integration-feasibility.md` — survey + integration roadmap
- `docs/assets/` — README screenshots
- `scripts/init.ps1` — first-time dev environment setup

Working style
- Self-directed: pick the next meaningful piece of work and ship it.
- When the codebase is in a working state, prefer to extend it over re-architecting it.
- Tests alongside code: every phase / module ships with unit tests in the same PR.
- Bilingual (en/zh) UI strings and LLM prompts.
- Don't introduce heavy new dependencies without justification.
