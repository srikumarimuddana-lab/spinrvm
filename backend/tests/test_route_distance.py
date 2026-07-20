"""Tests for backend/utils/route_distance.py — Roads-API distance recompute.

The function is on the hot completion path (ride end → receipt), so failure
modes must be soft: every branch that can't compute a value returns None,
and the caller in complete_ride falls back to the haversine-with-spike-filter
value. These tests assert each branch returns None when appropriate and
returns a sensible km value on a happy path.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


def _crumb(lat, lng, phase="trip_in_progress"):
    return {"lat": lat, "lng": lng, "tracking_phase": phase}


class TestComputeRoadDistanceKm:
    def test_returns_none_on_empty_breadcrumbs(self):
        from backend.utils.route_distance import compute_road_distance_km

        result = asyncio.run(compute_road_distance_km([]))
        assert result is None

    def test_returns_none_when_no_api_key(self):
        from backend.utils import route_distance as rd

        crumbs = [_crumb(52.10 + i * 0.001, -106.70) for i in range(10)]
        with patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": ""})):
            result = asyncio.run(rd.compute_road_distance_km(crumbs))
        assert result is None

    def test_returns_none_with_fewer_than_min_trip_points(self):
        from backend.utils import route_distance as rd

        # 3 trip points, _MIN_POINTS is 5
        crumbs = [_crumb(52.10, -106.70) for _ in range(3)]
        with patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})):
            result = asyncio.run(rd.compute_road_distance_km(crumbs))
        assert result is None

    def test_filters_to_trip_in_progress_phase(self):
        from backend.utils import route_distance as rd

        # 4 trip points + 4 navigating — only trip counts, so we're under the min.
        crumbs = [_crumb(52.10, -106.70, phase="navigating_to_pickup") for _ in range(10)]
        crumbs += [_crumb(52.10, -106.70, phase="trip_in_progress") for _ in range(4)]
        with patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})):
            result = asyncio.run(rd.compute_road_distance_km(crumbs))
        assert result is None

    def test_returns_none_on_http_error(self):
        from backend.utils import route_distance as rd

        crumbs = [_crumb(52.10 + i * 0.001, -106.70) for i in range(10)]

        class FakeResp:
            status_code = 500

            def json(self):  # pragma: no cover
                return {}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **kw):
                return FakeResp()

        with (
            patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})),
            patch.object(rd.httpx, "AsyncClient", FakeClient),
        ):
            result = asyncio.run(rd.compute_road_distance_km(crumbs))
        assert result is None

    def test_returns_none_when_api_raises(self):
        from backend.utils import route_distance as rd

        crumbs = [_crumb(52.10 + i * 0.001, -106.70) for i in range(10)]

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **kw):
                raise RuntimeError("network down")

        with (
            patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})),
            patch.object(rd.httpx, "AsyncClient", FakeClient),
        ):
            result = asyncio.run(rd.compute_road_distance_km(crumbs))
        assert result is None

    def test_returns_none_when_snapped_points_too_few(self):
        from backend.utils import route_distance as rd

        crumbs = [_crumb(52.10 + i * 0.001, -106.70) for i in range(10)]

        class FakeResp:
            status_code = 200

            def json(self):
                return {"snappedPoints": [{"location": {"latitude": 52.10, "longitude": -106.70}}]}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **kw):
                return FakeResp()

        with (
            patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})),
            patch.object(rd.httpx, "AsyncClient", FakeClient),
        ):
            result = asyncio.run(rd.compute_road_distance_km(crumbs))
        assert result is None

    def test_sums_snapped_polyline(self):
        """Happy path: ~7 km trip, API returns interpolated snapped points,
        we sum haversine between them and get back ~7 km."""
        from backend.utils import route_distance as rd

        # Send 10 input points; API returns interpolated 8 points along a
        # 7 km path (north along constant longitude, ~1 km per step).
        crumbs = [_crumb(52.10 + i * 0.007, -106.70) for i in range(10)]
        snapped = [{"location": {"latitude": 52.10 + i * 0.009, "longitude": -106.70}} for i in range(8)]

        class FakeResp:
            status_code = 200

            def json(self):
                return {"snappedPoints": snapped}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **kw):
                return FakeResp()

        with (
            patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})),
            patch.object(rd.httpx, "AsyncClient", FakeClient),
        ):
            result = asyncio.run(rd.compute_road_distance_km(crumbs))
        # 7 segments of ~1 km each = ~7 km. Allow slack for haversine + degree.
        assert result is not None
        assert 6.0 <= result <= 9.0, f"Expected ~7 km, got {result}"

    def test_downsamples_when_over_max_points(self):
        from backend.utils import route_distance as rd

        # 250 points should be downsampled to <=100 before the API call.
        crumbs = [_crumb(52.10 + i * 0.0001, -106.70) for i in range(250)]
        sent_path: list[str] = []

        class FakeResp:
            status_code = 200

            def json(self):
                return {
                    "snappedPoints": [
                        {"location": {"latitude": 52.10, "longitude": -106.70}},
                        {"location": {"latitude": 52.11, "longitude": -106.70}},
                    ]
                }

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, _url, params=None, **_kw):
                sent_path.append(params.get("path", ""))
                return FakeResp()

        with (
            patch.object(rd, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})),
            patch.object(rd.httpx, "AsyncClient", FakeClient),
        ):
            asyncio.run(rd.compute_road_distance_km(crumbs))

        assert sent_path, "API was not called"
        # Hard cap: Roads API rejects > 100 points. The downsampler must
        # reserve a slot for the appended trailing point so the total never
        # exceeds _MAX_POINTS_PER_REQUEST.
        sent_count = len(sent_path[0].split("|"))
        assert sent_count <= 100, f"Sent {sent_count} points, expected <=100"

    def test_segmented_google_fallback_preserves_each_observed_boundary_and_sums_only_successes(self):
        from backend.utils import route_distance as rd

        first_segment = [_crumb(52.10 + i * 0.001, -106.70) for i in range(6)]
        second_segment = [_crumb(52.20 + i * 0.001, -106.70) for i in range(6)]
        google_calls = []

        async def fake_osrm(_points, _url):
            return None

        async def fake_google(points, _key):
            google_calls.append(points)
            distance = 1.0 if points[0]["lat"] < 52.15 else 2.0
            return distance, [[points[0]["lat"], points[0]["lng"]], [points[-1]["lat"], points[-1]["lng"]]]

        with (
            patch.object(
                rd,
                "get_app_settings",
                AsyncMock(return_value={"osrm_url": "http://osrm", "google_maps_api_key": "key"}),
            ),
            patch.object(rd, "_compute_osrm_chunk_matchings", fake_osrm),
            patch.object(rd, "_compute_via_google_roads", fake_google),
        ):
            result = asyncio.run(rd.compute_segmented_road_route([first_segment, second_segment]))

        assert len(google_calls) == 2
        assert result["distance_km"] == 3.0
        assert [segment["distance_km"] for segment in result["segments"]] == [1.0, 2.0]
        assert result["segments"][0]["matched_segments"] is not result["segments"][1]["matched_segments"]
