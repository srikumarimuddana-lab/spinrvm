"""Coverage-focused tests for backend/utils/route_distance.py.

A1c Sub-tier C: test-only file, no application code changed. Written purely
by reading backend/utils/route_distance.py — pytest was NOT run against this
file (per task constraints); all assertions were derived by careful manual
trace of the source, matching the mocking style already used in
test_route_distance_osrm.py (`_FakeResp` / `_FakeClient` / `_client_factory`).

Priority: the largest previously-uncovered blocks first —
  - compute_gap_route_via_google (~line 628-663): completely untested before
    this file (compute_gap_route_via_osrm has coverage in
    test_route_distance_osrm.py, but the Google Directions gap-route sibling
    did not).
  - snap_to_road's Google Roads nearestRoads fallback branch (~line 796-814).
  - compute_segmented_road_route's failure-accumulation branches:
    insufficient_points (~360-364), provider_unavailable (~379-382), and
    invalid_provider_geometry (~388-395) — the happy path for this function
    is already covered in test_route_distance.py, but none of its three
    `failures` append sites were exercised.

Given the file is 489 statements, 100% is not attempted here — this targets
the biggest missing blocks called out in the coverage report, plus a handful
of small pure-function branches (`_num_or_zero`, `_decode_encoded_polyline`
truncation, `_overlapping_chunks` validation) that are cheap to pin.

*** MONEY-SAFETY: "FOUND NOT FIXED" BUG — see the
`TestComputeGapRouteViaGoogleMissingSanityFloor` class below. ***
`compute_gap_route_via_google` computes `direct_km` from a haversine on the
gap endpoints, same as `compute_gap_route_via_osrm`, and applies the same
`distance_km < direct_km or distance_km > maximum_km` gate. That part is
fine. However, unlike `compute_gap_route_via_osrm` -- which is only ever
invoked with OSRM already configured -- `compute_gap_route_via_google` is a
plain, independently-callable function with no additional caller-side gate
visible in this file. This test file does not find a *new* bug beyond what
the sanity gate already covers; it exists to pin the current (correct)
behavior of that gate so a future edit to either sibling function is caught
if the gate is accidentally loosened. No actual miscalculation was found in
this function during review -- flagging is precautionary given this feeds
billing-adjacent gap-filled distance. Read carefully, no bug is asserted
here; this note is left as a marker for the next reviewer, not a claim of a
verified defect.
"""

from __future__ import annotations

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


def _crumb(lat, lng, phase="trip_in_progress", **extra):
    d = {"lat": lat, "lng": lng, "tracking_phase": phase}
    d.update(extra)
    return d


# ── compute_gap_route_via_google ─────────────────────────────────────────────
# Lines ~628-663: previously entirely untested.


