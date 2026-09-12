"""Tests for turn-by-turn navigation steps — Phase 1 of
docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md.

Two layers, mirroring test_compute_route_fallback.py (route_distance-level)
and test_live_route.py (endpoint-level):
  1. utils.route_distance.compute_navigation_steps — Google Directions
     steps=true, budget-gated, its own short-lived cache.
  2. GET /rides/{id}/navigation-steps — feature-flagged, ride+leg-scoped
     cache wrapping (1).
"""

import contextlib
import json
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest

from backend.utils import route_distance as rd

RIDER = {"id": "rider_u", "role": "rider"}

_DIRECTIONS_STEPS_BODY = {
    "status": "OK",
    "routes": [
        {
            "legs": [
                {
                    "steps": [
                        {
                            "html_instructions": "Head <b>north</b> on <b>Main St</b>",
                            "distance": {"value": 120},
                            "start_location": {"lat": 50.45, "lng": -104.62},
                            "end_location": {"lat": 50.451, "lng": -104.62},
                            # no "maneuver" key — a plain continue step
                        },
                        {
                            "html_instructions": "Turn <b>right</b> onto <b>Albert St</b>",
                            "maneuver": "turn-right",
                            "distance": {"value": 400},
                            "start_location": {"lat": 50.451, "lng": -104.62},
                            "end_location": {"lat": 50.451, "lng": -104.615},
                        },
                    ]
                }
            ]
        }
    ],
}


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def _fake_client(handler, calls):
    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, params=None):
            calls.append(url)
            return handler(url)

    return _C


@contextmanager
def _rd_patches(app_settings, handler, calls, *, budget_allowed=True, cached=None, recorded=None, cache_writes=None):
    async def _settings():
        return app_settings

    async def _budget():
        return (budget_allowed, 5.0 if not budget_allowed else 0.0, 5.0)

    async def _record(sku):
        if recorded is not None:
            recorded.append(sku)

    async def _redis_get(_key):
        return cached

    async def _redis_set(key, value, ttl=None, **_kw):
        if cache_writes is not None:
            cache_writes.append({"key": key, "value": value, "ttl": ttl})
        return None

    with ExitStack() as stack:
        for p in (
            patch.object(rd, "get_app_settings", _settings),
            patch.object(rd, "check_budget", _budget),
            patch.object(rd, "record_call", _record),
            patch.object(rd, "redis_get", _redis_get),
            patch.object(rd, "redis_set", _redis_set),
            patch.object(rd.httpx, "AsyncClient", _fake_client(handler, calls)),
        ):
            stack.enter_context(p)
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1: utils.route_distance.compute_navigation_steps
# ─────────────────────────────────────────────────────────────────────────────


def test_strip_html_instructions_sanitizes_and_unescapes():
    assert rd._strip_html_instructions("Turn <b>right</b> onto <b>Albert St</b>") == "Turn right onto Albert St"
    assert rd._strip_html_instructions("Head north &amp; continue") == "Head north & continue"
    assert rd._strip_html_instructions("") == ""
    assert rd._strip_html_instructions(None) == ""  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_compute_navigation_steps_parses_and_sanitizes():
    calls, recorded, writes = [], [], []

    def handler(url):
        assert "steps" not in url  # steps is a query param, not in the path
        return _Resp(200, _DIRECTIONS_STEPS_BODY)

    with _rd_patches({"google_maps_api_key": "test-key"}, handler, calls, recorded=recorded, cache_writes=writes):
        steps = await rd.compute_navigation_steps(50.45, -104.62, 50.451, -104.615)

    assert steps == [
        {
            "instruction": "Head north on Main St",
            "maneuver": None,
            "distanceMeters": 120,
            "startLocation": [50.45, -104.62],
            "endLocation": [50.451, -104.62],
        },
        {
            "instruction": "Turn right onto Albert St",
            "maneuver": "turn-right",
            "distanceMeters": 400,
            "startLocation": [50.451, -104.62],
            "endLocation": [50.451, -104.615],
        },
    ]
    assert recorded == ["directions"]  # billed exactly once
    assert len(writes) == 1 and writes[0]["ttl"] == rd._NAV_STEPS_CACHE_TTL_S


@pytest.mark.asyncio
async def test_compute_navigation_steps_no_api_key_returns_none_without_calling():
    calls = []

    def handler(_url):
        raise AssertionError("must not call Directions without an API key")

    with _rd_patches({"google_maps_api_key": ""}, handler, calls):
        steps = await rd.compute_navigation_steps(50.45, -104.62, 50.451, -104.615)
    assert steps is None
    assert calls == []


@pytest.mark.asyncio
async def test_compute_navigation_steps_budget_exhausted_returns_none():
    calls = []

    def handler(_url):
        raise AssertionError("must not call Directions when budget is exhausted")

    with _rd_patches({"google_maps_api_key": "test-key"}, handler, calls, budget_allowed=False):
        steps = await rd.compute_navigation_steps(50.45, -104.62, 50.451, -104.615)
    assert steps is None
    assert calls == []


