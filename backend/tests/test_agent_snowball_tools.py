"""
Tests for the snowball-style agent tools:
  - get_citing_papers
  - get_referenced_papers
  - search_by_author

These three are the agent's escape hatch when ``search_papers`` returns
empty: backward snowball (who cites X), forward snowball (what X
cites), and author-based lookup. We mock the underlying crawlers so
the tests don't need network access.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent_runtime import tools as tools_mod  # noqa: E402
from app.crawlers import openalex, semantic_scholar, arxiv  # noqa: E402
from app.models import Paper  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mk_paper(title: str, year: int, **kwargs) -> Paper:
    return Paper(
        id=kwargs.pop("id", f"S2:test-{title.replace(' ', '-').lower()}"),
        title=title,
        authors=kwargs.pop("authors", ["Alice", "Bob"]),
        year=year,
        doi=kwargs.pop("doi", None),
        arxiv_id=kwargs.pop("arxiv_id", None),
        venue=kwargs.pop("venue", "NeurIPS"),
        citation_count=kwargs.pop("citation_count", 10),
        reference_count=kwargs.pop("reference_count", 0),
        fields=kwargs.pop("fields", []),
        **kwargs,
    )


CITING_PAPERS = [
    _mk_paper("Follow-up 1", 2024, doi="10.1/follow-up-1", citation_count=120),
    _mk_paper("Follow-up 2", 2023, doi="10.1/follow-up-2", citation_count=80),
]
REFERENCED_PAPERS = [
    _mk_paper("Foundation A", 2010, doi="10.1/foundation-a", citation_count=5000),
    _mk_paper("Foundation B", 2008, doi="10.1/foundation-b", citation_count=3000),
]
AUTHOR_PAPERS = [
    _mk_paper("Hinton 2024", 2024, citation_count=200),
    _mk_paper("Hinton 2022", 2022, citation_count=400),
]


# ---------------------------------------------------------------------------
# get_citing_papers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_citing_papers_uses_openalex_first():
    handler = tools_mod.tool_registry.get("get_citing_papers").handler
    with patch.object(
        openalex, "get_citations", new=AsyncMock(return_value=CITING_PAPERS)
    ) as mock_oa, patch.object(
        semantic_scholar, "get_citations", new=AsyncMock()
    ) as mock_s2:
        result = await handler("DOI:10.1/anchor", limit=5)

    assert result["total"] == 2
    assert result["direction"] == "citing"
    assert result["anchor_paper_id"] == "DOI:10.1/anchor"
    assert result["sources_searched"] == ["openalex"]
    assert {p["title"] for p in result["papers"]} == {"Follow-up 1", "Follow-up 2"}
    mock_oa.assert_awaited_once_with("DOI:10.1/anchor", limit=5)
    # S2 must NOT have been called when OpenAlex returned results.
    mock_s2.assert_not_called()


@pytest.mark.asyncio
async def test_get_citing_papers_falls_back_to_semantic_scholar():
    handler = tools_mod.tool_registry.get("get_citing_papers").handler
    with patch.object(
        openalex,
        "get_citations",
        new=AsyncMock(return_value=[]),  # OpenAlex returns nothing
    ), patch.object(
        semantic_scholar,
        "get_citations",
        new=AsyncMock(return_value=CITING_PAPERS),
    ) as mock_s2:
        result = await handler("arXiv:2106.09685", limit=3)

    assert result["total"] == 2
    assert result["sources_searched"] == ["openalex", "semantic_scholar"]
    mock_s2.assert_awaited_once_with("arXiv:2106.09685", limit=3)


@pytest.mark.asyncio
async def test_get_citing_papers_requires_paper_id():
    handler = tools_mod.tool_registry.get("get_citing_papers").handler
    result = await handler("", limit=3)
    assert "error" in result
    assert result["papers"] == []


@pytest.mark.asyncio
async def test_get_citing_papers_caps_limit_at_30():
    """Limit is clamped to [1, 30] regardless of what the LLM asks for."""
    handler = tools_mod.tool_registry.get("get_citing_papers").handler
    with patch.object(
        openalex, "get_citations", new=AsyncMock(return_value=[])
    ) as mock_oa:
        await handler("DOI:10.1/anchor", limit=999)
        # Forwarded to the crawler at the clamped value.
        called_limit = mock_oa.await_args.kwargs["limit"]
        assert called_limit == 30


# ---------------------------------------------------------------------------
# get_referenced_papers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_referenced_papers_uses_openalex_first():
    handler = tools_mod.tool_registry.get("get_referenced_papers").handler
    with patch.object(
        openalex, "get_references", new=AsyncMock(return_value=REFERENCED_PAPERS)
    ):
        result = await handler("DOI:10.1/anchor", limit=5)

    assert result["total"] == 2
    assert result["direction"] == "referenced"
    assert {p["title"] for p in result["papers"]} == {"Foundation A", "Foundation B"}


@pytest.mark.asyncio
async def test_get_referenced_papers_falls_back_to_semantic_scholar():
    handler = tools_mod.tool_registry.get("get_referenced_papers").handler
    with patch.object(
        openalex, "get_references", new=AsyncMock(return_value=[])
    ), patch.object(
        semantic_scholar, "get_references", new=AsyncMock(return_value=REFERENCED_PAPERS)
    ) as mock_s2:
        result = await handler("DOI:10.1/anchor", limit=2)

    assert result["total"] == 2
    assert "semantic_scholar" in result["sources_searched"]
    mock_s2.assert_awaited_once()


# ---------------------------------------------------------------------------
# search_by_author
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_author_uses_openalex_author_search_filter():
    handler = tools_mod.tool_registry.get("search_by_author").handler

    fake_response = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "ids": {"doi": "https://doi.org/10.1/a", "arxiv": None},
                "title": "Hinton 2024",
                "publication_year": 2024,
                "authorships": [{"author": {"display_name": "Geoffrey Hinton"}}],
                "cited_by_count": 200,
                "referenced_works_count": 30,
                "primary_location": {"source": {"display_name": "NeurIPS"}},
                "abstract_inverted_index": None,
                "concepts": [],
            }
        ]
    }

    with patch.object(
        openalex, "_request", new=AsyncMock(return_value=fake_response)
    ) as mock_req, patch.object(
        openalex, "_parse_paper", side_effect=lambda w: _mk_paper(
            w["title"], w["publication_year"], doi="10.1/a", citation_count=w["cited_by_count"]
        )
    ) as mock_parse, patch.object(
        arxiv, "search_papers", new=AsyncMock()
    ) as mock_arxiv:
        result = await handler("Hinton", limit=5)

    assert result["total"] == 1
    assert result["author_name"] == "Hinton"
    assert result["sources_searched"] == ["openalex"]
    assert result["papers"][0]["title"] == "Hinton 2024"
    # Confirm the right OpenAlex filter was used.
    call_args = mock_req.await_args
    params = call_args.args[1]  # second positional arg
    assert "author.search:Hinton" in params["filter"]
    assert params["per-page"] == 5
    # arXiv must not be called when OpenAlex returned results.
    mock_arxiv.assert_not_called()
    mock_parse.assert_called_once()


@pytest.mark.asyncio
async def test_search_by_author_falls_back_to_arxiv():
    handler = tools_mod.tool_registry.get("search_by_author").handler
    with patch.object(
        openalex, "_request", new=AsyncMock(return_value={"results": []})
    ), patch.object(
        arxiv, "search_papers", new=AsyncMock(return_value=AUTHOR_PAPERS)
    ) as mock_arxiv:
        result = await handler("Smith", limit=3)

    assert result["total"] == 2
    assert result["sources_searched"] == ["openalex", "arxiv"]
    mock_arxiv.assert_awaited_once()
    # arXiv's query is the standard "au:Name" form.
    call_args = mock_arxiv.await_args
    assert "au:Smith" in call_args.args[0] or "au:Smith" in str(call_args.kwargs)


@pytest.mark.asyncio
async def test_search_by_author_rejects_empty_name():
    handler = tools_mod.tool_registry.get("search_by_author").handler
    result = await handler("   ", limit=3)
    assert "error" in result
    assert result["papers"] == []


# ---------------------------------------------------------------------------
# Registry sanity: the three new tools are exposed with the right schema
# ---------------------------------------------------------------------------


def test_new_tools_are_registered_with_schemas():
    names = tools_mod.tool_registry.names()
    for tool in ("get_citing_papers", "get_referenced_papers", "search_by_author"):
        assert tool in names, f"tool {tool} missing from registry"
        definition = tools_mod.tool_registry.get(tool)
        # The schema must require the right primary arg.
        if tool == "search_by_author":
            assert definition.parameters["required"] == ["author_name"]
        else:
            assert definition.parameters["required"] == ["paper_id"]
        # Limit must be present and bounded.
        assert "limit" in definition.parameters["properties"]
        limit = definition.parameters["properties"]["limit"]
        assert limit["minimum"] >= 1
        assert limit["maximum"] <= 30