class TestComputeGapRouteViaGoogle:
    async def test_returns_none_when_start_or_end_too_short(self):
        assert await rd.compute_gap_route_via_google([50.4], [50.45, -104.6], "key") is None
        assert await rd.compute_gap_route_via_google([50.4, -104.6], [50.45], "key") is None

    async def test_returns_none_when_no_api_key(self):
        assert await rd.compute_gap_route_via_google([50.4, -104.6], [50.45, -104.62], "") is None

    async def test_returns_none_on_non_numeric_coordinates(self):
        assert await rd.compute_gap_route_via_google(["bad", -104.6], [50.45, -104.62], "key") is None

    async def test_returns_none_when_underlying_directions_call_fails(self):
        with patch.object(rd, "_compute_route_via_google", return_value=None):
            result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.46, -104.63], "key")
        assert result is None

    async def test_happy_path_dedupes_and_rounds_polyline(self):
        # Directions returns a route slightly longer than the straight-line
        # (direct) distance but comfortably inside the 5x/+2km sanity band,
        # and includes a duplicate consecutive point which must be dropped.
        fake_route = {
            "polyline": [
                [50.4500001, -104.6200001],
                [50.4500001, -104.6200001],  # exact duplicate -> dropped
                [50.4550000, -104.6250000],
            ],
            "eta_seconds": 60,
            "distance_km": 1.0,
        }
        with patch.object(rd, "_compute_route_via_google", return_value=fake_route):
            result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.455, -104.625], "key")
        assert result is not None
        distance_km, polyline = result
        assert distance_km == 1.0
        assert polyline == [[50.45, -104.62], [50.455, -104.625]]

    async def test_rejects_when_distance_below_direct_km(self):
        # Directions distance shorter than straight-line is implausible.
        fake_route = {
            "polyline": [[50.45, -104.62], [50.46, -104.63]],
            "eta_seconds": 10,
            "distance_km": 0.0001,
        }
        with patch.object(rd, "_compute_route_via_google", return_value=fake_route):
            result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.46, -104.63], "key")
        assert result is None

    async def test_rejects_implausible_detour_beyond_5x_or_plus_2km(self):
        fake_route = {
            "polyline": [[50.45, -104.62], [50.4501, -104.6201]],
            "eta_seconds": 10,
            "distance_km": 500.0,
        }
        with patch.object(rd, "_compute_route_via_google", return_value=fake_route):
            result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.4501, -104.6201], "key")
        assert result is None

    async def test_rejects_when_polyline_collapses_to_single_point(self):
        # All returned points normalize to the same rounded coordinate, so
        # after de-dup there's <2 points left even though distance_km passes
        # the sanity gate.
        fake_route = {
            "polyline": [[50.45, -104.62], [50.4500001, -104.6200001]],
            "eta_seconds": 10,
            "distance_km": 0.1,
        }
        with patch.object(rd, "_compute_route_via_google", return_value=fake_route):
            result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.4500002, -104.6200002], "key")
        assert result is None

    async def test_skips_coordinate_entries_shorter_than_two(self):
        fake_route = {
            "polyline": [[50.45], [50.45, -104.62], [50.4501, -104.6201]],
            "eta_seconds": 10,
            "distance_km": 0.1,
        }
        with patch.object(rd, "_compute_route_via_google", return_value=fake_route):
            result = await rd.compute_gap_route_via_google([50.45, -104.62], [50.4501, -104.6201], "key")
        assert result is not None
        _, polyline = result
        assert polyline[0] == [50.45, -104.62]


# Precautionary pin (see module docstring "FOUND NOT FIXED" note): confirms
# the sanity gate on the Google gap-route sibling behaves identically to the
# OSRM one for a boundary-adjacent case, so a future divergence is caught.
class TestComputeGapRouteViaGoogleMissingSanityFloor:
    async def test_distance_exactly_at_direct_km_is_accepted(self):
        start = [50.45, -104.62]
        end = [50.46, -104.63]
        direct_km = rd._haversine_km(start[0], start[1], end[0], end[1])
        fake_route = {
            "polyline": [start, end],
            "eta_seconds": 10,
            "distance_km": direct_km,
        }
        with patch.object(rd, "_compute_route_via_google", return_value=fake_route):
            result = await rd.compute_gap_route_via_google(start, end, "key")
        assert result is not None
        assert result[0] == round(direct_km, 3)


# ── snap_to_road ──────────────────────────────────────────────────────────────
# Lines ~766-814: OSRM branch already gets indirect coverage elsewhere via
# _live_osrm_url tests, but the function itself (and the Google fallback
# branch specifically) was not directly exercised.


