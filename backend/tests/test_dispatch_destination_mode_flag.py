"""C136 / migration 482 — ``settings.destination_mode_enabled`` reaches dispatch.

Drives ``_match_driver_to_ride_attempt`` down its no-drivers path with an area
that has a vehicle cascade (same harness shape as
test_dispatch_expanded_radius.py), so both ``filter_and_rank_drivers`` call
sites — primary pool and cascade pool — run. Asserts each receives the
switch exactly as the settings row says, and that a missing key or a
non-bool value never turns the destination filter on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

_AREA = {
    "id": "area-1",
    "subscription_required": False,
    "parent_service_area_id": None,
    "vehicle_cascade_map": [{"from": "vt-std", "to": ["vt-xl"]}],
}


def _ride() -> dict:
    return {
        "id": "ride-1",
        "rider_id": "rider-1",
        "vehicle_type_id": "vt-std",
        "service_area_id": "area-1",
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "requires_wav": False,
        "status": "searching",
        "ride_requested_at": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
    }


async def _run(app_settings: dict) -> list:
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    seen: list = []

    async def _fetch(**kw):
        return []

    def _rank(ride_, drivers, algorithm, min_rating, radius, **kw):
        seen.append(kw.get("destination_mode_enabled", "<missing>"))
        return []

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value=app_settings)),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch("backend.routes.rides.matching.resolve_dispatch_area_scope", AsyncMock(return_value=(None, True))),
        patch("backend.routes.rides.matching.fetch_dispatch_candidates", side_effect=_fetch),
        patch("backend.routes.rides.matching._deps.filter_and_rank_drivers", side_effect=_rank),
        patch("backend.routes.rides.matching._metric_inc", MagicMock()),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_db.find_one = AsyncMock(return_value=dict(_AREA))
        await _match_driver_to_ride_attempt("ride-1", ride=_ride())
    return seen


@pytest.mark.parametrize(
    "app_settings",
    [
        {},  # migration 482 not applied / key absent
        {"destination_mode_enabled": False},
        {"destination_mode_enabled": None},
        {"destination_mode_enabled": "true"},  # non-bool never enables the hard filter
    ],
)
async def test_destination_filter_off_unless_switch_is_true(app_settings):
    seen = await _run(app_settings)
    # Primary pool + cascade pool.
    assert seen == [False, False]


async def test_destination_filter_on_when_switch_true():
    seen = await _run({"destination_mode_enabled": True})
    assert seen == [True, True]
