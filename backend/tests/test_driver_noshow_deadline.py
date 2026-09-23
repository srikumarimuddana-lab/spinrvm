"""Driver no-show display uses the mutation's area/settings wait policy."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend import settings_loader
from backend.routes.drivers import ride_reads

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def active_ride(monkeypatch):
    ride = {
        "id": "ride-1",
        "driver_id": "driver-1",
        "rider_id": "rider-1",
        "vehicle_type_id": "vehicle-1",
        "status": "driver_arrived",
        "driver_arrived_at": "2026-09-23T12:00:00+00:00",
        "service_area_id": "area-1",
    }

    async def rows(table, *args, **kwargs):
        return {"drivers": [{"id": "driver-1"}], "rides": [ride]}.get(table, [])

    monkeypatch.setattr(ride_reads.db_supabase, "get_rows", AsyncMock(side_effect=rows))
    monkeypatch.setattr(ride_reads.db_supabase, "get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(ride_reads.db_supabase, "find_one", AsyncMock(return_value={}))
    monkeypatch.setattr(ride_reads, "match_ride_incentives", AsyncMock(return_value=[]))
    monkeypatch.setattr(settings_loader, "get_app_settings", AsyncMock(return_value={"noshow_wait_seconds": 120}))
    return ride


@pytest.mark.parametrize("area_wait,expected_wait", [(600, 600), (0, 0), (None, 120)])
async def test_active_ride_returns_authoritative_deadline(active_ride, monkeypatch, area_wait, expected_wait):
    monkeypatch.setattr(ride_reads.db_supabase, "find_one", AsyncMock(return_value={"noshow_wait_seconds": area_wait}))
    before = datetime.now(timezone.utc)
    result = await ride_reads.get_active_ride(current_user={"id": "driver-user"})
    after = datetime.now(timezone.utc)
    payload = result["ride"]
    assert datetime.fromisoformat(payload["noshow_eligible_at"]) == (
        datetime.fromisoformat(active_ride["driver_arrived_at"]) + timedelta(seconds=expected_wait)
    )
    assert before <= datetime.fromisoformat(payload["noshow_server_now"]) <= after


async def test_scheduled_deadline_starts_at_booked_pickup(active_ride):
    active_ride.update(is_scheduled=True, scheduled_time="2026-09-23T12:30:00Z")
    result = await ride_reads.get_active_ride(current_user={"id": "driver-user"})
    assert result["ride"]["noshow_eligible_at"] == "2026-09-23T12:32:00+00:00"


@pytest.mark.parametrize(
    "status,arrival", [("driver_accepted", None), ("in_progress", "2026-09-23T12:00:00Z"), ("driver_arrived", None)]
)
async def test_unavailable_no_show_has_no_deadline(active_ride, status, arrival):
    active_ride.update(status=status, driver_arrived_at=arrival)
    result = await ride_reads.get_active_ride(current_user={"id": "driver-user"})
    assert result["ride"].get("noshow_eligible_at") is None


async def test_wait_policy_failure_is_not_replaced_by_five_minutes(active_ride, monkeypatch):
    monkeypatch.setattr(
        settings_loader, "get_app_settings", AsyncMock(side_effect=RuntimeError("settings unavailable"))
    )
    with pytest.raises(HTTPException) as error:
        await ride_reads.get_active_ride(current_user={"id": "driver-user"})
    assert error.value.status_code == 503
