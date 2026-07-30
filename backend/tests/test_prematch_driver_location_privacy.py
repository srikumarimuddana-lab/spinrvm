"""Pre-match driver positions must be coarse, capped, and killable.

Launch-gate hard no-go: "Any client can enumerate precise driver locations
without an assigned ride."

``GET /drivers/nearby`` is reachable by *any* authenticated rider with arbitrary
lat/lng and (previously) an uncapped radius, and it returned exact coordinates
plus a stable driver id, vehicle make and model. That combination let a caller
sweep a grid of coordinates to enumerate every online driver and then follow an
individual vehicle between polls.

Note on prior coverage: the existing suite tests ``db_supabase.find_nearby_drivers``
(a different function) and the **admin** ``/admin/drivers/nearby`` endpoint. Neither
the rider REST endpoint nor the ``get_nearby_drivers`` WebSocket handler had any
test, so 280 unrelated tests passed unchanged when this behaviour was rewritten.
These are the first tests of either path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from utils.driver_map_visibility import (
    MIN_CELL_M,
    clamp_radius,
    map_settings,
    prematch_driver_list,
    prematch_driver_payload,
)

RIDER_LAT = 52.1332
RIDER_LNG = -106.6700

# Two drivers ~60m apart — close enough that exact coordinates distinguish them
# and a 500m cell should not.
DRIVER_A_LAT, DRIVER_A_LNG = 52.13340, -106.67010
DRIVER_B_LAT, DRIVER_B_LNG = 52.13394, -106.67010


def _driver_row(driver_id: str, lat: float, lng: float, **extra) -> dict:
    """A realistic `drivers` row, including the columns that must NOT egress."""
    return {
        "id": driver_id,
        "user_id": f"user_{driver_id}",
        "lat": lat,
        "lng": lng,
        "heading": 91.0,
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "vehicle_type_id": "vt_standard",
        # Must never reach a pre-match rider:
        "name": "Jane Doe",
        "phone": "+13065551234",
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "vehicle_vin": "1HGBH41JXMN109186",
        "license_number": "vault-uuid-not-a-licence",
        **extra,
    }


# ── The projection ──────────────────────────────────────────────────


def test_payload_never_carries_identifying_or_exact_fields():
    driver = _driver_row("drv_1", DRIVER_A_LAT, DRIVER_A_LNG)
    out = prematch_driver_payload(driver, cell_m=500)
    assert out is not None

    for forbidden in ("name", "phone", "vehicle_make", "vehicle_model", "vehicle_vin", "license_number", "user_id"):
        assert forbidden not in out, f"{forbidden} leaked into the pre-match payload: {out!r}"

    blob = repr(out)
    for value in ("Jane Doe", "+13065551234", "Toyota", "Corolla", "1HGBH41JXMN109186"):
        assert value not in blob, f"{value!r} leaked: {blob}"

    # Exact coordinates must not survive.
    assert out["lat"] != DRIVER_A_LAT
    assert out["lng"] != DRIVER_A_LNG


def test_payload_keeps_what_the_map_actually_needs():
    """Over-redaction is its own failure — the map still has to render a car."""
    driver = _driver_row("drv_1", DRIVER_A_LAT, DRIVER_A_LNG, vehicle_type_name="Standard", marker_variant="sedan")
    out = prematch_driver_payload(driver, cell_m=500)
    assert out is not None

    assert out["id"] == "drv_1"
    assert out["vehicle_type_id"] == "vt_standard"
    assert out["vehicle_type_name"] == "Standard"
    assert out["marker_variant"] == "sedan"
    assert isinstance(out["lat"], float) and isinstance(out["lng"], float)
    # The client is told the position is approximate rather than left to assume
    # GPS precision it does not have.
    assert out["precision"] == "approximate"
    assert out["precision_m"] == 500


def test_allowlist_means_a_new_sensitive_column_is_safe_by_default():
    """The regression that matters long-term: someone adds a column to `drivers`
    and it must not become rider-visible for free."""
    driver = _driver_row("drv_1", DRIVER_A_LAT, DRIVER_A_LNG, home_address="123 Main St", sin="123456789")
    out = prematch_driver_payload(driver, cell_m=500)
    assert out is not None
    assert "home_address" not in out
    assert "sin" not in out


def test_a_cluster_of_drivers_collapses_to_a_handful_of_positions():
    """The anti-enumeration property, stated as an aggregate.

    Asserting that one *specific* pair 60m apart collapses is wrong — any grid has
    boundaries, and a chosen pair can straddle one. (An earlier version of this
    test did exactly that and failed for that reason.) What must hold is that many
    distinct real positions over a small area yield very few reported positions,
    so polling cannot resolve individual vehicles.
    """
    import math

    rows = []
    n = 0
    for i in range(9):  # 9x9 grid of drivers, 50m apart -> 400m box
        for j in range(9):
            lat = DRIVER_A_LAT + (i * 50) / 111_320.0
            lng = DRIVER_A_LNG + (j * 50) / (111_320.0 * math.cos(math.radians(DRIVER_A_LAT)))
            rows.append(_driver_row(f"drv_{i}_{j}", lat, lng))
            n += 1

    out = prematch_driver_list(rows, cell_m=500)

    assert len(out) == n == 81, "every driver must still be listed"
    positions = {(d["lat"], d["lng"]) for d in out}
    # A 400m box can straddle at most 2x2 cells of 500m.
    assert len(positions) <= 4, f"{n} drivers resolved to {len(positions)} distinct positions"
    assert len(positions) < n / 10, "coarsening barely reduced resolution"


def test_driver_without_a_position_is_dropped_not_reported_at_null_island():
    rows = [
        _driver_row("drv_no_gps", 0.0, 0.0),
        _driver_row("drv_null", None, None),
        _driver_row("drv_ok", DRIVER_A_LAT, DRIVER_A_LNG),
    ]
    out = prematch_driver_list(rows, cell_m=500)
    assert [d["id"] for d in out] == ["drv_ok"]


def test_cell_floor_stops_a_settings_row_re_enabling_exact_positions():
    """coarsen_coord treats cell_m<=0 as an exact passthrough, which is correct for
    the assigned-ride caller. A pre-match path must not be able to reach it, even
    with a hostile or fat-fingered settings value."""
    driver = _driver_row("drv_1", DRIVER_A_LAT, DRIVER_A_LNG)
    for bad_cell in (0, -1, -10_000, 1):
        out = prematch_driver_payload(driver, cell_m=bad_cell)
        assert out is not None
        assert out["lat"] != DRIVER_A_LAT, f"cell_m={bad_cell} produced exact coordinates"
        assert out["precision_m"] >= MIN_CELL_M


# ── Settings resolution and the radius cap ──────────────────────────


def test_settings_defaults_are_safe_when_the_row_is_missing_or_junk():
    """A settings outage must not fail open into exact coordinates."""
    for bad in (None, {}, {"driver_map_cell_m": None}, {"driver_map_cell_m": "abc"}, {"driver_map_cell_m": 0}):
        show, cell_m, max_radius = map_settings(bad)
        assert show is True
        assert cell_m >= MIN_CELL_M, f"unsafe cell for {bad!r}: {cell_m}"
        assert max_radius > 0


def test_kill_switch_is_read_from_settings():
    show, _, _ = map_settings({"driver_map_show_locations": False})
    assert show is False
    show, _, _ = map_settings({"driver_map_show_locations": True})
    assert show is True


@pytest.mark.parametrize(
    "requested,expected",
    [
        (5, 5.0),
        (15, 15.0),
        (1000, 15.0),  # the province-sweep case
        (10**9, 15.0),
        (0, 10.0),  # non-positive -> default
        (-5, 10.0),
        (None, 10.0),
        ("abc", 10.0),
    ],
)
def test_radius_is_capped_server_side(requested, expected):
    assert clamp_radius(requested, max_radius_km=15.0, default_km=10.0) == expected


# ── The REST route ──────────────────────────────────────────────────


async def _call_nearby(drivers, settings, radius=None):
    """Invoke the real route function with its deps patched."""
    from backend.routes.drivers import location as loc_mod

    async def _get_rows(table, filters=None, limit=None, **kwargs):
        if table == "drivers":
            return list(drivers)
        if table == "vehicle_types":
            return [{"id": "vt_standard", "name": "Standard", "marker_variant": "sedan"}]
        return []

    with (
        patch("backend.routes.drivers.location.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=settings)),
        patch(
            "backend.routes.drivers.location.present_driver_ids_checked",
            AsyncMock(return_value=({d["id"] for d in drivers}, True)),
        ),
    ):
        return await loc_mod.get_nearby_drivers_public(
            lat=RIDER_LAT,
            lng=RIDER_LNG,
            radius=radius,
            vehicle_type=None,
            current_user={"id": "rider_1", "role": "rider"},
        )


@pytest.mark.anyio
async def test_rest_endpoint_returns_coarse_positions_only():
    rows = [_driver_row("drv_a", DRIVER_A_LAT, DRIVER_A_LNG)]
    out = await _call_nearby(rows, {"search_radius_km": 10.0, "driver_map_cell_m": 500})

    assert len(out) == 1
    got = out[0]
    assert got["lat"] != DRIVER_A_LAT, "endpoint returned exact latitude"
    assert got["lng"] != DRIVER_A_LNG, "endpoint returned exact longitude"
    assert got["precision"] == "approximate"
    for forbidden in ("vehicle_make", "vehicle_model", "name", "phone"):
        assert forbidden not in got


@pytest.mark.anyio
async def test_rest_endpoint_honours_the_kill_switch():
    rows = [_driver_row("drv_a", DRIVER_A_LAT, DRIVER_A_LNG)]
    out = await _call_nearby(rows, {"search_radius_km": 10.0, "driver_map_show_locations": False})
    assert out == [], "kill switch must suppress all positions"


@pytest.mark.anyio
async def test_rest_endpoint_still_lists_drivers_in_radius():
    """Anti-vacuity: the privacy change must not empty the map."""
    rows = [
        _driver_row("drv_a", DRIVER_A_LAT, DRIVER_A_LNG),
        _driver_row("drv_b", DRIVER_B_LAT, DRIVER_B_LNG),
    ]
    out = await _call_nearby(rows, {"search_radius_km": 10.0, "driver_map_cell_m": 500})
    assert {d["id"] for d in out} == {"drv_a", "drv_b"}


@pytest.mark.anyio
async def test_rest_endpoint_excludes_a_driver_outside_the_radius():
    """The radius must still be enforced against the TRUE position, not the
    coarsened one — otherwise coarsening would widen the effective radius."""
    far = _driver_row("drv_far", RIDER_LAT + 0.5, RIDER_LNG)  # ~55km north
    near = _driver_row("drv_near", DRIVER_A_LAT, DRIVER_A_LNG)
    out = await _call_nearby([far, near], {"search_radius_km": 10.0, "driver_map_cell_m": 500})
    assert [d["id"] for d in out] == ["drv_near"]


# ── The WebSocket handler ───────────────────────────────────────────
# The same exposure existed on `get_nearby_drivers`, and it had drifted further
# than the REST endpoint: no geo-bound on the query, and a truthiness check on
# lat/lng that rejected a rider legitimately at lat=0.


@pytest.fixture
def ws_app():
    from fastapi import FastAPI

    from backend.routes.websocket import router

    app = FastAPI()
    app.include_router(router)
    return app


def _ws_nearby(ws_app, drivers, settings):
    """Drive the real WS handler through a TestClient and return the payload."""
    from fastapi.testclient import TestClient

    async def _get_rows(table, filters=None, limit=None, **kwargs):
        if table == "drivers":
            # Assert the geo-bound is actually applied — its absence was a real
            # defect here, not a hypothetical one.
            assert "$and" in (filters or {}), f"WS driver query was not geo-bounded: {filters!r}"
            return list(drivers)
        return []

    client = TestClient(ws_app)
    with (
        # The JWT branch resolves the user from payload["user_id"], not "sub", and
        # a "role" claim would route into the admin verification path.
        patch("backend.routes.websocket.verify_jwt_token", return_value={"user_id": "rider_1"}),
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            side_effect=Exception("firebase not configured in test"),
        ),
        patch("backend.routes.websocket.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        # The JWT trust model re-reads rider/driver role from `users` on every
        # request rather than trusting the token claim, so this must be present
        # or auth closes the socket.
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_id",
            AsyncMock(return_value={"id": "rider_1", "role": "rider", "token_version": 0}),
        ),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=settings)),
    ):
        with client.websocket_connect("/ws/rider/rider_1") as ws:
            ws.send_json({"type": "auth", "token": "valid"})
            ack = ws.receive_json()
            assert ack["type"] == "auth_success", f"WS auth failed: {ack!r}"

            ws.send_json({"type": "get_nearby_drivers", "lat": RIDER_LAT, "lng": RIDER_LNG, "radius": 5})
            # The socket also emits a heartbeat `ping` every 10s. Skip those, but
            # bound the loop tightly: a generous retry count would turn a missing
            # response into a multi-minute hang instead of a failure.
            for _ in range(3):
                msg = ws.receive_json()
                if msg.get("type") == "nearby_drivers":
                    return msg["drivers"]
                assert msg.get("type") == "ping", f"unexpected frame: {msg!r}"
    pytest.fail("no nearby_drivers frame received")


def test_ws_handler_returns_coarse_positions_only(ws_app):
    rows = [_driver_row("drv_a", DRIVER_A_LAT, DRIVER_A_LNG)]
    out = _ws_nearby(ws_app, rows, {"driver_map_cell_m": 500})

    assert len(out) == 1
    assert out[0]["lat"] != DRIVER_A_LAT, "WS handler returned exact latitude"
    assert out[0]["lng"] != DRIVER_A_LNG, "WS handler returned exact longitude"
    assert out[0]["precision"] == "approximate"
    for forbidden in ("vehicle_make", "vehicle_model", "name", "phone"):
        assert forbidden not in out[0]


def test_ws_handler_honours_the_kill_switch(ws_app):
    rows = [_driver_row("drv_a", DRIVER_A_LAT, DRIVER_A_LNG)]
    out = _ws_nearby(ws_app, rows, {"driver_map_show_locations": False})
    assert out == []


def test_ws_and_rest_agree_on_the_same_input(ws_app):
    """The two paths existed independently and drifted. Pin them together so a
    future change to one is caught if it does not reach the other."""
    import anyio

    rows = [_driver_row("drv_a", DRIVER_A_LAT, DRIVER_A_LNG)]
    settings = {"search_radius_km": 5.0, "driver_map_cell_m": 500}

    ws_out = _ws_nearby(ws_app, rows, settings)
    rest_out = anyio.run(lambda: _call_nearby(rows, settings, radius=5))

    assert len(ws_out) == len(rest_out) == 1
    assert (ws_out[0]["lat"], ws_out[0]["lng"]) == (rest_out[0]["lat"], rest_out[0]["lng"])
    assert ws_out[0]["precision_m"] == rest_out[0]["precision_m"]
