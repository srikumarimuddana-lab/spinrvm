# backend/tests/test_corporate_status.py
from unittest.mock import AsyncMock, patch

from backend.tests._factories import corporate_account_row


def test_suspend_active_company(test_client, admin_override):
    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=corporate_account_row("suspended")),
        ),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "suspended", "reason": "overdue balance"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"


def test_cannot_reopen_closed_company(test_client, admin_override):
    with patch(
        "routes.corporate_accounts.get_corporate_account_by_id",
        AsyncMock(return_value=corporate_account_row("closed")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "active"},
        )
    assert resp.status_code == 409, resp.text
    assert "closed" in resp.json()["detail"].lower()


def test_status_change_404_when_company_missing(test_client, admin_override):
    with patch(
        "routes.corporate_accounts.get_corporate_account_by_id",
        AsyncMock(return_value=None),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/nonexistent/status",
            json={"status": "suspended"},
        )
    assert resp.status_code == 404, resp.text
