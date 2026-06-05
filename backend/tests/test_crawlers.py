"""
Tests for arXiv crawler
"""

import pytest
from unittest.mock import patch, MagicMock
from app.crawlers.arxiv import ArxivCrawler


class TestArxivCrawler:
    """Tests for arXiv API crawler"""

    @pytest.fixture
    def crawler(self):
        """Create arxiv crawler instance"""
        return ArxivCrawler()

    def test_extract_arxiv_id_from_url(self, crawler):
        """Test extracting arXiv ID from URL"""
        url = "https://arxiv.org/abs/2106.09685"
        result = crawler._extract_arxiv_id(url)
        assert result == "2106.09685"

    def test_extract_arxiv_id_from_url_with_version(self, crawler):
        """Test extracting arXiv ID with version from URL"""
        url = "https://arxiv.org/abs/2106.09685v2"
        result = crawler._extract_arxiv_id(url)
        assert result == "2106.09685v2"

    def test_extract_arxiv_id_with_prefix(self, crawler):
        """Test extracting arXiv ID with arXiv: prefix"""
        id_str = "arXiv:2106.09685"
        result = crawler._extract_arxiv_id(id_str)
        assert result == "2106.09685"

    def test_extract_arxiv_id_plain(self, crawler):
        """Test extracting plain arXiv ID"""
        id_str = "2106.09685"
        result = crawler._extract_arxiv_id(id_str)
        assert result == "2106.09685"

    @pytest.mark.asyncio
    async def test_search_papers_returns_empty_on_error(self, crawler):
        """Test that search returns empty list on API error"""
        with patch.object(crawler, "_rate_limit_wait"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 500

                mock_client.return_value.__aenter__.return_value.get.return_value = (
                    mock_response
                )

                result = await crawler.search_papers("test query", limit=5)
                assert result == []
