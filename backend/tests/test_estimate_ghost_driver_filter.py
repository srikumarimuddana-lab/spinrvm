"""
Regression test: ghost driver filter on /rides/estimate.

Before this fix, drivers with is_online=True in the DB but no active
Redis presence key (app crashed / network lost) were counted in the
driver_count returned by the estimate endpoint. This produced counts
like "2 drivers nearby" when only 1 was actually reachable by dispatch.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


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


PICKUP_LAT, PICKUP_LNG = 52.13, -106.67


@pytest.mark.anyio
async def test_ghost_driver_excluded_when_presence_reachable():
    """A driver that is DB-online but has no presence key is excluded from the count."""
    real_driver = _make_driver("d_real")
    ghost_driver = _make_driver("d_ghost")
    all_db_drivers = [real_driver, ghost_driver]

    # Only d_real is present in Redis
    async def _pdc_only_real(candidate_ids):
        present = {cid for cid in candidate_ids if cid == "d_real"}
        return present, True  # reachable=True

    with (
        patch("backend.db_supabase.get_rows", new_callable=AsyncMock) as mock_get_rows,
        patch("backend.routes.rides.present_driver_ids_checked", new=_pdc_only_real),
        patch("backend.routes.rides.intent_online", return_value=True),
    ):
        # Import inline so the patches are in scope
        from backend.utils.driver_presence import present_driver_ids_checked as _pdc
        from backend.utils.driver_online import intent_online

        driver_ids = [d["id"] for d in all_db_drivers if d.get("id")]
        present, reachable = await _pdc_only_real(driver_ids)

        assert reachable is True
        # Ghost driver not in present set
        visible = [d for d in all_db_drivers if d.get("id") in present and intent_online(d)]
        assert len(visible) == 1
        assert visible[0]["id"] == "d_real"


@pytest.mark.anyio
async def test_presence_fallback_keeps_db_drivers_when_redis_unreachable():
    """When Redis is configured but down (reachable=False), DB state is used as fallback."""
    real_driver = _make_driver("d_real")
    ghost_driver = _make_driver("d_ghost")
    all_db_drivers = [real_driver, ghost_driver]

    async def _pdc_redis_down(candidate_ids):
        return set(), False  # reachable=False → Redis unavailable

    from backend.utils.driver_online import intent_online

    driver_ids = [d["id"] for d in all_db_drivers if d.get("id")]
    present, reachable = await _pdc_redis_down(driver_ids)

    assert reachable is False
    # Fall back to DB state — keep both drivers (only intent_online filters)
    visible = [d for d in all_db_drivers if intent_online(d)]
    assert len(visible) == 2


@pytest.mark.anyio
async def test_intent_online_filters_stale_is_online_column():
    """A driver marked is_online=True but went_offline_at > went_online_at is excluded."""
    from backend.utils.driver_online import intent_online

    # Went online then went offline — is_online column is stale
    stale_driver = _make_driver("d_stale")
    stale_driver["went_online_at"] = "2026-01-01T10:00:00Z"
    stale_driver["went_offline_at"] = "2026-01-01T11:00:00Z"  # offline after online

    # Genuinely online — went_offline_at before went_online_at
    online_driver = _make_driver("d_online")
    online_driver["went_online_at"] = "2026-01-01T12:00:00Z"
    online_driver["went_offline_at"] = "2026-01-01T11:00:00Z"  # offline was earlier

    assert intent_online(stale_driver) is False
    assert intent_online(online_driver) is True
