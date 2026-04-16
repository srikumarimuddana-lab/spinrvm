# backend/tests/test_corporate_kyb.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def _admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def _row(status_value: str) -> dict:
    return {
        "id": "c1",
        "name": "Acme",
        "status": status_value,
        "country_code": "CA",
        "currency": "CAD",
        "locale": "en-CA",
        "timezone": "America/Toronto",
        "size_tier": "smb",
        "is_active": True,
        "credit_limit": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_approve_kyb_flips_status_to_active(test_client, _admin_override):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=_row("active")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


def test_reject_kyb_flips_status_to_suspended(test_client, _admin_override):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=_row("suspended")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False, "note": "doc unreadable"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"


def test_kyb_review_404_on_missing_company(test_client, _admin_override):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=None),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/nonexistent/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 404, resp.text
