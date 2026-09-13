"""Tests for the Google Directions road-route parser.

`_fetch_directions_route` (backend/routes/rides/_shared.py) is the source of
the authoritative *road* distance a fare is priced on — the fix for rides
being billed on straight-line haversine (e.g. 0.7 km) instead of the real
road route (1.8 km). It sums distance/duration across all legs so multi-stop
rides accumulate the full path, and degrades to None (→ haversine fallback)
on any malformed/absent response. `_fetch_directions_polyline` is now a thin
back-compat wrapper returning only the overview polyline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Classic Google-documented encoded polyline → 3 points:
#   (38.5, -120.2), (40.7, -120.95), (43.252, -126.453)
ENCODED_POLYLINE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"

_TARGET = "backend.routes.rides._shared"


@pytest.fixture(autouse=True)
def _isolated_directions_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8 added a 30s Redis cache to `_fetch_directions_route`, keyed on the
    call's coordinates. Every test in this file calls it with the same fixed
    origin/destination (52.13, -106.67, 52.12, -106.65) -- without a fresh
    cache per test, an earlier test's cached result would leak into a later
    one expecting different `legs`/status/etc. Mirrors conftest.py's
    `mock_redis` fixture body, just applied file-wide via autouse rather than
    opt-in per test."""
    from backend.utils import redis_client as rc

    monkeypatch.setattr(rc, "_local", {})


def _mock_async_client(payload: dict) -> MagicMock:
    """Build an httpx.AsyncClient stand-in whose GET returns `payload`."""
    resp = MagicMock()
    resp.json = MagicMock(return_value=payload)
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)
    return client


def _ok_payload(legs: list) -> dict:
    return {
        "status": "OK",
        "routes": [
            {
                "overview_polyline": {"points": ENCODED_POLYLINE},
                "legs": legs,
            }
        ],
    }


async def _call_route(payload: dict):
    from backend.routes.rides._shared import _fetch_directions_route

    with patch(
        f"{_TARGET}._httpx.AsyncClient",
        return_value=_mock_async_client(payload),
    ):
        return await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")


class TestRoadDistanceParsing:
    async def test_single_leg_distance_duration_polyline(self):
        payload = _ok_payload([{"distance": {"value": 1800}, "duration": {"value": 360}}])
        route = await _call_route(payload)
        assert route is not None
        assert route["distance_km"] == 1.8
        assert route["duration_s"] == 360
        assert len(route["polyline"]) == 3
        assert abs(route["polyline"][0][0] - 38.5) < 0.01

    async def test_multi_leg_sums_distance_and_duration(self):
        payload = _ok_payload(
            [
                {"distance": {"value": 1800}, "duration": {"value": 360}},
                {"distance": {"value": 700}, "duration": {"value": 120}},
            ]
        )
        route = await _call_route(payload)
        assert route["distance_km"] == 2.5
        assert route["duration_s"] == 480

    async def test_missing_legs_yields_none_distance_but_keeps_polyline(self):
        payload = _ok_payload([])
        route = await _call_route(payload)
        assert route is not None
        assert route["distance_km"] is None
        assert route["duration_s"] is None
        assert len(route["polyline"]) == 3

    async def test_malformed_leg_field_degrades_to_none_distance(self):
        # A leg with no numeric distance value must not poison the sum with a
        # partial figure — it degrades to the haversine fallback.
        payload = _ok_payload([{"distance": {}, "duration": {}}])
        route = await _call_route(payload)
        assert route["distance_km"] is None


class TestSoftFailures:
    async def test_status_not_ok_returns_none(self):
        route = await _call_route({"status": "ZERO_RESULTS", "routes": []})
        assert route is None

    async def test_no_api_key_short_circuits(self):
        from backend.routes.rides._shared import _fetch_directions_route

        # Empty key → None without any HTTP call (AsyncClient must never run).
        with patch(f"{_TARGET}._httpx.AsyncClient") as client_cls:
            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="")
        assert route is None
        client_cls.assert_not_called()

    async def test_empty_polyline_returns_none(self):
        # Neither a billable distance NOR a drawable line — nothing useful.
        payload = {
            "status": "OK",
            "routes": [{"overview_polyline": {"points": ""}, "legs": []}],
        }
        route = await _call_route(payload)
        assert route is None

    async def test_missing_polyline_still_returns_road_distance(self):
        """The overview polyline only draws the map line — it must never gate
        the billable distance. This used to `return None`, throwing away a
        valid legs[].distance and dropping the fare to straight-line
        haversine. Haversine is always <= road, so that was a silent,
        one-directional undercharge every time Google answered OK without
        overview geometry.
        """
        payload = {
            "status": "OK",
            "routes": [
                {
                    "overview_polyline": {"points": ""},
                    "legs": [{"distance": {"value": 16600}, "duration": {"value": 1500}}],
                }
            ],
        }
        route = await _call_route(payload)
        assert route is not None, "a missing map line must not discard the road distance"
        assert route["distance_km"] == 16.6
        assert route["duration_s"] == 1500
        assert route["polyline"] == []

    async def test_degenerate_polyline_still_returns_road_distance(self):
        """Same for a polyline that decodes to fewer than 2 points: unusable
        for rendering, irrelevant to pricing."""
        single_point = "_p~iF~ps|U"  # decodes to one coordinate
        payload = {
            "status": "OK",
            "routes": [
                {
                    "overview_polyline": {"points": single_point},
                    "legs": [{"distance": {"value": 16600}, "duration": {"value": 1500}}],
                }
            ],
        }
        route = await _call_route(payload)
        assert route is not None
        assert route["distance_km"] == 16.6
        assert route["polyline"] == []


