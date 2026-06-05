"""
Per-project progress event bus for the CTDP draft pipeline.

Why a per-project bus?
- Multiple UIs (the DraftGenerator tab, a future CLI, an ops
  dashboard) can watch the same project. Each subscriber gets its own
  queue so a slow consumer never blocks the runner.
- The runner should not have to know about HTTP, SSE, or any transport
  — it just calls ``bus.publish(event)`` and forgets. Transport
  adapters translate the published event into SSE / WebSocket / log
  lines.

Concurrency model
-----------------
- One ``ProgressBus`` per ``project_id``, held in a module-level
  dict protected by a lock.
- Subscribers are ``asyncio.Queue`` objects. ``publish`` puts a copy
  on every queue. ``subscribe`` returns the queue and records it.
  ``unsubscribe`` removes the queue.
- A bounded queue (``maxsize=128``) protects against a runaway
  subscriber. When the queue is full, ``publish`` drops the event
  for *that* subscriber (other subscribers still get it) and logs
  a warning. We prefer dropping to back-pressuring the runner.

Event shape
-----------
``Event`` is a small dataclass:

- ``type``    : string discriminator (see ``EVT_*`` constants)
- ``data``    : dict of payload, JSON-serializable
- ``project_id``: owning project id (useful for multi-project
                subscribers, but currently every bus is single-project)
- ``at``      : ``datetime`` when the event was created

Event type vocabulary
---------------------
- ``phase-start``    : a phase began executing
- ``phase-progress`` : a phase emitted a progress update
- ``phase-end``      : a phase finished (succeeded, failed, or skipped)
- ``error``          : an unrecoverable error was observed
- ``done``           : a runner completed a multi-phase ``run_all`` /
                     ``resume_from`` sequence (terminal event)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Event ``type`` discriminators. Kept short so JSON stays small in SSE.
EVT_PHASE_START = "phase-start"
EVT_PHASE_PROGRESS = "phase-progress"
EVT_PHASE_END = "phase-end"
EVT_ERROR = "error"
EVT_DONE = "done"


# All recognized event types. Useful for tests / debugging.
ALL_EVENT_TYPES = frozenset(
    {EVT_PHASE_START, EVT_PHASE_PROGRESS, EVT_PHASE_END, EVT_ERROR, EVT_DONE}
)


@dataclass
class Event:
    """A single progress event.

    ``data`` should be JSON-serializable. ``at`` is set automatically
    at construction time unless the caller overrides it (used by
    tests and by replaying historical events).
    """

    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    project_id: Optional[str] = None
    at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "project_id": self.project_id,
            "at": self.at.isoformat(),
            "data": self.data,
        }


class ProgressBus:
    """In-process pub/sub for one project's progress events.

    Each ``subscribe`` call returns a fresh ``asyncio.Queue``; the
    caller is expected to drain it. ``unsubscribe`` removes the
    queue and prevents further delivery.

    Thread-safety: all mutation goes through the asyncio lock, but
    the queues themselves are async-only (no threading).
    """

    def __init__(self, project_id: str, queue_maxsize: int = 128) -> None:
        self.project_id = project_id
        self._subscribers: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self._queue_maxsize = queue_maxsize

    async def subscribe(self) -> asyncio.Queue:
        """Register a new subscriber and return its queue.

        The returned queue is bounded; if a slow consumer lets it
        fill up, ``publish`` will drop events for that subscriber
        rather than block the runner.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._subscribers.append(q)
        logger.debug(
            "progress bus[%s]: subscriber added (total=%d)",
            self.project_id, len(self._subscribers),
        )
        return q

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscriber. Safe to call twice."""
        async with self._lock:
            try:
                self._subscribers.remove(queue)
            except ValueError:
                return
        logger.debug(
            "progress bus[%s]: subscriber removed (total=%d)",
            self.project_id, len(self._subscribers),
        )

    async def publish(self, event: Event) -> None:
        """Fan out an event to every subscriber.

        Subscribers with a full queue are skipped (the event is
        dropped for them, not for the others) and a warning is
        logged. We do not block the producer.
        """
        if event.project_id is None:
            event.project_id = self.project_id
        # Snapshot the subscriber list under the lock so we don't hold
        # it while putting (which can yield).
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "progress bus[%s]: dropping event %s for slow subscriber",
                    self.project_id, event.type,
                )

    def subscriber_count(self) -> int:
        """Synchronous count, for tests."""
        return len(self._subscribers)


# ---------------------------------------------------------------------------
# Module-level registry: one bus per project_id
# ---------------------------------------------------------------------------


_REGISTRY: Dict[str, ProgressBus] = {}
_REGISTRY_LOCK = asyncio.Lock()


async def get_bus(project_id: str) -> ProgressBus:
    """Get (or create) the bus for ``project_id``.

    The bus lives for the lifetime of the process. In practice this
    is fine — a project typically sees a handful of subscribers
    over its lifetime and the queue size cap keeps memory bounded.
    """
    bus = _REGISTRY.get(project_id)
    if bus is not None:
        return bus
    async with _REGISTRY_LOCK:
        bus = _REGISTRY.get(project_id)
        if bus is None:
            bus = ProgressBus(project_id)
            _REGISTRY[project_id] = bus
        return bus


def get_bus_sync(project_id: str) -> ProgressBus:
    """Synchronous lookup; returns the bus if one exists, else None.

    Use this from places where you're already in an async context but
    don't want to ``await`` a registration. Returns None when the
    project has never been touched (so callers can decide whether
    to spin one up).
    """
    return _REGISTRY.get(project_id)


def reset_bus(project_id: str) -> None:
    """Drop the bus for ``project_id`` from the registry.

    Intended for tests only. The bus's existing subscribers will
    keep their (now-detached) queues; this is harmless because
    they will be garbage-collected when the test ends.
    """
    _REGISTRY.pop(project_id, None)


def reset_all() -> None:
    """Drop every bus. Tests only."""
    _REGISTRY.clear()


__all__ = [
    "EVT_PHASE_START",
    "EVT_PHASE_PROGRESS",
    "EVT_PHASE_END",
    "EVT_ERROR",
    "EVT_DONE",
    "ALL_EVENT_TYPES",
    "Event",
    "ProgressBus",
    "get_bus",
    "get_bus_sync",
    "reset_bus",
    "reset_all",
]
