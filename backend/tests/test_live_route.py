"""Tests for GET /rides/{id}/live-route — OSRM route line + ETA for the live map.

Routes from the assigned driver's live position to pickup (pre-trip) or dropoff
(in-trip); empty when there's no active leg or no live position.

Also tests breadcrumb_trail — the gap-filled actual GPS history.
"""

from unittest.mock import patch

import pytest

RIDER = {"id": "rider_u", "role": "rider"}


def _ride(status, **over):
    r = {
        "id": "r1",
        "rider_id": "rider_u",
        "driver_id": "drv_1",
        "status": status,
        "pickup_lat": 50.45,
        "pickup_lng": -104.62,
        "dropoff_lat": 50.40,
        "dropoff_lng": -104.66,
    }
    r.update(over)
    return r


def _patches(ride, *, driver_pos, compute, capture, breadcrumb_trail=None):
    async def _get_ride(_rid):
        return ride

    async def _get_rows(table, filters=None, **kw):
        if table == "drivers" and filters and "id" in filters:
            return [{"id": "drv_1", **driver_pos}] if driver_pos is not None else []
        return []  # requester's own driver row (rider → none)

    async def _compute_route(flat, flng, tlat, tlng):
        capture["args"] = (flat, flng, tlat, tlng)
        return compute

    async def _build_breadcrumb_trail(ride_id, current_driver_pos, destination):
        capture["breadcrumb_args"] = (ride_id, current_driver_pos, destination)
        return breadcrumb_trail or []

    return (
        patch("backend.routes.rides._deps.db_supabase.get_ride", _get_ride),
        patch("backend.routes.rides._deps.db_supabase.get_rows", _get_rows),
        patch("utils.route_distance.compute_route", _compute_route),
        patch("utils.live_breadcrumbs.build_breadcrumb_trail", _build_breadcrumb_trail),
    )


@pytest.mark.asyncio
async def test_in_progress_routes_to_dropoff():
    from backend.routes import rides as rides_mod

    cap = {}
    result = {"polyline": [[50.44, -104.63], [50.40, -104.66]], "eta_seconds": 420, "distance_km": 3.1}
    g, r, c, b = _patches(
        _ride("in_progress"),
        driver_pos={"lat": 50.44, "lng": -104.63},
        compute=result,
        capture=cap,
        breadcrumb_trail=[[50.43, -104.64], [50.44, -104.63]],  # historical path
    )
    with g, r, c, b:
        out = await rides_mod.get_live_route(ride_id="r1", current_user=RIDER)
    assert out["destination"] == "dropoff"
    assert out["eta_seconds"] == 420
    assert out["breadcrumb_trail"] == [[50.43, -104.64], [50.44, -104.63]]
    # routed from the driver's live position to the DROPOFF
    assert cap["args"] == (50.44, -104.63, 50.40, -104.66)
    assert cap["breadcrumb_args"] == ("r1", [50.44, -104.63], [50.40, -104.66])


@pytest.mark.asyncio
async def test_pre_trip_routes_to_pickup():
    from backend.routes import rides as rides_mod

    cap = {}
    result = {"polyline": [[50.44, -104.63], [50.45, -104.62]], "eta_seconds": 180, "distance_km": 1.2}
    g, r, c, b = _patches(
        _ride("driver_assigned"),
        driver_pos={"lat": 50.44, "lng": -104.63},
        compute=result,
        capture=cap,
        breadcrumb_trail=[[50.43, -104.64]],
    )
    with g, r, c, b:
        out = await rides_mod.get_live_route(ride_id="r1", current_user=RIDER)
    assert out["destination"] == "pickup"
    assert out["breadcrumb_trail"] == [[50.43, -104.64]]
    assert cap["args"] == (50.44, -104.63, 50.45, -104.62)  # → PICKUP
    assert cap["breadcrumb_args"] == ("r1", [50.44, -104.63], [50.45, -104.62])


@pytest.mark.asyncio
async def test_empty_when_not_active():
    from backend.routes import rides as rides_mod

    cap = {}
    g, r, c, b = _patches(_ride("searching"), driver_pos={"lat": 50.44, "lng": -104.63}, compute=None, capture=cap)
    with g, r, c, b:
        out = await rides_mod.get_live_route(ride_id="r1", current_user=RIDER)
    assert out["polyline"] == [] and out["eta_seconds"] is None and out["destination"] is None
    assert out["breadcrumb_trail"] == []
    assert "args" not in cap  # compute_route never called
    assert "breadcrumb_args" not in cap  # breadcrumb trail never called


@pytest.mark.asyncio
async def test_empty_when_no_driver_position():
    from backend.routes import rides as rides_mod

    cap = {}
    g, r, c, b = _patches(_ride("in_progress"), driver_pos=None, compute=None, capture=cap)
    with g, r, c, b:
        out = await rides_mod.get_live_route(ride_id="r1", current_user=RIDER)
    assert out["polyline"] == [] and out["eta_seconds"] is None
    assert out["destination"] == "dropoff"  # leg known, just no live origin
    assert out["breadcrumb_trail"] == []
    assert "args" not in cap
    assert "breadcrumb_args" not in cap
