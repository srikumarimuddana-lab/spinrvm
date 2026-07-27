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
        {
            "lat": 50.45 - i * 0.001,
            "lng": -104.62 - i * 0.001,
            "tracking_phase": "trip_in_progress",
            "accuracy": 8,
        }
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


def test_osrm_timestamp_accepts_iso_seconds_and_expo_millis():
    assert rd._osrm_timestamp({"timestamp": "2026-07-09T12:34:56Z"}) == 1783600496
    assert rd._osrm_timestamp({"timestamp": 1783600496}) == 1783600496
    assert rd._osrm_timestamp({"timestamp": 1783600496000}) == 1783600496
    assert rd._osrm_timestamp({"timestamp": "bad"}) is None


def test_osrm_bearing_uses_heading_when_available():
    assert rd._osrm_bearing({"heading": 361.2}) == "1,45"
    assert rd._osrm_bearing({"bearing": 90}) == "90,45"
    assert rd._osrm_bearing({"course": "bad"}) == ""


# ── _compute_via_osrm (distance + geometry) ───────────────────────────────────


@pytest.mark.asyncio
async def test_osrm_returns_distance_and_geometry():
    trip = _trip(6)
    for i, p in enumerate(trip):
        p["captured_at"] = f"2026-07-09T12:00:{i:02d}Z"
        p["heading"] = 180 + i
    capture = {}
    with patch.object(
        rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=_osrm_payload()), capture=capture)
    ):
        result = await rd._compute_via_osrm(trip, "http://osrm:5000/")

    assert result is not None
    distance_km, polyline = result
    assert distance_km == 1.5  # (1200 + 300) m across matchings -> km
    # geojson [lng,lat] -> stored [lat,lng]; matchings concatenated
    assert polyline[0] == [50.45, -104.62]
    assert polyline[-1] == [50.43, -104.64]
    # requests geometry, and uses lng,lat order + /match/v1/driving
    assert capture["params"]["overview"] == "full"
    assert capture["params"]["geometries"] == "geojson"
    assert capture["params"]["gaps"] == "split"
    assert capture["params"]["timestamps"] == "1783598400;1783598401;1783598402;1783598403;1783598404;1783598405"
    assert capture["params"]["bearings"] == "180,45;181,45;182,45;183,45;184,45;185,45"
    assert "/match/v1/driving/" in capture["url"]
    assert "-104.62,50.45" in capture["url"]


@pytest.mark.asyncio
async def test_osrm_omits_optional_hints_when_unavailable():
    capture = {}
    with patch.object(
        rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=_osrm_payload()), capture=capture)
    ):
        result = await rd._compute_via_osrm(_trip(6), "http://osrm:5000/")

    assert result is not None
    assert "timestamps" not in capture["params"]
    assert "bearings" not in capture["params"]


@pytest.mark.asyncio
async def test_osrm_omits_legacy_server_receive_timestamps():
    trip = _trip(6)
    for i, point in enumerate(trip):
        point["timestamp"] = f"2026-07-09T12:00:{i:02d}Z"
    capture = {}
    with patch.object(
        rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=_osrm_payload()), capture=capture)
    ):
        result = await rd._compute_via_osrm(trip, "http://osrm:5000/")

    assert result is not None
    assert "timestamps" not in capture["params"]


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


@pytest.mark.asyncio
async def test_segmented_matching_chunks_293_points_with_overlap_and_keeps_osrm_matchings_separate():
    calls = []

    async def _fake_app_settings():
        return {"osrm_url": "http://osrm:5000"}

    async def _fake_osrm_matchings(points, _url):
        calls.append(points)
        return [
            (1.0, [[points[0]["lat"], points[0]["lng"]], [points[-1]["lat"], points[-1]["lng"]]]),
            (0.5, [[points[0]["lat"] + 0.0001, points[0]["lng"]], [points[-1]["lat"] + 0.0001, points[-1]["lng"]]]),
        ]

    points = _trip(293)
    with (
        patch.object(rd, "get_app_settings", _fake_app_settings),
        patch.object(rd, "_compute_osrm_chunk_matchings", _fake_osrm_matchings),
    ):
        result = await rd.compute_segmented_road_route([points])

    assert calls and all(len(call) <= 100 for call in calls)
    assert calls[0][-10:] == calls[1][:10]
    matched = result["segments"][0]["matched_segments"]
    assert len(matched) == len(calls) * 2
    assert matched[0]["polyline"] != matched[1]["polyline"]
    assert result["distance_km"] == round(len(calls) * 1.5, 3)


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
async def test_compute_route_none_when_osrm_unset_and_fallback_disabled():
    with (
        patch.object(rd, "get_app_settings", _empty_settings),
        patch.object(rd.settings, "OSRM_URL", ""),
        patch.object(rd.settings, "OSRM_FALLBACK_URL", "", create=True),
    ):
        assert await rd.compute_route(50.45, -104.62, 50.44, -104.63) is None


