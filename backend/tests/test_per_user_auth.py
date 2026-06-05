"""
Tests for the per-user auth, rate limit, and cost guard layer (P2-1).

Covers:
- Single-secret fallback still works (dev mode unchanged)
- Per-user tokens resolve correctly
- Wrong / missing token in users mode returns 401
- Cross-user isolation: alice's projects are hidden from bob
- Rate limit: 10 OK, 11th = 429 with Retry-After
- Rate limit: different users have separate buckets
- Rate limit: free endpoints (e.g. /api/projects) are not gated
- Cost guard: record + read back usage
- Cost guard: under-budget OK
- Cost guard: over-budget = 429 with X-Reason: budget_exceeded
- Admin usage endpoint returns aggregated stats
- Admin endpoint: non-admin can't read another user's usage

Total: 16 tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ---------------------------------------------------------------------------
# Sample user fixtures
# ---------------------------------------------------------------------------

ALICE = {
    "user_id": "alice",
    "token": "alice-token-xyz",
    "role": "member",
    "rate_limit_per_minute": 10,
    "monthly_token_budget": 100_000,
}
BOB = {
    "user_id": "bob",
    "token": "bob-token-abc",
    "role": "member",
    "rate_limit_per_minute": 10,
    "monthly_token_budget": 1_000,
}
ADMIN = {
    "user_id": "admin",
    "token": "admin-secret",
    "role": "admin",
    "rate_limit_per_minute": 1_000,
    "monthly_token_budget": 10_000_000,
}


def _load_users(*users: dict) -> int:
    from app.users import USER_STORE

    USER_STORE.load_from_list(list(users), source="<test>")
    return USER_STORE.size


@pytest.fixture
def users_mode():
    """Switch to users-file mode for the duration of the test, then
    restore the prior state. Auth is reinitialised on next request."""
    from app import auth as auth_mod
    from app.users import USER_STORE

    had_users = USER_STORE.is_loaded
    prior = list(USER_STORE.all_users()) if had_users else []

    yield USER_STORE

    # Restore: clear + reload prior list, then re-init auth so the
    # next request sees the same state as before the fixture.
    USER_STORE.clear()
    if prior:
        USER_STORE.load_from_list([u.to_public_dict() | {"token": u.token} for u in prior])
    auth_mod.reset_auth_state()
    RATE_LIMITER = auth_mod.__dict__  # type: ignore[attr-defined]
    # Reset all rate-limit buckets + cost-guard totals so the next
    # test starts fresh even if it doesn't use a fresh tmp dir.
    from app.rate_limit import RATE_LIMITER as RL
    from app.cost_guard import COST_GUARD
    RL.reset()
    COST_GUARD.reset()


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    """Point the project storage at a tmp dir so user-isolation
    tests can create real projects without touching the dev store."""
    from app.config import settings
    from app.services import storage
    from app.services.storage import project_storage

    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(storage, "settings", settings, raising=False)
    project_storage.projects_dir = Path(settings.data_dir) / "projects"
    project_storage.projects_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def app_client():
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Single-secret (legacy) mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_secret_fallback_still_works(app_client):
    """With no users.json and a configured ``settings.auth_token``,
    the legacy bearer check still passes — this is the dev default
    all existing tests depend on."""
    from app import auth as auth_mod
    from app.config import settings

    settings.auth_token = "legacy-token"
    auth_mod.settings.auth_token = "legacy-token"
    try:
        resp = await app_client.get(
            "/api/projects", headers={"Authorization": "Bearer legacy-token"}
        )
        assert resp.status_code != 401
    finally:
        settings.auth_token = ""
        auth_mod.settings.auth_token = ""


@pytest.mark.asyncio
async def test_single_secret_wrong_token_returns_401(app_client):
    from app import auth as auth_mod
    from app.config import settings

    settings.auth_token = "legacy-token"
    auth_mod.settings.auth_token = "legacy-token"
    try:
        resp = await app_client.get(
            "/api/projects", headers={"Authorization": "Bearer wrong"}
        )
        assert resp.status_code == 401
    finally:
        settings.auth_token = ""
        auth_mod.settings.auth_token = ""


# ---------------------------------------------------------------------------
# 2. Per-user tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_user_token_resolves_correctly(app_client, users_mode):
    _load_users(ALICE, BOB)
    resp = await app_client.get(
        "/api/projects", headers={"Authorization": f"Bearer {ALICE['token']}"}
    )
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_wrong_token_in_users_mode_returns_401(app_client, users_mode):
    _load_users(ALICE, BOB)
    resp = await app_client.get(
        "/api/projects", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_token_in_users_mode_returns_401(app_client, users_mode):
    _load_users(ALICE, BOB)
    resp = await app_client.get("/api/projects")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_resolved_user_has_expected_role_and_limits(app_client, users_mode):
    """The /api/admin/usage/users endpoint echoes the configured
    per-user settings, so we can verify resolution indirectly without
    exposing ``UserContext`` over HTTP."""
    _load_users(ALICE, ADMIN)
    resp = await app_client.get(
        "/api/admin/usage/users",
        headers={"Authorization": f"Bearer {ADMIN['token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    by_id = {u["user_id"]: u for u in body["users"]}
    assert by_id["alice"]["rate_limit_per_minute"] == 10
    assert by_id["alice"]["monthly_token_budget"] == 100_000
    assert by_id["admin"]["role"] == "admin"


# ---------------------------------------------------------------------------
# 3. Cross-user isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alice_cannot_see_bob_project_in_list(
    app_client, users_mode, isolated_data_dir
):
    """In users mode, the projects list is filtered by the requesting
    user's id. Bob's project must not show up in alice's list."""
    from app.services import project_storage

    _load_users(ALICE, BOB)
    proj = project_storage.create_project(
        seed_paper_id="seed:xxx", name="Bob's", user_id="bob"
    )
    bob_id = proj.id

    alice_resp = await app_client.get(
        "/api/projects", headers={"Authorization": f"Bearer {ALICE['token']}"}
    )
    assert alice_resp.status_code == 200
    ids = [p["id"] for p in alice_resp.json()]
    assert bob_id not in ids, "alice must not see bob's project"

    bob_resp = await app_client.get(
        "/api/projects", headers={"Authorization": f"Bearer {BOB['token']}"}
    )
    assert bob_resp.status_code == 200
    ids_bob = [p["id"] for p in bob_resp.json()]
    assert bob_id in ids_bob


