"""Unit tests for background/REST breadcrumb persistence (trip-distance capture).

Regression guards:
  - full batch persisted (not just the last point)
  - per-point, server-authoritative phase from each point's OWN capture
    timestamp — a batch spanning pickup→trip must bill the navigation points as
    navigation, not inflate trip_in_progress
  - stale points discarded: other-ride ride_id, or captured before this ride
  - batch capped at MAX_BREADCRUMB_BATCH (no unbounded REST insert)
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils.breadcrumbs import MAX_BREADCRUMB_BATCH, persist_ride_breadcrumbs

ACCEPTED = "2026-06-01T23:00:00Z"
ARRIVED = "2026-06-01T23:03:00Z"
STARTED = "2026-06-01T23:05:00Z"


def _pt(lat, lng, ts, **extra):
    return {"lat": lat, "lng": lng, "timestamp": ts, **extra}


def _ride(**over):
    r = {
        "id": "ride_1",
        "status": "in_progress",
        "driver_accepted_at": ACCEPTED,
        "driver_arrived_at": ARRIVED,
        "ride_started_at": STARTED,
    }
    r.update(over)
    return r


def _patches(ride_rows, capture):
    async def _get_rows(table, query, **kw):
        assert table == "rides"
        return ride_rows

    async def _insert_many(table, docs):
        capture["table"] = table
        capture["docs"] = docs
        return docs

    return (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", _insert_many),
    )


@pytest.mark.asyncio
async def test_persists_all_points_with_trip_phase():
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.45, -104.62, "2026-06-01T23:06:17Z", speed=12),
                _pt(50.43, -104.64, "2026-06-01T23:07:17Z"),
                _pt(50.42, -104.65, "2026-06-01T23:08:17Z"),
            ],
        )
    assert n == 3
    assert cap["table"] == "driver_location_history"
    docs = cap["docs"]
    assert len(docs) == 3, "all points persisted, not just the last"
    assert all(d["ride_id"] == "ride_1" for d in docs)
    # ts >= ride_started_at → trip_in_progress, derived server-side
    assert all(d["tracking_phase"] == "trip_in_progress" for d in docs)
    assert all(isinstance(d["timestamp"], datetime) for d in docs)
    assert all(isinstance(d["received_at"], datetime) for d in docs)


@pytest.mark.asyncio
async def test_no_active_ride_persists_nothing():
    insert = AsyncMock()

    async def _get_rows(table, query, **kw):
        return []

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", insert),
    ):
        n = await persist_ride_breadcrumbs("drv_1", [_pt(50.45, -104.62, "2026-06-01T23:06:17Z")])
    assert n == 0
    insert.assert_not_called()




@pytest.mark.asyncio
async def test_no_active_ride_can_persist_idle_for_live_ws_ping():
    cap = {}

    async def _get_rows(table, query, **kw):
        return []

    async def _insert_many(table, docs):
        cap["table"] = table
        cap["docs"] = docs
        return docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", _insert_many),
    ):
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [_pt(50.45, -104.62, "2026-06-01T23:06:17Z")],
            persist_idle=True,
        )

    assert n == 1
    assert cap["docs"][0]["ride_id"] is None
    assert cap["docs"][0]["tracking_phase"] == "online_idle"


@pytest.mark.asyncio
async def test_phase_attributed_per_point_across_transition():
    """A batch spanning pickup→trip bills navigation points as navigation, not trip."""
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.40, -104.60, "2026-06-01T23:01:00Z"),  # accepted<ts<arrived → nav
                _pt(50.41, -104.61, "2026-06-01T23:04:00Z"),  # arrived<ts<started → arrived
                _pt(50.42, -104.62, "2026-06-01T23:06:00Z"),  # ts>=started → trip
            ],
        )
    assert n == 3
    assert [d["tracking_phase"] for d in cap["docs"]] == [
        "navigating_to_pickup",
        "arrived_at_pickup",
        "trip_in_progress",
    ]


@pytest.mark.asyncio
async def test_discards_other_ride_and_stale_points():
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.42, -104.62, "2026-06-01T23:06:00Z"),  # ok → trip
                _pt(50.40, -104.60, "2026-06-01T23:06:30Z", ride_id="ride_OTHER"),  # other ride → drop
                _pt(50.39, -104.59, "2026-06-01T22:50:00Z"),  # before accepted → stale drop
            ],
        )
    assert n == 1
    assert len(cap["docs"]) == 1
    assert cap["docs"][0]["tracking_phase"] == "trip_in_progress"


@pytest.mark.asyncio
async def test_invalid_and_mocked_points_skipped():
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.42, -104.62, "2026-06-01T23:06:00Z"),
                {"lat": None, "lng": -104.0, "timestamp": "2026-06-01T23:06:30Z"},  # missing lat
                _pt(50.40, -104.66, "2026-06-01T23:07:00Z", mocked=True),  # spoofed
            ],
        )
    assert n == 1
    assert len(cap["docs"]) == 1


@pytest.mark.asyncio
async def test_batch_capped_to_max():
    cap = {}
    g, i = _patches([_ride()], cap)
    pts = [_pt(50.40 + k * 1e-5, -104.60, "2026-06-01T23:06:00Z") for k in range(MAX_BREADCRUMB_BATCH + 200)]
    with g, i:
        n = await persist_ride_breadcrumbs("drv_1", pts)
    assert n == MAX_BREADCRUMB_BATCH, "REST batch must be bounded"
    assert len(cap["docs"]) == MAX_BREADCRUMB_BATCH


@pytest.mark.asyncio
async def test_non_list_and_non_dict_points_are_ignored():
    insert = AsyncMock()

    async def _get_rows(table, query, **kw):
        return [_ride()]

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", insert),
    ):
        assert await persist_ride_breadcrumbs("drv_1", "not-a-list") == 0
        assert await persist_ride_breadcrumbs("drv_1", ["not-a-dict"]) == 0

    insert.assert_not_called()
