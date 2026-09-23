"""Live-route and turn instructions follow persisted stop progress."""

from unittest.mock import patch

import pytest


def _common_patches(ride, cap):
    async def get_ride(_ride_id):
        return ride

    async def get_rows(table, filters=None, **_kwargs):
        return [{"id": "driver-1", "lat": 52.0, "lng": -106.0}] if table == "drivers" and filters and filters.get("id") else []

    async def compute_route(_o_lat, _o_lng, d_lat, d_lng):
        cap["route_destination"] = (d_lat, d_lng)
        return {"polyline": [[52.0, -106.0], [d_lat, d_lng]], "eta_seconds": 100, "distance_km": 1}

    async def compute_steps(_o_lat, _o_lng, d_lat, d_lng):
        cap["steps_destination"] = (d_lat, d_lng)
        return [{"instruction": "Continue", "startLocation": [52.0, -106.0], "endLocation": [d_lat, d_lng]}]

    return [
        patch("backend.routes.rides._deps.db_supabase.get_ride", get_ride),
        patch("backend.routes.rides._deps.db_supabase.get_rows", get_rows),
        patch("utils.route_distance.compute_route", compute_route),
        patch("utils.route_distance.compute_navigation_steps", compute_steps),
        patch("utils.redis_client.redis_get", return_value=None),
        patch("utils.redis_client.redis_set"),
        patch("utils.live_breadcrumbs.build_breadcrumb_trail", return_value=[]),
        patch("backend.settings_loader.get_app_settings", return_value={"driver_turn_by_turn_enabled": True}),
    ]


@pytest.mark.asyncio
async def test_active_route_and_turn_steps_target_first_uncompleted_stop():
    from backend.routes import rides as rides_mod

    ride = {
        "id": "ride-1", "rider_id": "rider-1", "driver_id": "driver-1", "status": "in_progress",
        "pickup_lat": 51.0, "pickup_lng": -105.0, "dropoff_lat": 53.0, "dropoff_lng": -107.0,
        "stops": [
            {"id": "done", "lat": 51.5, "lng": -105.5, "completed": True},
            {"id": "next", "lat": 52.2, "lng": -106.2},
            {"id": "later", "lat": 52.8, "lng": -106.8},
        ],
    }
    cap = {}
    patches = _common_patches(ride, cap)
    from contextlib import ExitStack

    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        live = await rides_mod.get_live_route("ride-1", current_user={"id": "rider-1"})
        steps = await rides_mod.get_navigation_steps("ride-1", current_user={"id": "rider-1"})

    assert live["destination"] == "dropoff"
    assert steps["destination"] == "dropoff"
    assert cap["route_destination"] == (52.2, -106.2)
    assert cap["steps_destination"] == (52.2, -106.2)


@pytest.mark.asyncio
async def test_invalid_pending_stop_fails_closed_instead_of_routing_to_dropoff():
    from contextlib import ExitStack

    from backend.routes import rides as rides_mod

    ride = {
        "id": "ride-1", "rider_id": "rider-1", "driver_id": "driver-1", "status": "in_progress",
        "dropoff_lat": 53.0, "dropoff_lng": -107.0,
        "stops": [{"address": "Ungeocoded stop"}],
    }
    cap = {}
    patches = _common_patches(ride, cap)
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        live = await rides_mod.get_live_route("ride-1", current_user={"id": "rider-1"})
        steps = await rides_mod.get_navigation_steps("ride-1", current_user={"id": "rider-1"})

    assert live["polyline"] == []
    assert steps["steps"] == []
    assert cap == {}
