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


_AREA_ROW = {
    "id": "area-001",
    "name": "Saskatoon",
    "city": "Saskatoon",
    "is_active": True,
    "is_airport": False,
    "search_radius_km": 10.0,
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

    def test_empty_list_when_no_areas(self, client):
        from backend.routes import service_areas as mod

        with patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=None)):
            r = client.get("/api/v1/service-areas")

        assert r.status_code == 200
        assert r.json() == []