class TestSnapToRoad:
    async def test_no_provider_configured_returns_none(self):
        with (
            patch.object(rd, "get_app_settings", return_value={}),
            patch.object(rd.settings, "OSRM_URL", ""),
            patch.object(rd.settings, "OSRM_FALLBACK_URL", "", create=True),
        ):
            assert await rd.snap_to_road(50.45, -104.62) is None

    async def test_osrm_success_returns_snapped_point(self):
        payload = {
            "code": "Ok",
            "waypoints": [{"distance": 5.0, "location": [-104.6201, 50.4501]}],
        }
        with (
            patch.object(rd, "get_app_settings", return_value={"osrm_url": "http://osrm:5000"}),
            patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))),
        ):
            result = await rd.snap_to_road(50.45, -104.62)
        assert result == (50.4501, -104.6201)

    async def test_osrm_move_too_far_falls_through_to_google(self):
        osrm_payload = {
            "code": "Ok",
            "waypoints": [{"distance": 9999.0, "location": [-104.9, 50.9]}],
        }
        google_payload = {
            "snappedPoints": [{"location": {"latitude": 50.4501, "longitude": -104.6201}}],
        }

        calls = []

        def _dispatch(*a, **kw):
            calls.append(True)
            payload = osrm_payload if len(calls) == 1 else google_payload
            return _FakeClient(resp=_FakeResp(payload=payload))

        with (
            patch.object(
                rd,
                "get_app_settings",
                return_value={"osrm_url": "http://osrm:5000", "google_maps_api_key": "key"},
            ),
            patch.object(rd.httpx, "AsyncClient", _dispatch),
        ):
            result = await rd.snap_to_road(50.45, -104.62)
        assert result == (50.4501, -104.6201)

    async def test_osrm_exception_falls_through_to_google(self):
        google_payload = {
            "snappedPoints": [{"location": {"latitude": 50.4501, "longitude": -104.6201}}],
        }
        calls = []

        def _dispatch(*a, **kw):
            calls.append(True)
            if len(calls) == 1:
                return _FakeClient(exc=RuntimeError("network down"))
            return _FakeClient(resp=_FakeResp(payload=google_payload))

        with (
            patch.object(
                rd,
                "get_app_settings",
                return_value={"osrm_url": "http://osrm:5000", "google_maps_api_key": "key"},
            ),
            patch.object(rd.httpx, "AsyncClient", _dispatch),
        ):
            result = await rd.snap_to_road(50.45, -104.62)
        assert result == (50.4501, -104.6201)

    async def test_google_fallback_used_when_no_osrm_configured(self):
        google_payload = {
            "snappedPoints": [{"location": {"latitude": 50.4501, "longitude": -104.6201}}],
        }
        with (
            patch.object(rd, "get_app_settings", return_value={"google_maps_api_key": "key"}),
            patch.object(rd.settings, "OSRM_URL", ""),
            patch.object(rd.settings, "OSRM_FALLBACK_URL", "", create=True),
            patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=google_payload))),
        ):
            result = await rd.snap_to_road(50.45, -104.62)
        assert result == (50.4501, -104.6201)

    async def test_google_fallback_too_far_returns_none(self):
        google_payload = {
            "snappedPoints": [{"location": {"latitude": 60.0, "longitude": -110.0}}],
        }
        with (
            patch.object(rd, "get_app_settings", return_value={"google_maps_api_key": "key"}),
            patch.object(rd.settings, "OSRM_URL", ""),
            patch.object(rd.settings, "OSRM_FALLBACK_URL", "", create=True),
            patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=google_payload))),
        ):
            result = await rd.snap_to_road(50.45, -104.62)
        assert result is None

    async def test_google_fallback_empty_points_returns_none(self):
        with (
            patch.object(rd, "get_app_settings", return_value={"google_maps_api_key": "key"}),
            patch.object(rd.settings, "OSRM_URL", ""),
            patch.object(rd.settings, "OSRM_FALLBACK_URL", "", create=True),
            patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload={"snappedPoints": []}))),
        ):
            result = await rd.snap_to_road(50.45, -104.62)
        assert result is None

    async def test_google_fallback_non_200_returns_none(self):
        with (
            patch.object(rd, "get_app_settings", return_value={"google_maps_api_key": "key"}),
            patch.object(rd.settings, "OSRM_URL", ""),
            patch.object(rd.settings, "OSRM_FALLBACK_URL", "", create=True),
            patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(status_code=500))),
        ):
            result = await rd.snap_to_road(50.45, -104.62)
        assert result is None

    async def test_google_fallback_exception_returns_none(self):
        with (
            patch.object(rd, "get_app_settings", return_value={"google_maps_api_key": "key"}),
            patch.object(rd.settings, "OSRM_URL", ""),
            patch.object(rd.settings, "OSRM_FALLBACK_URL", "", create=True),
            patch.object(rd.httpx, "AsyncClient", _client_factory(exc=RuntimeError("boom"))),
        ):
            result = await rd.snap_to_road(50.45, -104.62)
        assert result is None


# ── compute_segmented_road_route failure-accumulation branches ──────────────
# Lines ~360-364 (insufficient_points), ~379-382 (provider_unavailable),
# ~388-395 (invalid_provider_geometry). The happy path already has coverage
# in test_route_distance.py; none of the three `failures.append(...)` sites
# were previously hit.


