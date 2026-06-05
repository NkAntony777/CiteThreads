"""
Tests for SmartSearch filter passthrough.

These tests focus on the unified paper search service layer: that
``SearchFilters`` is well-formed, that ``_compute_filters_applied``
correctly reports which sources honor which constraints today, and
that ``search()`` with a ``filters`` arg forwards them to
``_search_source`` (which logs "does not honor" for sources that
aren't wired up yet).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, List

import pytest

# Ensure backend/ is importable when pytest is run from repo root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.paper_search_service import (  # noqa: E402
    SearchFilters,
    SearchResult,
    SearchSource,
    UnifiedPaperSearchService,
    paper_search_service,
)


# ---------------------------------------------------------------------------
# SearchFilters shape
# ---------------------------------------------------------------------------


def test_search_filters_default_construction():
    """A bare ``SearchFilters()`` should be safe to pass anywhere
    that expects filters; every field is None or empty."""
    f = SearchFilters()
    assert f.year_range is None
    assert f.conferences is None
    assert f.keywords_all is None
    assert f.fields is None
    assert f.venues is None
    assert f.min_citations is None
    assert f.sort is None


def test_search_filters_full_construction():
    """All filter fields should round-trip through the dataclass."""
    f = SearchFilters(
        year_range=(2020, 2024),
        conferences=["NeurIPS", "ICML"],
        keywords_all=["graph", "neural", "network"],
        fields=["cs.LG"],
        venues=["CVPR"],
        min_citations=10,
        sort="citations",
    )
    assert f.year_range == (2020, 2024)
    assert f.conferences == ["NeurIPS", "ICML"]
    assert f.keywords_all == ["graph", "neural", "network"]
    assert f.fields == ["cs.LG"]
    assert f.venues == ["CVPR"]
    assert f.min_citations == 10
    assert f.sort == "citations"
    summary = f.to_log_summary()
    assert summary["year_range"] == (2020, 2024)
    assert summary["min_citations"] == 10


# ---------------------------------------------------------------------------
# _compute_filters_applied
# ---------------------------------------------------------------------------


def test_compute_filters_applied_none_filters():
    """No filters passed -> DBLP shows False (nothing to honor), all
    other sources False too."""
    out = UnifiedPaperSearchService._compute_filters_applied(None)
    assert out == {
        "openalex": False,
        "arxiv": False,
        "dblp": False,
        "pubmed": False,
        "semantic_scholar": False,
    }


def test_compute_filters_applied_only_year_range():
    """year_range is one of the fields DBLP honors, so DBLP becomes
    True; the other sources stay False."""
    f = SearchFilters(year_range=(2022, 2024))
    out = UnifiedPaperSearchService._compute_filters_applied(f)
    assert out["dblp"] is True
    assert out["openalex"] is False
    assert out["arxiv"] is False
    assert out["pubmed"] is False
    assert out["semantic_scholar"] is False


def test_compute_filters_applied_only_venues():
    """``venues`` maps to DBLP conferences and should make DBLP True."""
    f = SearchFilters(venues=["SIGGRAPH"])
    out = UnifiedPaperSearchService._compute_filters_applied(f)
    assert out["dblp"] is True


def test_compute_filters_applied_only_min_citations():
    """min_citations is not a DBLP-honored field today; it stays False
    everywhere until a crawler grows support for it."""
    f = SearchFilters(min_citations=5)
    out = UnifiedPaperSearchService._compute_filters_applied(f)
    assert all(v is False for v in out.values())


def test_compute_filters_applied_only_fields():
    """``fields`` is advisory only and not honored by any source today."""
    f = SearchFilters(fields=["cs.LG"])
    out = UnifiedPaperSearchService._compute_filters_applied(f)
    assert all(v is False for v in out.values())


# ---------------------------------------------------------------------------
# search() integration: filters propagate, "does not honor" is logged
# ---------------------------------------------------------------------------


class _StubCrawler:
    """Tiny stand-in for a crawler module. Records the last query
    it was called with so tests can verify passthrough behavior."""

    def __init__(self) -> None:
        self.calls: List[dict] = []

    async def search_papers(self, query, limit=5):  # noqa: ARG002
        from app.models import Paper

        self.calls.append({"query": query, "limit": limit})
        return []


@pytest.mark.asyncio
async def test_search_forwards_filters_to_search_source(monkeypatch):
    """When ``search()`` is called with ``filters``, it must call
    ``_search_source`` with those filters attached so the per-source
    dispatch can decide what to honor."""
    svc = UnifiedPaperSearchService()
    captured: List[dict] = []

    async def fake_source(self, source, query, filters, limit):  # noqa: ARG001
        captured.append({"source": source, "filters": filters, "limit": limit})
        return []

    monkeypatch.setattr(
        UnifiedPaperSearchService, "_search_source", fake_source
    )

    f = SearchFilters(year_range=(2022, 2024), venues=["NeurIPS"])
    await svc.search(
        query="graph neural network",
        sources=["openalex", "dblp"],
        filters=f,
        limit=7,
    )

    assert len(captured) == 2
    by_source = {c["source"]: c for c in captured}
    assert by_source[SearchSource.OPENALEX]["filters"] is f
    assert by_source[SearchSource.DBLP]["filters"] is f
    assert by_source[SearchSource.OPENALEX]["limit"] == 7
    assert by_source[SearchSource.DBLP]["limit"] == 7


@pytest.mark.asyncio
async def test_search_sets_filters_applied_on_result(monkeypatch):
    """The SearchResult returned by ``search()`` should carry a
    ``filters_applied`` map that the tool layer forwards to the
    frontend."""
    svc = UnifiedPaperSearchService()

    async def fake_source(self, source, query, filters, limit):  # noqa: ARG001
        return []

    monkeypatch.setattr(
        UnifiedPaperSearchService, "_search_source", fake_source
    )

    f = SearchFilters(year_range=(2020, 2024))
    result = await svc.search(
        query="anything",
        sources=["openalex", "dblp"],
        filters=f,
    )
    assert isinstance(result, SearchResult)
    assert result.filters_applied["dblp"] is True
    assert result.filters_applied["openalex"] is False
    # Even with no filters at all, the field is present (not absent).
    empty_result = await svc.search(query="", sources=["openalex"])
    assert "openalex" in empty_result.filters_applied


@pytest.mark.asyncio
async def test_search_logs_unhonored_filters_for_openalex(caplog, monkeypatch):
    """When OpenAlex receives filters it can't honor, the service
    must log a single info line that names the filter fields. This
    is the audit trail the WS-BE brief calls out — we don't silently
    swallow structured intent."""
    from app.services.paper_search_service import openalex as openalex_mod

    async def fake_search_papers(query, limit=5):  # noqa: ARG001
        return []

    monkeypatch.setattr(openalex_mod, "search_papers", fake_search_papers)

    svc = UnifiedPaperSearchService()
    f = SearchFilters(
        year_range=(2022, 2024),
        min_citations=10,
        fields=["cs.LG"],
    )

    with caplog.at_level(logging.INFO, logger="app.services.paper_search_service"):
        await svc._search_source(
            source=SearchSource.OPENALEX,
            query="x",
            filters=f,
            limit=5,
        )

    matches = [r for r in caplog.records if "does not honor them yet" in r.message]
    assert matches, f"expected a 'does not honor' log, got: {[r.message for r in caplog.records]}"
    msg = matches[0].message
    assert "openalex" in msg
    assert "year_range" in msg
    assert "min_citations" in msg


@pytest.mark.asyncio
async def test_search_does_not_log_when_no_filters(caplog, monkeypatch):
    """If no filters were passed, we shouldn't be noisy about honors."""
    from app.services.paper_search_service import openalex as openalex_mod

    async def fake_search_papers(query, limit=5):  # noqa: ARG001
        return []

    monkeypatch.setattr(openalex_mod, "search_papers", fake_search_papers)

    svc = UnifiedPaperSearchService()
    with caplog.at_level(logging.INFO, logger="app.services.paper_search_service"):
        await svc._search_source(
            source=SearchSource.OPENALEX,
            query="x",
            filters=None,
            limit=5,
        )
    matches = [r for r in caplog.records if "does not honor them yet" in r.message]
    assert matches == []