@pytest.mark.asyncio
async def test_compute_navigation_steps_served_from_cache_skips_the_call():
    calls = []
    cached_steps = [
        {
            "instruction": "Continue",
            "maneuver": None,
            "distanceMeters": 50,
            "startLocation": [1, 2],
            "endLocation": [1, 3],
        }
    ]

    def handler(_url):
        raise AssertionError("must not call Directions on a cache hit")

    with _rd_patches({"google_maps_api_key": "test-key"}, handler, calls, cached=json.dumps(cached_steps)):
        steps = await rd.compute_navigation_steps(50.45, -104.62, 50.451, -104.615)
    assert steps == cached_steps
    assert calls == []


@pytest.mark.asyncio
async def test_compute_navigation_steps_provider_error_returns_none():
    calls = []

    def handler(_url):
        return _Resp(200, {"status": "ZERO_RESULTS", "routes": []})

    with _rd_patches({"google_maps_api_key": "test-key"}, handler, calls):
        steps = await rd.compute_navigation_steps(50.45, -104.62, 50.451, -104.615)
    assert steps is None


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2: GET /rides/{id}/navigation-steps
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _clear_nav_steps_caches():
    from utils.redis_client import redis_delete_pattern

    await redis_delete_pattern("nav_steps:*")
    await redis_delete_pattern("nav_steps_leg:*")
    yield
    await redis_delete_pattern("nav_steps:*")
    await redis_delete_pattern("nav_steps_leg:*")


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


@contextmanager
def _endpoint_patches(ride, *, driver_pos, steps, capture, flag_on=True):
    async def _get_ride(_rid):
        return ride

    async def _get_rows(table, filters=None, **kw):
        if table == "drivers" and filters and "id" in filters:
            return [{"id": "drv_1", **driver_pos}] if driver_pos is not None else []
        return []

    async def _get_app_settings():
        return {"driver_turn_by_turn_enabled": flag_on}

    async def _compute_navigation_steps(flat, flng, tlat, tlng):
        capture["args"] = (flat, flng, tlat, tlng)
        capture["calls"] = capture.get("calls", 0) + 1
        return steps

    # Both spellings, same reasoning as test_accept_ride_service_area_gate.py:
    # the endpoint's dual-import (`from ...settings_loader import ...` /
    # `from settings_loader import ...`) can resolve to either module object
    # depending on import order, and the bare spelling isn't always loaded as
    # a separate module in every test run.
    with ExitStack() as stack:
        stack.enter_context(patch("backend.routes.rides._deps.db_supabase.get_ride", _get_ride))
        stack.enter_context(patch("backend.routes.rides._deps.db_supabase.get_rows", _get_rows))
        stack.enter_context(patch("backend.settings_loader.get_app_settings", _get_app_settings))
        with contextlib.suppress(ModuleNotFoundError, AttributeError):
            stack.enter_context(patch("settings_loader.get_app_settings", _get_app_settings))
        stack.enter_context(patch("utils.route_distance.compute_navigation_steps", _compute_navigation_steps))
        yield


@pytest.mark.asyncio
async def test_flag_off_returns_empty_without_touching_the_ride():
    from backend.routes import rides as rides_mod

    cap = {}
    with _endpoint_patches(
        _ride("in_progress"), driver_pos={"lat": 50.44, "lng": -104.63}, steps=[], capture=cap, flag_on=False
    ):
        out = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)
    assert out == {"steps": [], "destination": None}
    assert "calls" not in cap  # compute_navigation_steps never called while flagged off


@pytest.mark.asyncio
async def test_pre_trip_fetches_steps_to_pickup():
    from backend.routes import rides as rides_mod

    cap = {}
    steps = [
        {
            "instruction": "Turn right",
            "maneuver": "turn-right",
            "distanceMeters": 100,
            "startLocation": [50.44, -104.63],
            "endLocation": [50.45, -104.62],
        }
    ]
    with _endpoint_patches(
        _ride("driver_assigned"), driver_pos={"lat": 50.44, "lng": -104.63}, steps=steps, capture=cap
    ):
        out = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)
    assert out == {"steps": steps, "destination": "pickup"}
    assert cap["args"] == (50.44, -104.63, 50.45, -104.62)  # routed to PICKUP


@pytest.mark.asyncio
async def test_in_trip_fetches_steps_to_dropoff():
    from backend.routes import rides as rides_mod

    cap = {}
    steps = [
        {
            "instruction": "Continue straight",
            "maneuver": None,
            "distanceMeters": 300,
            "startLocation": [50.44, -104.63],
            "endLocation": [50.40, -104.66],
        }
    ]
    with _endpoint_patches(_ride("in_progress"), driver_pos={"lat": 50.44, "lng": -104.63}, steps=steps, capture=cap):
        out = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)
    assert out == {"steps": steps, "destination": "dropoff"}
    assert cap["args"] == (50.44, -104.63, 50.40, -104.66)  # routed to DROPOFF


