"""Coverage-gap tests for backend/utils/route_distance.py (A1c Sub-tier C, batch
locintegrity-routegap-routedist).

`tests/test_route_distance.py` and `tests/test_route_distance_osrm.py` already
cover the main happy/soft-failure paths for the OSRM and Google Roads
providers. This file closes the remaining branches: small-helper edge cases
(`_downsample`/`_cap_polyline` with `max_count < 2`, `_osrm_timestamp`'s
missing/naive/unsupported-type inputs, `_overlapping_chunks` validation,
`_observed_segment_points`'s dataclass-vs-dict-vs-fallback-key branches),
`compute_segmented_road_route`'s insufficient-points/provider-unavailable/
invalid-geometry failure branches, `_compute_route_via_osrm`'s exception and
short-polyline paths, `snap_endpoint_via_osrm`'s malformed-input/HTTP-error/
exception/short-distance branches, `compute_gap_route_via_osrm` and its
Google-Directions twin `compute_gap_route_via_google`, `_decode_encoded_polyline`,
`_compute_route_via_google`'s cache-hit/cache-exception/budget-gate/HTTP-error/
status-not-OK/short-polyline/cache-set-exception branches, and `snap_to_road`'s
OSRM-then-Google-fallback chain.

No GPS coordinates are logged or asserted beyond what the branch under test
requires; test fixtures use literal lat/lng values (fine for test code per
CLAUDE.md), never real rider/driver locations.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import route_distance as rd


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
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


# ── _downsample / _cap_polyline: max_count < 2 ────────────────────────────────


def test_downsample_with_max_count_below_two_keeps_only_the_last_point():
    points = [{"lat": 1.0}, {"lat": 2.0}, {"lat": 3.0}]
    assert rd._downsample(points, 1) == [{"lat": 3.0}]


def test_cap_polyline_with_max_count_below_two_keeps_only_the_last_coordinate():
    coords = [[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]]
    assert rd._cap_polyline(coords, 1) == [[3.0, 3.0]]


# ── _osrm_timestamp edge cases ────────────────────────────────────────────────


def test_osrm_timestamp_none_when_no_timestamp_field_present():
    assert rd._osrm_timestamp({}) is None


def test_osrm_timestamp_treats_naive_iso_string_as_utc():
    # No trailing Z / offset — fromisoformat parses tz-naive, code must attach UTC.
    assert rd._osrm_timestamp({"timestamp": "2026-07-09T12:34:56"}) == 1783600496


def test_osrm_timestamp_none_for_unsupported_type():
    assert rd._osrm_timestamp({"timestamp": [1, 2, 3]}) is None


# ── _compute_via_google_roads: missing coordinate + zero distance ────────────


@pytest.mark.asyncio
async def test_google_roads_skips_snapped_points_missing_lat_or_lng():
    payload = {
        "snappedPoints": [
            {"location": {"latitude": 50.45, "longitude": -104.62}},
            {"location": {"latitude": None, "longitude": -104.63}},  # skipped
            {"location": {"latitude": 50.46, "longitude": -104.64}},
        ]
    }
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))):
        result = await rd._compute_via_google_roads(
            [{"lat": 50.45, "lng": -104.62}, {"lat": 50.46, "lng": -104.64}], "key"
        )
    assert result is not None
    distance_km, polyline = result
    assert len(polyline) == 2  # the malformed middle point never made it in
    assert distance_km > 0


@pytest.mark.asyncio
async def test_google_roads_returns_none_when_total_distance_is_zero():
    # Two snapped points at the exact same location -> haversine sum is 0.
    payload = {
        "snappedPoints": [
            {"location": {"latitude": 50.45, "longitude": -104.62}},
            {"location": {"latitude": 50.45, "longitude": -104.62}},
        ]
    }
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))):
        result = await rd._compute_via_google_roads(
            [{"lat": 50.45, "lng": -104.62}, {"lat": 50.45, "lng": -104.62}], "key"
        )
    assert result is None


# ── _overlapping_chunks validation ────────────────────────────────────────────


def test_overlapping_chunks_rejects_overlap_that_is_not_smaller_than_size():
    with pytest.raises(ValueError):
        list(rd._overlapping_chunks([{"lat": 1, "lng": 1}], size=10, overlap=10))


def test_overlapping_chunks_rejects_size_below_two():
    with pytest.raises(ValueError):
        list(rd._overlapping_chunks([{"lat": 1, "lng": 1}], size=1, overlap=0))


# ── _observed_segment_points: dataclass / dict-key fallback ──────────────────


class _FakeSegment:
    def __init__(self, points):
        self.points = points


def test_observed_segment_points_reads_dataclass_points_attribute():
    seg = _FakeSegment([{"lat": 1.0, "lng": 2.0}, {"lat": None, "lng": 2.0}])
    assert rd._observed_segment_points(seg) == [{"lat": 1.0, "lng": 2.0}]


def test_observed_segment_points_falls_back_to_observed_points_key():
    seg = {"observed_points": [{"lat": 1.0, "lng": 2.0}]}
    assert rd._observed_segment_points(seg) == [{"lat": 1.0, "lng": 2.0}]


def test_observed_segment_points_prefers_points_key_over_observed_points():
    seg = {"points": [{"lat": 9.0, "lng": 9.0}], "observed_points": [{"lat": 1.0, "lng": 1.0}]}
    assert rd._observed_segment_points(seg) == [{"lat": 9.0, "lng": 9.0}]


# ── compute_segmented_road_route: failure branches ────────────────────────────


@pytest.mark.asyncio
async def test_segmented_route_flags_insufficient_points_but_keeps_the_segment_boundary():
    with patch.object(rd, "get_app_settings", AsyncMock(return_value={})):
        result = await rd.compute_segmented_road_route([[{"lat": 1.0, "lng": 1.0}]])

    assert result["segments"] == [{"segment_index": 0, "distance_km": 0.0, "matched_segments": []}]
    assert result["failures"] == [{"segment_index": 0, "reason": "insufficient_points"}]
    assert result["distance_km"] == 0.0
    assert result["provider"] is None


@pytest.mark.asyncio
async def test_segmented_route_records_provider_unavailable_when_no_provider_configured():
    segment = [{"lat": 50.10 + i * 0.001, "lng": -104.6} for i in range(4)]
    with patch.object(rd, "get_app_settings", AsyncMock(return_value={})):
        result = await rd.compute_segmented_road_route([segment])

    assert result["failures"] == [{"segment_index": 0, "chunk_index": 0, "reason": "provider_unavailable"}]
    assert result["segments"][0]["matched_segments"] == []
    assert result["provider"] is None


@pytest.mark.asyncio
async def test_segmented_route_rejects_matchings_with_invalid_geometry():
    segment = [{"lat": 50.10 + i * 0.001, "lng": -104.6} for i in range(4)]
    with (
        patch.object(rd, "get_app_settings", AsyncMock(return_value={"osrm_url": "http://osrm"})),
        # A matching with a distance but a single-point (unusable) polyline.
        patch.object(rd, "_compute_osrm_chunk_matchings", AsyncMock(return_value=[(1.2, [[50.10, -104.6]])])),
    ):
        result = await rd.compute_segmented_road_route([segment])

    assert result["failures"] == [{"segment_index": 0, "chunk_index": 0, "reason": "invalid_provider_geometry"}]
    assert result["segments"][0]["matched_segments"] == []
    assert result["segments"][0]["distance_km"] == 0.0


# ── _compute_route_via_osrm: exception + short polyline ──────────────────────


@pytest.mark.asyncio
async def test_compute_route_via_osrm_returns_none_on_request_exception():
    with patch.object(rd.httpx, "AsyncClient", _client_factory(exc=RuntimeError("network down"))):
        result = await rd._compute_route_via_osrm(50.45, -104.62, 50.44, -104.63, "http://osrm:5000")
    assert result is None


@pytest.mark.asyncio
async def test_compute_route_via_osrm_returns_none_when_polyline_too_short():
    payload = {
        "code": "Ok",
        "routes": [{"distance": 100.0, "duration": 10.0, "geometry": {"coordinates": [[-104.62, 50.45]]}}],
    }
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))):
        result = await rd._compute_route_via_osrm(50.45, -104.62, 50.44, -104.63, "http://osrm:5000")
    assert result is None


# ── snap_endpoint_via_osrm: malformed input / HTTP error / exception ─────────


@pytest.mark.asyncio
async def test_snap_endpoint_via_osrm_returns_none_for_malformed_point():
    assert await rd.snap_endpoint_via_osrm({"lat": "not-a-number", "lng": -104.62}, "http://osrm:5000") is None
    assert await rd.snap_endpoint_via_osrm({"lng": -104.62}, "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_snap_endpoint_via_osrm_returns_none_without_an_osrm_url():
    assert await rd.snap_endpoint_via_osrm({"lat": 50.45, "lng": -104.62}, "") is None


@pytest.mark.asyncio
async def test_snap_endpoint_via_osrm_returns_none_on_http_error():
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(status_code=500))):
        assert await rd.snap_endpoint_via_osrm({"lat": 50.45, "lng": -104.62}, "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_snap_endpoint_via_osrm_returns_none_on_request_exception():
    with patch.object(rd.httpx, "AsyncClient", _client_factory(exc=RuntimeError("boom"))):
        assert await rd.snap_endpoint_via_osrm({"lat": 50.45, "lng": -104.62}, "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_snap_endpoint_via_osrm_returns_none_when_no_waypoint_returned():
    with patch.object(
        rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload={"code": "Ok", "waypoints": []}))
    ):
        assert await rd.snap_endpoint_via_osrm({"lat": 50.45, "lng": -104.62}, "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_snap_endpoint_via_osrm_returns_none_when_waypoint_fields_are_malformed():
    payload = {"code": "Ok", "waypoints": [{"distance": "bad", "location": [-104.62, 50.45]}]}
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))):
        assert await rd.snap_endpoint_via_osrm({"lat": 50.45, "lng": -104.62}, "http://osrm:5000") is None


# ── compute_gap_route_via_osrm: validation branches ───────────────────────────


@pytest.mark.asyncio
async def test_gap_route_via_osrm_returns_none_for_short_endpoints_or_missing_url():
    assert await rd.compute_gap_route_via_osrm([50.45], [50.46, -104.63], "http://osrm:5000") is None
    assert await rd.compute_gap_route_via_osrm([50.45, -104.62], [50.46, -104.63], "") is None


@pytest.mark.asyncio
async def test_gap_route_via_osrm_returns_none_when_endpoint_values_are_not_numeric():
    assert await rd.compute_gap_route_via_osrm(["bad", -104.62], [50.46, -104.63], "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_gap_route_via_osrm_returns_none_for_non_finite_coordinates():
    assert await rd.compute_gap_route_via_osrm([float("inf"), -104.62], [50.46, -104.63], "http://osrm:5000") is None


@pytest.mark.asyncio
async def test_gap_route_via_osrm_returns_none_when_deduped_polyline_collapses_below_two_points():
    routed = {"distance_km": 0.05, "polyline": [[50.45, -104.62], [50.45, -104.62]]}
    with patch.object(rd, "_compute_route_via_osrm", AsyncMock(return_value=routed)):
        result = await rd.compute_gap_route_via_osrm([50.45, -104.62], [50.4501, -104.6201], "http://osrm:5000")
    assert result is None


@pytest.mark.asyncio
async def test_gap_route_via_osrm_returns_none_when_the_route_provider_finds_nothing():
    with patch.object(rd, "_compute_route_via_osrm", AsyncMock(return_value=None)):
        result = await rd.compute_gap_route_via_osrm([50.45, -104.62], [50.46, -104.63], "http://osrm:5000")
    assert result is None


@pytest.mark.asyncio
async def test_gap_route_via_osrm_dedupes_repeated_coordinates_and_skips_short_ones():
    # Endpoints are metres apart so the 0.5 km routed distance clears the
    # sanity gate (direct_km <= distance_km <= max(direct_km*5, direct_km+2)).
    routed = {
        "distance_km": 0.5,
        "polyline": [[50.45, -104.62], [50.45, -104.62], [1.0], [50.46, -104.63]],
    }
    with patch.object(rd, "_compute_route_via_osrm", AsyncMock(return_value=routed)):
        result = await rd.compute_gap_route_via_osrm([50.45, -104.62], [50.4501, -104.6201], "http://osrm:5000")
    assert result == (0.5, [[50.45, -104.62], [50.46, -104.63]])


# ── compute_gap_route_via_google: mirrors the OSRM twin ──────────────────────


@pytest.mark.asyncio
async def test_gap_route_via_google_returns_none_for_short_endpoints_or_missing_key():
    assert await rd.compute_gap_route_via_google([50.45], [50.46, -104.63], "key") is None
    assert await rd.compute_gap_route_via_google([50.45, -104.62], [50.46, -104.63], "") is None


@pytest.mark.asyncio
async def test_gap_route_via_google_returns_none_when_endpoint_values_are_not_numeric():
    assert await rd.compute_gap_route_via_google(["bad", -104.62], [50.46, -104.63], "key") is None


@pytest.mark.asyncio
async def test_gap_route_via_google_returns_none_for_non_finite_coordinates():
    assert await rd.compute_gap_route_via_google([float("nan"), -104.62], [50.46, -104.63], "key") is None


@pytest.mark.asyncio
async def test_gap_route_via_google_returns_none_when_deduped_polyline_collapses_below_two_points():
    routed = {"distance_km": 0.05, "polyline": [[50.45, -104.62], [50.45, -104.62]]}
    with patch.object(rd, "_compute_route_via_google", AsyncMock(return_value=routed)):
        result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.4501, -104.6201], "key")
    assert result is None


@pytest.mark.asyncio
async def test_gap_route_via_google_returns_none_when_provider_finds_nothing():
    with patch.object(rd, "_compute_route_via_google", AsyncMock(return_value=None)):
        result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.46, -104.63], "key")
    assert result is None


@pytest.mark.asyncio
async def test_gap_route_via_google_rejects_implausible_detour():
    # direct haversine distance is small; returned distance is absurdly large.
    routed = {"distance_km": 500.0, "polyline": [[50.45, -104.62], [50.46, -104.63]]}
    with patch.object(rd, "_compute_route_via_google", AsyncMock(return_value=routed)):
        result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.46, -104.63], "key")
    assert result is None


@pytest.mark.asyncio
async def test_gap_route_via_google_dedupes_repeated_coordinates_and_skips_short_ones():
    routed = {
        "distance_km": 0.5,
        "polyline": [[50.45, -104.62], [50.45, -104.62], [1.0], [50.46, -104.63]],
    }
    with patch.object(rd, "_compute_route_via_google", AsyncMock(return_value=routed)):
        result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.4501, -104.6201], "key")
    assert result == (0.5, [[50.45, -104.62], [50.46, -104.63]])


# ── _decode_encoded_polyline ───────────────────────────────────────────────────


def test_decode_encoded_polyline_matches_the_google_reference_example():
    # Reference example from Google's polyline algorithm docs, decodes to
    # [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)].
    encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    coords = rd._decode_encoded_polyline(encoded)
    assert coords == [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]


def test_decode_encoded_polyline_returns_partial_result_on_truncated_input():
    # A single leading byte (with the continuation bit set) can never resolve
    # to a full coordinate pair — the decoder must return what it has so far
    # (nothing) rather than raise.
    assert rd._decode_encoded_polyline("_") == []


# ── _compute_route_via_google: cache / budget / HTTP branches ────────────────


@pytest.mark.asyncio
async def test_compute_route_via_google_returns_cached_value_without_calling_the_api():
    cached_payload = '{"polyline": [[1.0, 1.0], [2.0, 2.0]], "eta_seconds": 60, "distance_km": 1.0}'
    get_called = AsyncMock()
    with (
        patch.object(rd, "redis_get", AsyncMock(return_value=cached_payload)),
        patch.object(rd.httpx, "AsyncClient", _client_factory()),
    ):
        result = await rd._compute_route_via_google(50.45, -104.62, 50.46, -104.63, "key")
    assert result == {"polyline": [[1.0, 1.0], [2.0, 2.0]], "eta_seconds": 60, "distance_km": 1.0}
    get_called.assert_not_called()


@pytest.mark.asyncio
async def test_compute_route_via_google_survives_a_cache_read_failure():
    payload = {
        "status": "OK",
        "routes": [
            {
                "overview_polyline": {"points": "_p~iF~ps|U_ulLnnqC"},
                "legs": [{"duration": {"value": 60}, "distance": {"value": 500}}],
            }
        ],
    }
    with (
        patch.object(rd, "redis_get", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(rd, "check_budget", AsyncMock(return_value=(True, 0.0, 10.0))),
        patch.object(rd, "record_call", AsyncMock()),
        patch.object(rd, "redis_set", AsyncMock()),
        patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))),
    ):
        result = await rd._compute_route_via_google(50.45, -104.62, 50.46, -104.63, "key")
    assert result is not None
    assert result["distance_km"] == 0.5


@pytest.mark.asyncio
async def test_compute_route_via_google_returns_none_when_budget_exhausted():
    with (
        patch.object(rd, "redis_get", AsyncMock(return_value=None)),
        patch.object(rd, "check_budget", AsyncMock(return_value=(False, 10.0, 10.0))),
    ):
        result = await rd._compute_route_via_google(50.45, -104.62, 50.46, -104.63, "key")
    assert result is None


@pytest.mark.asyncio
async def test_compute_route_via_google_returns_none_on_http_error():
    with (
        patch.object(rd, "redis_get", AsyncMock(return_value=None)),
        patch.object(rd, "check_budget", AsyncMock(return_value=(True, 0.0, 10.0))),
        patch.object(rd, "record_call", AsyncMock()),
        patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(status_code=500))),
    ):
        result = await rd._compute_route_via_google(50.45, -104.62, 50.46, -104.63, "key")
    assert result is None


@pytest.mark.asyncio
async def test_compute_route_via_google_returns_none_on_request_exception():
    with (
        patch.object(rd, "redis_get", AsyncMock(return_value=None)),
        patch.object(rd, "check_budget", AsyncMock(return_value=(True, 0.0, 10.0))),
        patch.object(rd, "record_call", AsyncMock()),
        patch.object(rd.httpx, "AsyncClient", _client_factory(exc=RuntimeError("boom"))),
    ):
        result = await rd._compute_route_via_google(50.45, -104.62, 50.46, -104.63, "key")
    assert result is None


@pytest.mark.asyncio
async def test_compute_route_via_google_returns_none_when_status_is_not_ok():
    with (
        patch.object(rd, "redis_get", AsyncMock(return_value=None)),
        patch.object(rd, "check_budget", AsyncMock(return_value=(True, 0.0, 10.0))),
        patch.object(rd, "record_call", AsyncMock()),
        patch.object(
            rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload={"status": "ZERO_RESULTS", "routes": []}))
        ),
    ):
        result = await rd._compute_route_via_google(50.45, -104.62, 50.46, -104.63, "key")
    assert result is None


@pytest.mark.asyncio
async def test_compute_route_via_google_returns_none_when_polyline_too_short():
    payload = {"status": "OK", "routes": [{"overview_polyline": {"points": ""}, "legs": []}]}
    with (
        patch.object(rd, "redis_get", AsyncMock(return_value=None)),
        patch.object(rd, "check_budget", AsyncMock(return_value=(True, 0.0, 10.0))),
        patch.object(rd, "record_call", AsyncMock()),
        patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))),
    ):
        result = await rd._compute_route_via_google(50.45, -104.62, 50.46, -104.63, "key")
    assert result is None


@pytest.mark.asyncio
async def test_compute_route_via_google_survives_a_cache_write_failure():
    payload = {
        "status": "OK",
        "routes": [
            {
                "overview_polyline": {"points": "_p~iF~ps|U_ulLnnqC"},
                "legs": [{"duration": {"value": 60}, "distance": {"value": 500}}],
            }
        ],
    }
    with (
        patch.object(rd, "redis_get", AsyncMock(return_value=None)),
        patch.object(rd, "check_budget", AsyncMock(return_value=(True, 0.0, 10.0))),
        patch.object(rd, "record_call", AsyncMock()),
        patch.object(rd, "redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))),
    ):
        # Must not raise even though the cache write blows up.
        result = await rd._compute_route_via_google(50.45, -104.62, 50.46, -104.63, "key")
    assert result is not None
    assert result["distance_km"] == 0.5


# ── snap_to_road: OSRM -> Google fallback chain ───────────────────────────────


@pytest.mark.asyncio
async def test_snap_to_road_returns_none_without_any_provider_configured():
    # _live_osrm_url() falls back to the public OSRM_FALLBACK_URL demo router
    # (core/config.py) when neither app_settings nor OSRM_URL is set — that
    # fallback must be cleared too, or this test silently makes a real network
    # call to router.project-osrm.org and asserts against live data instead of
    # exercising the "no provider configured" path its name promises.
    with (
        patch.object(rd, "get_app_settings", AsyncMock(return_value={})),
        patch.object(rd.settings, "OSRM_URL", ""),
        patch.object(rd.settings, "OSRM_FALLBACK_URL", ""),
    ):
        result = await rd.snap_to_road(50.45, -104.62)
    assert result is None


@pytest.mark.asyncio
async def test_snap_to_road_uses_osrm_nearest_when_within_move_limit():
    payload = {"code": "Ok", "waypoints": [{"location": [-104.6201, 50.4501], "distance": 5.0}]}
    with (
        patch.object(rd, "get_app_settings", AsyncMock(return_value={"osrm_url": "http://osrm:5000"})),
        patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))),
    ):
        result = await rd.snap_to_road(50.45, -104.62)
    assert result == (50.4501, -104.6201)


@pytest.mark.asyncio
async def test_snap_to_road_falls_back_to_google_when_osrm_move_too_large():
    osrm_payload = {"code": "Ok", "waypoints": [{"location": [-104.7, 50.5], "distance": 999999.0}]}
    google_payload = {"snappedPoints": [{"location": {"latitude": 50.4502, "longitude": -104.6202}}]}
    call_count = {"n": 0}

    # httpx.AsyncClient(...) is a synchronous constructor call (the client
    # returned is then used as `async with`) — this stand-in must be a plain
    # callable, not a coroutine function, or the `async with` fails.
    def _client(*_a, **_kw):
        call_count["n"] += 1
        payload = osrm_payload if call_count["n"] == 1 else google_payload
        return _FakeClient(resp=_FakeResp(payload=payload))

    with (
        patch.object(
            rd,
            "get_app_settings",
            AsyncMock(return_value={"osrm_url": "http://osrm:5000", "google_maps_api_key": "key"}),
        ),
        patch.object(rd.httpx, "AsyncClient", _client),
    ):
        result = await rd.snap_to_road(50.45, -104.62)
    assert result == (50.4502, -104.6202)


@pytest.mark.asyncio
async def test_snap_to_road_falls_back_to_google_when_osrm_raises():
    google_payload = {"snappedPoints": [{"location": {"latitude": 50.4502, "longitude": -104.6202}}]}
    call_count = {"n": 0}

    def _client(*_a, **_kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeClient(exc=RuntimeError("osrm down"))
        return _FakeClient(resp=_FakeResp(payload=google_payload))

    with (
        patch.object(
            rd,
            "get_app_settings",
            AsyncMock(return_value={"osrm_url": "http://osrm:5000", "google_maps_api_key": "key"}),
        ),
        patch.object(rd.httpx, "AsyncClient", _client),
    ):
        result = await rd.snap_to_road(50.45, -104.62)
    assert result == (50.4502, -104.6202)


@pytest.mark.asyncio
async def test_snap_to_road_returns_none_when_google_move_too_large():
    google_payload = {"snappedPoints": [{"location": {"latitude": 55.0, "longitude": -110.0}}]}
    with (
        patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})),
        patch.object(rd.settings, "OSRM_URL", ""),
        patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=google_payload))),
    ):
        result = await rd.snap_to_road(50.45, -104.62)
    assert result is None


@pytest.mark.asyncio
async def test_snap_to_road_survives_google_exception_and_returns_none():
    with (
        patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})),
        patch.object(rd.settings, "OSRM_URL", ""),
        patch.object(rd.httpx, "AsyncClient", _client_factory(exc=RuntimeError("google down"))),
    ):
        result = await rd.snap_to_road(50.45, -104.62)
    assert result is None