@pytest.mark.asyncio
async def test_compute_route_uses_public_fallback_when_unconfigured():
    """No self-hosted OSRM → light live-routing calls use OSRM_FALLBACK_URL.

    This was the 'route line never updates' bug: an unset OSRM_URL made
    /rides/{id}/live-route always return an empty polyline, so both apps kept
    drawing the static booking-time planned_route_polyline.
    """
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 900.0,
                "duration": 120.0,
                "geometry": {"coordinates": [[-104.62, 50.45], [-104.63, 50.44]]},
            }
        ],
    }
    cap = {}
    with (
        patch.object(rd, "get_app_settings", _empty_settings),
        patch.object(rd.settings, "OSRM_URL", ""),
        patch.object(rd.settings, "OSRM_FALLBACK_URL", "https://fallback.example", create=True),
        patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload), capture=cap)),
    ):
        out = await rd.compute_route(50.45, -104.62, 50.44, -104.63)

    assert out is not None
    assert out["eta_seconds"] == 120
    assert cap["url"].startswith("https://fallback.example/route/v1/driving/")


@pytest.mark.asyncio
async def test_match_billing_path_ignores_public_fallback():
    """/match (billable distance) must NOT silently use the demo server."""
    called = {"osrm": False, "google": False}

    async def _fake_osrm(points, url):
        called["osrm"] = True
        return (1.0, [])

    async def _fake_google(points, key):
        called["google"] = True
        return (1.0, [])

    with (
        patch.object(rd, "get_app_settings", _empty_settings),
        patch.object(rd.settings, "OSRM_URL", ""),
        patch.object(rd.settings, "OSRM_FALLBACK_URL", "https://fallback.example", create=True),
        patch.object(rd, "_compute_via_osrm", _fake_osrm),
        patch.object(rd, "_compute_via_google_roads", _fake_google),
    ):
        # No google key in settings either → None, but the assertion that
        # matters is that the OSRM matcher was never invoked via the fallback.
        result = await rd.compute_road_route(_trip(6))

    assert called["osrm"] is False
    assert result is None


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


# ── Completed-route endpoint and gap reconstruction ──────────────────────────


@pytest.mark.asyncio
async def test_completed_route_endpoint_snap_uses_nearest_and_enforces_150m_limit():
    accepted_capture = {}
    accepted = {
        "code": "Ok",
        "waypoints": [{"distance": 12.5, "location": [-104.6201, 50.4501]}],
    }
    with patch.object(
        rd.httpx,
        "AsyncClient",
        _client_factory(resp=_FakeResp(payload=accepted), capture=accepted_capture),
    ):
        assert await rd.snap_endpoint_via_osrm({"lat": 50.45, "lng": -104.62}, "http://osrm:5000/") == [
            50.4501,
            -104.6201,
        ]

    assert "/nearest/v1/driving/-104.62,50.45" in accepted_capture["url"]
    assert accepted_capture["params"] == {"number": 1}

    rejected = {
        "code": "Ok",
        # _MAX_COMPLETED_ENDPOINT_SNAP_M is 150.0 (utils/route_distance.py) --
        # was 75.0 when this test was written; keep the rejected case just
        # over the current limit.
        "waypoints": [{"distance": 150.1, "location": [-104.6201, 50.4501]}],
    }
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=rejected))):
        assert await rd.snap_endpoint_via_osrm({"lat": 50.45, "lng": -104.62}, "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_completed_gap_route_returns_ordered_sane_geojson_geometry():
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 420.0,
                "duration": 90.0,
                "geometry": {
                    "coordinates": [
                        [-104.6200, 50.4500],
                        [-104.6225, 50.4510],
                        [-104.6250, 50.4520],
                    ]
                },
            }
        ],
    }
    capture = {}
    with patch.object(
        rd.httpx,
        "AsyncClient",
        _client_factory(resp=_FakeResp(payload=payload), capture=capture),
    ):
        result = await rd.compute_gap_route_via_osrm([50.45, -104.62], [50.452, -104.625], "http://osrm:5000")

    assert result == (
        0.42,
        [[50.45, -104.62], [50.451, -104.6225], [50.452, -104.625]],
    )
    assert "/route/v1/driving/-104.62,50.45;-104.625,50.452" in capture["url"]
    assert capture["params"] == {
        "alternatives": "false",
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }


@pytest.mark.asyncio
async def test_completed_gap_route_rejects_implausible_detour():
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 10_000.0,
                "geometry": {"coordinates": [[-104.62, 50.45], [-104.6201, 50.4501]]},
            }
        ],
    }
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))):
        assert await rd.compute_gap_route_via_osrm([50.45, -104.62], [50.4501, -104.6201], "http://osrm:5000") is None