class TestBudgetGate:
    """Roadmap R2: the highest-volume Directions call site in the app had no
    budget accounting at all, unlike every sibling call site. Scenario this
    covers — before: a budget spike here never tripped the daily breaker and
    Google kept being called and billed past the ceiling; after: the call is
    skipped and haversine's existing fallback (`select_fare_distance`) takes
    over, exactly like every other exhausted-budget Maps call site already
    does.
    """

    async def test_budget_exceeded_skips_http_call_entirely(self):
        from backend.routes.rides._shared import _fetch_directions_route

        with (
            patch(f"{_TARGET}.reserve_budget", AsyncMock(return_value=(False, 5.5, 5.0))),
            patch(f"{_TARGET}._httpx.AsyncClient") as client_cls,
        ):
            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert route is None, "budget-exhausted state must soft-fail into haversine, not raise"
        client_cls.assert_not_called(), "no Google call — and no spend — once the breaker is open"

    async def test_budget_allowed_reserves_spend_before_the_call(self):
        """C104: this call site now reserves spend atomically (before the
        Google call) via reserve_budget(), not check_budget()+record_call()
        after. Confirms the reservation happens with the right SKU name."""
        payload = _ok_payload([{"distance": {"value": 1800}, "duration": {"value": 360}}])
        reserve_mock = AsyncMock(return_value=(True, 0.0, 5.0))
        with (
            patch(f"{_TARGET}.reserve_budget", reserve_mock),
            patch(
                f"{_TARGET}._httpx.AsyncClient",
                return_value=_mock_async_client(payload),
            ),
        ):
            from backend.routes.rides._shared import _fetch_directions_route

            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert route is not None
        reserve_mock.assert_awaited_once_with("directions")

    async def test_budget_reserved_even_on_non_ok_status(self):
        """The reservation happens before the Google call, so it's already
        made regardless of what Google eventually answers — unlike the old
        record_call()-after-the-fact placement, this doesn't depend on the
        response at all."""
        reserve_mock = AsyncMock(return_value=(True, 0.0, 5.0))
        with (
            patch(f"{_TARGET}.reserve_budget", reserve_mock),
            patch(
                f"{_TARGET}._httpx.AsyncClient",
                return_value=_mock_async_client({"status": "ZERO_RESULTS", "routes": []}),
            ),
        ):
            from backend.routes.rides._shared import _fetch_directions_route

            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert route is None
        reserve_mock.assert_awaited_once_with("directions")

    async def test_no_api_key_never_checks_budget(self):
        """An empty key short-circuits before any budget/HTTP work — no
        change from the pre-existing behavior this test already covered."""
        from backend.routes.rides._shared import _fetch_directions_route

        with patch(f"{_TARGET}.reserve_budget") as budget_mock:
            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="")
        assert route is None
        budget_mock.assert_not_called()


