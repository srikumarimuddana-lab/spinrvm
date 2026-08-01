"""Coverage for admin venue CRUD endpoints (routes/admin/venues.py).

Lower risk than promotions.py (venue/POI content, not fare-affecting), but
prioritizes write paths and 404/503 error branches over the plain list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app", "modules": ["service_areas"]}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _venue_payload(**overrides):
    payload = {
        "name": "Midtown Mall",
        "center_lat": 50.4452,
        "center_lng": -104.6189,
        "radius_m": 150,
        "pickup_points": [{"name": "Main Entrance", "lat": 50.4453, "lng": -104.6190}],
        "is_active": True,
    }
    payload.update(overrides)
    return payload


# ---------- list ----------


def test_list_venues_no_filter(admin_client):
    with patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[{"id": "v1"}])) as mock_get_rows:
        resp = admin_client.get("/api/admin/venues")

    assert resp.status_code == 200
    assert resp.json() == {"venues": [{"id": "v1"}]}
    assert mock_get_rows.call_args[0][1] == {}


def test_list_venues_filters_by_service_area(admin_client):
    with patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[])) as mock_get_rows:
        resp = admin_client.get("/api/admin/venues", params={"service_area_id": "area-1"})

    assert resp.status_code == 200
    assert mock_get_rows.call_args[0][1] == {"service_area_id": "area-1"}


def test_list_venues_db_error_returns_503(admin_client):
    with patch("backend.db_supabase.get_rows", new=AsyncMock(side_effect=Exception("db down"))):
        resp = admin_client.get("/api/admin/venues")

    assert resp.status_code == 503
    # 5xx detail strings are sanitized to a generic message by the global
    # HTTPException handler (utils/error_handling.py); only status is asserted.


# ---------- create ----------


def test_create_venue_success_audits(admin_client):
    with (
        patch("backend.db_supabase.insert_one", new=AsyncMock(return_value={"id": "v-1"})) as mock_insert,
        patch("backend.routes.admin.venues.log_admin_action", new=AsyncMock()) as mock_audit,
    ):
        resp = admin_client.post("/api/admin/venues", json=_venue_payload())

    assert resp.status_code == 200
    assert resp.json() == {"id": "v-1"}
    payload = mock_insert.call_args[0][1]
    assert payload["name"] == "Midtown Mall"
    assert payload["pickup_points"] == [{"name": "Main Entrance", "lat": 50.4453, "lng": -104.6190}]
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args[0][1] == "venue_created"


def test_create_venue_strips_name_whitespace(admin_client):
    with (
        patch("backend.db_supabase.insert_one", new=AsyncMock(return_value={"id": "v-2"})) as mock_insert,
        patch("backend.routes.admin.venues.log_admin_action", new=AsyncMock()),
    ):
        resp = admin_client.post("/api/admin/venues", json=_venue_payload(name="  Airport  "))

    assert resp.status_code == 200
    assert mock_insert.call_args[0][1]["name"] == "Airport"


def test_create_venue_invalid_lat_rejected(admin_client):
    resp = admin_client.post("/api/admin/venues", json=_venue_payload(center_lat=200))
    assert resp.status_code == 422


def test_create_venue_db_error_returns_503(admin_client):
    with patch("backend.db_supabase.insert_one", new=AsyncMock(side_effect=Exception("db down"))):
        resp = admin_client.post("/api/admin/venues", json=_venue_payload())

    assert resp.status_code == 503
    # 5xx detail strings are sanitized to a generic message by the global
    # HTTPException handler (utils/error_handling.py); only status is asserted.


# ---------- update ----------


def test_update_venue_success_audits(admin_client):
    with (
        patch(
            "backend.db_supabase.find_one",
            new=AsyncMock(side_effect=[{"id": "v-1", "name": "Old"}, {"id": "v-1", "name": "New"}]),
        ),
        patch("backend.db_supabase.update_one", new=AsyncMock()) as mock_update,
        patch("backend.routes.admin.venues.log_admin_action", new=AsyncMock()) as mock_audit,
    ):
        resp = admin_client.put("/api/admin/venues/v-1", json=_venue_payload(name="New"))

    assert resp.status_code == 200
    assert resp.json() == {"id": "v-1", "name": "New"}
    mock_update.assert_awaited_once()
    assert mock_update.call_args[0][1] == {"id": "v-1"}
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args[0][1] == "venue_updated"


def test_update_venue_not_found_returns_404(admin_client):
    with patch("backend.db_supabase.find_one", new=AsyncMock(return_value=None)):
        resp = admin_client.put("/api/admin/venues/missing", json=_venue_payload())

    assert resp.status_code == 404


def test_update_venue_db_error_returns_503(admin_client):
    with (
        patch("backend.db_supabase.find_one", new=AsyncMock(return_value={"id": "v-1"})),
        patch("backend.db_supabase.update_one", new=AsyncMock(side_effect=Exception("db down"))),
    ):
        resp = admin_client.put("/api/admin/venues/v-1", json=_venue_payload())

    assert resp.status_code == 503
    # 5xx detail strings are sanitized to a generic message by the global
    # HTTPException handler (utils/error_handling.py); only status is asserted.


# ---------- delete ----------


def test_delete_venue_success_audits(admin_client):
    with (
        patch("backend.db_supabase.find_one", new=AsyncMock(return_value={"id": "v-1", "name": "Midtown Mall"})),
        patch("backend.db_supabase.delete_one", new=AsyncMock()) as mock_delete,
        patch("backend.routes.admin.venues.log_admin_action", new=AsyncMock()) as mock_audit,
    ):
        resp = admin_client.delete("/api/admin/venues/v-1")

    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    mock_delete.assert_awaited_once_with("venues", {"id": "v-1"})
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args[0][1] == "venue_deleted"
    assert mock_audit.call_args[0][4] == {"name": "Midtown Mall"}


def test_delete_venue_not_found_returns_404(admin_client):
    with patch("backend.db_supabase.find_one", new=AsyncMock(return_value=None)):
        resp = admin_client.delete("/api/admin/venues/missing")

    assert resp.status_code == 404


def test_delete_venue_db_error_returns_503(admin_client):
    with (
        patch("backend.db_supabase.find_one", new=AsyncMock(return_value={"id": "v-1"})),
        patch("backend.db_supabase.delete_one", new=AsyncMock(side_effect=Exception("db down"))),
    ):
        resp = admin_client.delete("/api/admin/venues/v-1")

    assert resp.status_code == 503
    # 5xx detail strings are sanitized to a generic message by the global
    # HTTPException handler (utils/error_handling.py); only status is asserted.
