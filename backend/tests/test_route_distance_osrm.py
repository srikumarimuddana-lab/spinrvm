"""Tests for the OSRM /match road provider (distance + geometry) and selection.

OSRM is preferred for billable road distance + the saved road-snapped polyline
when OSRM_URL is configured; Google Roads is the fallback. Both feed the
1/3x-3x sanity gate in complete_ride, so a bad value can't corrupt a fare or
persist a bogus map. These pin: lng,lat request order, geojson geometry parsed
to [[lat,lng],...], distance summed across matchings (m -> km), soft-None on
errors, OSRM-first selection, and the back-compat distance-only shim.
"""

from unittest.mock import patch

import pytest

from backend.utils import route_distance as rd


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal async-context httpx.AsyncClient stand-in."""

    def __init__(self, resp=None, exc=None, capture=None):
        self._resp = resp
        self._exc = exc
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, url, params=None):
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["params"] = params
        if self._exc:
            raise self._exc
        return self._resp


def _client_factory(resp=None, exc=None, capture=None):
    return lambda *a, **kw: _FakeClient(resp=resp, exc=exc, capture=capture)


def _trip(n=6):
    return [
        {"lat": 50.45 - i * 0.001, "lng": -104.62 - i * 0.001, "tracking_phase": "trip_in_progress", "accuracy": 8}
        for i in range(n)
    ]


def _osrm_payload():
    return {
        "code": "Ok",
        "matchings": [
            {"distance": 1200.0, "geometry": {"coordinates": [[-104.62, 50.45], [-104.63, 50.44]]}},
            {"distance": 300.0, "geometry": {"coordinates": [[-104.63, 50.44], [-104.64, 50.43]]}},
        ],
    }


# ── _osrm_radius ──────────────────────────────────────────────────────────────


def test_osrm_radius_clamped_and_defaulted():
    assert rd._osrm_radius({"accuracy": 8}) == "10"  # floored to MIN
    assert rd._osrm_radius({"accuracy": 30}) == "30"
    assert rd._osrm_radius({"accuracy": 999}) == "50"  # capped at MAX
    assert rd._osrm_radius({}) == "20"  # default when missing
    assert rd._osrm_radius({"accuracy": "bad"}) == "20"  # non-numeric -> default


# ── _compute_via_osrm (distance + geometry) ───────────────────────────────────


@pytest.mark.asyncio
async def test_osrm_returns_distance_and_geometry():
    capture = {}
    with patch.object(
        rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=_osrm_payload()), capture=capture)
    ):
        result = await rd._compute_via_osrm(_trip(6), "http://osrm:5000/")

    assert result is not None
    distance_km, polyline = result
    assert distance_km == 1.5  # (1200 + 300) m across matchings -> km
    # geojson [lng,lat] -> stored [lat,lng]; matchings concatenated
    assert polyline[0] == [50.45, -104.62]
    assert polyline[-1] == [50.43, -104.64]
    # requests geometry, and uses lng,lat order + /match/v1/driving
    assert capture["params"]["overview"] == "full"
    assert capture["params"]["geometries"] == "geojson"
    assert "/match/v1/driving/" in capture["url"]
    assert "-104.62,50.45" in capture["url"]


@pytest.mark.asyncio
async def test_osrm_polyline_capped():
    # one matching with a long geometry -> capped to _MAX_ROAD_POLYLINE_POINTS
    long_coords = [[-104.6 - i * 0.0001, 50.4 + i * 0.0001] for i in range(1000)]
    payload = {"code": "Ok", "matchings": [{"distance": 5000.0, "geometry": {"coordinates": long_coords}}]}
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))):
        result = await rd._compute_via_osrm(_trip(6), "http://osrm:5000")
    assert result is not None
    _, polyline = result
    assert len(polyline) <= rd._MAX_ROAD_POLYLINE_POINTS


@pytest.mark.asyncio
async def test_osrm_non_ok_code_returns_none():
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload={"code": "NoMatch"}))):
        assert await rd._compute_via_osrm(_trip(6), "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_osrm_http_error_returns_none():
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(status_code=502))):
        assert await rd._compute_via_osrm(_trip(6), "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_osrm_network_exception_returns_none():
    with patch.object(rd.httpx, "AsyncClient", _client_factory(exc=RuntimeError("boom"))):
        assert await rd._compute_via_osrm(_trip(6), "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_osrm_zero_distance_returns_none():
    payload = {"code": "Ok", "matchings": [{"distance": 0, "geometry": {"coordinates": []}}]}
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))):
        assert await rd._compute_via_osrm(_trip(6), "http://osrm:5000") is None


# ── compute_road_route — provider selection ───────────────────────────────────


@pytest.mark.asyncio
async def test_prefers_osrm_and_returns_polyline():
    async def _fake_app_settings():
        return {"google_maps_api_key": "should-not-be-used"}

    google_called = {"n": 0}

    async def _fake_osrm(points, url):
        assert url == "http://osrm:5000"
        return (7.42, [[50.4, -104.6], [50.41, -104.61]])

    async def _fake_google(points, key):
        google_called["n"] += 1
        return (9.99, [[1, 1]])

    with (
        patch.object(rd, "get_app_settings", _fake_app_settings),
        patch.object(rd.settings, "OSRM_URL", "http://osrm:5000"),
        patch.object(rd, "_compute_via_osrm", _fake_osrm),
        patch.object(rd, "_compute_via_google_roads", _fake_google),
    ):
        result = await rd.compute_road_route(_trip(6))

    assert result == {
        "distance_km": 7.42,
        "polyline": [[50.4, -104.6], [50.41, -104.61]],
        "provider": "osrm_match",
        "input_points_count": 6,
    }
    assert google_called["n"] == 0, "Google must not be called when OSRM returns a value"


@pytest.mark.asyncio
async def test_falls_back_to_google_when_osrm_none():
    async def _fake_app_settings():
        return {"google_maps_api_key": "key-123"}

    async def _fake_osrm(points, url):
        return None

    async def _fake_google(points, key):
        assert key == "key-123"
        return (5.5, [[50.0, -104.0]])

    with (
        patch.object(rd, "get_app_settings", _fake_app_settings),
        patch.object(rd.settings, "OSRM_URL", "http://osrm:5000"),
        patch.object(rd, "_compute_via_osrm", _fake_osrm),
        patch.object(rd, "_compute_via_google_roads", _fake_google),
    ):
        result = await rd.compute_road_route(_trip(6))

    assert result == {
        "distance_km": 5.5,
        "polyline": [[50.0, -104.0]],
        "provider": "google_roads",
        "input_points_count": 6,
    }


@pytest.mark.asyncio
async def test_db_override_osrm_url_wins_over_env():
    captured = {}

    async def _fake_app_settings():
        return {"osrm_url": "http://override:5000"}

    async def _fake_osrm(points, url):
        captured["url"] = url
        return (3.3, [])

    with (
        patch.object(rd, "get_app_settings", _fake_app_settings),
        patch.object(rd.settings, "OSRM_URL", "http://env:5000"),
        patch.object(rd, "_compute_via_osrm", _fake_osrm),
    ):
        result = await rd.compute_road_route(_trip(6))

    assert result["distance_km"] == 3.3
    assert captured["url"] == "http://override:5000"


@pytest.mark.asyncio
async def test_returns_none_when_too_few_trip_points():
    async def _fake_app_settings():
        return {"osrm_url": "http://osrm:5000"}

    with patch.object(rd, "get_app_settings", _fake_app_settings):
        assert await rd.compute_road_route(_trip(3)) is None  # < _MIN_POINTS


@pytest.mark.asyncio
async def test_distance_shim_returns_float_only():
    """compute_road_distance_km stays back-compat: distance float or None."""

    async def _fake_app_settings():
        return {}

    async def _fake_route(breadcrumbs):
        return {"distance_km": 4.2, "polyline": [[50.0, -104.0]]}

    with patch.object(rd, "compute_road_route", _fake_route), patch.object(rd, "get_app_settings", _fake_app_settings):
        assert await rd.compute_road_distance_km(_trip(6)) == 4.2

    async def _none(breadcrumbs):
        return None

    with patch.object(rd, "compute_road_route", _none):
        assert await rd.compute_road_distance_km(_trip(6)) is None


# ── compute_route (live OSRM /route — route line + ETA) ───────────────────────


async def _empty_settings():
    return {}


@pytest.mark.asyncio
async def test_compute_route_returns_polyline_eta_distance():
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 1500.0,
                "duration": 300.0,
                "geometry": {"coordinates": [[-104.62, 50.45], [-104.63, 50.44]]},
            }
        ],
    }
    cap = {}
    with (
        patch.object(rd, "get_app_settings", _empty_settings),
        patch.object(rd.settings, "OSRM_URL", "http://osrm:5000"),
        patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload), capture=cap)),
    ):
        out = await rd.compute_route(50.45, -104.62, 50.44, -104.63)

    assert out["eta_seconds"] == 300
    assert out["distance_km"] == 1.5
    assert out["polyline"][0] == [50.45, -104.62]  # geojson [lng,lat] -> [lat,lng]
    assert "/route/v1/driving/" in cap["url"]
    assert "-104.62,50.45" in cap["url"]  # lng,lat order in the request


@pytest.mark.asyncio
async def test_compute_route_none_when_osrm_unset():
    with patch.object(rd, "get_app_settings", _empty_settings), patch.object(rd.settings, "OSRM_URL", ""):
        assert await rd.compute_route(50.45, -104.62, 50.44, -104.63) is None


@pytest.mark.asyncio
async def test_compute_route_non_ok_returns_none():
    with (
        patch.object(rd, "get_app_settings", _empty_settings),
        patch.object(rd.settings, "OSRM_URL", "http://osrm:5000"),
        patch.object(
            rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload={"code": "NoRoute", "routes": []}))
        ),
    ):
        assert await rd.compute_route(50.45, -104.62, 50.44, -104.63) is None
