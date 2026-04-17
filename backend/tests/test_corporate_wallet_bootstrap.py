# backend/tests/test_corporate_wallet_bootstrap.py
"""Wallet is created (idempotently) when KYB is approved, not on rejection."""
from unittest.mock import AsyncMock, patch

from backend.tests._factories import corporate_account_row


def test_wallet_created_on_kyb_approval(test_client, admin_override):
    active_row = corporate_account_row("active", id="c1")
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=active_row),
    ) as m_kyb, patch(
        "routes.corporate_accounts.ensure_corporate_wallet",
        AsyncMock(return_value={"id": "w1", "company_id": "c1", "balance": 0}),
    ) as m_wallet, patch(
        "routes.corporate_accounts.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": ""}),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    m_kyb.assert_awaited_once()
    m_wallet.assert_awaited_once_with(company_id="c1")


def test_wallet_not_created_on_rejection(test_client, admin_override):
    rejected_row = corporate_account_row("suspended", id="c1")
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=rejected_row),
    ), patch(
        "routes.corporate_accounts.ensure_corporate_wallet",
        AsyncMock(),
    ) as m_wallet:
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False, "note": "doc unreadable"},
        )
    assert resp.status_code == 200, resp.text
    m_wallet.assert_not_awaited()
