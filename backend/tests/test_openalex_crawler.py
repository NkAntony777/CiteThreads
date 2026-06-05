"""Tests for OpenAlex crawler.

Focus: citation/reference direction for OpenAlex convenience filters.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.crawlers.openalex import OpenAlexCrawler


def _work(work_id: str, *, title: str) -> dict:
    return {
        "id": f"https://openalex.org/{work_id}",
        "display_name": title,
        "publication_year": 2024,
        "ids": {},
        "authorships": [],
        "primary_location": {},
        "concepts": [],
        "cited_by_count": 0,
    }


class TestOpenAlexCrawler:
    @pytest.fixture
    def crawler(self) -> OpenAlexCrawler:
        return OpenAlexCrawler()

    @pytest.mark.asyncio
    async def test_get_references_uses_cited_by_filter(
        self, crawler: OpenAlexCrawler
    ) -> None:
        with patch.object(
            crawler, "_resolve_to_openalex_id", AsyncMock(return_value="W123")
        ):
            with patch.object(
                crawler,
                "_request",
                AsyncMock(return_value={"results": [_work("W999", title="Ref")]}),
            ) as mock_request:
                refs = await crawler.get_references("OpenAlex:W123", limit=3)

        assert len(refs) == 1
        assert refs[0].id == "OpenAlex:W999"

        endpoint, params = mock_request.await_args.args
        assert endpoint == "/works"
        assert params["filter"] == "cited_by:W123"
        assert params["per-page"] == 3

    @pytest.mark.asyncio
    async def test_get_citations_uses_cites_filter(
        self, crawler: OpenAlexCrawler
    ) -> None:
        with patch.object(
            crawler, "_resolve_to_openalex_id", AsyncMock(return_value="W123")
        ):
            with patch.object(
                crawler,
                "_request",
                AsyncMock(return_value={"results": [_work("W888", title="Cite")]}),
            ) as mock_request:
                cites = await crawler.get_citations("OpenAlex:W123", limit=7)

        assert len(cites) == 1
        assert cites[0].id == "OpenAlex:W888"

        endpoint, params = mock_request.await_args.args
        assert endpoint == "/works"
        assert params["filter"] == "cites:W123"
        assert params["per-page"] == 7

    @pytest.mark.asyncio
    async def test_returns_empty_if_openalex_id_cannot_be_resolved(
        self, crawler: OpenAlexCrawler
    ) -> None:
        with patch.object(
            crawler, "_resolve_to_openalex_id", AsyncMock(return_value=None)
        ):
            with patch.object(crawler, "_request", AsyncMock()) as mock_request:
                refs = await crawler.get_references(
                    "DOI:10.0000/does-not-resolve", limit=5
                )
                cites = await crawler.get_citations(
                    "DOI:10.0000/does-not-resolve", limit=5
                )

        assert refs == []
        assert cites == []
        mock_request.assert_not_awaited()
