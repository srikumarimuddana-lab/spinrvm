"""HTTP-level (TestClient) tests for routes/admin/data_transfer_search.py's
route handler — gap H4 from
reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md.
Companion to test_data_transfer_search.py, which covers
_text_filter/_build_filters directly (pure functions) but never exercises
the actual endpoint (auth, entity_type dispatch, pagination, total_count).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}
_ROWS = [{"id": "u1", "first_name": "Jane", "last_name": "Doe", "email": "jane@example.com", "phone": "+15550001111"}]


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def test_search_requires_admin_auth(test_client):
    resp = test_client.get("/api/admin/data-transfer/search")
    assert resp.status_code in (401, 403)


def test_search_default_entity_type_queries_users(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=_ROWS)) as get_rows,
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=1)),
    ):
        resp = admin_client.get("/api/admin/data-transfer/search?q=Jane")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 1
    assert body["rows"] == [{**_ROWS[0], "full_name": "Jane Doe"}]
    assert get_rows.call_args.args[0] == "users"


def test_search_driver_entity_type_queries_drivers_table(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])) as get_rows,
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=0)),
    ):
        resp = admin_client.get("/api/admin/data-transfer/search?entity_type=driver")
    assert resp.status_code == 200
    assert get_rows.call_args.args[0] == "drivers"


def test_search_rider_entity_type_filters_role(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])) as get_rows,
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=0)),
    ):
        resp = admin_client.get("/api/admin/data-transfer/search?entity_type=rider")
    assert resp.status_code == 200
    assert get_rows.call_args.args[0] == "users"
    assert get_rows.call_args.args[1].get("role") == "rider"


def test_search_driver_full_name_prefers_name_column(admin_client):
    """Regression: neither users nor drivers has a full_name column
    (drivers has a separate `name` column; users has none at all) —
    filtering/selecting on full_name made every search 500. The response
    must still expose full_name for the UI, derived from the real columns."""
    driver_row = {
        "id": "d1",
        "user_id": "u1",
        "name": "Driver Name",
        "first_name": "First",
        "last_name": "Last",
        "phone": "+15550002222",
        "created_at": "2026-07-01T00:00:00Z",
        "license_plate": "ABC123",
    }
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[driver_row])),
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=1)),
    ):
        resp = admin_client.get("/api/admin/data-transfer/search?entity_type=driver")
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["full_name"] == "Driver Name"
    assert row["vehicle_plate"] == "ABC123"
    assert "license_plate" not in row
    assert "email" not in row


def test_search_rider_full_name_falls_back_to_first_last(admin_client):
    rider_row = {
        "id": "u2",
        "first_name": "First",
        "last_name": "Last",
        "email": "r@example.com",
        "phone": "+15550003333",
        "created_at": "2026-07-01T00:00:00Z",
        "role": "rider",
    }
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[rider_row])),
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=1)),
    ):
        resp = admin_client.get("/api/admin/data-transfer/search?entity_type=rider")
    assert resp.status_code == 200
    assert resp.json()["rows"][0]["full_name"] == "First Last"


def test_search_rejects_invalid_entity_type(admin_client):
    resp = admin_client.get("/api/admin/data-transfer/search?entity_type=admin")
    assert resp.status_code == 422


def test_search_rejects_page_size_over_max(admin_client):
    resp = admin_client.get("/api/admin/data-transfer/search?page_size=500")
    assert resp.status_code == 422


def test_search_pagination_computes_offset(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])) as get_rows,
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=0)),
    ):
        resp = admin_client.get("/api/admin/data-transfer/search?page=3&page_size=20")
    assert resp.status_code == 200
    assert get_rows.call_args.kwargs.get("offset") == 40
