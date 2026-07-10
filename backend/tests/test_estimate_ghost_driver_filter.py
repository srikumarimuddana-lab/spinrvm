"""
Regression test: ghost driver filter on /rides/estimate.

Before this fix, drivers with is_online=True in the DB but no active
Redis presence key (app crashed / network lost) were counted in the
driver_count returned by the estimate endpoint. This produced counts
like "2 drivers nearby" when only 1 was actually reachable by dispatch.

These tests exercise the real production helper
``backend.routes.rides.estimates._filter_reachable_drivers``. Only the presence
store is mocked (at its source module, which the helper's inline import
resolves to); ``intent_online`` runs for real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.rides import _filter_reachable_drivers


def _make_driver(driver_id: str, vehicle_type_id: str = "economy", lat: float = 52.13, lng: float = -106.67) -> dict:
    return {
        "id": driver_id,
        "user_id": f"user_{driver_id}",
        "vehicle_type_id": vehicle_type_id,
        "lat": lat,
        "lng": lng,
        "is_online": True,
        "is_available": True,
        "went_online_at": "2026-01-01T00:00:00Z",
        "went_offline_at": None,
    }


@pytest.mark.anyio
async def test_ghost_driver_excluded_when_presence_reachable():
    """A driver that is DB-online but has no presence key is excluded."""
    all_db_drivers = [_make_driver("d_real"), _make_driver("d_ghost")]

    # Redis reachable; only d_real has a live heartbeat.
    async def _pdc(candidate_ids):
        return {cid for cid in candidate_ids if cid == "d_real"}, True

    with patch(
        "backend.utils.driver_presence.present_driver_ids_checked",
        new=AsyncMock(side_effect=_pdc),
    ):
        result = await _filter_reachable_drivers(all_db_drivers)

    assert [d["id"] for d in result] == ["d_real"]


@pytest.mark.anyio
async def test_empty_present_set_yields_empty_when_reachable():
    """Redis reachable but nobody present → empty count is correct, not a bug."""
    all_db_drivers = [_make_driver("d1"), _make_driver("d2")]

    async def _pdc(candidate_ids):
        return set(), True

    with patch(
        "backend.utils.driver_presence.present_driver_ids_checked",
        new=AsyncMock(side_effect=_pdc),
    ):
        result = await _filter_reachable_drivers(all_db_drivers)

    assert result == []


@pytest.mark.anyio
async def test_presence_fallback_keeps_db_drivers_when_redis_unreachable():
    """Redis configured but down (reachable=False) → fall back to DB state."""
    all_db_drivers = [_make_driver("d_real"), _make_driver("d_ghost")]

    async def _pdc(candidate_ids):
        return set(), False  # reachable=False

    with patch(
        "backend.utils.driver_presence.present_driver_ids_checked",
        new=AsyncMock(side_effect=_pdc),
    ):
        result = await _filter_reachable_drivers(all_db_drivers)

    # Both kept — only intent_online filters in the fallback path.
    assert {d["id"] for d in result} == {"d_real", "d_ghost"}


@pytest.mark.anyio
async def test_presence_exception_falls_back_to_db_state():
    """An unexpected presence error degrades to DB state, never blanks the count."""
    all_db_drivers = [_make_driver("d1"), _make_driver("d2")]

    with patch(
        "backend.utils.driver_presence.present_driver_ids_checked",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await _filter_reachable_drivers(all_db_drivers)

    assert {d["id"] for d in result} == {"d1", "d2"}


@pytest.mark.anyio
async def test_stale_is_online_driver_excluded_even_when_present():
    """A present driver whose went_offline_at is newer than went_online_at is dropped."""
    fresh = _make_driver("d_fresh")
    fresh["went_online_at"] = "2026-01-01T12:00:00Z"
    fresh["went_offline_at"] = "2026-01-01T11:00:00Z"  # online is more recent → online
    stale = _make_driver("d_stale")
    stale["went_online_at"] = "2026-01-01T10:00:00Z"
    stale["went_offline_at"] = "2026-01-01T11:00:00Z"  # offline is more recent → offline

    async def _pdc(candidate_ids):
        return set(candidate_ids), True  # both present in Redis

    with patch(
        "backend.utils.driver_presence.present_driver_ids_checked",
        new=AsyncMock(side_effect=_pdc),
    ):
        result = await _filter_reachable_drivers([fresh, stale])

    assert [d["id"] for d in result] == ["d_fresh"]


@pytest.mark.anyio
async def test_empty_input_short_circuits():
    """No drivers in → no drivers out, without touching the presence store."""
    with patch(
        "backend.utils.driver_presence.present_driver_ids_checked",
        new=AsyncMock(side_effect=AssertionError("should not be called")),
    ):
        result = await _filter_reachable_drivers([])

    assert result == []