@pytest.mark.asyncio
async def test_alice_cannot_get_bob_project_by_id(
    app_client, users_mode, isolated_data_dir
):
    """``GET /api/projects/{id}`` must 404 when alice requests bob's
    project, even though the project exists on disk."""
    from app.services import project_storage

    _load_users(ALICE, BOB)
    proj = project_storage.create_project(
        seed_paper_id="seed:yyy", name="Bob's 2", user_id="bob"
    )

    resp = await app_client.get(
        f"/api/projects/{proj.id}",
        headers={"Authorization": f"Bearer {ALICE['token']}"},
    )
    assert resp.status_code == 404

    resp_bob = await app_client.get(
        f"/api/projects/{proj.id}",
        headers={"Authorization": f"Bearer {BOB['token']}"},
    )
    assert resp_bob.status_code == 200


# ---------------------------------------------------------------------------
# 4. Rate limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_quota(
    app_client, users_mode, isolated_data_dir
):
    """``rate_limit_per_minute=10`` means the 11th request in the
    same minute must return 429 with a ``Retry-After`` header."""
    from app.rate_limit import RATE_LIMITER
    RATE_LIMITER.reset()

    _load_users(ALICE)

    # Burn 10 successful requests (GET /api/projects is not LLM-gated,
    # so it's only protected by the app-level bearer, not the LLM
    # guard. We have to hit an LLM endpoint to trip the LLM guard,
    # but in this test we go through ``enforce_llm_guard`` directly
    # so we don't need a real LLM.)
    from app.rate_limit import enforce_llm_guard
    from app.users import USER_STORE

    alice = USER_STORE.find_by_id("alice")
    for i in range(10):
        enforce_llm_guard(alice, "test.phase")
    # 11th call must raise
    with pytest.raises(Exception) as excinfo:
        enforce_llm_guard(alice, "test.phase")
    assert excinfo.value.status_code == 429
    assert "Retry-After" in (excinfo.value.headers or {})


@pytest.mark.asyncio
async def test_rate_limit_buckets_are_per_user(users_mode):
    """Alice and Bob each get their own bucket. Burning alice's
    quota must not block bob."""
    from app.rate_limit import RATE_LIMITER, enforce_llm_guard
    from app.users import USER_STORE

    RATE_LIMITER.reset()
    _load_users(ALICE, BOB)

    alice = USER_STORE.find_by_id("alice")
    bob = USER_STORE.find_by_id("bob")
    for _ in range(10):
        enforce_llm_guard(alice, "test.phase")
    with pytest.raises(Exception):
        enforce_llm_guard(alice, "test.phase")
    # Bob still has full quota
    enforce_llm_guard(bob, "test.phase")


@pytest.mark.asyncio
async def test_rate_limit_does_not_apply_to_free_endpoints(
    app_client, users_mode, isolated_data_dir
):
    """``GET /api/projects`` is not an LLM-calling endpoint, so the
    rate-limit guard does not gate it. Even a tight limit (e.g. 2
    req/min) should not 429 the listing endpoint."""
    tiny = {
        **ALICE,
        "rate_limit_per_minute": 2,
    }
    _load_users(tiny)
    headers = {"Authorization": f"Bearer {tiny['token']}"}
    for _ in range(5):
        resp = await app_client.get("/api/projects", headers=headers)
        assert resp.status_code != 429


