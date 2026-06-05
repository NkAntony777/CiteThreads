"""
Tests for the structured JSON logging module.

Covers:
- :class:`JsonFormatter` shape and context-var injection
- :class:`RequestIdFilter` propagation
- :func:`configure_json_logging` idempotency
- :class:`RequestIdMiddleware` request id handling
"""

import io
import json
import logging

import pytest

from app.logging_config import (
    JsonFormatter,
    RequestIdFilter,
    configure_json_logging,
    set_request_id,
    set_user_id,
    set_phase,
    get_request_id,
    clear_request_id,
    clear_user_id,
    clear_phase,
)


def _make_record(
    msg: str = "hello",
    level: int = logging.INFO,
    name: str = "test",
    **extra,
) -> logging.LogRecord:
    """Build a LogRecord with arbitrary extras."""
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


class TestJsonFormatter:
    """Verify the JSON output shape."""

    def test_basic_shape(self):
        formatter = JsonFormatter()
        record = _make_record(msg="hi", name="app.test", level=logging.INFO)
        out = formatter.format(record)
        payload = json.loads(out)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.test"
        assert payload["msg"] == "hi"
        # Timestamp is ISO-8601 with Z suffix
        assert payload["ts"].endswith("Z")
        # No context fields when none are set
        assert "request_id" not in payload
        assert "user_id" not in payload
        assert "phase" not in payload

    def test_extra_fields_passthrough(self):
        """``extra={...}`` fields land in the JSON payload."""
        formatter = JsonFormatter()
        record = _make_record(
            msg="phase done",
            project_id="abc",
            phase_name="compile",
        )
        out = formatter.format(record)
        payload = json.loads(out)
        assert payload["project_id"] == "abc"
        assert payload["phase_name"] == "compile"

    def test_request_id_from_record_takes_precedence(self):
        """Explicit ``request_id`` on the record wins over the
        contextvar — lets a phase attach a synthetic id when needed."""
        formatter = JsonFormatter()
        record = _make_record(request_id="rec-1")
        token = set_request_id("ctx-1")
        try:
            payload = json.loads(formatter.format(record))
        finally:
            clear_request_id(token)
        assert payload["request_id"] == "rec-1"

    def test_request_id_falls_back_to_contextvar(self):
        formatter = JsonFormatter()
        token = set_request_id("ctx-xyz")
        try:
            payload = json.loads(formatter.format(_make_record()))
        finally:
            clear_request_id(token)
        assert payload["request_id"] == "ctx-xyz"

    def test_user_id_and_phase_via_contextvar(self):
        formatter = JsonFormatter()
        rid = set_request_id("r1")
        uid = set_user_id("u1")
        ph = set_phase("compile")
        try:
            payload = json.loads(formatter.format(_make_record()))
        finally:
            clear_phase(ph)
            clear_user_id(uid)
            clear_request_id(rid)
        assert payload["request_id"] == "r1"
        assert payload["user_id"] == "u1"
        assert payload["phase"] == "compile"

    def test_duration_ms_is_emitted(self):
        formatter = JsonFormatter()
        record = _make_record(duration_ms=42.5)
        payload = json.loads(formatter.format(record))
        assert payload["duration_ms"] == 42.5

    def test_exception_info_serialized(self):
        formatter = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = _make_record(level=logging.ERROR)
            record.exc_info = sys.exc_info()
        payload = json.loads(formatter.format(record))
        assert "exc_info" in payload
        assert "ValueError: boom" in payload["exc_info"]

    def test_non_serializable_extra_uses_default_str(self):
        """Non-JSON-native values fall through ``default=str``."""
        formatter = JsonFormatter()
        from pathlib import Path
        record = _make_record(file=Path("/tmp/example"))
        out = formatter.format(record)
        payload = json.loads(out)
        assert "file" in payload


class TestRequestIdFilter:
    """The filter should pull contextvars onto every record."""

    def test_filter_injects_context(self):
        filt = RequestIdFilter()
        rid = set_request_id("rid-filter")
        uid = set_user_id("uid-filter")
        try:
            record = _make_record()
            assert filt.filter(record) is True
            assert record.request_id == "rid-filter"
            assert record.user_id == "uid-filter"
        finally:
            clear_user_id(uid)
            clear_request_id(rid)


class TestConfigureJsonLogging:
    """The configuration entry point is idempotent and rewires root."""

    def test_replaces_handlers(self):
        root = logging.getLogger()
        # Configure once
        configure_json_logging(level=logging.INFO, stream=io.StringIO())
        first_handlers = list(root.handlers)
        assert any(isinstance(h.formatter, JsonFormatter) for h in first_handlers)
        # Configure again — should not pile up
        configure_json_logging(level=logging.INFO, stream=io.StringIO())
        second_handlers = list(root.handlers)
        assert len(second_handlers) == len(first_handlers)

    def test_emits_valid_json(self):
        stream = io.StringIO()
        configure_json_logging(level=logging.INFO, stream=stream)
        logging.getLogger("json.test").info("structured line")
        # Find our line in the stream
        lines = [ln for ln in stream.getvalue().splitlines() if ln.startswith("{")]
        assert lines, "no JSON line emitted"
        payload = json.loads(lines[-1])
        assert payload["msg"] == "structured line"
        assert payload["level"] == "INFO"


class TestRequestIdMiddleware:
    """Smoke tests for the ASGI middleware."""

    @pytest.mark.asyncio
    async def test_generates_request_id_when_missing(self):
        from app.logging_config import RequestIdMiddleware

        async def app(scope, receive, send):
            # Echo the request id from the contextvar so the test
            # can assert it was bound.
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            })
            await send({
                "type": "http.response.body",
                "body": get_request_id().encode("utf-8"),
            })

        wrapped = RequestIdMiddleware(app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        response_headers = []
        body_parts = []

        async def send(message):
            if message["type"] == "http.response.start":
                response_headers.extend(message.get("headers") or [])
            elif message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))

        await wrapped(scope, receive, send)
        # Response includes X-Request-ID
        rid_header = next(
            (v for k, v in response_headers if k == b"x-request-id"), None
        )
        assert rid_header is not None
        # Body echoes the same id back to the test
        assert body_parts[0] == rid_header
        assert len(rid_header) > 8  # uuid4 hex

    @pytest.mark.asyncio
    async def test_preserves_inbound_request_id(self):
        from app.logging_config import RequestIdMiddleware

        async def app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            })
            await send({
                "type": "http.response.body",
                "body": b"",
            })

        wrapped = RequestIdMiddleware(app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [(b"x-request-id", b"inbound-rid")],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        response_headers = []

        async def send(message):
            if message["type"] == "http.response.start":
                response_headers.extend(message.get("headers") or [])

        await wrapped(scope, receive, send)
        rid_header = next(
            (v for k, v in response_headers if k == b"x-request-id"), None
        )
        assert rid_header == b"inbound-rid"
