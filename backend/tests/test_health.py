"""
Tests for the health-check module.

We exercise the individual check functions and the combined report
builder. The disk-write check is monkeypatched to a temp dir so
the test never touches the real ``data/projects/`` directory.
"""

import os
import tempfile
from pathlib import Path

import pytest

from app import health
from app.config import settings


@pytest.fixture
def tmp_data_dir(monkeypatch, tmp_path: Path):
    """Point ``settings.data_dir`` at a temp directory for the test."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


class TestIndividualChecks:
    def test_data_writable_ok(self, tmp_data_dir):
        result = health._check_data_writable()
        assert result.name == "data_writable"
        assert result.status == "ok"
        # Tempfile should be cleaned up
        leftover = list(tmp_data_dir.glob("projects/.healthcheck-*"))
        assert not leftover

    def test_data_writable_down_when_readonly(self, monkeypatch, tmp_data_dir):
        # Simulate a read-only filesystem by raising on os.makedirs
        import builtins

        def boom(*args, **kwargs):
            raise PermissionError("simulated read-only")

        monkeypatch.setattr(os, "makedirs", boom)
        result = health._check_data_writable()
        assert result.status == "down"
        assert "PermissionError" in result.detail or "simulated" in result.detail

    def test_pipeline_loaded_ok(self):
        result = health._check_pipeline_loaded()
        assert result.name == "pipeline_loaded"
        assert result.status == "ok"

    def test_pipeline_loaded_down_on_import_error(self, monkeypatch):
        def boom(_name):
            raise ImportError("simulated module failure")

        monkeypatch.setattr(
            "app.health.importlib.import_module", boom
        )
        result = health._check_pipeline_loaded()
        assert result.status == "down"
        assert "simulated module failure" in result.detail

    def test_llm_check_degraded_when_no_key(self, monkeypatch):
        # Patch the bound reference inside app.health so the check
        # observes the patched return value.
        monkeypatch.setattr("app.health.create_llm_client", lambda: None)
        result = health._check_llm()
        assert result.status == "degraded"
        assert result.critical is False

    def test_llm_check_ok_when_key_set(self, monkeypatch):
        from openai import AsyncOpenAI

        def fake_client():
            return AsyncOpenAI(api_key="x", base_url="http://example.invalid/v1")

        monkeypatch.setattr("app.health.create_llm_client", fake_client)
        result = health._check_llm()
        assert result.status == "ok"


class TestReportBuilder:
    def test_combined_report(self, tmp_data_dir, monkeypatch):
        from openai import AsyncOpenAI

        monkeypatch.setattr(
            "app.health.create_llm_client",
            lambda: AsyncOpenAI(api_key="x", base_url="http://x"),
        )
        report = health.run_health_checks()
        assert report["status"] == "ok"
        assert set(report["checks"].keys()) == {
            "llm_configured",
            "data_writable",
            "pipeline_loaded",
        }
        for name, payload in report["checks"].items():
            assert "status" in payload
            assert "detail" in payload
            assert "latency_ms" in payload

    def test_worst_status_wins(self, monkeypatch, tmp_data_dir):
        # Make data_writable fail so the overall status flips to "down"
        def boom(*args, **kwargs):
            raise PermissionError("nope")
        monkeypatch.setattr(os, "makedirs", boom)
        report = health.run_health_checks()
        assert report["status"] == "down"

    def test_live_is_minimal(self):
        report = health.live()
        assert report["status"] == "ok"
        assert report["checks"] == {}

    def test_ready_delegates_to_run(self, tmp_data_dir):
        report = health.ready()
        assert "checks" in report
        # data_writable is critical; should be ok in the temp dir
        assert report["checks"]["data_writable"]["status"] == "ok"