# ---------------------------------------------------------------------------
# 5. Cost guard
# ---------------------------------------------------------------------------


def test_cost_guard_records_and_aggregates(tmp_path):
    """Direct test of the cost-guard layer (no HTTP). Two records
    on the same day for the same user must sum."""
    from app.cost_guard import CostGuard
    from datetime import datetime, timezone

    cg = CostGuard(base_dir=str(tmp_path))
    cg.record("alice", "draft.research", 100, 50)
    cg.record("alice", "draft.compose", 200, 80)
    summary = cg.get_summary("alice")
    assert summary.total_tokens == 430
    assert summary.prompt_tokens == 300
    assert summary.completion_tokens == 130
    assert summary.call_count == 2
    assert summary.by_phase["draft.research"] == 150
    assert summary.by_phase["draft.compose"] == 280


def test_cost_guard_budget_enforcement(tmp_path):
    from app.cost_guard import CostGuard

    cg = CostGuard(base_dir=str(tmp_path))
    cg.record("alice", "draft.research", 800, 100)  # 900 tokens
    under, used, budget = cg.check_budget("alice", 1000)
    assert (under, used, budget) == (True, 900, 1000)
    # Push over
    cg.record("alice", "draft.compose", 200, 0)  # 1100 total
    under, used, _ = cg.check_budget("alice", 1000)
    assert (under, used) == (False, 1100)


@pytest.mark.asyncio
async def test_budget_exceeded_returns_429(users_mode, tmp_path):
    """A user with a tight budget hits 429 from enforce_llm_guard
    once their cumulative spend crosses the line."""
    from app.rate_limit import RATE_LIMITER, enforce_llm_guard
    from app.cost_guard import CostGuard
    from app.users import USER_STORE
    from app import cost_guard as cg_mod

    RATE_LIMITER.reset()
    # Patch the global cost guard to use the tmp dir so we don't
    # pollute the dev data/usage/ tree.
    original_guard = cg_mod.COST_GUARD
    cg_mod.COST_GUARD = CostGuard(base_dir=str(tmp_path))
    try:
        _load_users(BOB)  # monthly_token_budget = 1_000
        bob = USER_STORE.find_by_id("bob")
        # Pre-fill usage so bob is over budget
        cg_mod.COST_GUARD.record("bob", "draft.research", 800, 300)
        with pytest.raises(Exception) as excinfo:
            enforce_llm_guard(bob, "draft.compose")
        assert excinfo.value.status_code == 429
        assert (excinfo.value.headers or {}).get("X-Reason") == "budget_exceeded"
    finally:
        cg_mod.COST_GUARD = original_guard


# ---------------------------------------------------------------------------
# 6. Admin usage endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_usage_returns_aggregated_stats(
    app_client, users_mode, tmp_path
):
    from app import cost_guard as cg_mod
    from app.routers import admin as admin_mod
    from app.cost_guard import CostGuard

    _load_users(ADMIN, ALICE)
    original_guard = cg_mod.COST_GUARD
    original_admin = admin_mod.COST_GUARD
    test_guard = CostGuard(base_dir=str(tmp_path))
    cg_mod.COST_GUARD = test_guard
    admin_mod.COST_GUARD = test_guard
    try:
        test_guard.record("alice", "draft.research", 200, 100)
        test_guard.record("alice", "draft.compose", 300, 50)

        resp = await app_client.get(
            "/api/admin/usage?user_id=alice",
            headers={"Authorization": f"Bearer {ADMIN['token']}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "alice"
        assert body["summary"]["total_tokens"] == 650
        assert body["summary"]["call_count"] == 2
        assert body["budget"] == 100_000
        assert body["under_budget"] is True
    finally:
        cg_mod.COST_GUARD = original_guard
        admin_mod.COST_GUARD = original_admin


@pytest.mark.asyncio
async def test_non_admin_cannot_read_other_users_usage(
    app_client, users_mode, tmp_path
):
    """A non-admin user may read their own usage but not someone
    else's. This is enforced in routers/admin.py."""
    _load_users(ALICE, ADMIN)
    resp = await app_client.get(
        "/api/admin/usage?user_id=admin",
        headers={"Authorization": f"Bearer {ALICE['token']}"},
    )
    assert resp.status_code == 403

    # But alice can read her own usage
    resp2 = await app_client.get(
        "/api/admin/usage?user_id=alice",
        headers={"Authorization": f"Bearer {ALICE['token']}"},
    )
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_admin_usage_invalid_month_returns_400(app_client, users_mode):
    _load_users(ADMIN)
    resp = await app_client.get(
        "/api/admin/usage?user_id=alice&month=2026/06",
        headers={"Authorization": f"Bearer {ADMIN['token']}"},
    )
    assert resp.status_code == 400
