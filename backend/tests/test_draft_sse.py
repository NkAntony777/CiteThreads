"""
Tests for the Server-Sent Events progress stream of the draft pipeline.

Coverage
--------
- The ``/stream`` endpoint serves ``text/event-stream`` and the
  opening comment is emitted before any data.
- A subscriber receives events in the order the runner publishes them.
- Two concurrent subscribers on the same project each receive every
  event (no event lost, no cross-talk).
- Client disconnect cleans up the subscription (no leaked queue on
  the bus).
- A long pause between events triggers a ``: heartbeat`` keepalive
  comment so intermediate proxies don't drop the connection.
- The stream ends naturally after a ``done`` event is published.
- Auth: ``/stream`` requires a bearer token (401 without).
- 404: unknown project, 400: malformed project id.
- The runner-published events flow through the endpoint unchanged.

We use the same LLM stubs as ``test_draft_router.py`` so the runner
runs offline; the streaming endpoint itself is exercised via
``httpx.AsyncClient`` against the in-process FastAPI app.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, AsyncIterator, List

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ---------------------------------------------------------------------------
# Auth + project fixtures (mirrored from test_draft_router.py)
# ---------------------------------------------------------------------------


def _set_auth(token: str) -> None:
    from app.config import settings
    from app import auth as auth_mod

    settings.auth_token = token
    auth_mod.settings.auth_token = token


@pytest.fixture
def auth_token():
    _set_auth("sse-test-token")
    yield "sse-test-token"
    _set_auth("")


@pytest.fixture
def no_auth():
    _set_auth("")
    yield
    _set_auth("")


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import storage
    from app.services.storage import project_storage

    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(storage, "settings", settings, raising=False)
    project_storage.projects_dir = Path(settings.data_dir) / "projects"
    project_storage.projects_dir.mkdir(parents=True, exist_ok=True)

    proj = project_storage.create_project(
        seed_paper_id="seed:abc",
        name="SSE Test Project",
        depth=1,
        direction="both",
    )
    yield {"project_id": proj.id, "tmp_path": tmp_path}


@pytest.fixture
async def app_client():
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Bus-level tests (no HTTP): events flow in order, multi-subscriber, cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_subscribe_receives_events_in_publish_order():
    """Events published on a bus are delivered to a subscriber queue
    in the order they were published."""
    from app.services.draft_pipeline.progress import (
        Event,
        ProgressBus,
        EVT_PHASE_START,
        EVT_PHASE_END,
    )

    bus = ProgressBus("bus-order-test")
    q = await bus.subscribe()
    await bus.publish(
        Event(type=EVT_PHASE_START, data={"phase": "research"})
    )
    await bus.publish(
        Event(type=EVT_PHASE_END, data={"phase": "research", "status": "ok"})
    )
    e1 = await asyncio.wait_for(q.get(), timeout=1.0)
    e2 = await asyncio.wait_for(q.get(), timeout=1.0)
    assert e1.type == EVT_PHASE_START
    assert e2.type == EVT_PHASE_END
    assert e2.data["status"] == "ok"
    await bus.unsubscribe(q)


@pytest.mark.asyncio
async def test_bus_multi_subscriber_each_gets_every_event():
    """Two concurrent subscribers each receive every published event,
    with no cross-talk and no drops."""
    from app.services.draft_pipeline.progress import (
        Event,
        ProgressBus,
    )

    bus = ProgressBus("bus-multi-test")
    q1 = await bus.subscribe()
    q2 = await bus.subscribe()
    assert bus.subscriber_count() == 2

    n_events = 5
    for i in range(n_events):
        await bus.publish(
            Event(type="phase-progress", data={"i": i})
        )
    received_1 = []
    received_2 = []
    for _ in range(n_events):
        e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        received_1.append(e1.data["i"])
        received_2.append(e2.data["i"])
    assert received_1 == list(range(n_events))
    assert received_2 == list(range(n_events))
    await bus.unsubscribe(q1)
    await bus.unsubscribe(q2)


@pytest.mark.asyncio
async def test_bus_unsubscribe_stops_delivery():
    """After unsubscribe, no further events are delivered to that
    subscriber; the bus's count drops accordingly."""
    from app.services.draft_pipeline.progress import (
        Event,
        ProgressBus,
    )

    bus = ProgressBus("bus-unsub-test")
    q = await bus.subscribe()
    assert bus.subscriber_count() == 1
    await bus.publish(Event(type="phase-start", data={}))
    assert (await asyncio.wait_for(q.get(), timeout=1.0)).type == "phase-start"

    await bus.unsubscribe(q)
    assert bus.subscriber_count() == 0

    # A subsequent publish should NOT add anything to q (we'll prove
    # this by ensuring q.get_nowait() raises QueueEmpty).
    await bus.publish(Event(type="phase-end", data={}))
    import asyncio as _a
    with pytest.raises(_a.QueueEmpty):
        q.get_nowait()


