"""E2E: approve company → Stripe customer created → wallet provisioned →
manual top-up intent → webhook credits wallet → suspend freezes auto-topup.

One sync TestClient flight that walks the full lifecycle. Each step patches
only what that step's handler touches, because FastAPI resolves dependencies
and imports per-request.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from backend.tests._factories import corporate_account_row


def test_wallet_lifecycle(test_client, admin_override):
    # --- 1) KYB approve → Stripe customer + wallet provisioned ─────────
    approved_row = corporate_account_row(
        "active",
        id="c1",
        legal_name="Acme Inc",
        billing_email="billing@acme.com",
        stripe_customer_id=None,
    )
    with (
        patch(
            "db_supabase.record_kyb_decision",
            AsyncMock(return_value=approved_row),
        ),
        patch(
            "routes.corporate_accounts.ensure_corporate_wallet",
            AsyncMock(return_value={"id": "w1", "company_id": "c1", "balance": 0}),
        ),
        patch(
            "services.corporate_stripe_identity.db_supabase.update_one",
            AsyncMock(),
        ) as m_set_cust,
        patch(
            "routes.corporate_accounts.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
        patch("stripe.Customer.create", return_value=MagicMock(id="cus_A")),
    ):
        r = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
        assert r.status_code == 200, r.text
        m_set_cust.assert_awaited_once()
        # Persisted via the shared corporate identity service, which writes the
        # id and its Stripe mode together (positional update_one args).
        assert m_set_cust.await_args.args[2]["stripe_customer_id"] == "cus_A"

    # --- 2) Manual top-up intent ───────────────────────────────────────
    with (
        patch(
            "routes.corporate_wallet.get_corporate_account_by_id",
            AsyncMock(
                return_value={
                    "id": "c1",
                    "status": "active",
                    "stripe_customer_id": "cus_A",
                }
            ),
        ),
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1"}),
        ),
        patch(
            "routes.corporate_wallet.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
        patch(
            "stripe.PaymentIntent.create",
            return_value=MagicMock(id="pi_1", client_secret="cs_1"),
        ),
    ):
        r = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
        )
        assert r.status_code == 200, r.text
        assert r.json()["payment_intent_id"] == "pi_1"

    # --- 3) Webhook fires and credits wallet ───────────────────────────
    event = {
        "id": "evt_1",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_1",
                "amount_received": 50000,
                "currency": "cad",
                "metadata": {
                    "scope": "corporate_topup",
                    "company_id": "c1",
                    "wallet_id": "w1",
                    "initiated_by": "admin_1",
                },
            }
        },
    }
    with (
        patch(
            "routes.webhooks.get_app_settings",
            AsyncMock(
                return_value={
                    "stripe_webhook_secret": "whsec_test",
                    "stripe_secret_key": "sk_test",
                }
            ),
        ),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch(
            "services.corporate_wallet_service.apply_topup",
            AsyncMock(return_value={"transaction_id": "t1", "balance_after": "500.00"}),
        ) as m_topup,
    ):
        r = test_client.post(
            "/api/v1/webhooks/stripe",
            content=json.dumps(event).encode(),
            headers={"stripe-signature": "t=1,v1=dummy"},
        )
        assert r.status_code == 200, r.text
        assert m_topup.await_count == 1
        assert m_topup.await_args.kwargs["amount"] == 500
        assert m_topup.await_args.kwargs["wallet_id"] == "w1"

    # --- 4) Suspension disables auto-topup ─────────────────────────────
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
        ) as m_cfg,
    ):
        r = test_client.post(
            "/api/admin/corporate-accounts/c1/status",
            json={"status": "suspended", "reason": "test"},
        )
        assert r.status_code == 200, r.text
        m_cfg.assert_awaited_once()
        assert m_cfg.call_args.kwargs["patch"] == {"auto_topup_enabled": False}
