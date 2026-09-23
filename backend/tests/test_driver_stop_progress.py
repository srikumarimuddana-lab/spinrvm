"""Persisted driver stop progression and stale-edit protection."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.anyio
async def test_driver_completes_stop_with_compare_and_swap():
    from backend.routes.rides.stops import complete_stop

    ride = {
        "id": "ride-1",
        "driver_id": "driver-1",
        "status": "in_progress",
        "stops": [{"address": "Stop", "lat": 52.1, "lng": -106.6}],
    }
    updated = {**ride, "stops": [{**ride["stops"][0], "id": "stable-id", "completed": True}]}
    with patch("backend.routes.rides._deps.db") as db:
        db.get_rows = AsyncMock(return_value=[{"id": "driver-1"}])
        db.find_one = AsyncMock(return_value=ride)
        db.update_one = AsyncMock(return_value=updated)

        result = await complete_stop("ride-1", 0, current_user={"id": "user-1"})

    assert result["stops"][0]["completed"] is True
    filters = db.update_one.await_args.args[1]
    assert filters["driver_id"] == "driver-1"
    assert filters["stops"] == {"$eq": ride["stops"]}


@pytest.mark.anyio
async def test_stop_completion_rejects_non_assigned_driver():
    from backend.routes.rides.stops import complete_stop

    with patch("backend.routes.rides._deps.db") as db:
        db.get_rows = AsyncMock(return_value=[])
        with pytest.raises(HTTPException) as exc:
            await complete_stop("ride-1", 0, current_user={"id": "other-user"})

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_stop_completion_rejects_a_concurrent_route_edit():
    from backend.routes.rides.stops import complete_stop

    ride = {
        "id": "ride-1",
        "driver_id": "driver-1",
        "status": "in_progress",
        "stops": [{"address": "Stop", "lat": 52.1, "lng": -106.6}],
    }
    with patch("backend.routes.rides._deps.db") as db:
        db.get_rows = AsyncMock(return_value=[{"id": "driver-1"}])
        db.find_one = AsyncMock(return_value=ride)
        db.update_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await complete_stop("ride-1", 0, current_user={"id": "user-1"})

    assert exc.value.status_code == 409
    assert exc.value.detail == "Stops changed. Refresh the ride and try again."


@pytest.mark.anyio
async def test_ride_completion_rejects_uncompleted_stops_before_side_effects():
    from backend.routes.drivers import ride_complete

    driver = {"id": "driver-1"}
    ride = {
        "id": "ride-1", "driver_id": "driver-1", "status": "in_progress",
        "stops": [{"id": "stop-1", "lat": 52.1, "lng": -106.6}],
    }
    with (
        patch.object(ride_complete.db_supabase, "get_rows", AsyncMock(side_effect=[[driver], [ride]])),
        patch.object(ride_complete, "prepare_completion_location", AsyncMock()) as prepare,
    ):
        with pytest.raises(HTTPException) as exc:
            await ride_complete.complete_ride("ride-1", current_user={"id": "user-1"})

    assert exc.value.status_code == 409
    prepare.assert_not_awaited()