class TestFareDirectionsCache:
    """R8 (docs/audit/ride-experience/ROADMAP.md): fine-precision (5-decimal,
    ~1m) Redis cache, 30s TTL, deliberately NOT route_distance.py's coarser
    ~110m origin grid -- two fixed rider-chosen endpoints price the fare
    directly, so a coarse grid could bill two ~100m-apart pins on the same
    cached road distance."""

    async def test_cache_hit_skips_http_call_and_budget_check(self):
        from backend.routes.rides._shared import _fare_directions_cache_key, _fetch_directions_route
        from backend.utils.redis_client import redis_set

        cache_key = _fare_directions_cache_key(52.13, -106.67, 52.12, -106.65, None)
        await redis_set(
            cache_key,
            '{"polyline": [[38.5, -120.2]], "distance_km": 1.8, "duration_s": 360}',
            ttl=30,
        )

        with (
            patch(f"{_TARGET}.reserve_budget") as budget_mock,
            patch(f"{_TARGET}._httpx.AsyncClient") as client_cls,
        ):
            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")

        assert route == {"polyline": [[38.5, -120.2]], "distance_km": 1.8, "duration_s": 360}
        budget_mock.assert_not_called()
        client_cls.assert_not_called()

    async def test_cache_miss_populates_cache_for_next_call(self):
        from backend.routes.rides._shared import _fetch_directions_route
        from backend.utils.redis_client import redis_get

        payload = _ok_payload([{"distance": {"value": 1800}, "duration": {"value": 360}}])
        with (
            patch(f"{_TARGET}.reserve_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch(f"{_TARGET}._httpx.AsyncClient", return_value=_mock_async_client(payload)),
        ):
            first = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert first is not None

        from backend.routes.rides._shared import _fare_directions_cache_key

        cached = await redis_get(_fare_directions_cache_key(52.13, -106.67, 52.12, -106.65, None))
        assert cached is not None

        # Second call must be served from cache -- no second HTTP round trip.
        with patch(f"{_TARGET}._httpx.AsyncClient") as client_cls:
            second = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert second == first
        client_cls.assert_not_called()

    async def test_distinct_pins_a_hundred_meters_apart_do_not_share_a_cache_entry(self):
        """The whole point of the 5-decimal (~1m) key vs route_distance.py's
        ~110m grid: two riders whose pins differ by ~0.001 deg (~100m at this
        latitude) must never be billed on each other's cached road distance."""
        from backend.routes.rides._shared import _fare_directions_cache_key

        key_a = _fare_directions_cache_key(52.1300, -106.6700, 52.12, -106.65, None)
        key_b = _fare_directions_cache_key(52.1310, -106.6700, 52.12, -106.65, None)  # ~111m north
        assert key_a != key_b

    async def test_waypoints_change_the_cache_key(self):
        from backend.routes.rides._shared import _fare_directions_cache_key

        no_stop = _fare_directions_cache_key(52.13, -106.67, 52.12, -106.65, None)
        with_stop = _fare_directions_cache_key(52.13, -106.67, 52.12, -106.65, [{"lat": 52.125, "lng": -106.66}])
        assert no_stop != with_stop

    async def test_none_distance_result_is_never_cached(self):
        """A malformed leg degrades distance_km to None (see
        TestRoadDistanceParsing.test_malformed_leg_field_degrades_to_none_distance)
        while the polyline can still decode -- that response must never be
        cached. booking.py's no-token safety-net re-derive calls this same
        function and feeds distance_km straight into the billed fare; caching
        a null-distance answer would let an unrelated earlier estimate call
        silently force a later booking confirm onto the haversine fallback
        for the TTL window (spinr-money-auditor finding)."""
        from backend.routes.rides._shared import _fare_directions_cache_key, _fetch_directions_route
        from backend.utils.redis_client import redis_get

        payload = _ok_payload([{"distance": {}, "duration": {}}])  # malformed leg
        with (
            patch(f"{_TARGET}.reserve_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch(f"{_TARGET}._httpx.AsyncClient", return_value=_mock_async_client(payload)),
        ):
            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")

        assert route is not None
        assert route["distance_km"] is None
        assert len(route["polyline"]) == 3  # polyline still decoded fine

        cached = await redis_get(_fare_directions_cache_key(52.13, -106.67, 52.12, -106.65, None))
        assert cached is None, "a null-distance result must never be cached"

    async def test_cache_get_failure_falls_through_to_google_call(self):
        """A Redis outage on the read side must never break pricing -- it
        degrades to exactly the pre-cache behaviour (always call Google)."""
        from backend.routes.rides._shared import _fetch_directions_route

        payload = _ok_payload([{"distance": {"value": 1800}, "duration": {"value": 360}}])
        with (
            patch(f"{_TARGET}.redis_get", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch(f"{_TARGET}.reserve_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch(f"{_TARGET}._httpx.AsyncClient", return_value=_mock_async_client(payload)),
        ):
            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert route is not None
        assert route["distance_km"] == 1.8

    async def test_cache_set_failure_does_not_break_a_successful_response(self):
        """A Redis outage on the write side must not turn a successful Google
        response into a failure -- the caller still gets a real route."""
        from backend.routes.rides._shared import _fetch_directions_route

        payload = _ok_payload([{"distance": {"value": 1800}, "duration": {"value": 360}}])
        with (
            patch(f"{_TARGET}.redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch(f"{_TARGET}.reserve_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch(f"{_TARGET}._httpx.AsyncClient", return_value=_mock_async_client(payload)),
        ):
            route = await _fetch_directions_route(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert route is not None
        assert route["distance_km"] == 1.8


class TestPolylineWrapper:
    async def test_wrapper_returns_only_points(self):
        from backend.routes.rides._shared import _fetch_directions_polyline

        payload = _ok_payload([{"distance": {"value": 1800}, "duration": {"value": 360}}])
        with patch(
            f"{_TARGET}._httpx.AsyncClient",
            return_value=_mock_async_client(payload),
        ):
            pts = await _fetch_directions_polyline(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert isinstance(pts, list)
        assert len(pts) == 3

    async def test_wrapper_none_when_route_none(self):
        from backend.routes.rides._shared import _fetch_directions_polyline

        with patch(
            f"{_TARGET}._httpx.AsyncClient",
            return_value=_mock_async_client({"status": "NOT_FOUND", "routes": []}),
        ):
            pts = await _fetch_directions_polyline(52.13, -106.67, 52.12, -106.65, api_key="k")
        assert pts is None
