"""
Admin router — usage and budget visibility
==========================================

Exposes the per-user token-usage ledger that the :mod:`cost_guard`
maintains. The endpoint is gated behind the same bearer-auth
dependency the rest of the API uses; in users-file mode it is
further restricted to ``role=admin``. In single-secret / dev mode
the dev token is treated as admin so the endpoint is reachable
during local testing.

Endpoints (all under ``/api/admin``):

- ``GET /api/admin/usage?user_id=X&month=YYYY-MM`` — aggregated
  token usage for one user/month. Returns the running total,
  per-phase breakdown, and the user's current budget state.
- ``GET /api/admin/usage/users`` — list of users the admin has
  usage data for (walks ``data/usage/`` directory tree).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..auth import UserAuthDep
from ..cost_guard import COST_GUARD, UsageSummary
from ..users import ANONYMOUS_ADMIN, USER_STORE, UserContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class UsageReport(BaseModel):
    """The response shape for ``GET /api/admin/usage``."""

    user_id: str
    month: str
    summary: UsageSummary
    budget: int
    under_budget: bool
    rate_limit_per_minute: int


class UserListItem(BaseModel):
    user_id: str
    role: str
    rate_limit_per_minute: int
    monthly_token_budget: int
    has_usage_data: bool


class UserListResponse(BaseModel):
    users: List[UserListItem]


def _require_admin(user: UserContext) -> None:
    """Raise 403 when the caller is not an admin. In dev / single-secret
    mode the ANONYMOUS_ADMIN is allowed through."""
    if user.is_admin:
        return
    if user.user_id == ANONYMOUS_ADMIN.user_id:
        return
    raise HTTPException(status_code=403, detail="admin only")


@router.get("/usage", response_model=UsageReport)
async def get_user_usage(
    user_id: str = Query(..., min_length=1),
    month: Optional[str] = Query(
        None,
        description="YYYY-MM (defaults to current month in UTC)",
    ),
    user: UserContext = UserAuthDep,
) -> UsageReport:
    """Return aggregated token usage and budget state for ``user_id``.

    In users-file mode only ``role=admin`` may call this endpoint
    for *other* users; non-admins may read their own usage. In
    single-secret / dev mode any caller with the configured token
    passes through.
    """
    if user.user_id != user_id and not (user.is_admin or user.user_id == ANONYMOUS_ADMIN.user_id):
        raise HTTPException(
            status_code=403,
            detail="only admins may read other users' usage",
        )

    try:
        summary = COST_GUARD.get_summary(user_id, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Pull the configured budget from the user store, falling back
    # to the dev defaults for legacy/anonymous callers.
    stored = USER_STORE.find_by_id(user_id)
    if stored is not None:
        budget = stored.monthly_token_budget
        rate_limit = stored.rate_limit_per_minute
    else:
        # Unknown user — surface whatever budget the caller has
        # so the response still makes sense (the caller may be an
        # admin inspecting a forgotten id).
        budget = ANONYMOUS_ADMIN.monthly_token_budget
        rate_limit = ANONYMOUS_ADMIN.rate_limit_per_minute

    return UsageReport(
        user_id=user_id,
        month=summary.month,
        summary=summary,
        budget=budget,
        under_budget=summary.total_tokens < budget,
        rate_limit_per_minute=rate_limit,
    )


@router.get("/usage/users", response_model=UserListResponse)
async def list_usage_users(
    user: UserContext = UserAuthDep,
) -> UserListResponse:
    """List users visible to the admin (configured users + anyone
    with usage data on disk)."""
    _require_admin(user)

    seen: dict[str, UserListItem] = {}

    # 1) Configured users
    for u in USER_STORE.all_users():
        seen[u.user_id] = UserListItem(
            user_id=u.user_id,
            role=u.role,
            rate_limit_per_minute=u.rate_limit_per_minute,
            monthly_token_budget=u.monthly_token_budget,
            has_usage_data=False,  # updated below
        )

    # 2) Users with usage data on disk but not in the store
    base = Path(os.environ.get("CITETHREADS_DATA_DIR", "./data")) / "usage"
    if base.is_dir():
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            uid = entry.name
            if uid in seen:
                seen[uid].has_usage_data = True
            else:
                seen[uid] = UserListItem(
                    user_id=uid,
                    role="unknown",
                    rate_limit_per_minute=0,
                    monthly_token_budget=0,
                    has_usage_data=True,
                )

    return UserListResponse(users=sorted(seen.values(), key=lambda u: u.user_id))


__all__ = ["router"]
