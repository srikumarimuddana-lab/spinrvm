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


def test_suspend_skips_cancellation_when_flag_disabled(test_client, admin_override):
    """Rollback path: flipping corporate_suspend_cancels_pre_pickup_rides off
    in app_settings must fully disable the new cancellation behavior without
    a redeploy."""
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
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value={"corporate_suspend_cancels_pre_pickup_rides": False}),
        ),
        patch(
            "routes.corporate_accounts.cancel_pre_pickup_rides_for_company",
            AsyncMock(),
        ) as mock_cancel,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "suspended", "reason": "overdue balance"},
        )
    assert resp.status_code == 200, resp.text
    mock_cancel.assert_not_awaited()


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


def test_close_skips_wallet_winddown_when_flag_disabled(test_client, admin_override):
    """Default-off rollout: corporate_close_refunds_wallet_balance defaults to
    False, so closing a company must not attempt any Stripe refund unless an
    admin explicitly flips the flag on in app_settings."""
    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=corporate_account_row("closed", stripe_customer_id="cus_1")),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value=None),
        ),
        patch(
            "routes.corporate_accounts.cancel_pre_pickup_rides_for_company",
            AsyncMock(return_value=0),
        ),
        patch(
            "routes.corporate_accounts.refund_wallet_balance_on_close",
            AsyncMock(),
        ) as mock_winddown,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "closed"},
        )
    assert resp.status_code == 200, resp.text
    mock_winddown.assert_not_awaited()
    assert resp.json()["status"] == "closed"


def test_close_refunds_wallet_when_flag_enabled(test_client, admin_override):
    winddown_result = {
        "attempted": True,
        "refunded_total": "42.00",
        "unrefundable_amount": "0.00",
        "stripe_refund_ids": ["re_1"],
        "stripe_error": None,
        "skipped_reason": None,
    }
    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=corporate_account_row("closed", stripe_customer_id="cus_1")),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value=None),
        ),
        patch(
            "routes.corporate_accounts.cancel_pre_pickup_rides_for_company",
            AsyncMock(return_value=0),
        ),
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(
                return_value={
                    "corporate_suspend_cancels_pre_pickup_rides": True,
                    "corporate_close_refunds_wallet_balance": True,
                }
            ),
        ),
        patch(
            "routes.corporate_accounts.refund_wallet_balance_on_close",
            AsyncMock(return_value=winddown_result),
        ) as mock_winddown,
        patch("routes.corporate_accounts.log_admin_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "closed"},
        )
    assert resp.status_code == 200, resp.text
    mock_winddown.assert_awaited_once_with(company_id="c1", stripe_customer_id="cus_1", actor_user_id="admin_1")
    assert mock_audit.await_args.kwargs["details"]["wallet_winddown"] == winddown_result


def test_suspend_never_triggers_wallet_winddown(test_client, admin_override):
    """Suspend is reversible — wind-down is close-only, per design."""
    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=corporate_account_row("active")),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=corporate_account_row("suspended", stripe_customer_id="cus_1")),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value=None),
        ),
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value={"corporate_close_refunds_wallet_balance": True}),
        ),
        patch(
            "routes.corporate_accounts.refund_wallet_balance_on_close",
            AsyncMock(),
        ) as mock_winddown,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "suspended"},
        )
    assert resp.status_code == 200, resp.text
    mock_winddown.assert_not_awaited()


def test_reactivation_flags_auto_topup_still_disabled_for_review(test_client, admin_override):
    """Corporate module lifecycle audit Finding 4: change_company_status only
    ever DISABLES auto-topup on suspend/close — there's no automatic restore
    on reactivation (deliberately, since auto-restoring a billing toggle
    without knowing prior intent is a real behavior change). Surface it in
    the audit log instead so an admin notices and re-enables manually if
    appropriate."""
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
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": False}),
        ),
        patch("routes.corporate_accounts.log_admin_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "active"},
        )
    assert resp.status_code == 200, resp.text
    assert mock_audit.await_args.kwargs["details"]["auto_topup_needs_review"] is True


def test_reactivation_no_flag_when_auto_topup_already_on(test_client, admin_override):
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
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": True}),
        ),
        patch("routes.corporate_accounts.log_admin_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "active"},
        )
    assert resp.status_code == 200, resp.text
    assert mock_audit.await_args.kwargs["details"]["auto_topup_needs_review"] is False
