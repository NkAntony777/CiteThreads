"""
Per-session message memory
==========================

In-process store keyed by ``project_id`` (a writing project). For now
this is intentionally simple: a sliding window of the last N messages.
Replace with Redis/SQL when the project needs persistence.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field


class MemoryMessage(BaseModel):
    """One turn in the agent conversation history.

    ``role`` is one of ``system``, ``user``, ``assistant``, or ``tool``.
    For ``tool`` messages, ``tool_call_id`` must be set; for ``assistant``
    messages that triggered tools, ``tool_calls`` carries the LLM's
    decisions and ``name``/``content`` carry tool results.
    """

    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[dict]] = None


class SessionMemory:
    """Thread-safe sliding-window message store keyed by session id."""

    def __init__(self, max_messages: int = 40) -> None:
        self._max = max_messages
        self._lock = threading.Lock()
        self._sessions: Dict[str, Deque[MemoryMessage]] = {}

    def _bucket(self, session_id: str) -> Deque[MemoryMessage]:
        bucket = self._sessions.get(session_id)
        if bucket is None:
            bucket = deque(maxlen=self._max)
            self._sessions[session_id] = bucket
        return bucket

    def append(self, session_id: str, message: MemoryMessage) -> None:
        with self._lock:
            self._bucket(session_id).append(message)

    def extend(self, session_id: str, messages: Iterable[MemoryMessage]) -> None:
        with self._lock:
            bucket = self._bucket(session_id)
            for m in messages:
                bucket.append(m)

    def get(self, session_id: str) -> List[MemoryMessage]:
        with self._lock:
            return list(self._bucket(session_id))

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def set_system(self, session_id: str, content: str) -> None:
        """Insert or replace the system message at index 0."""
        with self._lock:
            bucket = self._bucket(session_id)
            if bucket and bucket[0].role == "system":
                bucket[0] = MemoryMessage(role="system", content=content)
            else:
                bucket.appendleft(MemoryMessage(role="system", content=content))


# Module-level singleton
session_memory: SessionMemory = SessionMemory()
