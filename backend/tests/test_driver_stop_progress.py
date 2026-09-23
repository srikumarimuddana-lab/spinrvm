"""Persisted driver stop progression and stale-edit protection."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.anyio
async def test_driver_completes_stop_with_compare_and_swap():
    from backend.routes.rides.stops import CompleteStopRequest, complete_stop

    ride = {
        "id": "ride-1",
        "driver_id": "driver-1",
        "rider_id": "rider-1",
        "status": "in_progress",
        "stops": [{"address": "Stop", "lat": 52.1, "lng": -106.6}],
    }
    updated = {**ride, "stops": [{**ride["stops"][0], "id": "stable-id", "completed": True}]}
    with patch("backend.routes.rides._deps.db") as db:
        db.get_rows = AsyncMock(return_value=[{"id": "driver-1", "user_id": "user-1"}])
        db.find_one = AsyncMock(return_value=ride)
        db.update_one = AsyncMock(return_value=updated)
        with patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()) as send:
            result = await complete_stop(
                "ride-1", 0, body=CompleteStopRequest(expected_stops=ride["stops"]), current_user={"id": "user-1"}
            )

    assert result["stops"][0]["completed"] is True
    assert {call.args[1] for call in send.await_args_list} == {"rider_rider-1", "driver_user-1"}
    filters = db.update_one.await_args.args[1]
    assert filters["driver_id"] == "driver-1"
    json_stop_snapshot = json.dumps(ride["stops"], separators=(",", ":"))
    assert filters["stops"] == {"$eq": json_stop_snapshot}

    # Use the actual PostgREST builder to ensure the JSON snapshot reaches the
    # URL as valid JSON text rather than Python repr (True/None/list syntax).
    from postgrest import SyncPostgrestClient

    client = SyncPostgrestClient("http://localhost:54321")
    query = client.from_("rides").update({"stops": result["stops"]}).eq("stops", json_stop_snapshot)
    assert query.request.params.get("stops") == f"eq.{json_stop_snapshot}"
    client.session.close()


@pytest.mark.anyio
async def test_stop_completion_rejects_non_assigned_driver():
    from backend.routes.rides.stops import CompleteStopRequest, complete_stop

    with patch("backend.routes.rides._deps.db") as db:
        db.get_rows = AsyncMock(return_value=[])
        with pytest.raises(HTTPException) as exc:
            await complete_stop(
                "ride-1", 0, body=CompleteStopRequest(expected_stops=[]), current_user={"id": "other-user"}
            )

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_stop_completion_rejects_a_concurrent_route_edit():
    from backend.routes.rides.stops import CompleteStopRequest, complete_stop

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
            await complete_stop(
                "ride-1", 0, body=CompleteStopRequest(expected_stops=ride["stops"]), current_user={"id": "user-1"}
            )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Stops changed. Refresh the ride and try again."


@pytest.mark.anyio
async def test_stop_completion_rejects_client_snapshot_changed_before_request():
    from backend.routes.rides.stops import CompleteStopRequest, complete_stop

    current_stops = [{"id": "new", "address": "Different stop", "lat": 52.2, "lng": -106.5}]
    ride = {"id": "ride-1", "driver_id": "driver-1", "status": "in_progress", "stops": current_stops}
    with patch("backend.routes.rides._deps.db") as db:
        db.get_rows = AsyncMock(return_value=[{"id": "driver-1"}])
        db.find_one = AsyncMock(return_value=ride)
        db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await complete_stop(
                "ride-1",
                0,
                body=CompleteStopRequest(expected_stops=[{"id": "old", "address": "Old stop", "lat": 52.1, "lng": -106.6}]),
                current_user={"id": "user-1"},
            )

    assert exc.value.status_code == 409
    db.update_one.assert_not_awaited()


def test_completion_final_cas_includes_exact_stop_snapshot():
    from backend.routes.drivers.ride_complete import _completion_cas_filters

    stops = [{"id": "s1", "lat": 52.1, "lng": -106.6, "completed": True}]
    filters = _completion_cas_filters({"id": "r1", "stops": stops}, "d1")
    assert filters["stops"] == {"$eq": json.dumps(stops, separators=(",", ":"))}
    assert filters["status"] == "in_progress"


def test_legacy_null_stop_array_uses_null_filter():
    from backend.routes.rides.stops import _stops_cas_filters
    from postgrest import SyncPostgrestClient

    filters = _stops_cas_filters({"id": "ride-1", "status": "in_progress", "stops": None})
    assert filters["stops"] is None
    client = SyncPostgrestClient("http://localhost:54321")
    query = client.from_("rides").update({"stops": []}).is_("stops", "null")
    assert query.request.params.get("stops") == "is.null"
    client.session.close()


@pytest.mark.anyio
async def test_stop_completion_rejects_later_pending_stop():
    from backend.routes.rides.stops import CompleteStopRequest, complete_stop

    stops = [
        {"id": "s1", "lat": 52.1, "lng": -106.6, "completed": True},
        {"id": "s2", "lat": 52.2, "lng": -106.5},
        {"id": "s3", "lat": 52.3, "lng": -106.4},
    ]
    ride = {"id": "r1", "driver_id": "d1", "rider_id": "rider-1", "status": "in_progress", "stops": stops}
    with patch("backend.routes.rides._deps.db") as db:
        db.get_rows = AsyncMock(return_value=[{"id": "d1"}])
        db.find_one = AsyncMock(return_value=ride)
        db.update_one = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await complete_stop("r1", 2, body=CompleteStopRequest(expected_stops=stops), current_user={"id": "u1"})
    assert exc.value.status_code == 409
    db.update_one.assert_not_awaited()


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
            await ride_complete.complete_ride(
                "ride-1",
                completion_request=ride_complete.RideCompletionRequest(stop_progress_enabled=True),
                current_user={"id": "user-1"},
            )

    assert exc.value.status_code == 409
    prepare.assert_not_awaited()