@pytest.mark.asyncio
async def test_empty_when_not_active():
    from backend.routes import rides as rides_mod

    cap = {}
    with _endpoint_patches(_ride("searching"), driver_pos={"lat": 50.44, "lng": -104.63}, steps=None, capture=cap):
        out = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)
    assert out == {"steps": [], "destination": None}
    assert "calls" not in cap


@pytest.mark.asyncio
async def test_empty_when_no_driver_position():
    from backend.routes import rides as rides_mod

    cap = {}
    with _endpoint_patches(_ride("in_progress"), driver_pos=None, steps=None, capture=cap):
        out = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)
    assert out == {"steps": [], "destination": "dropoff"}  # leg known, just no live origin
    assert "calls" not in cap


@pytest.mark.asyncio
async def test_second_call_is_served_from_the_ride_scoped_cache():
    """Unlike /live-route, the cache key does NOT depend on driver position —
    a second call from a DIFFERENT position within the same leg must still
    hit the cache (see NAV_STEPS_CACHE_TTL_SECONDS's own comment)."""
    from backend.routes import rides as rides_mod

    cap = {}
    steps = [
        {
            "instruction": "Turn right",
            "maneuver": "turn-right",
            "distanceMeters": 100,
            "startLocation": [50.44, -104.63],
            "endLocation": [50.45, -104.62],
        }
    ]
    with _endpoint_patches(_ride("in_progress"), driver_pos={"lat": 50.44, "lng": -104.63}, steps=steps, capture=cap):
        first = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)

    # Driver has moved well past what /live-route's position bucket would
    # tolerate — the nav-steps cache must not care.
    with _endpoint_patches(_ride("in_progress"), driver_pos={"lat": 50.30, "lng": -104.90}, steps=steps, capture=cap):
        second = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)

    assert first == second == {"steps": steps, "destination": "dropoff"}
    assert cap["calls"] == 1  # compute_navigation_steps only called once


@pytest.mark.asyncio
async def test_force_refresh_bypasses_the_cache():
    from backend.routes import rides as rides_mod

    cap = {}
    steps_v1 = [
        {
            "instruction": "Turn right",
            "maneuver": "turn-right",
            "distanceMeters": 100,
            "startLocation": [50.44, -104.63],
            "endLocation": [50.45, -104.62],
        }
    ]
    steps_v2 = [
        {
            "instruction": "Turn left (recalculated)",
            "maneuver": "turn-left",
            "distanceMeters": 50,
            "startLocation": [50.44, -104.63],
            "endLocation": [50.45, -104.61],
        }
    ]

    with _endpoint_patches(
        _ride("in_progress"), driver_pos={"lat": 50.44, "lng": -104.63}, steps=steps_v1, capture=cap
    ):
        first = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)
    assert first["steps"] == steps_v1

    with _endpoint_patches(
        _ride("in_progress"), driver_pos={"lat": 50.44, "lng": -104.63}, steps=steps_v2, capture=cap
    ):
        refreshed = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER, force_refresh=True)
    assert refreshed["steps"] == steps_v2
    assert cap["calls"] == 2


@pytest.mark.asyncio
async def test_failed_fetch_is_not_cached_and_retried_next_call():
    from backend.routes import rides as rides_mod

    cap = {"calls": 0}

    async def _get_ride(_rid):
        return _ride("in_progress")

    async def _get_rows(table, filters=None, **kw):
        if table == "drivers" and filters and "id" in filters:
            return [{"id": "drv_1", "lat": 50.44, "lng": -104.63}]
        return []

    async def _get_app_settings():
        return {"driver_turn_by_turn_enabled": True}

    async def _compute_navigation_steps(flat, flng, tlat, tlng):
        cap["calls"] += 1
        return (
            None
            if cap["calls"] == 1
            else [
                {
                    "instruction": "Continue",
                    "maneuver": None,
                    "distanceMeters": 10,
                    "startLocation": [1, 2],
                    "endLocation": [1, 3],
                }
            ]
        )

    with ExitStack() as stack:
        stack.enter_context(patch("backend.routes.rides._deps.db_supabase.get_ride", _get_ride))
        stack.enter_context(patch("backend.routes.rides._deps.db_supabase.get_rows", _get_rows))
        stack.enter_context(patch("backend.settings_loader.get_app_settings", _get_app_settings))
        with contextlib.suppress(ModuleNotFoundError, AttributeError):
            stack.enter_context(patch("settings_loader.get_app_settings", _get_app_settings))
        stack.enter_context(patch("utils.route_distance.compute_navigation_steps", _compute_navigation_steps))
        first = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)
        second = await rides_mod.get_navigation_steps(ride_id="r1", current_user=RIDER)

    assert first["steps"] == []
    assert len(second["steps"]) == 1
    assert cap["calls"] == 2
