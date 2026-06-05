"""
Paper Cache Service - Two-tier caching (LRU memory + SQLite persistent).
Avoids redundant API calls for paper metadata across sessions.
"""

import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..models import Paper
from ..config import settings

logger = logging.getLogger(__name__)


class LRUMemoryCache:
    """Thread-safe in-memory LRU cache."""

    def __init__(self, maxsize: int = 2000):
        self._cache: OrderedDict[str, Paper] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Paper]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key: str, paper: Paper) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)
            self._cache[key] = paper

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._cache


class SQLiteCache:
    """Persistent SQLite cache for paper metadata."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path(settings.data_dir) / "paper_cache.db")
        self._db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                paper_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_papers_updated
            ON papers(updated_at)
        """)
        conn.commit()

    def get(self, paper_id: str) -> Optional[Paper]:
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT data FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if row:
                data = json.loads(row[0])
                return Paper(**data)
        except Exception as e:
            logger.warning(f"SQLite cache read error for {paper_id}: {e}")
        return None

    def put(self, paper_id: str, paper: Paper) -> None:
        try:
            conn = self._get_conn()
            data = json.dumps(paper.model_dump(), ensure_ascii=False, default=str)
            conn.execute(
                "INSERT OR REPLACE INTO papers (paper_id, data, updated_at) VALUES (?, ?, ?)",
                (paper_id, data, time.time())
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"SQLite cache write error for {paper_id}: {e}")

    def get_batch(self, paper_ids: List[str]) -> Dict[str, Paper]:
        """Fetch multiple papers from cache at once."""
        if not paper_ids:
            return {}
        try:
            conn = self._get_conn()
            placeholders = ",".join("?" * len(paper_ids))
            rows = conn.execute(
                f"SELECT paper_id, data FROM papers WHERE paper_id IN ({placeholders})",
                paper_ids
            ).fetchall()
            result = {}
            for pid, data_str in rows:
                try:
                    result[pid] = Paper(**json.loads(data_str))
                except Exception:
                    pass
            return result
        except Exception as e:
            logger.warning(f"SQLite cache batch read error: {e}")
            return {}

    def evict_old(self, max_age_days: int = 30) -> int:
        """Remove entries older than max_age_days. Returns count removed."""
        cutoff = time.time() - max_age_days * 86400
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "DELETE FROM papers WHERE updated_at < ?", (cutoff,)
            )
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.warning(f"SQLite cache eviction error: {e}")
            return 0


class PaperCache:
    """
    Two-tier paper cache: LRU memory -> SQLite persistent.
    Memory cache is fastest, SQLite survives restarts.
    """

    def __init__(self):
        self._memory = LRUMemoryCache(maxsize=2000)
        self._disk = SQLiteCache()

    def get(self, paper_id: str) -> Optional[Paper]:
        """Get paper from cache (memory first, then disk)."""
        # Normalize key
        norm_id = self._normalize_id(paper_id)
        if not norm_id:
            return None

        # L1: memory
        paper = self._memory.get(norm_id)
        if paper:
            return paper

        # L2: disk
        paper = self._disk.get(norm_id)
        if paper:
            self._memory.put(norm_id, paper)
            return paper

        return None

    def put(self, paper: Paper) -> None:
        """Store paper in both cache tiers."""
        if not paper or not paper.id:
            return
        norm_id = self._normalize_id(paper.id)
        if norm_id:
            self._memory.put(norm_id, paper)
            self._disk.put(norm_id, paper)

    def put_batch(self, papers: List[Paper]) -> None:
        """Store multiple papers in cache."""
        for paper in papers:
            self.put(paper)

    def has(self, paper_id: str) -> bool:
        """Check if paper exists in any cache tier."""
        norm_id = self._normalize_id(paper_id)
        return bool(norm_id and (self._memory.has(norm_id) or self._disk.get(norm_id) is not None))

    def get_batch(self, paper_ids: List[str]) -> Dict[str, Paper]:
        """Fetch multiple papers, using memory first then disk for misses."""
        result: Dict[str, Paper] = {}
        missing: List[str] = []

        for pid in paper_ids:
            norm_id = self._normalize_id(pid)
            if not norm_id:
                continue
            paper = self._memory.get(norm_id)
            if paper:
                result[norm_id] = paper
            else:
                missing.append(norm_id)

        if missing:
            disk_results = self._disk.get_batch(missing)
            for pid, paper in disk_results.items():
                self._memory.put(pid, paper)
                result[pid] = paper

        return result

    @staticmethod
    def _normalize_id(paper_id: str) -> Optional[str]:
        """Normalize paper ID to a consistent cache key."""
        if not paper_id:
            return None
        # Strip whitespace
        pid = paper_id.strip()
        # Normalize DOI: prefix variations
        if pid.lower().startswith("doi:"):
            pid = "DOI:" + pid[4:]
        # Normalize arXiv: prefix
        if pid.lower().startswith("arxiv:"):
            pid = "arXiv:" + pid[6:]
        return pid


# Singleton
paper_cache = PaperCache()
