"""Tests for geo_utils — pure math, no external deps."""

import pytest

from geo_utils import calculate_distance, get_service_area_polygon, point_in_polygon


@pytest.fixture(autouse=True)
def patch_external_dependencies():
    """Override the conftest autouse fixture — these tests need no mocking."""
    yield


# ── calculate_distance ──


class TestCalculateDistance:
    def test_same_point_is_zero(self):
        assert calculate_distance(52.13, -106.67, 52.13, -106.67) == 0.0

    def test_known_distance_saskatoon_to_regina(self):
        # Saskatoon (52.13, -106.67) to Regina (50.45, -104.62) ≈ 235 km
        dist = calculate_distance(52.13, -106.67, 50.45, -104.62)
        assert 230 < dist < 250

    def test_known_distance_short(self):
        # ~1.1 km apart within Saskatoon
        dist = calculate_distance(52.130, -106.670, 52.140, -106.670)
        assert 1.0 < dist < 1.2

    def test_known_distance_toronto_to_montreal(self):
        # Toronto (43.65, -79.38) to Montreal (45.50, -73.57) ≈ 504 km
        dist = calculate_distance(43.65, -79.38, 45.50, -73.57)
        assert 490 < dist < 520

    def test_symmetry(self):
        d1 = calculate_distance(52.13, -106.67, 50.45, -104.62)
        d2 = calculate_distance(50.45, -104.62, 52.13, -106.67)
        assert abs(d1 - d2) < 0.001

    def test_cross_equator(self):
        dist = calculate_distance(1.0, 0.0, -1.0, 0.0)
        assert 220 < dist < 225  # ~222 km

    def test_returns_km(self):
        # 1 degree latitude ≈ 111 km
        dist = calculate_distance(0.0, 0.0, 1.0, 0.0)
        assert 110 < dist < 112


# ── point_in_polygon ──

SASKATOON_POLYGON = [
    {"lat": 52.19, "lng": -106.75},
    {"lat": 52.19, "lng": -106.55},
    {"lat": 52.08, "lng": -106.55},
    {"lat": 52.08, "lng": -106.75},
]


class TestPointInPolygon:
    def test_point_inside(self):
        assert point_in_polygon(52.13, -106.67, SASKATOON_POLYGON) is True

    def test_point_outside(self):
        assert point_in_polygon(50.0, -106.67, SASKATOON_POLYGON) is False

    def test_point_far_away(self):
        assert point_in_polygon(0.0, 0.0, SASKATOON_POLYGON) is False

    def test_triangle(self):
        tri = [
            {"lat": 0.0, "lng": 0.0},
            {"lat": 0.0, "lng": 10.0},
            {"lat": 10.0, "lng": 0.0},
        ]
        assert point_in_polygon(2.0, 2.0, tri) is True
        assert point_in_polygon(8.0, 8.0, tri) is False


class TestPointInPolygonEdgeCases:
    """Edge cases for the n < 3 guard and invalid inputs."""

    def test_empty_polygon(self):
        assert point_in_polygon(52.13, -106.67, []) is False

    def test_single_point_polygon(self):
        assert point_in_polygon(0.0, 0.0, [{"lat": 0.0, "lng": 0.0}]) is False

    def test_two_point_polygon(self):
        line = [{"lat": 0.0, "lng": 0.0}, {"lat": 1.0, "lng": 1.0}]
        assert point_in_polygon(0.5, 0.5, line) is False

    def test_three_point_minimum(self):
        """Exactly 3 points (triangle) should work."""
        tri = [
            {"lat": 0.0, "lng": 0.0},
            {"lat": 0.0, "lng": 10.0},
            {"lat": 10.0, "lng": 0.0},
        ]
        assert point_in_polygon(1.0, 1.0, tri) is True

    def test_collinear_points(self):
        """3 points on a line form a degenerate polygon — point shouldn't be 'inside'."""
        line = [
            {"lat": 0.0, "lng": 0.0},
            {"lat": 1.0, "lng": 0.0},
            {"lat": 2.0, "lng": 0.0},
        ]
        assert point_in_polygon(1.0, 1.0, line) is False

    def test_very_large_polygon(self):
        """Polygon covering a large area should still work."""
        big = [
            {"lat": -80.0, "lng": -170.0},
            {"lat": -80.0, "lng": 170.0},
            {"lat": 80.0, "lng": 170.0},
            {"lat": 80.0, "lng": -170.0},
        ]
        assert point_in_polygon(0.0, 0.0, big) is True
        assert point_in_polygon(85.0, 0.0, big) is False

    def test_negative_coordinates(self):
        poly = [
            {"lat": -10.0, "lng": -10.0},
            {"lat": -10.0, "lng": -5.0},
            {"lat": -5.0, "lng": -5.0},
            {"lat": -5.0, "lng": -10.0},
        ]
        assert point_in_polygon(-7.0, -7.0, poly) is True
        assert point_in_polygon(0.0, 0.0, poly) is False


# ── get_service_area_polygon ──


class TestGetServiceAreaPolygon:
    def test_from_polygon_field(self):
        area = {
            "polygon": [
                {"lat": 52.19, "lng": -106.75},
                {"lat": 52.19, "lng": -106.55},
                {"lat": 52.08, "lng": -106.55},
            ]
        }
        result = get_service_area_polygon(area)
        assert len(result) == 3
        assert result[0]["lat"] == 52.19

    def test_from_geojson(self):
        area = {
            "geojson": {
                "type": "Polygon",
                "coordinates": [[[-106.75, 52.19], [-106.55, 52.19], [-106.55, 52.08], [-106.75, 52.08]]],
            }
        }
        result = get_service_area_polygon(area)
        assert len(result) == 4
        assert result[0]["lat"] == 52.19
        assert result[0]["lng"] == -106.75

    def test_empty_area(self):
        assert get_service_area_polygon({}) == []
        assert get_service_area_polygon(None) == []

    def test_polygon_too_short(self):
        area = {"polygon": [{"lat": 0, "lng": 0}, {"lat": 1, "lng": 1}]}
        assert get_service_area_polygon(area) == []
