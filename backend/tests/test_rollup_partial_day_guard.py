"""
Partial-day rollup guard for admin_rollup_driver_daily.

get_driver_leaderboard tops up rides newer than MAX(stat_date) in
driver_daily_stats, treating that day as fully covered. A rollup for the
current (still-running) UTC day would write a partial stat row, making the
top-up boundary skip every ride completed later that day — silently
undercounting earnings until the next nightly pass. The endpoint therefore
rejects target_date >= today (UTC) with a 422.

Run:
    pytest backend/tests/test_rollup_partial_day_guard.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

try:
    from backend.routes.admin import maintenance as maintenance_mod
except ImportError:
    from routes.admin import maintenance as maintenance_mod  # type: ignore


try:
    from backend.utils.driver_activity import REGINA_TZ
except ImportError:
    from utils.driver_activity import REGINA_TZ  # type: ignore


def _regina_today():
    """The guard is Regina-date based: between 00:00 and 06:00 UTC the UTC
    calendar is already a day ahead of Saskatchewan, and rejecting/accepting
    on UTC dates would make this suite flaky by wall clock."""
    return datetime.now(timezone.utc).astimezone(REGINA_TZ).date()


@pytest.mark.anyio
async def test_rollup_rejects_regina_today():
    today = _regina_today().isoformat()
    with pytest.raises(HTTPException) as exc_info:
        await maintenance_mod.admin_rollup_driver_daily(target_date=today)
    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_rollup_rejects_future_date():
    tomorrow = (_regina_today() + timedelta(days=1)).isoformat()
    with pytest.raises(HTTPException) as exc_info:
        await maintenance_mod.admin_rollup_driver_daily(target_date=tomorrow)
    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_rollup_accepts_regina_yesterday():
    yesterday = (_regina_today() - timedelta(days=1)).isoformat()
    with patch.object(maintenance_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
        result = await maintenance_mod.admin_rollup_driver_daily(target_date=yesterday)
    assert result.get("stat_date") == yesterday


@pytest.mark.anyio
async def test_rollup_default_is_regina_yesterday():
    """No target_date → the endpoint rolls up the newest COMPLETED Regina
    day, never the in-progress one."""
    yesterday = (_regina_today() - timedelta(days=1)).isoformat()
    with patch.object(maintenance_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
        result = await maintenance_mod.admin_rollup_driver_daily(target_date=None)
    assert result.get("stat_date") == yesterday
