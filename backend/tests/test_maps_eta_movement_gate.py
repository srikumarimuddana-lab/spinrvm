"""B3.2 — ETA movement gate: recompute routing only after >100m of movement.

When the 15 s ETA cache has expired, a driver who hasn't moved past
_ETA_MOVE_THRESHOLD_M reuses the last computed ETA (driver:{id}:last_eta_loc)
instead of re-calling OSRM/Google — a stationary driver (red light, waiting
at pickup) stops costing routing calls. Reuse is ride-scoped: a new ride
means a new destination, so the stored ETA must never carry over.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import maps_eta
from backend.utils.redis_client import redis_delete, redis_set

DEST_LAT, DEST_LNG = 52.1500, -106.6000
BASE_LAT, BASE_LNG = 52.1300, -106.6700
NEARBY_LAT = 52.13045  # ~50 m north of BASE
FAR_LAT = 52.1320  # ~222 m north of BASE


async def _seed_last_eta(driver_id: str, ride_id: str, eta: int = 300) -> None:
    await redis_set(
        maps_eta._last_eta_loc_key(driver_id),
        f"{ride_id}|{BASE_LAT}|{BASE_LNG}|{eta}",
        ttl=120,
    )


@pytest.mark.anyio
async def test_stationary_driver_reuses_last_eta_without_provider_call():
    driver_id, ride_id = "drv-gate-still", "ride-gate-still"
    await _seed_last_eta(driver_id, ride_id, eta=300)
    osrm = AsyncMock(return_value=999)

    with patch("backend.utils.maps_eta._osrm_eta_seconds", osrm):
        eta = await maps_eta.get_ride_eta_seconds(
            NEARBY_LAT, BASE_LNG, DEST_LAT, DEST_LNG, "", driver_id=driver_id, ride_id=ride_id
        )

    assert eta == 300
    osrm.assert_not_awaited()


@pytest.mark.anyio
async def test_moved_driver_recomputes():
    driver_id, ride_id = "drv-gate-moved", "ride-gate-moved"
    await _seed_last_eta(driver_id, ride_id, eta=300)
    osrm = AsyncMock(return_value=240)

    with patch("backend.utils.maps_eta._osrm_eta_seconds", osrm):
        eta = await maps_eta.get_ride_eta_seconds(
            FAR_LAT, BASE_LNG, DEST_LAT, DEST_LNG, "", driver_id=driver_id, ride_id=ride_id
        )

    assert eta == 240
    osrm.assert_awaited_once()


@pytest.mark.anyio
async def test_new_ride_never_reuses_old_destination_eta():
    driver_id = "drv-gate-newride"
    await _seed_last_eta(driver_id, "ride-old", eta=300)
    osrm = AsyncMock(return_value=240)

    with patch("backend.utils.maps_eta._osrm_eta_seconds", osrm):
        eta = await maps_eta.get_ride_eta_seconds(
            BASE_LAT, BASE_LNG, DEST_LAT, DEST_LNG, "", driver_id=driver_id, ride_id="ride-new"
        )

    assert eta == 240
    osrm.assert_awaited_once()


@pytest.mark.anyio
async def test_provider_calc_stores_gate_state_for_reuse():
    driver_id, ride_id = "drv-gate-store", "ride-gate-store"
    osrm = AsyncMock(return_value=300)

    with patch("backend.utils.maps_eta._osrm_eta_seconds", osrm):
        first = await maps_eta.get_ride_eta_seconds(
            BASE_LAT, BASE_LNG, DEST_LAT, DEST_LNG, "", driver_id=driver_id, ride_id=ride_id
        )
        # Expire the 15 s cache so the second call exercises the gate, not it.
        await redis_delete(f"eta:{driver_id}:{ride_id}")
        second = await maps_eta.get_ride_eta_seconds(
            NEARBY_LAT, BASE_LNG, DEST_LAT, DEST_LNG, "", driver_id=driver_id, ride_id=ride_id
        )

    assert first == 300
    assert second == 300
    osrm.assert_awaited_once()