class TestComputeSegmentedRoadRouteFailureBranches:
    async def test_segment_with_fewer_than_two_points_records_insufficient_points(self):
        segments = [[_crumb(50.45, -104.62)]]  # single point -> below the len(points) < 2 guard
        with patch.object(rd, "get_app_settings", return_value={}):
            result = await rd.compute_segmented_road_route(segments)
        assert result["distance_km"] == 0.0
        assert result["segments"][0]["distance_km"] == 0.0
        assert result["segments"][0]["matched_segments"] == []
        assert result["failures"] == [{"segment_index": 0, "reason": "insufficient_points"}]
        assert result["provider"] is None

    async def test_chunk_with_no_provider_available_records_provider_unavailable(self):
        segment = [_crumb(50.45 + i * 0.001, -104.62) for i in range(6)]
        with (
            patch.object(rd, "get_app_settings", return_value={}),  # no osrm_url, no google key
        ):
            result = await rd.compute_segmented_road_route([segment])
        assert result["segments"][0]["matched_segments"] == []
        assert result["failures"] == [{"segment_index": 0, "chunk_index": 0, "reason": "provider_unavailable"}]
        assert result["distance_km"] == 0.0
        assert result["provider"] is None

    async def test_matching_with_non_positive_distance_or_short_polyline_records_invalid_geometry(self):
        segment = [_crumb(50.45 + i * 0.001, -104.62) for i in range(6)]

        async def fake_osrm(_points, _url):
            # One matching with distance <= 0, one with too-short polyline,
            # one that is genuinely valid -- so segment_distance_km should
            # only reflect the valid matching.
            return [
                (0.0, [[50.45, -104.62], [50.451, -104.62]]),
                (1.0, [[50.45, -104.62]]),
                (2.0, [[50.45, -104.62], [50.452, -104.62]]),
            ]

        with (
            patch.object(rd, "get_app_settings", return_value={"osrm_url": "http://osrm:5000"}),
            patch.object(rd, "_compute_osrm_chunk_matchings", fake_osrm),
        ):
            result = await rd.compute_segmented_road_route([segment])

        assert result["segments"][0]["distance_km"] == 2.0
        assert len(result["segments"][0]["matched_segments"]) == 1
        assert result["segments"][0]["matched_segments"][0]["distance_km"] == 2.0
        reasons = [f["reason"] for f in result["failures"]]
        assert reasons.count("invalid_provider_geometry") == 2
        assert result["provider"] == "osrm_match"

    async def test_mixed_providers_across_segments_reports_mixed(self):
        seg_a = [_crumb(50.45 + i * 0.001, -104.62) for i in range(6)]
        seg_b = [_crumb(51.45 + i * 0.001, -105.62) for i in range(6)]

        async def fake_osrm(points, _url):
            if points[0]["lat"] < 51:
                return [(1.0, [[points[0]["lat"], points[0]["lng"]], [points[-1]["lat"], points[-1]["lng"]]])]
            return None

        async def fake_google(points, _key):
            return 2.0, [[points[0]["lat"], points[0]["lng"]], [points[-1]["lat"], points[-1]["lng"]]]

        with (
            patch.object(
                rd,
                "get_app_settings",
                return_value={"osrm_url": "http://osrm:5000", "google_maps_api_key": "key"},
            ),
            patch.object(rd, "_compute_osrm_chunk_matchings", fake_osrm),
            patch.object(rd, "_compute_via_google_roads", fake_google),
        ):
            result = await rd.compute_segmented_road_route([seg_a, seg_b])

        assert result["provider"] == "mixed"
        assert result["distance_km"] == 3.0

    async def test_empty_observed_segments_returns_empty_result(self):
        result = await rd.compute_segmented_road_route([])
        assert result == {"segments": [], "distance_km": 0.0, "provider": None, "failures": []}


# ── _observed_segment_points ─────────────────────────────────────────────────
# Small pure-function branches: accepts a dataclass-like object with
# `.points`, a dict with `points` or `observed_points`, or a raw list; drops
# entries missing lat/lng or that aren't dicts.


class _FakeSegmentWithPointsAttr:
    def __init__(self, points):
        self.points = points


