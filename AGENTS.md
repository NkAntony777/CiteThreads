# AGENTS.md - CiteThreads Project Guide

> This document provides essential context for AI coding agents working in this repository.

## Project Overview

**CiteThreads (引脉)** is a full-stack academic citation graph visualization and AI writing assistant. It crawls academic databases (OpenAlex, Semantic Scholar, arXiv, DBLP, PubMed) to build citation networks and provides an AI-powered writing environment.

- **Frontend**: React 18 + Vite + TypeScript + Ant Design 5
- **Backend**: FastAPI (Python 3.10+) + Pydantic + httpx
- **Languages**: Bilingual (Chinese/English) via i18next

---

## Build, Lint, and Test Commands

### Frontend (`frontend/`)

```bash
cd frontend

# Install dependencies
npm install

# Development server (runs on http://localhost:5173)
npm run dev

# Type checking
npx tsc --noEmit

# Build for production
npm run build

# Lint (ESLint)
npm run lint

# Preview production build
npm run preview
```

### Backend (`backend/`)

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Development server (runs on http://localhost:8000)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# API docs available at http://localhost:8000/docs
```

### Running Tests

#### Backend Tests
```bash
cd backend

# Run all tests
pytest

# Run specific test file
pytest tests/test_main.py

# Run with coverage
pytest --cov=app tests/

# Run specific test
pytest tests/test_crawlers.py::TestArxivCrawler::test_extract_arxiv_id_from_url -v
```

#### Frontend Tests
```bash
cd frontend

# Run tests in watch mode
npm run test

# Run tests once
npm run test:run

# Run with coverage
npm run test:coverage
```

---

## Project Structure

```
CiteThreads/
├── frontend/
│   ├── src/
│   │   ├── components/     # React components (each in own folder with index.ts)
│   │   ├── services/       # API client modules
│   │   ├── stores/         # Zustand state stores
│   │   ├── types/          # TypeScript type definitions
│   │   ├── utils/          # Utility functions
│   │   ├── locales/        # i18n translation files
│   │   ├── App.tsx         # Main app component
│   │   ├── main.tsx        # Entry point
│   │   └── i18n.ts         # i18n configuration
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── backend/
│   ├── app/
│   │   ├── crawlers/       # API clients for academic databases
│   │   ├── models/         # Pydantic models and schemas
│   │   ├── routers/        # FastAPI route handlers
│   │   ├── services/       # Business logic services
│   │   ├── config.py       # Settings via pydantic-settings
│   │   └── main.py         # FastAPI app entry point
│   ├── data/               # Local data storage
│   └── requirements.txt
├── docs/                   # Documentation and assets
├── .env.example            # Environment variables template
└── README.md
```

---

## Code Style Guidelines

### TypeScript/React (Frontend)

**Imports**: Group imports in this order, separated by blank lines:
1. React and React-related imports
2. Third-party libraries (antd, d3, axios, etc.)
3. Internal components (from `./components`)
4. Stores, services, types
5. CSS files

```typescript
// Example
import React, { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Drawer, Space } from 'antd';
import { CloseOutlined, DeleteOutlined } from '@ant-design/icons';

import { useGraphStore } from '../../stores/graphStore';
import { projectApi } from '../../services/api';
import type { Paper, CitationEdge } from '../../types';
import './ComponentName.css';
```

**Components**: Use functional components with explicit `React.FC` type annotation:

```typescript
export const ComponentName: React.FC = () => {
    // ...
};
```

**Types**: Prefer `interface` for object shapes, `type` for unions/aliases:

```typescript
export interface Paper {
    id: string;
    title: string;
    // ...
}

export type CitationIntent = 'SUPPORT' | 'OPPOSE' | 'NEUTRAL' | 'UNKNOWN';
```

**State Management**: Use Zustand stores from `src/stores/`. Access via hooks:

```typescript
const { currentProject, loadProject } = useGraphStore();
```

**API Calls**: Use service modules from `src/services/`:

```typescript
const papers = await paperApi.search(query, 'auto', 10);
```

**i18n**: All user-visible text must use translation keys:

```typescript
const { t } = useTranslation();
// In JSX: {t('component.someKey')}
```

**Naming Conventions**:
- Components: PascalCase (`NodePanel.tsx`)
- Utilities: camelCase (`graphFilters.ts`)
- CSS files: Match component name (`NodePanel.css`)
- Export index files: `index.ts` in each component folder

### Python (Backend)

**Docstrings**: All modules, classes, and public functions require docstrings:

```python
"""
Module description here.
"""
from typing import List, Optional


class ServiceName:
    """Brief class description."""
    
    async def get_paper(self, paper_id: str) -> Optional[Paper]:
        """
        Get paper by ID.
        
        Args:
            paper_id: The paper identifier (DOI, arXiv ID, etc.)
            
        Returns:
            Paper object if found, None otherwise
        """
```

**Imports**: Group imports in this order:
1. Standard library
2. Third-party libraries
3. Local imports (use relative imports within `app/`)

```python
import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models import Paper
from ..config import settings
```

**Type Hints**: Always include type hints for function parameters and return types:

```python
async def search_papers(query: str, limit: int = 10) -> List[Paper]:
```

**Pydantic Models**: Define request/response models in `models/schemas.py` or within routers for simple cases:

```python
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query")
    limit: int = Field(default=10, ge=1, le=50)
```

**Error Handling**: Use HTTPException for API errors, always include logging:

```python
logger = logging.getLogger(__name__)

try:
    # operation
except Exception as e:
    logger.error(f"Operation failed: {e}")
    raise HTTPException(status_code=500, detail=str(e))
```

**Singleton Services**: Use module-level singleton instances:

```python
class PaperSearchService:
    pass

# Singleton at module level
paper_search_service = PaperSearchService()
```

**Naming Conventions**:
- Modules: snake_case (`paper_search_service.py`)
- Classes: PascalCase (`PaperSearchService`)
- Functions/variables: snake_case (`search_papers`)
- Constants: UPPER_SNAKE_CASE (`ARXIV_API_URL`)

---

## Architecture Notes

### API Proxy
Frontend proxies `/api/*` requests to `http://localhost:8000` via Vite config.

### State Management
Zustand stores in `src/stores/`:
- `graphStore.ts`: Main state for project data, graph nodes/edges, clustering
- `uiStore.ts`: UI state (selected nodes/edges, panel visibility)
- `filterStore.ts`: Filter state (year range, intent filter, cluster filter)

Components access stores via hooks:
```typescript
// Main store (backward compatible)
const { currentProject, loadProject } = useGraphStore();

// Specific stores (recommended for new code)
const { selectedNode, setSelectedNode } = useUIStore();
const { yearRange, setYearRange } = useFilterStore();
```

### Crawler Pattern
Each crawler in `backend/app/crawlers/`:
- Is an async class with `search_papers()` and `get_paper_by_id()` methods
- Handles rate limiting internally
- Returns standardized `Paper` model instances

### Router Organization
Routers in `backend/app/routers/` are organized by domain:
- `papers.py`: Paper search and retrieval
- `projects.py`: Project/graph management
- `writing.py`: AI writing assistant endpoints
- `ai.py`: AI configuration and analysis
- `agent.py`: Agent runtime (tool-using chat)
- `draft.py`: CTDP draft pipeline (7 endpoints; see *Draft Pipeline* below)

### Draft Pipeline (CTDP)

CTDP is a 5-phase, end-to-end paper-drafting pipeline that takes a
project (a curated set of papers + a topic) and produces a
citation-grounded Markdown draft. It is a faithful port of opendraft's
phase design rebound to our async stack (no Gemini dependency).

**Where the code lives**

```
backend/app/services/draft_pipeline/
├── __init__.py        # public surface: DraftContext, QualityGate, DraftRunner, …
├── context.py         # DraftContext (Pydantic v2) + PhaseName / PhaseStatus / CitationStyle enums
├── quality_gate.py    # 5-dim QualityScore (word_count / citation_density / completeness / structure / graph_health)
├── prompts/
│   ├── __init__.py    # load_prompt(name, lang) with en/zh fallback
│   ├── en/            # 10 bilingual .md templates (scout, scribe, signal, architect, …)
│   └── zh/            # mirror of en/
├── phases/
│   ├── research.py    # scout + scribe + signal + run_research_phase
│   ├── structure.py   # architect + formatter + run_structure_phase
│   ├── compose.py     # crafter (6 IMRaD sections) + refiner + run_compose_phase
│   ├── validate.py    # referee + factcheck + run_validate_phase
│   ├── compile.py     # compiler + abstract_writer + run_compile_phase
│   └── tests/         # 100+ unit tests (one file per phase family)
├── runner.py          # DraftRunner: load/persist DraftContext, dispatch phases
├── STATUS.md          # full status, limitations, roadmap
└── tests/             # context + quality_gate tests

backend/app/routers/draft.py        # 7 HTTP endpoints (see Quick Start in STATUS.md)
backend/tests/test_draft_router.py  # 18 router + runner tests
backend/tests/integration/test_real_llm.py  # real LLM smoke tests (opt-in)
```

**How to add a new phase**

1. Pick the next free `PhaseName` enum value (currently `EXPORT` is
   reserved for the future export phase; do not reuse it).
2. Create `phases/<your_phase>.py` with three things:
   - One or more sub-phase async functions that take `(ctx, llm_client)`
     and mutate `ctx` in place.
   - An orchestrator `run_<your_phase>_phase(ctx, llm_client=None)`
     that calls `ctx.mark_phase(PhaseName.<YOUR>, PhaseStatus.RUNNING)`
     at the start, runs the sub-phases, and marks `SUCCEEDED` /
     `FAILED` at the end.
3. Register the orchestrator in `phases/__init__.py`'s re-exports and
   add a `PhaseName.<YOUR>: run_<your_phase>_phase` entry to
   `runner._PHASE_DISPATCH`.
4. Add the prompt template(s) to both `prompts/en/<role>.md` and
   `prompts/zh/<role>.md`. The bilingual loader falls back to `en/`
   if a `zh/` translation is missing, so partial translations are OK
   for initial land.
5. Add unit tests in `phases/tests/test_<your_phase>.py`. Use the
   `_MockLLMClient` / `_MockCompletions` pattern from
   `phases/tests/test_research.py` (scripted JSON responses). Aim for
   one test per sub-phase plus an end-to-end orchestrator test.
6. Update `STATUS.md` (mark the task done, add a row to the
   "production-ready" table).

**How to add a new prompt**

1. Create the file at `prompts/<lang>/<name>.md`. Names are
   lowercase-snake (e.g. `scout.md`, `crafter_introduction.md`).
2. Call `load_prompt("<name>", lang="<lang>")` from the phase code.
   The loader raises `FileNotFoundError` if the prompt is missing
   in both `lang/` and `en/` — that's intentional; tests assert it.
3. The prompt is just a markdown body; placeholders for runtime
   context go in the user message, not the system prompt.

**How to add a new quality dimension**

The `QualityGate` (`quality_gate.py`) scores a draft on 5 axes:
`word_count`, `citation_density`, `completeness`, `structure`,
`graph_health`. To add a 6th:

1. Add a new method `_<your_axis>(self, ctx) -> tuple[int, list[str]]`
   that returns `(score, [issue_messages])`. The score should be
   normalized to 0-25 (5 axes × 25 = 125 total).
2. Add the method call to `QualityGate.score()`.
3. Add a field to `QualityScore` (e.g. `your_axis: int = 0` and
   `your_axis_issues: list[str] = field(default_factory=list)`).
4. Update `to_dict()` and the test in
   `tests/test_quality_gate.py` to assert the new axis.

**Test pattern**

CTDP tests follow a strict two-tier pattern:

- **Unit tests** (default CI) — all LLM calls mocked via
  `_MockLLMClient` / `_MockCompletions`. These tests are fast
  (~1.85s for the full research suite) and deterministic. They live
  in `app/services/draft_pipeline/phases/tests/` and run on every
  `pytest` invocation via the `testpaths` entries in `pytest.ini`.
- **Integration tests** (opt-in) — real LLM calls against
  SiliconFlow / DeepSeek. Skipped automatically when
  `INTEGRATION_LLM_KEY` is not set (CI without secrets). Run
  locally with::

      export INTEGRATION_LLM_KEY=sk-...
      cd backend
      pytest -m integration -v

  The point isn't exhaustive coverage; it's "if you change a prompt,
  run a real model and confirm it still works". The canonical
  example is `backend/tests/integration/test_real_llm.py`.

**Status and roadmap**

See `backend/app/services/draft_pipeline/STATUS.md` for the full
status (what's production-ready, limitations, roadmap) and
`docs/architecture.md` for the architecture diagram.

---

## Environment Setup

Copy `.env.example` to `backend/.env`:

```bash
cp .env.example backend/.env
```

Required for AI features:
- `SILICONFLOW_API_KEY` or any OpenAI-compatible API key

---

## Important Constraints

1. **Never suppress type errors** with `as any`, `@ts-ignore`, or `@ts-expect-error`
2. **Never commit** `.env` files or files in `backend/data/`
3. **All user-facing strings** must go through i18n
4. **Async operations** in Python must use `async/await` properly
5. **Rate limiting**: External API crawlers must respect rate limits
6. **Bilingual support**: Add translations to both `locales/en-US.json` and `locales/zh-CN.json`
