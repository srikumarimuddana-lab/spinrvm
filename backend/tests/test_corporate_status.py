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
        patch(
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value=None),  # no wallet → skip freeze step
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


def test_suspend_cancels_pre_pickup_rides_and_logs_count(test_client, admin_override):
    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=corporate_account_row("suspended")),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value=None),
        ),
        patch(
            "routes.corporate_accounts.cancel_pre_pickup_rides_for_company",
            AsyncMock(return_value=2),
        ) as mock_cancel,
        patch("routes.corporate_accounts.log_admin_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "suspended", "reason": "overdue balance"},
        )
    assert resp.status_code == 200, resp.text
    mock_cancel.assert_awaited_once_with("c1")
    assert mock_audit.await_args.kwargs["details"]["pre_pickup_rides_cancelled"] == 2


def test_reactivating_company_does_not_cancel_rides(test_client, admin_override):
    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("suspended")),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "routes.corporate_accounts.cancel_pre_pickup_rides_for_company",
            AsyncMock(),
        ) as mock_cancel,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "active"},
        )
    assert resp.status_code == 200, resp.text
    mock_cancel.assert_not_awaited()


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