@pytest.mark.asyncio
async def test_bus_drops_events_for_slow_subscriber():
    """When a subscriber's queue is full, additional events for that
    subscriber are dropped (logged at WARNING) without affecting
    other subscribers.

    We construct q_slow manually with maxsize=1 and inject it via
    the bus's internal ``_subscribers`` list, since the public API
    creates every queue with the same maxsize.
    """
    from app.services.draft_pipeline.progress import (
        Event,
        ProgressBus,
    )

    bus = ProgressBus("bus-overflow-test")
    # Default subscriber with the bus's queue_maxsize (no overflow).
    q_fast = await bus.subscribe()
    # Manually create a small queue and register it as a slow
    # subscriber so we can verify per-subscriber drop semantics.
    q_slow: asyncio.Queue = asyncio.Queue(maxsize=1)
    async with bus._lock:
        bus._subscribers.append(q_slow)

    # Publish 3 events. q_slow will keep only the first; q_fast sees
    # all 3.
    for i in range(3):
        await bus.publish(Event(type="phase-progress", data={"i": i}))

    seen = [
        (await asyncio.wait_for(q_fast.get(), timeout=1.0)).data["i"]
        for _ in range(3)
    ]
    assert seen == [0, 1, 2]
    # q_slow only has 1 item (the first).
    assert (await asyncio.wait_for(q_slow.get(), timeout=1.0)).data["i"] == 0
    import asyncio as _a
    with pytest.raises(_a.QueueEmpty):
        q_slow.get_nowait()
    # Cleanup.
    await bus.unsubscribe(q_fast)
    async with bus._lock:
        try:
            bus._subscribers.remove(q_slow)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Generator tests: format + heartbeat + done-termination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_event_stream_emits_opening_comment_and_event():
    """The generator's first emitted frame is the ``: stream-open``
    comment; subsequent events follow SSE format with the event name
    and a JSON data payload."""
    from app.services.draft_pipeline.progress import (
        Event,
        ProgressBus,
        EVT_PHASE_START,
    )
    from app.routers.draft import _draft_event_stream

    bus = ProgressBus("sse-open-test")
    gen = _draft_event_stream("sse-open-test", bus, keepalive_interval=0.5)
    # First yield: opening comment.
    first = await gen.__anext__()
    assert first.startswith(": stream-open")

    # Publish an event from a concurrent task.
    async def _push():
        await asyncio.sleep(0.05)
        await bus.publish(
            Event(type=EVT_PHASE_START, data={"phase": "research"})
        )

    push_task = asyncio.create_task(_push())
    second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    push_task.cancel()
    try:
        await push_task
    except (asyncio.CancelledError, Exception):
        pass
    assert second.startswith("event: phase-start\n")
    # data line is JSON-decodable.
    data_line = second.splitlines()[1]
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["type"] == "phase-start"
    assert payload["data"]["phase"] == "research"
    # Don't drain the generator: just close it.
    await gen.aclose()


