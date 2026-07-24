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

# Classic Google-documented encoded polyline → 3 points:
#   (38.5, -120.2), (40.7, -120.95), (43.252, -126.453)
ENCODED_POLYLINE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"

_TARGET = "backend.routes.rides._shared"


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
        return await _fetch_directions_route(
            52.13, -106.67, 52.12, -106.65, api_key="k"
        )


class TestRoadDistanceParsing:
    async def test_single_leg_distance_duration_polyline(self):
        payload = _ok_payload(
            [{"distance": {"value": 1800}, "duration": {"value": 360}}]
        )
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
            route = await _fetch_directions_route(
                52.13, -106.67, 52.12, -106.65, api_key=""
            )
        assert route is None
        client_cls.assert_not_called()

    async def test_empty_polyline_returns_none(self):
        payload = {
            "status": "OK",
            "routes": [{"overview_polyline": {"points": ""}, "legs": []}],
        }
        route = await _call_route(payload)
        assert route is None


class TestPolylineWrapper:
    async def test_wrapper_returns_only_points(self):
        from backend.routes.rides._shared import _fetch_directions_polyline

        payload = _ok_payload(
            [{"distance": {"value": 1800}, "duration": {"value": 360}}]
        )
        with patch(
            f"{_TARGET}._httpx.AsyncClient",
            return_value=_mock_async_client(payload),
        ):
            pts = await _fetch_directions_polyline(
                52.13, -106.67, 52.12, -106.65, api_key="k"
            )
        assert isinstance(pts, list)
        assert len(pts) == 3

    async def test_wrapper_none_when_route_none(self):
        from backend.routes.rides._shared import _fetch_directions_polyline

        with patch(
            f"{_TARGET}._httpx.AsyncClient",
            return_value=_mock_async_client({"status": "NOT_FOUND", "routes": []}),
        ):
            pts = await _fetch_directions_polyline(
                52.13, -106.67, 52.12, -106.65, api_key="k"
            )
        assert pts is None
