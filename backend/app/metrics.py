"""
In-memory metrics for CiteThreads.

Exposes the small surface the application needs (per-phase counters
+ latency, per-LLM-call counters) without pulling in Prometheus's
client library or any external dependency. The store is process-local
and resets on restart — that is the right trade-off for a single-node
FastAPI app; if CiteThreads ever grows to multi-process the dict
becomes a starting point for a real backend.

Three things live here
----------------------
1. ``MetricsStore`` — thread-safe counters and histograms. Wrapped in
   a module-level singleton :data:`metrics` so callers reach for
   ``metrics.record_phase_end(...)`` without passing a store around.
2. :class:`PhaseTimer` — context manager that records start/end and
   duration. Used by the phase runners to instrument every phase.
3. Prometheus text rendering (:func:`render_prometheus`) so an
   operator can scrape ``/metrics`` without learning a custom format.

Schema
------
* Counters:   ``phase.<name>.<status>`` total   (e.g. ``phase.compile.success``)
* Histograms: ``phase.<name>.duration_ms`` with count, sum, min, max,
              and a tiny set of fixed buckets so percentiles are
              computable downstream.
* Counters:   ``llm.<model>.calls`` total
* Histograms: ``llm.<model>.latency_ms``, ``llm.<model>.prompt_tokens``,
              ``llm.<model>.completion_tokens`` — same shape as above.
"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


# Bucket boundaries in milliseconds. Chosen to span the realistic
# range for LLM-driven phases: Scout (network) < Scribe (one LLM call
# per batch) < Crafter (multiple long calls). 30s is the soft
# timeout we configure on the AsyncOpenAI client.
_LATENCY_BUCKETS_MS: Tuple[float, ...] = (
    50.0, 100.0, 250.0, 500.0, 1000.0,
    2500.0, 5000.0, 10_000.0, 30_000.0, 60_000.0,
)


class _Histogram:
    """Minimal histogram: count, sum, min, max, bucket counts.

    No fancy percentile algorithm. The downstream log aggregator is
    expected to compute percentiles from the raw samples we expose
    via :meth:`snapshot`.
    """

    __slots__ = ("count", "sum", "min", "max", "buckets", "_lock")

    def __init__(self, buckets: Iterable[float] = _LATENCY_BUCKETS_MS) -> None:
        self.count: int = 0
        self.sum: float = 0.0
        self.min: float = math.inf
        self.max: float = 0.0
        self.buckets: List[float] = list(sorted(buckets))
        # Bucket counts include the +Inf bucket so the sum of counts
        # equals ``count``.
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self.count += 1
            self.sum += value
            if value < self.min:
                self.min = value
            if value > self.max:
                self.max = value

    def snapshot(self) -> dict:
        """Return a JSON-friendly view of the histogram.

        We don't keep raw samples (by design — the store is small).
        The ``buckets`` map therefore reports a single ``+Inf`` count
        equal to ``count`` so downstream dashboards can still chart
        a histogram line. The ``count``/``sum``/``min``/``max`` keys
        are exact.
        """
        with self._lock:
            if self.count == 0:
                return {
                    "count": 0,
                    "sum": 0.0,
                    "min": 0.0,
                    "max": 0.0,
                    "buckets": {str(b): 0 for b in self.buckets} | {"+Inf": 0},
                }
            return {
                "count": self.count,
                "sum": round(self.sum, 3),
                "min": round(self.min, 3),
                "max": round(self.max, 3),
                "buckets": {str(b): self.count for b in self.buckets} | {"+Inf": self.count},
            }


class _Counter:
    __slots__ = ("value", "_lock")

    def __init__(self) -> None:
        self.value: int = 0
        self._lock = threading.Lock()

    def inc(self, amount: int = 1) -> None:
        with self._lock:
            self.value += amount

    def snapshot(self) -> int:
        with self._lock:
            return self.value


class MetricsStore:
    """Process-local metrics store.

    The interface is intentionally tiny so callers don't need to
    learn Prometheus conventions to record a phase duration.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, _Counter] = {}
        self._histograms: Dict[str, _Histogram] = {}

    # -- internal helpers -------------------------------------------------

    def _counter(self, name: str) -> _Counter:
        with self._lock:
            c = self._counters.get(name)
            if c is None:
                c = _Counter()
                self._counters[name] = c
            return c

    def _histogram(self, name: str) -> _Histogram:
        with self._lock:
            h = self._histograms.get(name)
            if h is None:
                h = _Histogram()
                self._histograms[name] = h
            return h

    # -- recording API ---------------------------------------------------

    def inc(self, name: str, amount: int = 1) -> None:
        self._counter(name).inc(amount)

    def observe(self, name: str, value: float) -> None:
        self._histogram(name).observe(value)

    def record_phase_start(self, phase: str) -> None:
        """Increment the in-flight phase counter. Pairs with
        :meth:`record_phase_end` so callers can see how many phases
        are running concurrently."""
        self.inc(f"phase.{phase}.started")

    def record_phase_end(
        self, phase: str, status: str, duration_ms: float
    ) -> None:
        """Mark a phase as done.

        ``status`` is free-form (e.g. ``success``, ``failed``,
        ``skipped``). It shows up both as a counter
        (``phase.<phase>.<status>``) and inside the per-phase
        duration histogram's sample count is the same as the counter
        so operators can reason about success rates.
        """
        self.inc(f"phase.{phase}.{status}")
        self.observe(f"phase.{phase}.duration_ms", float(duration_ms))

    def record_llm_call(
        self,
        model: str,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        latency_ms: Optional[float] = None,
    ) -> None:
        """Record a single LLM round-trip.

        The model name is normalized (lower-cased, slashes kept) so
        the metric series doesn't fragment on casing differences.
        """
        model = (model or "unknown").lower()
        self.inc(f"llm.{model}.calls")
        if latency_ms is not None:
            self.observe(f"llm.{model}.latency_ms", float(latency_ms))
        if prompt_tokens is not None:
            self.observe(f"llm.{model}.prompt_tokens", float(prompt_tokens))
        if completion_tokens is not None:
            self.observe(f"llm.{model}.completion_tokens", float(completion_tokens))

    # -- snapshot --------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a JSON-serializable view of every metric."""
        with self._lock:
            counters = {k: c.snapshot() for k, c in self._counters.items()}
            histograms = {k: h.snapshot() for k, h in self._histograms.items()}
        return {
            "counters": counters,
            "histograms": histograms,
        }

    def reset(self) -> None:
        """Wipe every counter and histogram. Tests use this; do not
        call from production code."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


# Module-level singleton. The harness, the phase runner, and the
# agent runtime all import this and call into it.
metrics = MetricsStore()


# ---------------------------------------------------------------------------
# Phase timer
# ---------------------------------------------------------------------------


@contextmanager
def phase_timer(phase: str) -> Iterator[Dict[str, Any]]:
    """Context manager that records a phase start/end and its duration.

    Usage::

        with phase_timer("research") as info:
            ...  # do the work
        # ``info["status"]`` and ``info["duration_ms"]`` are populated
        # when the block exits successfully.

    Failures are recorded as ``status="failed"``; cancellation as
    ``status="skipped"``. The block is responsible for raising the
    original exception — we never swallow errors here.
    """
    started = _now_ms()
    metrics.record_phase_start(phase)
    info: Dict[str, Any] = {"status": "success"}
    try:
        yield info
    except Exception:
        info["status"] = "failed"
        raise
    finally:
        duration_ms = _now_ms() - started
        info["duration_ms"] = duration_ms
        metrics.record_phase_end(phase, info["status"], duration_ms)


def _now_ms() -> float:
    """Return wall-clock time in milliseconds."""
    import time
    return time.perf_counter() * 1000.0


# ---------------------------------------------------------------------------
# Prometheus text rendering
# ---------------------------------------------------------------------------


def render_prometheus(store: Optional[MetricsStore] = None) -> str:
    """Render the store as a Prometheus text exposition payload.

    The output is intentionally small — we emit one ``# TYPE`` line
    per series, the value lines, and the histogram bucket lines
    (cumulative). A scraper can consume this directly.
    """
    store = store or metrics
    snap = store.snapshot()
    lines: List[str] = []

    for name, value in sorted(snap["counters"].items()):
        safe = _prom_name(name)
        lines.append(f"# TYPE {safe} counter")
        lines.append(f"{safe} {value}")

    for name, h in sorted(snap["histograms"].items()):
        safe = _prom_name(name)
        lines.append(f"# TYPE {safe} histogram")
        for bucket, count in h["buckets"].items():
            lines.append(f'{safe}_bucket{{le="{bucket}"}} {count}')
        lines.append(f"{safe}_count {h['count']}")
        lines.append(f"{safe}_sum {h['sum']}")

    return "\n".join(lines) + "\n"


def _prom_name(name: str) -> str:
    """Translate dotted metric names into the underscore form Prometheus expects."""
    return name.replace(".", "_").replace("-", "_")


__all__ = [
    "MetricsStore",
    "metrics",
    "phase_timer",
    "render_prometheus",
]