@pytest.mark.asyncio
async def test_draft_event_stream_emits_heartbeat_after_idle():
    """When no event arrives within ``keepalive_interval``, the
    generator emits a ``: heartbeat`` SSE comment so the connection
    stays warm."""
    from app.services.draft_pipeline.progress import ProgressBus
    from app.routers.draft import _draft_event_stream

    bus = ProgressBus("sse-heartbeat-test")
    gen = _draft_event_stream(
        "sse-heartbeat-test", bus, keepalive_interval=0.1
    )
    # First yield: opening comment.
    first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert first.startswith(": stream-open")
    # Now wait > keepalive_interval with no events; we should get a heartbeat.
    hb = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert hb.startswith(": heartbeat")
    await gen.aclose()


@pytest.mark.asyncio
async def test_draft_event_stream_terminates_on_done_event():
    """When a ``done`` event is published, the generator emits the
    event, then a ``: stream-end`` comment, then returns."""
    from app.services.draft_pipeline.progress import (
        Event,
        ProgressBus,
        EVT_DONE,
    )
    from app.routers.draft import _draft_event_stream

    bus = ProgressBus("sse-done-test")
    gen = _draft_event_stream("sse-done-test", bus, keepalive_interval=0.5)
    # Open + data.
    await gen.__anext__()

    async def _push_done():
        await asyncio.sleep(0.05)
        await bus.publish(Event(type=EVT_DONE, data={"status": "completed"}))

    push_task = asyncio.create_task(_push_done())
    frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert frame.startswith("event: done\n")
    end = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert end.startswith(": stream-end")
    push_task.cancel()
    try:
        await push_task
    except (asyncio.CancelledError, Exception):
        pass
    # Next call should raise StopAsyncIteration.
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)


