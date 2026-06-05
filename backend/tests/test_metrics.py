"""
Tests for the in-memory metrics store.

Covers:
- counter / histogram recording
- per-phase helpers (``record_phase_start`` / ``record_phase_end``)
- per-LLM helpers (``record_llm_call``)
- :func:`phase_timer` context manager
- Prometheus text rendering
"""

import pytest

from app.metrics import (
    MetricsStore,
    metrics,
    phase_timer,
    render_prometheus,
)


@pytest.fixture
def fresh_store():
    """Reset the module-level store between tests so cross-test
    pollution cannot mask a regression."""
    metrics.reset()
    yield metrics
    metrics.reset()


class TestMetricsStore:
    """Direct store API."""

    def test_counter_and_histogram(self, fresh_store):
        fresh_store.inc("custom.counter", 3)
        fresh_store.observe("custom.hist", 100.0)
        fresh_store.observe("custom.hist", 200.0)
        snap = fresh_store.snapshot()
        assert snap["counters"]["custom.counter"] == 3
        h = snap["histograms"]["custom.hist"]
        assert h["count"] == 2
        assert h["min"] == 100.0
        assert h["max"] == 200.0
        assert h["sum"] == 300.0

    def test_record_phase_lifecycle(self, fresh_store):
        fresh_store.record_phase_start("compile")
        fresh_store.record_phase_end("compile", "success", 1234.5)
        fresh_store.record_phase_end("compile", "failed", 99.0)
        snap = fresh_store.snapshot()
        assert snap["counters"]["phase.compile.started"] == 1
        assert snap["counters"]["phase.compile.success"] == 1
        assert snap["counters"]["phase.compile.failed"] == 1
        h = snap["histograms"]["phase.compile.duration_ms"]
        assert h["count"] == 2
        assert h["sum"] == 1234.5 + 99.0

    def test_record_llm_call(self, fresh_store):
        fresh_store.record_llm_call(
            model="DeepSeek-V3",
            prompt_tokens=1000,
            completion_tokens=500,
            latency_ms=750.0,
        )
        fresh_store.record_llm_call("deepseek-v3", latency_ms=600.0)
        snap = fresh_store.snapshot()
        # Model name is normalized to lower case
        assert snap["counters"]["llm.deepseek-v3.calls"] == 2
        h = snap["histograms"]["llm.deepseek-v3.latency_ms"]
        assert h["count"] == 2
        h = snap["histograms"]["llm.deepseek-v3.prompt_tokens"]
        assert h["count"] == 1
        assert h["sum"] == 1000.0
        h = snap["histograms"]["llm.deepseek-v3.completion_tokens"]
        assert h["count"] == 1
        assert h["sum"] == 500.0

    def test_unknown_model_defaults(self, fresh_store):
        fresh_store.record_llm_call(model="", latency_ms=10.0)
        snap = fresh_store.snapshot()
        assert snap["counters"]["llm.unknown.calls"] == 1


class TestPhaseTimer:
    """The ``phase_timer`` context manager."""

    def test_records_success(self, fresh_store):
        with phase_timer("compile") as info:
            pass
        assert info["status"] == "success"
        assert info["duration_ms"] >= 0
        snap = fresh_store.snapshot()
        assert snap["counters"]["phase.compile.started"] == 1
        assert snap["counters"]["phase.compile.success"] == 1

    def test_records_failure_and_reraises(self, fresh_store):
        with pytest.raises(ValueError):
            with phase_timer("compile") as info:
                raise ValueError("boom")
        assert info["status"] == "failed"
        snap = fresh_store.snapshot()
        assert snap["counters"]["phase.compile.failed"] == 1

    def test_recognizes_skipped(self, fresh_store):
        # ``phase_timer`` records success/failed; skipping is a
        # matter of convention. Verify the underlying store handles
        # a custom status string without raising.
        fresh_store.record_phase_end("compile", "skipped", 1.0)
        snap = fresh_store.snapshot()
        assert snap["counters"]["phase.compile.skipped"] == 1


class TestPrometheusRendering:
    """The text exposition format."""

    def test_renders_counters_and_histograms(self, fresh_store):
        fresh_store.inc("phase.research.success", 4)
        fresh_store.observe("phase.research.duration_ms", 100.0)
        fresh_store.observe("phase.research.duration_ms", 200.0)
        body = render_prometheus()
        # Counters
        assert "# TYPE phase_research_success counter" in body
        assert "phase_research_success 4" in body
        # Histogram (TYPE line + bucket lines + count + sum)
        assert "# TYPE phase_research_duration_ms histogram" in body
        assert "phase_research_duration_ms_count 2" in body
        assert "phase_research_duration_ms_sum 300" in body
        # At least the +Inf bucket line is emitted
        assert 'phase_research_duration_ms_bucket{le="+Inf"} 2' in body
