"""Tests for the OSRM /match road-distance provider and provider selection.

OSRM is preferred for billable road distance when OSRM_URL is configured; Google
Roads is the fallback. Both feed the 1/3x-3x sanity gate in complete_ride, so a
bad value can't corrupt a fare — these tests pin the contract: lng,lat order,
matched-distance summed across matchings (meters -> km), soft-None on errors,
and OSRM-first selection with Google fallback.
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
    # n trip_in_progress breadcrumbs around Regina.
    return [
        {"lat": 50.45 - i * 0.001, "lng": -104.62 - i * 0.001, "tracking_phase": "trip_in_progress", "accuracy": 8}
        for i in range(n)
    ]


# ── _osrm_radius ──────────────────────────────────────────────────────────────


def test_osrm_radius_clamped_and_defaulted():
    assert rd._osrm_radius({"accuracy": 8}) == "10"  # floored to MIN
    assert rd._osrm_radius({"accuracy": 30}) == "30"
    assert rd._osrm_radius({"accuracy": 999}) == "50"  # capped at MAX
    assert rd._osrm_radius({}) == "20"  # default when missing
    assert rd._osrm_radius({"accuracy": "bad"}) == "20"  # non-numeric -> default


# ── _compute_via_osrm ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_osrm_sums_matchings_meters_to_km():
    capture = {}
    resp = _FakeResp(payload={"code": "Ok", "matchings": [{"distance": 1200.0}, {"distance": 300.0}]})
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=resp, capture=capture)):
        km = await rd._compute_via_osrm(_trip(6), "http://osrm:5000/")

    assert km == 1.5  # (1200 + 300) m summed across matchings -> km
    # lng,lat order and /match/v1/driving path, no double slash from trailing /
    assert "/match/v1/driving/" in capture["url"]
    assert "-104.62,50.45" in capture["url"]  # first coord is lng,lat
    assert capture["params"]["gaps"] == "ignore"
    assert capture["params"]["tidy"] == "true"


@pytest.mark.asyncio
async def test_osrm_non_ok_code_returns_none():
    resp = _FakeResp(payload={"code": "NoMatch", "matchings": []})
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=resp)):
        km = await rd._compute_via_osrm(_trip(6), "http://osrm:5000")
    assert km is None


@pytest.mark.asyncio
async def test_osrm_http_error_returns_none():
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(status_code=502))):
        km = await rd._compute_via_osrm(_trip(6), "http://osrm:5000")
    assert km is None


@pytest.mark.asyncio
async def test_osrm_network_exception_returns_none():
    with patch.object(rd.httpx, "AsyncClient", _client_factory(exc=RuntimeError("boom"))):
        km = await rd._compute_via_osrm(_trip(6), "http://osrm:5000")
    assert km is None


@pytest.mark.asyncio
async def test_osrm_zero_distance_returns_none():
    resp = _FakeResp(payload={"code": "Ok", "matchings": [{"distance": 0}]})
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=resp)):
        km = await rd._compute_via_osrm(_trip(6), "http://osrm:5000")
    assert km is None


# ── compute_road_distance_km provider selection ───────────────────────────────


@pytest.mark.asyncio
async def test_prefers_osrm_when_configured_and_skips_google():
    async def _fake_app_settings():
        return {"google_maps_api_key": "should-not-be-used"}

    google_called = {"n": 0}

    async def _fake_osrm(points, url):
        assert url == "http://osrm:5000"
        return 7.42

    async def _fake_google(points, key):
        google_called["n"] += 1
        return 9.99

    with (
        patch.object(rd, "get_app_settings", _fake_app_settings),
        patch.object(rd.settings, "OSRM_URL", "http://osrm:5000"),
        patch.object(rd, "_compute_via_osrm", _fake_osrm),
        patch.object(rd, "_compute_via_google_roads", _fake_google),
    ):
        km = await rd.compute_road_distance_km(_trip(6))

    assert km == 7.42
    assert google_called["n"] == 0, "Google must not be called when OSRM returns a value"


@pytest.mark.asyncio
async def test_falls_back_to_google_when_osrm_returns_none():
    async def _fake_app_settings():
        return {"google_maps_api_key": "key-123"}

    async def _fake_osrm(points, url):
        return None  # OSRM down / no match

    async def _fake_google(points, key):
        assert key == "key-123"
        return 5.5

    with (
        patch.object(rd, "get_app_settings", _fake_app_settings),
        patch.object(rd.settings, "OSRM_URL", "http://osrm:5000"),
        patch.object(rd, "_compute_via_osrm", _fake_osrm),
        patch.object(rd, "_compute_via_google_roads", _fake_google),
    ):
        km = await rd.compute_road_distance_km(_trip(6))

    assert km == 5.5


@pytest.mark.asyncio
async def test_db_override_osrm_url_wins_over_env():
    captured = {}

    async def _fake_app_settings():
        return {"osrm_url": "http://override:5000"}

    async def _fake_osrm(points, url):
        captured["url"] = url
        return 3.3

    with (
        patch.object(rd, "get_app_settings", _fake_app_settings),
        patch.object(rd.settings, "OSRM_URL", "http://env:5000"),
        patch.object(rd, "_compute_via_osrm", _fake_osrm),
    ):
        km = await rd.compute_road_distance_km(_trip(6))

    assert km == 3.3
    assert captured["url"] == "http://override:5000"


@pytest.mark.asyncio
async def test_returns_none_when_too_few_trip_points():
    async def _fake_app_settings():
        return {"osrm_url": "http://osrm:5000"}

    with patch.object(rd, "get_app_settings", _fake_app_settings):
        km = await rd.compute_road_distance_km(_trip(3))  # < _MIN_POINTS
    assert km is None
