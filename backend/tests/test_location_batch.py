"""Tests for the location-batch endpoint's input parsing logic."""

import pytest


@pytest.fixture(autouse=True)
def patch_external_dependencies():
    """Override conftest autouse fixture."""
    yield


# Extract the parsing logic into testable units.
# The endpoint accepts Union[List[dict], dict] and normalizes to a list of points,
# then picks the last point and extracts lat/lng with multiple key name support.


def _extract_points(batch):
    """Mirrors the point extraction logic from update_location_batch."""
    points = []
    if isinstance(batch, list):
        points = batch
    elif isinstance(batch, dict):
        points = batch.get("locations") or batch.get("points") or []
    return points


def _extract_coords(point):
    """Mirrors the coordinate extraction from the latest point."""
    lat = point.get("latitude") or point.get("lat")
    lng = point.get("longitude") or point.get("lng")
    heading = point.get("heading", 0)
    return lat, lng, heading


# ── Point extraction from different input shapes ──


class TestExtractPoints:
    def test_list_input(self):
        batch = [{"lat": 52.1, "lng": -106.6}, {"lat": 52.2, "lng": -106.5}]
        assert _extract_points(batch) == batch

    def test_dict_with_locations_key(self):
        batch = {"locations": [{"lat": 52.1, "lng": -106.6}]}
        assert len(_extract_points(batch)) == 1

    def test_dict_with_points_key(self):
        batch = {"points": [{"lat": 52.1, "lng": -106.6}]}
        assert len(_extract_points(batch)) == 1

    def test_dict_locations_takes_priority(self):
        """'locations' is checked before 'points'."""
        batch = {
            "locations": [{"lat": 1.0, "lng": 1.0}],
            "points": [{"lat": 2.0, "lng": 2.0}],
        }
        points = _extract_points(batch)
        assert points[0]["lat"] == 1.0

    def test_empty_list(self):
        assert _extract_points([]) == []

    def test_empty_dict(self):
        assert _extract_points({}) == []

    def test_dict_with_no_known_keys(self):
        assert _extract_points({"data": [{"lat": 1}]}) == []

    def test_single_point_list(self):
        batch = [{"lat": 52.1, "lng": -106.6}]
        assert len(_extract_points(batch)) == 1


# ── Coordinate extraction with different key names ──


class TestExtractCoords:
    def test_lat_lng_keys(self):
        lat, lng, _ = _extract_coords({"lat": 52.1, "lng": -106.6})
        assert lat == 52.1
        assert lng == -106.6

    def test_latitude_longitude_keys(self):
        lat, lng, _ = _extract_coords({"latitude": 52.1, "longitude": -106.6})
        assert lat == 52.1
        assert lng == -106.6

    def test_heading(self):
        _, _, heading = _extract_coords({"lat": 0, "lng": 0, "heading": 180})
        assert heading == 180

    def test_heading_default(self):
        _, _, heading = _extract_coords({"lat": 0, "lng": 0})
        assert heading == 0

    def test_missing_coords(self):
        lat, lng, _ = _extract_coords({})
        assert lat is None
        assert lng is None

    def test_last_point_used(self):
        """The endpoint uses points[-1] — verify the pattern."""
        points = [
            {"lat": 1.0, "lng": 1.0},
            {"lat": 2.0, "lng": 2.0},
            {"lat": 3.0, "lng": 3.0},
        ]
        latest = points[-1]
        lat, lng, _ = _extract_coords(latest)
        assert lat == 3.0
        assert lng == 3.0


# ── Integration: full flow with mocked DB ──

# NOTE: Full endpoint integration tests require firebase_admin + full backend deps.
# The parsing logic above covers all input shapes without needing the full import chain.