@pytest.mark.asyncio
async def test_search_does_not_log_for_dblp(caplog, monkeypatch):
    """DBLP does honor year_range and conferences; we should not
    log a 'does not honor' line for it."""
    from app.services.paper_search_service import dblp_crawler as dblp_mod

    async def fake_search_papers(
        keywords=None,  # noqa: ARG001
        keywords_all=None,
        conferences=None,
        year_range=None,
        limit=5,
    ):
        return []

    monkeypatch.setattr(dblp_mod, "search_papers", fake_search_papers)

    svc = UnifiedPaperSearchService()
    f = SearchFilters(year_range=(2020, 2024), conferences=["NeurIPS"])
    with caplog.at_level(logging.INFO, logger="app.services.paper_search_service"):
        await svc._search_source(
            source=SearchSource.DBLP,
            query="graph neural network",
            filters=f,
            limit=5,
        )
    matches = [r for r in caplog.records if "does not honor them yet" in r.message]
    assert matches == []


# ---------------------------------------------------------------------------
# search_papers tool layer translates the agent's filters dict correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_papers_tool_handler_passes_filters(monkeypatch):
    """The tool handler should construct a SearchFilters and call the
    service. We patch the service to a stub so this runs offline."""
    from app.agent_runtime import tools as tools_mod
    import sys as _sys
    pss_module = _sys.modules["app.services.paper_search_service"]
    pss_singleton = pss_module.paper_search_service

    captured: List[dict] = []

    async def fake_search(query, sources=None, filters=None, limit=20):  # noqa: ARG001
        captured.append(
            {
                "query": query,
                "sources": sources,
                "filters": filters,
                "limit": limit,
            }
        )
        return SearchResult(
            papers=[],
            total=0,
            sources_searched=list(sources or []),
            errors={},
            filters_applied=UnifiedPaperSearchService._compute_filters_applied(filters),
        )

    monkeypatch.setattr(pss_singleton, "search", fake_search)

    out = await tools_mod._search_papers_handler(
        query="graph neural network",
        sources=["openalex", "dblp"],
        limit=4,
        filters={
            "year_range": [2022, 2024],
            "min_citations": 5,
            "venues": ["NeurIPS"],
            "fields": ["cs.LG"],
            "sort": "citations",
        },
    )

    assert captured, "service.search should have been called"
    call = captured[0]
    assert call["query"] == "graph neural network"
    assert call["sources"] == ["openalex", "dblp"]
    assert call["limit"] == 4
    assert isinstance(call["filters"], SearchFilters)
    assert call["filters"].year_range == (2022, 2024)
    assert call["filters"].min_citations == 5
    assert call["filters"].venues == ["NeurIPS"]
    assert call["filters"].fields == ["cs.LG"]
    assert call["filters"].sort == "citations"

    # The response payload must include filters_applied.
    assert "filters_applied" in out
    assert out["filters_applied"]["dblp"] is True
    assert out["filters_applied"]["openalex"] is False