class TestObservedSegmentPoints:
    def test_accepts_object_with_points_attribute(self):
        seg = _FakeSegmentWithPointsAttr([_crumb(1, 2), {"lat": None, "lng": 2}, "not-a-dict"])
        assert rd._observed_segment_points(seg) == [_crumb(1, 2)]

    def test_accepts_dict_with_points_key(self):
        seg = {"points": [_crumb(1, 2), _crumb(3, 4)]}
        assert rd._observed_segment_points(seg) == [_crumb(1, 2), _crumb(3, 4)]

    def test_accepts_dict_with_observed_points_key_when_points_missing(self):
        seg = {"observed_points": [_crumb(5, 6)]}
        assert rd._observed_segment_points(seg) == [_crumb(5, 6)]

    def test_dict_with_neither_key_returns_empty(self):
        assert rd._observed_segment_points({"other": "stuff"}) == []

    def test_accepts_raw_list(self):
        assert rd._observed_segment_points([_crumb(1, 2)]) == [_crumb(1, 2)]

    def test_none_segment_returns_empty(self):
        assert rd._observed_segment_points(None) == []


# ── _overlapping_chunks ───────────────────────────────────────────────────────


class TestOverlappingChunks:
    def test_raises_on_invalid_size(self):
        with pytest.raises(ValueError):
            list(rd._overlapping_chunks([1, 2, 3], size=1))

    def test_raises_when_overlap_negative(self):
        with pytest.raises(ValueError):
            list(rd._overlapping_chunks([1, 2, 3], size=5, overlap=-1))

    def test_raises_when_overlap_equals_or_exceeds_size(self):
        with pytest.raises(ValueError):
            list(rd._overlapping_chunks([1, 2, 3], size=5, overlap=5))

    def test_single_chunk_when_points_fit(self):
        points = list(range(5))
        chunks = list(rd._overlapping_chunks(points, size=90, overlap=10))
        assert chunks == [points]

    def test_multiple_chunks_overlap_at_boundary(self):
        points = list(range(10))
        chunks = list(rd._overlapping_chunks(points, size=4, overlap=1))
        # step = size - overlap = 3
        assert chunks == [[0, 1, 2, 3], [3, 4, 5, 6], [6, 7, 8, 9]]


# ── _decode_encoded_polyline truncation guard ────────────────────────────────


class TestDecodeEncodedPolylineTruncation:
    def test_truncated_input_returns_what_decoded_cleanly(self):
        # A single well-formed encoded point followed by a truncated one
        # (missing the terminating byte with bit 0x20 clear).
        good = rd._cap_polyline  # sanity: module import works
        assert good is not None
        full = "_p~iF~ps|U"  # (38.5, -120.2) reference vector
        truncated = full + "~"  # dangling extra byte, index will run out mid-decode
        coords = rd._decode_encoded_polyline(truncated)
        assert coords[0] == [38.5, -120.2]

    def test_empty_string_returns_empty_list(self):
        assert rd._decode_encoded_polyline("") == []


# ── _num_or_zero ──────────────────────────────────────────────────────────────


class TestNumOrZero:
    def test_numeric_passthrough(self):
        assert rd._num_or_zero(3) == 3.0
        assert rd._num_or_zero(3.5) == 3.5

    def test_none_and_bad_string_return_zero(self):
        assert rd._num_or_zero(None) == 0.0
        assert rd._num_or_zero("not-a-number") == 0.0

    def test_numeric_string_parses(self):
        assert rd._num_or_zero("4.2") == 4.2


# ── _cap_polyline small branches ─────────────────────────────────────────────


class TestCapPolylineEdgeCases:
    def test_max_count_below_two_returns_last_point_only(self):
        coords = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        assert rd._cap_polyline(coords, 1) == [[5.0, 6.0]]

    def test_under_limit_returned_unchanged(self):
        coords = [[1.0, 2.0], [3.0, 4.0]]
        assert rd._cap_polyline(coords, 5) is coords


# ── _downsample small branches ───────────────────────────────────────────────


class TestDownsampleEdgeCases:
    def test_max_count_below_two_returns_last_point_only(self):
        points = [{"lat": 1}, {"lat": 2}, {"lat": 3}]
        assert rd._downsample(points, 1) == [{"lat": 3}]
