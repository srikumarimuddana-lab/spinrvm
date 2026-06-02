"""Unit tests for background/REST breadcrumb persistence (trip-distance capture).

Regression guard for the bug where ``POST /drivers/location-batch`` (the
background task + WS-down REST fallback) dropped every GPS point but the last,
so any backgrounded stretch of a trip produced no ``driver_location_history``
rows. Settled distance and the per-insurance-period SGI audit trail both
undercounted. ``persist_ride_breadcrumbs`` now records the full batch with a
server-derived ``ride_id`` + phase, gated on an active ride.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils.breadcrumbs import persist_ride_breadcrumbs


def _pt(lat, lng, ts, **extra):
    return {"lat": lat, "lng": lng, "timestamp": ts, **extra}


@pytest.mark.asyncio
async def test_persists_all_points_with_derived_trip_phase():
    captured = {}

    async def _get_rows(table, query, **kw):
        assert table == "rides"
        # status filter present so we only match active rides
        assert query.get("driver_id") == "drv_1"
        return [{"id": "ride_1", "status": "in_progress"}]

    async def _insert_many(table, docs):
        captured["table"] = table
        captured["docs"] = docs
        return docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", _insert_many),
    ):
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.45, -104.62, "2026-06-01T23:06:17Z", speed=12, heading=90),
                _pt(50.43, -104.64, "2026-06-01T23:07:17Z"),
                _pt(50.42, -104.65, "2026-06-01T23:08:17Z"),
            ],
        )

    assert n == 3
    assert captured["table"] == "driver_location_history"
    docs = captured["docs"]
    assert len(docs) == 3, "all points persisted, not just the last"
    assert all(d["ride_id"] == "ride_1" for d in docs)
    # in_progress → trip_in_progress, derived server-side (not from client tag)
    assert all(d["tracking_phase"] == "trip_in_progress" for d in docs)
    # client capture timestamps preserved (settlement gap/speed filter needs them)
    assert all(isinstance(d["timestamp"], datetime) for d in docs)
    assert docs[0]["lat"] == 50.45 and docs[0]["lng"] == -104.62


@pytest.mark.asyncio
async def test_no_active_ride_persists_nothing():
    async def _get_rows(table, query, **kw):
        return []

    insert = AsyncMock()
    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", insert),
    ):
        n = await persist_ride_breadcrumbs("drv_1", [_pt(50.45, -104.62, "2026-06-01T23:06:17Z")])

    assert n == 0
    insert.assert_not_called()


@pytest.mark.asyncio
async def test_phase_derived_from_status_navigating():
    async def _get_rows(table, query, **kw):
        return [{"id": "ride_2", "status": "driver_assigned"}]

    captured = {}

    async def _insert_many(table, docs):
        captured["docs"] = docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", _insert_many),
    ):
        n = await persist_ride_breadcrumbs("drv_1", [_pt(50.45, -104.62, "2026-06-01T23:06:17Z")])

    assert n == 1
    assert captured["docs"][0]["tracking_phase"] == "navigating_to_pickup"


@pytest.mark.asyncio
async def test_invalid_and_mocked_points_skipped():
    async def _get_rows(table, query, **kw):
        return [{"id": "ride_3", "status": "in_progress"}]

    captured = {}

    async def _insert_many(table, docs):
        captured["docs"] = docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", _insert_many),
    ):
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.45, -104.62, "2026-06-01T23:06:17Z"),
                {"lat": None, "lng": -104.0, "timestamp": "2026-06-01T23:07:17Z"},  # missing lat
                _pt(50.40, -104.66, "2026-06-01T23:08:17Z", mocked=True),  # spoofed
            ],
        )

    assert n == 1, "invalid + mocked points dropped"
    assert len(captured["docs"]) == 1
