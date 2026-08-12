# backend/tests/test_corporate_stripe_customer.py
"""Stripe customer is created and persisted on KYB approval."""

from unittest.mock import AsyncMock, MagicMock, patch

from backend.tests._factories import corporate_account_row


def test_stripe_customer_created_on_kyb_approval(test_client, admin_override):
    active_row = corporate_account_row(
        "active",
        id="c1",
        legal_name="Acme Inc",
        billing_email="billing@acme.com",
        stripe_customer_id=None,
    )
    fake_cust = MagicMock(id="cus_ABC")
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=active_row),
        ),
        patch(
            "routes.corporate_accounts.ensure_corporate_wallet",
            AsyncMock(return_value={"id": "w1"}),
        ),
        patch(
            "services.corporate_stripe_identity.db_supabase.update_one",
            AsyncMock(),
        ) as m_update,
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_123"}),
        ),
        patch("stripe.Customer.create", return_value=fake_cust),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    # The customer create + persist moved into services/corporate_stripe_identity
    # (shared with assign_subscription and the drift-repair paths), so the write
    # is now a generic update_one that also stamps the Stripe mode — mode
    # tracking is what lets a later test→live key rotation be detected.
    m_update.assert_awaited_once()
    table, filters, update = m_update.await_args.args
    assert (table, filters) == ("corporate_accounts", {"id": "c1"})
    assert update["stripe_customer_id"] == "cus_ABC"
    # MagicMock has no real `livemode` bool, so the stamp falls back to the
    # key's mode — sk_test_123 → "test".
    assert update["stripe_customer_id_mode"] == "test"


def test_stripe_customer_skipped_when_already_set(test_client, admin_override):
    already_has = corporate_account_row("active", id="c1", stripe_customer_id="cus_EXISTING")
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=already_has),
        ),
        patch(
            "routes.corporate_accounts.ensure_corporate_wallet",
            AsyncMock(return_value={"id": "w1"}),
        ),
        patch(
            "services.corporate_stripe_identity.db_supabase.update_one",
            AsyncMock(),
        ) as m_update,
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_123"}),
        ),
        patch("stripe.Customer.create") as m_create,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    m_create.assert_not_called()
    m_update.assert_not_awaited()


def test_stripe_customer_skipped_when_no_secret(test_client, admin_override):
    active_row = corporate_account_row("active", id="c1", stripe_customer_id=None)
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=active_row),
        ),
        patch(
            "routes.corporate_accounts.ensure_corporate_wallet",
            AsyncMock(return_value={"id": "w1"}),
        ),
        patch(
            "services.corporate_stripe_identity.db_supabase.update_one",
            AsyncMock(),
        ) as m_update,
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": ""}),
        ),
        patch("stripe.Customer.create") as m_create,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    m_create.assert_not_called()
    m_update.assert_not_awaited()


def test_stripe_customer_creation_failure_is_partial_success(test_client, admin_override):
    """Corporate + admin portal review, gap #40: record_kyb_decision already
    committed status='active' before this step runs, so a Stripe API
    failure must not raise — the response surfaces
    stripe_customer_creation_error=True instead, matching
    create_corporate_account's owner_bootstrap_error partial-success shape."""
    active_row = corporate_account_row("active", id="c1", stripe_customer_id=None)
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=active_row),
        ),
        patch(
            "routes.corporate_accounts.ensure_corporate_wallet",
            AsyncMock(return_value={"id": "w1"}),
        ),
        patch(
            "services.corporate_stripe_identity.db_supabase.update_one",
            AsyncMock(),
        ) as m_update,
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_123"}),
        ),
        patch("stripe.Customer.create", side_effect=RuntimeError("stripe unreachable")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "active"  # status change already committed
    assert data["wallet_provisioning_error"] is False
    assert data["stripe_customer_creation_error"] is True
    m_update.assert_not_awaited()