@pytest.mark.asyncio
async def test_search_papers_tool_handler_tolerates_bad_filters(monkeypatch):
    """If the LLM hands us a malformed ``filters`` dict, the handler
    should drop the bad parts and still complete the search rather
    than raising. The whole point of the helper is to be forgiving."""
    from app.agent_runtime import tools as tools_mod
    import sys as _sys
    pss_module = _sys.modules["app.services.paper_search_service"]
    pss_singleton = pss_module.paper_search_service

    captured: List[dict] = []

    async def fake_search(query, sources=None, filters=None, limit=20):  # noqa: ARG001
        captured.append({"filters": filters})
        return SearchResult(
            papers=[],
            total=0,
            sources_searched=[],
            errors={},
            filters_applied=UnifiedPaperSearchService._compute_filters_applied(filters),
        )

    monkeypatch.setattr(pss_singleton, "search", fake_search)

    # Mix of valid + garbage. The handler should:
    # - drop ``min_citations`` because it can't be coerced to int
    # - drop ``year_range`` because it has the wrong arity
    # - drop ``sort`` because it's not in the enum
    # - keep ``venues`` because it's a list
    await tools_mod._search_papers_handler(
        query="x",
        filters={
            "year_range": [2022],
            "min_citations": "not a number",
            "venues": ["CVPR"],
            "sort": "alphabetical",
        },
    )

    assert captured
    f = captured[0]["filters"]
    assert f is not None
    assert f.year_range is None
    assert f.min_citations is None
    assert f.venues == ["CVPR"]
    assert f.sort is None


@pytest.mark.asyncio
async def test_search_papers_tool_handler_empty_query(monkeypatch):
    """Empty query short-circuits to an empty result with a
    filters_applied map but does NOT call the service."""
    from app.agent_runtime import tools as tools_mod
    import sys as _sys
    pss_module = _sys.modules["app.services.paper_search_service"]
    pss_singleton = pss_module.paper_search_service

    called = {"count": 0}

    async def fake_search(*args, **kwargs):  # noqa: ARG001
        called["count"] += 1
        return SearchResult()

    monkeypatch.setattr(pss_singleton, "search", fake_search)

    out = await tools_mod._search_papers_handler(query="   ")
    assert out["papers"] == []
    assert out["total"] == 0
    assert "filters_applied" in out
    assert called["count"] == 0
