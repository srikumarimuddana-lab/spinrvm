"""Coverage for utils/route_validation.py (A1c, Sub-tier B).

Post-trip GPS route validation: snaps driver breadcrumbs to the road network
(OSRM /match primary, Google Roads snapToRoads fallback) and scores match
coverage/deviation to flag spoofed trips. Had no dedicated test file
(53.33% coverage as an incidental side effect of other tests exercising it
indirectly).

Pure helpers (_haversine_meters, _downsample, _osrm_radius) are called
directly. The two network-calling coroutines (_validate_via_osrm,
validate_trip_route) are exercised with httpx.AsyncClient and
get_app_settings/settings mocked at the module's own attribute (per the
dual-import rule: this module is loaded as `backend.utils.route_validation`,
so the relative imports bind `get_app_settings` and `settings` as module-level
names on that module, and `httpx` likewise as a module attribute) — patch
targets are `backend.utils.route_validation.<name>`.

Test-only change — no application code modified.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from backend.utils.route_validation import (
    _downsample,
    _haversine_meters,
    _osrm_radius,
    _validate_via_osrm,
    validate_trip_route,
)


def _mock_http_client(response_json, status_code=200):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = response_json
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _bc(lat=52.1, lng=-106.6, phase="trip_in_progress", accuracy=None):
    pt = {"lat": lat, "lng": lng, "tracking_phase": phase}
    if accuracy is not None:
        pt["accuracy"] = accuracy
    return pt


class TestHaversineMeters:
    def test_same_point_is_zero(self):
        assert _haversine_meters(52.1, -106.6, 52.1, -106.6) == 0

    def test_known_one_degree_latitude_is_about_111km(self):
        # 1 degree of latitude is ~111.19 km / 111,190 m everywhere on Earth.
        dist = _haversine_meters(0.0, 0.0, 1.0, 0.0)
        assert 110_500 < dist < 111_500

    def test_symmetric(self):
        d1 = _haversine_meters(52.1, -106.6, 52.2, -106.7)
        d2 = _haversine_meters(52.2, -106.7, 52.1, -106.6)
        assert math.isclose(d1, d2)


class TestDownsample:
    def test_under_limit_returns_unchanged(self):
        points = [{"i": i} for i in range(5)]
        result = _downsample(points, 100)
        assert result == points

    def test_equal_to_limit_returns_unchanged(self):
        points = [{"i": i} for i in range(10)]
        result = _downsample(points, 10)
        assert result == points

    def test_max_count_below_two_returns_last_point_only(self):
        points = [{"i": i} for i in range(10)]
        assert _downsample(points, 1) == [points[-1]]
        assert _downsample(points, 0) == [points[-1]]

    def test_downsamples_and_always_keeps_last_point(self):
        points = [{"i": i} for i in range(250)]
        result = _downsample(points, 100)
        assert len(result) == 100
        assert result[-1] == points[-1]

    def test_downsampled_points_are_from_original_list_in_order(self):
        points = [{"i": i} for i in range(50)]
        result = _downsample(points, 10)
        indices = [p["i"] for p in result]
        assert indices == sorted(indices)
        assert len(set(indices)) == len(indices)


class TestOsrmRadius:
    def test_no_accuracy_uses_default(self):
        assert _osrm_radius({}) == "20"

    def test_accuracy_none_uses_default(self):
        assert _osrm_radius({"accuracy": None}) == "20"

    def test_within_range_uses_accuracy(self):
        assert _osrm_radius({"accuracy": 30}) == "30"

    def test_below_min_clamps_to_min(self):
        assert _osrm_radius({"accuracy": 2}) == "10"

    def test_above_max_clamps_to_max(self):
        assert _osrm_radius({"accuracy": 500}) == "50"

    def test_invalid_accuracy_falls_back_to_default(self):
        assert _osrm_radius({"accuracy": "not-a-number"}) == "20"

    def test_none_like_object_accuracy_falls_back_to_default(self):
        assert _osrm_radius({"accuracy": object()}) == "20"


class TestValidateViaOsrm:
    @pytest.mark.anyio
    async def test_non_200_status_returns_none(self):
        points = [_bc() for _ in range(5)]
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client({}, status_code=500),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result is None

    @pytest.mark.anyio
    async def test_request_exception_returns_none(self):
        points = [_bc() for _ in range(5)]
        client = MagicMock()
        client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("backend.utils.route_validation.httpx.AsyncClient", return_value=ctx):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result is None

    @pytest.mark.anyio
    async def test_no_match_code_returns_likely_spoofed(self):
        points = [_bc() for _ in range(5)]
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client({"code": "NoMatch"}),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result["verdict"] == "likely_spoofed"
        assert result["provider"] == "osrm_match"
        assert result["total_points"] == 5
        assert result["snapped_points"] == 0
        assert result["deviation_pct"] == 100.0

    @pytest.mark.anyio
    async def test_other_non_ok_code_returns_none(self):
        points = [_bc() for _ in range(5)]
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client({"code": "InvalidUrl"}),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result is None

    @pytest.mark.anyio
    async def test_ok_all_matched_high_confidence_is_clean(self):
        points = [_bc() for _ in range(5)]
        data = {
            "code": "Ok",
            "tracepoints": [{"x": 1}] * 5,
            "matchings": [{"confidence": 0.95}],
        }
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client(data),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result["verdict"] == "clean"
        assert result["snapped_points"] == 5
        assert result["deviation_pct"] == 0.0
        assert result["match_confidence"] == 0.95

    @pytest.mark.anyio
    async def test_ok_high_deviation_pct_is_likely_spoofed(self):
        # 3 of 5 tracepoints null (None) -> unmatched=3 -> 60% deviation > 50
        points = [_bc() for _ in range(5)]
        data = {
            "code": "Ok",
            "tracepoints": [{"x": 1}, None, None, None, {"x": 1}],
            "matchings": [{"confidence": 0.9}],
        }
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client(data),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result["verdict"] == "likely_spoofed"
        assert result["deviation_pct"] == 60.0

    @pytest.mark.anyio
    async def test_ok_moderate_deviation_pct_is_suspicious(self):
        # 2 of 5 unmatched -> 40% deviation, in (20, 50] -> suspicious
        points = [_bc() for _ in range(5)]
        data = {
            "code": "Ok",
            "tracepoints": [{"x": 1}, {"x": 1}, {"x": 1}, None, None],
            "matchings": [{"confidence": 0.9}],
        }
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client(data),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result["verdict"] == "suspicious"
        assert result["deviation_pct"] == 40.0

    @pytest.mark.anyio
    async def test_low_confidence_forces_likely_spoofed_despite_low_deviation(self):
        points = [_bc() for _ in range(5)]
        data = {
            "code": "Ok",
            "tracepoints": [{"x": 1}] * 5,
            "matchings": [{"confidence": 0.1}],
        }
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client(data),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result["verdict"] == "likely_spoofed"
        assert result["deviation_pct"] == 0.0
        assert result["match_confidence"] == 0.1

    @pytest.mark.anyio
    async def test_mid_confidence_forces_suspicious_despite_low_deviation(self):
        points = [_bc() for _ in range(5)]
        data = {
            "code": "Ok",
            "tracepoints": [{"x": 1}] * 5,
            "matchings": [{"confidence": 0.45}],
        }
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client(data),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result["verdict"] == "suspicious"

    @pytest.mark.anyio
    async def test_no_matchings_confidence_is_none(self):
        points = [_bc() for _ in range(5)]
        data = {"code": "Ok", "tracepoints": [{"x": 1}] * 5, "matchings": []}
        with patch(
            "backend.utils.route_validation.httpx.AsyncClient",
            return_value=_mock_http_client(data),
        ):
            result = await _validate_via_osrm(points, "http://osrm.local")
        assert result["match_confidence"] is None
        assert result["verdict"] == "clean"


class TestValidateTripRoute:
    @pytest.mark.anyio
    async def test_empty_breadcrumbs_returns_none(self):
        assert await validate_trip_route([]) is None

    @pytest.mark.anyio
    async def test_none_breadcrumbs_returns_none(self):
        assert await validate_trip_route(None) is None

    @pytest.mark.anyio
    async def test_fewer_than_five_breadcrumbs_returns_none(self):
        breadcrumbs = [_bc() for _ in range(4)]
        assert await validate_trip_route(breadcrumbs) is None

    @pytest.mark.anyio
    async def test_fewer_than_five_trip_in_progress_points_returns_none(self):
        # 10 breadcrumbs total but only 3 are trip_in_progress with valid lat/lng
        breadcrumbs = [_bc(phase="trip_in_progress") for _ in range(3)] + [
            _bc(phase="en_route_to_pickup") for _ in range(7)
        ]
        with patch(
            "backend.utils.route_validation.get_app_settings",
            AsyncMock(return_value={}),
        ):
            result = await validate_trip_route(breadcrumbs)
        assert result is None

    @pytest.mark.anyio
    async def test_points_missing_lat_or_lng_are_filtered_out(self):
        breadcrumbs = [_bc() for _ in range(5)]
        breadcrumbs.append({"lat": None, "lng": -106.6, "tracking_phase": "trip_in_progress"})
        breadcrumbs.append({"lat": 52.1, "lng": None, "tracking_phase": "trip_in_progress"})
        with patch(
            "backend.utils.route_validation.get_app_settings",
            AsyncMock(return_value={}),
        ):
            result = await validate_trip_route(breadcrumbs)
        # Exactly 5 valid trip_in_progress points remain and no OSRM/API key
        # configured -> falls through to None (not an exception from the
        # malformed points).
        assert result is None

    @pytest.mark.anyio
    async def test_no_osrm_url_and_no_api_key_returns_none(self):
        breadcrumbs = [_bc() for _ in range(5)]
        with (
            patch("backend.utils.route_validation.get_app_settings", AsyncMock(return_value={})),
            patch("backend.utils.route_validation.settings") as mock_settings,
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result is None

    @pytest.mark.anyio
    async def test_osrm_url_from_app_settings_used_and_result_returned(self):
        breadcrumbs = [_bc() for _ in range(5)]
        osrm_data = {
            "code": "Ok",
            "tracepoints": [{"x": 1}] * 5,
            "matchings": [{"confidence": 0.9}],
        }
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"osrm_url": "http://osrm.internal"}),
            ),
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client(osrm_data),
            ),
        ):
            result = await validate_trip_route(breadcrumbs)
        assert result["provider"] == "osrm_match"
        assert result["verdict"] == "clean"

    @pytest.mark.anyio
    async def test_osrm_url_from_settings_fallback_used_when_app_settings_blank(self):
        breadcrumbs = [_bc() for _ in range(5)]
        osrm_data = {"code": "NoMatch"}
        with (
            patch("backend.utils.route_validation.get_app_settings", AsyncMock(return_value={})),
            patch("backend.utils.route_validation.settings") as mock_settings,
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client(osrm_data),
            ),
        ):
            mock_settings.OSRM_URL = "http://fallback-osrm"
            result = await validate_trip_route(breadcrumbs)
        assert result["provider"] == "osrm_match"
        assert result["verdict"] == "likely_spoofed"

    @pytest.mark.anyio
    async def test_osrm_configured_but_fails_falls_through_to_google_roads(self):
        breadcrumbs = [_bc() for _ in range(5)]
        # OSRM returns a non-200 (-> None from _validate_via_osrm), no
        # google api key configured -> overall None, but this proves the
        # fallthrough branch runs rather than raising.
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"osrm_url": "http://osrm.internal"}),
            ),
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client({}, status_code=503),
            ),
        ):
            result = await validate_trip_route(breadcrumbs)
        assert result is None

    @pytest.mark.anyio
    async def test_google_roads_used_when_no_osrm_configured(self):
        breadcrumbs = [_bc(lat=52.1 + i * 0.001, lng=-106.6) for i in range(5)]
        snap_data = {
            "snappedPoints": [
                {"originalIndex": i, "location": {"latitude": 52.1 + i * 0.001, "longitude": -106.6}} for i in range(5)
            ]
        }
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client(snap_data),
            ),
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result["provider"] == "google_roads"
        assert result["snapped_points"] == 5
        assert result["verdict"] == "clean"
        assert result["deviation_pct"] == 0.0

    @pytest.mark.anyio
    async def test_google_roads_no_api_key_returns_none(self):
        breadcrumbs = [_bc() for _ in range(5)]
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": ""}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result is None

    @pytest.mark.anyio
    async def test_google_roads_non_200_returns_none(self):
        breadcrumbs = [_bc() for _ in range(5)]
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client({}, status_code=500),
            ),
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result is None

    @pytest.mark.anyio
    async def test_google_roads_request_exception_returns_none(self):
        breadcrumbs = [_bc() for _ in range(5)]
        client = MagicMock()
        client.get = AsyncMock(side_effect=RuntimeError("timeout"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
            patch("backend.utils.route_validation.httpx.AsyncClient", return_value=ctx),
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result is None

    @pytest.mark.anyio
    async def test_google_roads_empty_snapped_points_is_likely_spoofed(self):
        breadcrumbs = [_bc() for _ in range(5)]
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client({"snappedPoints": []}),
            ),
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result["provider"] == "google_roads"
        assert result["verdict"] == "likely_spoofed"
        assert result["deviation_pct"] == 100.0
        assert result["snapped_points"] == 0

    @pytest.mark.anyio
    async def test_google_roads_missing_snappedpoints_key_is_likely_spoofed(self):
        breadcrumbs = [_bc() for _ in range(5)]
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client({}),
            ),
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result["verdict"] == "likely_spoofed"

    @pytest.mark.anyio
    async def test_google_roads_unsnapped_point_treated_as_far(self):
        # Only 3 of 5 originalIndex values are present in the response ->
        # the 2 missing ones fall into the `else` branch (999.0 deviation,
        # counted as far). 2/5 = 40% -> suspicious.
        breadcrumbs = [_bc(lat=52.1 + i * 0.001, lng=-106.6) for i in range(5)]
        snap_data = {
            "snappedPoints": [
                {"originalIndex": i, "location": {"latitude": 52.1 + i * 0.001, "longitude": -106.6}} for i in range(3)
            ]
        }
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client(snap_data),
            ),
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result["snapped_points"] == 3
        assert result["deviation_pct"] == 40.0
        assert result["verdict"] == "suspicious"

    @pytest.mark.anyio
    async def test_google_roads_far_deviation_marks_likely_spoofed(self):
        # Snapped location is ~1km away from the original point for every
        # point -> deviation_pct 100% (> 50 threshold each) -> likely_spoofed.
        breadcrumbs = [_bc(lat=52.1, lng=-106.6) for _ in range(5)]
        snap_data = {
            "snappedPoints": [
                {"originalIndex": i, "location": {"latitude": 52.11, "longitude": -106.6}} for i in range(5)
            ]
        }
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
            patch(
                "backend.utils.route_validation.httpx.AsyncClient",
                return_value=_mock_http_client(snap_data),
            ),
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result["verdict"] == "likely_spoofed"
        assert result["deviation_pct"] == 100.0
        assert result["max_deviation_m"] > 50

    @pytest.mark.anyio
    async def test_osrm_url_whitespace_only_treated_as_unset(self):
        # osrm_url of "   " strips to "" and is falsy -> falls through to
        # google roads path instead of attempting an OSRM call.
        breadcrumbs = [_bc() for _ in range(5)]
        with (
            patch(
                "backend.utils.route_validation.get_app_settings",
                AsyncMock(return_value={"osrm_url": "   ", "google_maps_api_key": ""}),
            ),
            patch("backend.utils.route_validation.settings") as mock_settings,
        ):
            mock_settings.OSRM_URL = ""
            result = await validate_trip_route(breadcrumbs)
        assert result is None