# ---------------------------------------------------------------------------
# HTTP-level tests: /stream endpoint behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_endpoint_requires_auth(
    app_client, auth_token, isolated_data_dir
):
    """With auth enabled, a request without ``Authorization`` is
    rejected with 401 *before* the SSE generator starts streaming."""
    pid = isolated_data_dir["project_id"]
    resp = await app_client.get(f"/api/draft/projects/{pid}/stream")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_endpoint_404_for_unknown_project(
    app_client, auth_token
):
    resp = await app_client.get(
        "/api/draft/projects/no-such-project/stream",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stream_endpoint_400_for_malformed_project_id(
    app_client, auth_token
):
    resp = await app_client.get(
        "/api/draft/projects/has..bad..chars/stream",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_stream_endpoint_serves_text_event_stream(
    app_client, auth_token, isolated_data_dir
):
    """The endpoint advertises ``text/event-stream`` and returns 200;
    the body includes the opening comment and any events that
    arrived on the bus while the client was connected."""
    pid = isolated_data_dir["project_id"]
    from app.services.draft_pipeline.progress import (
        Event,
        get_bus,
    )

    # Use a small keepalive so the test runs in <1s.
    from app.routers import draft as draft_router_mod
    original = draft_router_mod.SSE_KEEPALIVE_INTERVAL_S
    draft_router_mod.SSE_KEEPALIVE_INTERVAL_S = 0.05
    try:
        bus = await get_bus(pid)

        async def _push():
            await asyncio.sleep(0.1)
            await bus.publish(
                Event(type="phase-start", data={"phase": "research"})
            )
            await bus.publish(
                Event(type="done", data={"status": "completed"})
            )

        push_task = asyncio.create_task(_push())

        async def _do_get():
            return await app_client.get(
                f"/api/draft/projects/{pid}/stream",
                headers={"Authorization": f"Bearer {auth_token}"},
            )

        get_task = asyncio.create_task(_do_get())
        # Allow the events to flow through + done to terminate the
        # stream. A 1.5s budget is plenty for the small keepalive.
        resp = await asyncio.wait_for(get_task, timeout=1.5)
        # The SSE generator returns naturally after 'done', so the
        # response is fully buffered. The push task should also be
        # finished by now.
        try:
            await asyncio.wait_for(push_task, timeout=0.5)
        except asyncio.TimeoutError:
            push_task.cancel()

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert ": stream-open" in body
        assert "event: phase-start" in body
        assert "event: done" in body
    finally:
        draft_router_mod.SSE_KEEPALIVE_INTERVAL_S = original


@pytest.mark.asyncio
async def test_stream_endpoint_handles_multiple_concurrent_clients(
    app_client, auth_token, isolated_data_dir
):
    """Two concurrent clients on the same project each receive every
    event published on the bus. Proves the multi-subscriber fan-out
    works end-to-end through FastAPI's ASGI transport."""
    from app.services.draft_pipeline.progress import (
        Event,
        get_bus,
    )
    pid = isolated_data_dir["project_id"]
    bus = await get_bus(pid)

    # Use a small keepalive so the test runs in <1s.
    from app.routers import draft as draft_router_mod
    original = draft_router_mod.SSE_KEEPALIVE_INTERVAL_S
    draft_router_mod.SSE_KEEPALIVE_INTERVAL_S = 0.05
    try:
        async def _push():
            await asyncio.sleep(0.1)
            await bus.publish(
                Event(type="phase-start", data={"phase": "research"})
            )
            await bus.publish(
                Event(type="done", data={"status": "ok"})
            )

        push_task = asyncio.create_task(_push())

        async def _do_get():
            return await app_client.get(
                f"/api/draft/projects/{pid}/stream",
                headers={"Authorization": f"Bearer {auth_token}"},
            )

        h = {"Authorization": f"Bearer {auth_token}"}
        g1 = asyncio.create_task(_do_get())
        g2 = asyncio.create_task(_do_get())
        r1, r2 = await asyncio.wait_for(
            asyncio.gather(g1, g2), timeout=1.5
        )
        try:
            await asyncio.wait_for(push_task, timeout=0.5)
        except asyncio.TimeoutError:
            push_task.cancel()

        assert r1.status_code == 200
        assert r2.status_code == 200
        for resp in (r1, r2):
            body = resp.text
            assert ": stream-open" in body
            assert "event: phase-start" in body
            assert "event: done" in body
            assert '"phase": "research"' in body
    finally:
        draft_router_mod.SSE_KEEPALIVE_INTERVAL_S = original


@pytest.mark.asyncio
async def test_stream_generator_unsubscribes_on_cancellation(
    isolated_data_dir,
):
    """The ``_draft_event_stream`` generator's ``finally`` clause
    unsubscribes from the bus when the consumer cancels (simulating
    a client disconnect)."""
    from app.services.draft_pipeline.progress import ProgressBus
    from app.routers.draft import _draft_event_stream

    bus = ProgressBus("disconnect-test")
    pre_count = bus.subscriber_count()
    assert pre_count == 0

    gen = _draft_event_stream(
        "disconnect-test", bus, keepalive_interval=0.05
    )
    first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert first.startswith(": stream-open")
    # Drive the generator one more step so that the body of the
    # function runs past the opening yield — that's where
    # ``bus.subscribe()`` is awaited.
    second_task = asyncio.create_task(gen.__anext__())
    # Give the generator a tick to subscribe.
    for _ in range(20):
        if bus.subscriber_count() == pre_count + 1:
            break
        await asyncio.sleep(0.02)
    assert bus.subscriber_count() == pre_count + 1
    # Cancel the in-flight consumer task to trigger the finally.
    second_task.cancel()
    try:
        await second_task
    except (asyncio.CancelledError, StopAsyncIteration, Exception):
        pass
    # Also aclose the generator itself to be sure.
    await gen.aclose()
    # Give the event loop a tick to process the cleanup.
    for _ in range(20):
        if bus.subscriber_count() == pre_count:
            break
        await asyncio.sleep(0.02)
    assert bus.subscriber_count() == pre_count


# ---------------------------------------------------------------------------
# End-to-end: runner publishes -> SSE delivers
# ---------------------------------------------------------------------------


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockCompletions:
    def __init__(self, scripted: List[str]):
        self._scripted = list(scripted)
        self.calls: List[dict] = []

    async def create(self, *, model, messages, temperature, max_tokens):
        self.calls.append({})
        if not self._scripted:
            raise RuntimeError("MockCompletions ran out")
        return _MockResponse(self._scripted.pop(0))


class _MockLLM:
    def __init__(self, scripted: List[str]):
        self.chat = type(
            "Chat", (), {"completions": _MockCompletions(scripted)}
        )()


_REORDER = json.dumps(
    [{"id": "openalex:W100", "relevance_score": "High", "why_relevant": "core"}]
)
_SCRIBE = json.dumps(
    [
        {
            "paper_id": "openalex:W100",
            "research_question": "rq",
            "methodology": "m",
            "key_findings": ["k"],
            "implications": "i",
            "limitations": ["l"],
            "relevance_score": 4,
            "relevance_reason": "r",
        }
    ]
)
_SIGNAL = json.dumps(
    {
        "gaps": [
            {
                "title": "g",
                "description": "d",
                "gap_type": "methodological",
                "difficulty": "Low",
                "impact": 3,
                "suggested_approach": "sa",
            }
        ],
        "emerging_trends": ["t"],
        "novel_angles": ["a"],
    }
)


@pytest.mark.asyncio
async def test_runner_events_arrive_via_stream_endpoint(
    app_client, auth_token, isolated_data_dir, monkeypatch
):
    """End-to-end: subscribe to /stream, kick off a research phase
    on the same project, and confirm phase-start / phase-end arrive
    in the SSE body in the expected order."""
    from app.services import llm_factory
    from app.services.paper_search_service import (
        SearchResult,
        UnifiedPaperSearchService,
    )
    from app.models import Paper
    from app.services.draft_pipeline.runner import DraftRunner
    from app.services.draft_pipeline.progress import (
        EVT_PHASE_START,
        EVT_PHASE_END,
        Event as ProgressEvent,
        get_bus,
    )

    pid = isolated_data_dir["project_id"]
    bus = await get_bus(pid)

    # Wire up the LLM mock.
    script = [_REORDER, _SCRIBE, _SIGNAL]
    client = _MockLLM(script)
    monkeypatch.setattr(
        llm_factory, "create_llm_client", lambda *a, **kw: client
    )
    from app.routers import draft as draft_router_mod
    monkeypatch.setattr(
        draft_router_mod, "create_llm_client", lambda *a, **kw: client
    )

    async def _fake_search(self, query, sources=None, filters=None, limit=20):
        return SearchResult(
            papers=[
                Paper(
                    id="openalex:W100",
                    title="T",
                    authors=["A"],
                    year=2023,
                    abstract="abs",
                ),
            ],
            errors={},
            sources_searched=["openalex"],
        )

    monkeypatch.setattr(UnifiedPaperSearchService, "search", _fake_search)

    # Use a small keepalive so the test runs in <2s.
    original = draft_router_mod.SSE_KEEPALIVE_INTERVAL_S
    draft_router_mod.SSE_KEEPALIVE_INTERVAL_S = 0.05
    try:
        h = {"Authorization": f"Bearer {auth_token}"}

        async def _do_get():
            return await app_client.get(
                f"/api/draft/projects/{pid}/stream", headers=h
            )

        stream_resp_task = asyncio.create_task(_do_get())
        # Give the ASGI server a moment to register the subscriber.
        await asyncio.sleep(0.2)

        # Run a research phase; the runner publishes to the bus
        # that the stream is subscribed to.
        from app.services.draft_pipeline import PhaseName
        runner = DraftRunner(
            project_id=pid, llm_client=client, event_bus=bus
        )
        await runner.run_phase(PhaseName.RESEARCH)
        # run_phase doesn't emit 'done'; publish one to terminate.
        await bus.publish(
            ProgressEvent(type="done", project_id=pid, data={"status": "ok"})
        )

        resp = await asyncio.wait_for(stream_resp_task, timeout=2.0)
        assert resp.status_code == 200
        body = resp.text
        assert ": stream-open" in body
        start_idx = body.find("event: phase-start")
        end_idx = body.find("event: phase-end")
        assert start_idx != -1
        assert end_idx != -1
        assert start_idx < end_idx
        assert '"phase": "research"' in body
        assert '"status": "succeeded"' in body
    finally:
        draft_router_mod.SSE_KEEPALIVE_INTERVAL_S = original
