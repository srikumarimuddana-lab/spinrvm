# backend/tests/test_corporate_status.py
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


def test_suspend_active_company(test_client, _admin_override):
    with patch(
        "routes.corporate_accounts.get_corporate_account_by_id",
        AsyncMock(return_value=_row("active")),
    ), patch(
        "db_supabase.update_corporate_account_status",
        AsyncMock(return_value=_row("suspended")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "suspended", "reason": "overdue balance"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"


def test_cannot_reopen_closed_company(test_client, _admin_override):
    with patch(
        "routes.corporate_accounts.get_corporate_account_by_id",
        AsyncMock(return_value=_row("closed")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "active"},
        )
    assert resp.status_code == 409, resp.text
    assert "closed" in resp.json()["detail"].lower()


def test_status_change_404_when_company_missing(test_client, _admin_override):
    with patch(
        "routes.corporate_accounts.get_corporate_account_by_id",
        AsyncMock(return_value=None),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/nonexistent/status",
            json={"status": "suspended"},
        )
    assert resp.status_code == 404, resp.text
