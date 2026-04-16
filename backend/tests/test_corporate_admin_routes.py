# backend/tests/test_corporate_admin_routes.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def _admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def test_list_filters_by_status(test_client, _admin_override):
    rows = [
        {
            "id": "c1",
            "name": "A",
            "status": "pending_verification",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "country_code": "CA",
            "currency": "CAD",
            "locale": "en-CA",
            "size_tier": "smb",
            "is_active": True,
            "timezone": "America/Toronto",
            "credit_limit": 0,
        },
    ]
    with patch(
        "db_supabase.list_corporate_accounts_filtered",
        AsyncMock(return_value=rows),
    ):
        resp = test_client.get(
            "/api/admin/corporate-accounts?status=pending_verification",
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending_verification"


def test_status_filter_validates_enum(test_client, _admin_override):
    resp = test_client.get(
        "/api/admin/corporate-accounts?status=bogus",
    )
    assert resp.status_code == 422
