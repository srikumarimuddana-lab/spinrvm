"""Tests for the public GET /service-areas endpoint.

Pins that:
  - Active service areas are returned without authentication
  - Inactive areas are excluded
  - Sensitive admin fields (surge_multiplier, tax rates) are not exposed
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes.service_areas import api_router


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app)


_PLAIN_POLYGON = [
    {"lat": 52.1, "lng": -106.6},
    {"lat": 52.2, "lng": -106.6},
    {"lat": 52.2, "lng": -106.5},
    {"lat": 52.1, "lng": -106.5},
]

_GEOJSON_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[-106.6, 52.1], [-106.6, 52.2], [-106.5, 52.2], [-106.5, 52.1]]],
}

_AREA_ROW = {
    "id": "area-001",
    "name": "Saskatoon",
    "city": "Saskatoon",
    "is_active": True,
    "is_airport": False,
    "search_radius_km": 10.0,
    "polygon": _PLAIN_POLYGON,
    # surge_multiplier + surge_active are intentionally public (rider/driver
    # surge badge; surge must be visible before booking per regulatory rules).
    # surge_enabled is the per-area admin master gate — only when it is on does
    # the public endpoint report a live multiplier/active flag.
    "surge_enabled": True,
    "surge_multiplier": 1.5,
    "surge_active": True,
    # Admin-only fields that must NOT leak to the public endpoint:
    "gst_rate": 5.0,
    "pst_rate": 6.0,
    "driver_matching_algorithm": "nearest",
}

_INACTIVE_ROW = {**_AREA_ROW, "id": "area-002", "name": "Regina", "is_active": False}


class TestPublicServiceAreas:
    def test_returns_active_areas(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[_AREA_ROW])):
            r = client.get("/api/v1/service-areas")

        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["id"] == "area-001"
        assert body[0]["name"] == "Saskatoon"

    def test_no_auth_required(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
            r = client.get("/api/v1/service-areas")

        assert r.status_code == 200

    def test_admin_fields_not_exposed(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[_AREA_ROW])):
            r = client.get("/api/v1/service-areas")

        area = r.json()[0]
        # surge_multiplier + surge_active are intentionally public so clients
        # can render the surge badge and gate booking behind visibility rules.
        assert area["surge_multiplier"] == 1.5
        assert area["surge_active"] is True
        # These remain admin-only and must never reach the public response.
        assert "gst_rate" not in area
        assert "pst_rate" not in area
        assert "driver_matching_algorithm" not in area

    def test_surge_suppressed_when_not_enabled(self, client):
        """A stale multiplier/active flag must not surface unless surge_enabled."""
        from backend.routes import service_areas as mod

        stale = {**_AREA_ROW, "surge_enabled": False, "surge_active": True, "surge_multiplier": 2.0}
        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[stale])):
            r = client.get("/api/v1/service-areas")

        area = r.json()[0]
        assert area["surge_active"] is False
        assert area["surge_multiplier"] == 1.0

    def test_only_active_filter_passed_to_db(self, client):
        from backend.routes import service_areas as mod

        mock_get = AsyncMock(return_value=[])
        with patch.object(mod.db_supabase, "get_rows", mock_get):
            client.get("/api/v1/service-areas")

        call_filters = mock_get.call_args[0][1]
        assert call_filters.get("is_active") is True

    def test_excludes_child_airport_subareas(self, client):
        """Regression: child/sub-areas (airport zones carry a
        parent_service_area_id) must never reach the driver/rider picker.

        The query must constrain parent_service_area_id IS NULL — the key has
        to be present AND None so _apply_filters emits `is.null`, not merely
        absent (which would return every active row, airports included). This
        is the failure that put "Regina Airport" beside "Regina" on the driver
        profile-setup screen.
        """
        from backend.routes import service_areas as mod

        mock_get = AsyncMock(return_value=[])
        with patch.object(mod.db_supabase, "get_rows", mock_get):
            client.get("/api/v1/service-areas")

        call_filters = mock_get.call_args[0][1]
        assert "parent_service_area_id" in call_filters
        assert call_filters["parent_service_area_id"] is None

    def test_excludes_airport_flag_even_on_top_level_rows(self, client):
        """Regression: the parent-id filter alone is not enough.

        A correctly modeled airport is a child row, but airports also exist as
        hand-created/legacy TOP-LEVEL rows (e.g. "Regina Airport", "riyadh
        airport" on the driver profile-setup screen). Those have
        parent_service_area_id IS NULL, so the parent filter lets them through.
        The query must also constrain is_airport != true so an airport never
        appears in the picker regardless of how the row is modeled.
        """
        from backend.routes import service_areas as mod

        mock_get = AsyncMock(return_value=[])
        with patch.object(mod.db_supabase, "get_rows", mock_get):
            client.get("/api/v1/service-areas")

        call_filters = mock_get.call_args[0][1]
        assert call_filters.get("is_airport") == {"$ne": True}

    def test_empty_list_when_no_areas(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=None)):
            r = client.get("/api/v1/service-areas")

        assert r.status_code == 200
        assert r.json() == []

    def test_polygon_returned_as_lat_lng_array(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[_AREA_ROW])):
            r = client.get("/api/v1/service-areas")

        poly = r.json()[0]["polygon"]
        assert isinstance(poly, list)
        assert len(poly) == 4
        assert all("lat" in p and "lng" in p for p in poly)

    def test_geojson_polygon_normalized_to_lat_lng(self, client):
        """Admin dashboard saves GeoJSON dicts — endpoint must normalize them."""
        from backend.routes import service_areas as mod

        row = {**_AREA_ROW, "polygon": _GEOJSON_POLYGON}
        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[row])):
            r = client.get("/api/v1/service-areas")

        poly = r.json()[0]["polygon"]
        assert isinstance(poly, list)
        assert len(poly) == 4
        assert all("lat" in p and "lng" in p for p in poly)
        assert poly[0]["lat"] == pytest.approx(52.1)
        assert poly[0]["lng"] == pytest.approx(-106.6)

    def test_missing_polygon_returns_empty_list(self, client):
        from backend.routes import service_areas as mod

        row = {k: v for k, v in _AREA_ROW.items() if k != "polygon"}
        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[row])):
            r = client.get("/api/v1/service-areas")

        assert r.json()[0]["polygon"] == []


_AIRPORT_ZONE_ROW = {
    "id": "zone-apt-001",
    "name": "Regina Airport",
    "is_airport": True,
    "airport_fee": 5.0,
    "parent_service_area_id": "area-001",
    "is_active": True,
    "polygon": _PLAIN_POLYGON,
}


class TestAirportZones:
    """Tests for GET /service-areas/{area_id}/airport-zones (HM-21)."""

    def test_returns_airport_zones_for_parent(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[_AIRPORT_ZONE_ROW])):
            r = client.get("/api/v1/service-areas/area-001/airport-zones")

        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["id"] == "zone-apt-001"
        assert body[0]["name"] == "Regina Airport"
        assert body[0]["is_airport"] is True
        assert body[0]["airport_fee"] == 5.0

    def test_filters_by_parent_and_airport_flag(self, client):
        from backend.routes import service_areas as mod

        mock_get = AsyncMock(return_value=[])
        with patch.object(mod.db_supabase, "get_rows", mock_get):
            client.get("/api/v1/service-areas/area-001/airport-zones")

        call_args = mock_get.call_args
        filters = call_args[0][1]
        assert filters["parent_service_area_id"] == "area-001"
        assert filters["is_airport"] is True
        assert filters["is_active"] is True

    def test_returns_polygon_as_lat_lng_array(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[_AIRPORT_ZONE_ROW])):
            r = client.get("/api/v1/service-areas/area-001/airport-zones")

        poly = r.json()[0]["polygon"]
        assert isinstance(poly, list)
        assert len(poly) == 4
        assert all("lat" in p and "lng" in p for p in poly)

    def test_empty_when_no_airport_zones(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=None)):
            r = client.get("/api/v1/service-areas/area-001/airport-zones")

        assert r.status_code == 200
        assert r.json() == []

    def test_only_airport_fields_exposed(self, client):
        from backend.routes import service_areas as mod

        enriched = {**_AIRPORT_ZONE_ROW, "surge_multiplier": 2.0, "gst_rate": 5.0}
        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[enriched])):
            r = client.get("/api/v1/service-areas/area-001/airport-zones")

        zone = r.json()[0]
        assert "surge_multiplier" not in zone
        assert "gst_rate" not in zone
