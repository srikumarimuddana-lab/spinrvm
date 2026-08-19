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


@pytest.mark.anyio
async def test_rollup_rejects_today():
    today = datetime.now(timezone.utc).date().isoformat()
    with pytest.raises(HTTPException) as exc_info:
        await maintenance_mod.admin_rollup_driver_daily(target_date=today)
    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_rollup_rejects_future_date():
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    with pytest.raises(HTTPException) as exc_info:
        await maintenance_mod.admin_rollup_driver_daily(target_date=tomorrow)
    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_rollup_accepts_yesterday():
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    with patch.object(maintenance_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
        result = await maintenance_mod.admin_rollup_driver_daily(target_date=yesterday, admin={"id": "test_admin"})
    assert result.get("stat_date") == yesterday or result.get("success") is not False
