"""Freeze master wallet when company is suspended or closed."""

from unittest.mock import AsyncMock, patch

from backend.tests._factories import corporate_account_row


def test_suspend_disables_autotopup(test_client, admin_override):
    suspended_row = corporate_account_row("suspended", id="c1")
    active_row = corporate_account_row("active", id="c1")

    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=active_row),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=suspended_row),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": True}),
        ),
        patch(
            "routes.corporate_accounts.update_corporate_wallet_config",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": False}),
        ) as m_upd,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "suspended"},
        )

    assert resp.status_code == 200, resp.text
    m_upd.assert_awaited_once()
    kwargs = m_upd.call_args.kwargs
    assert kwargs["wallet_id"] == "w1"
    assert kwargs["patch"] == {"auto_topup_enabled": False}


def test_close_disables_autotopup(test_client, admin_override):
    closed_row = corporate_account_row("closed", id="c1")
    active_row = corporate_account_row("active", id="c1")

    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=active_row),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=closed_row),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": True}),
        ),
        patch(
            "routes.corporate_accounts.update_corporate_wallet_config",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": False}),
        ) as m_upd,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "closed"},
        )

    assert resp.status_code == 200, resp.text
    m_upd.assert_awaited_once()


def test_reactivate_does_not_touch_wallet(test_client, admin_override):
    suspended_row = corporate_account_row("suspended", id="c1")
    active_row = corporate_account_row("active", id="c1")

    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=suspended_row),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=active_row),
        ),
        patch(
            "routes.corporate_accounts.update_corporate_wallet_config",
            AsyncMock(),
        ) as m_upd,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "active"},
        )

    assert resp.status_code == 200, resp.text
    m_upd.assert_not_awaited()


def test_suspend_skips_wallet_update_when_autotopup_already_off(test_client, admin_override):
    suspended_row = corporate_account_row("suspended", id="c1")
    active_row = corporate_account_row("active", id="c1")

    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=active_row),
        ),
        patch(
            "db_supabase.update_corporate_account_status",
            AsyncMock(return_value=suspended_row),
        ),
        patch(
            "routes.corporate_accounts.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": False}),
        ),
        patch(
            "routes.corporate_accounts.update_corporate_wallet_config",
            AsyncMock(),
        ) as m_upd,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "suspended"},
        )

    assert resp.status_code == 200, resp.text
    m_upd.assert_not_awaited()


def test_topup_refused_when_company_suspended(test_client, admin_override):
    suspended = corporate_account_row("suspended", id="c1", stripe_customer_id="cus_X")
    with patch(
        "routes.corporate_wallet.get_corporate_account_by_id",
        AsyncMock(return_value=suspended),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
        )
    assert resp.status_code == 409, resp.text
