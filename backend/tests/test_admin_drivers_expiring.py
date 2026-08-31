from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_admin_get_expiring_documents_filters_rides_by_ride_completed_at():
    """The rides table uses ride_completed_at, not completed_at."""
    from routes.admin import drivers as admin_drivers

    now = datetime.now(timezone.utc)
    driver = {
        "id": "driver-1",
        "user_id": "user-1",
        "status": "active",
        "service_area_id": "area-1",
        "license_expiry_date": (now + timedelta(days=10)).isoformat(),
    }
    user = {
        "id": "user-1",
        "first_name": "Alex",
        "last_name": "Driver",
        "email": "alex@example.test",
    }
    area = {"id": "area-1", "name": "Saskatoon"}
    ride = {
        "id": "ride-1",
        "driver_id": "driver-1",
        "status": "completed",
        "ride_completed_at": (now - timedelta(days=1)).isoformat(),
    }

    async def fake_get_rows(table, filters=None, **kwargs):
        if table == "drivers":
            return [driver]
        if table == "users":
            return [user]
        if table == "service_areas":
            return [area]
        if table == "rides":
            assert "ride_completed_at" in filters
            assert "completed_at" not in filters
            assert filters["ride_completed_at"]["$gte"]
            return [ride]
        raise AssertionError(f"unexpected table: {table}")

    async def fake_get_rows_batched_in(table, column, values, extra_filters=None, *, columns="*", limit=None, batch_size=200):
        # See #4783: get_rows_batched_in calls its own module's internal
        # get_rows, bypassing a patch on db_supabase.get_rows alone.
        vals = list(values)
        if not vals:
            return []
        filters = dict(extra_filters or {})
        filters[column] = {"$in": vals}
        rows = await get_rows(table, filters, limit=limit or 1000, offset=0, columns=columns)
        return rows if limit is None else rows[:limit]

    get_rows = AsyncMock(side_effect=fake_get_rows)
    with (
        patch.object(admin_drivers.db_supabase, "get_rows", new=get_rows),
        patch.object(admin_drivers.db_supabase, "get_rows_batched_in", new=AsyncMock(side_effect=fake_get_rows_batched_in)),
    ):
        result = await admin_drivers.admin_get_expiring_documents(window_days=30)

    assert result["items"][0]["rides_last_30d"] == 1
    rides_call = next(call for call in get_rows.await_args_list if call.args[0] == "rides")
    rides_filters = rides_call.args[1]
    assert rides_filters == {
        "driver_id": {"$in": ["driver-1"]},
        "status": "completed",
        "ride_completed_at": {"$gte": rides_filters["ride_completed_at"]["$gte"]},
    }
