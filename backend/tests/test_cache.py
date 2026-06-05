"""
Tests for paper cache service (LRU memory + SQLite persistent).
"""
import os
import tempfile
import pytest

from app.models import Paper
from app.services.cache import LRUMemoryCache, SQLiteCache, PaperCache


def _make_paper(pid: str, title: str = "Test Paper", citations: int = 10) -> Paper:
    return Paper(
        id=pid,
        title=title,
        authors=["Author One"],
        abstract="An abstract.",
        year=2024,
        citation_count=citations,
        source="test",
    )


class TestLRUMemoryCache:
    def test_put_and_get(self):
        cache = LRUMemoryCache(maxsize=3)
        p = _make_paper("P1")
        cache.put("P1", p)
        assert cache.get("P1") is not None
        assert cache.get("P1").id == "P1"

    def test_miss(self):
        cache = LRUMemoryCache()
        assert cache.get("missing") is None

    def test_eviction(self):
        cache = LRUMemoryCache(maxsize=2)
        cache.put("A", _make_paper("A"))
        cache.put("B", _make_paper("B"))
        cache.put("C", _make_paper("C"))  # should evict A
        assert cache.get("A") is None
        assert cache.get("B") is not None
        assert cache.get("C") is not None

    def test_lru_order(self):
        cache = LRUMemoryCache(maxsize=2)
        cache.put("A", _make_paper("A"))
        cache.put("B", _make_paper("B"))
        cache.get("A")  # touch A -> B is LRU
        cache.put("C", _make_paper("C"))  # evicts B
        assert cache.get("A") is not None
        assert cache.get("B") is None

    def test_has(self):
        cache = LRUMemoryCache()
        assert not cache.has("X")
        cache.put("X", _make_paper("X"))
        assert cache.has("X")

    def test_update_existing(self):
        cache = LRUMemoryCache()
        cache.put("P1", _make_paper("P1", "v1"))
        cache.put("P1", _make_paper("P1", "v2"))
        assert cache.get("P1").title == "v2"


class TestSQLiteCache:
    @pytest.fixture
    def db_cache(self, tmp_path):
        db_path = str(tmp_path / "test_cache.db")
        return SQLiteCache(db_path=db_path)

    def test_put_and_get(self, db_cache):
        p = _make_paper("DOI:10.1234/test")
        db_cache.put("DOI:10.1234/test", p)
        result = db_cache.get("DOI:10.1234/test")
        assert result is not None
        assert result.id == "DOI:10.1234/test"

    def test_miss(self, db_cache):
        assert db_cache.get("nonexistent") is None

    def test_overwrite(self, db_cache):
        db_cache.put("P1", _make_paper("P1", "v1"))
        db_cache.put("P1", _make_paper("P1", "v2"))
        result = db_cache.get("P1")
        assert result.title == "v2"

    def test_get_batch(self, db_cache):
        for i in range(5):
            db_cache.put(f"P{i}", _make_paper(f"P{i}"))
        result = db_cache.get_batch(["P0", "P2", "P4", "MISSING"])
        assert len(result) == 3
        assert "P0" in result
        assert "P2" in result
        assert "MISSING" not in result

    def test_get_batch_empty(self, db_cache):
        assert db_cache.get_batch([]) == {}

    def test_evict_old(self, db_cache):
        import time
        # Insert with old timestamp
        conn = db_cache._get_conn()
        conn.execute(
            "INSERT INTO papers (paper_id, data, updated_at) VALUES (?, ?, ?)",
            ("OLD", '{"id":"OLD","title":"old","authors":[],"abstract":"","year":2020,"citation_count":0,"source":"test"}', time.time() - 40 * 86400)
        )
        conn.commit()
        db_cache.put("NEW", _make_paper("NEW"))
        removed = db_cache.evict_old(max_age_days=30)
        assert removed >= 1
        assert db_cache.get("OLD") is None
        assert db_cache.get("NEW") is not None


class TestPaperCache:
    @pytest.fixture
    def cache(self, tmp_path):
        db_path = str(tmp_path / "paper_cache.db")
        disk = SQLiteCache(db_path=db_path)
        mem = LRUMemoryCache(maxsize=10)
        pc = PaperCache.__new__(PaperCache)
        pc._memory = mem
        pc._disk = disk
        return pc

    def test_two_tier_fallback(self, cache):
        p = _make_paper("P1")
        # Only write to disk
        cache._disk.put("P1", p)
        # Should find via disk and promote to memory
        result = cache.get("P1")
        assert result is not None
        assert cache._memory.has("P1")

    def test_put_both_tiers(self, cache):
        p = _make_paper("P1")
        cache.put(p)
        assert cache._memory.has("P1")
        assert cache._disk.get("P1") is not None

    def test_normalize_doi(self):
        assert PaperCache._normalize_id("doi:10.1234/test") == "DOI:10.1234/test"
        assert PaperCache._normalize_id("DOI:10.1234/test") == "DOI:10.1234/test"

    def test_normalize_arxiv(self):
        assert PaperCache._normalize_id("arxiv:2401.12345") == "arXiv:2401.12345"
        assert PaperCache._normalize_id("ARXIV:2401.12345") == "arXiv:2401.12345"

    def test_normalize_empty(self):
        assert PaperCache._normalize_id("") is None
        assert PaperCache._normalize_id(None) is None

    def test_get_batch(self, cache):
        for i in range(5):
            cache.put(_make_paper(f"P{i}"))
        result = cache.get_batch(["P0", "P2", "MISSING"])
        assert len(result) == 2

    def test_put_batch(self, cache):
        papers = [_make_paper(f"P{i}") for i in range(3)]
        cache.put_batch(papers)
        assert cache.get("P0") is not None
        assert cache.get("P2") is not None

    def test_has(self, cache):
        assert not cache.has("X")
        cache.put(_make_paper("X"))
        assert cache.has("X")

    def test_none_id_ignored(self, cache):
        cache.put(Paper(id="", title="empty", authors=[], abstract="", year=2024, citation_count=0, source="test"))
        assert cache.get("") is None
