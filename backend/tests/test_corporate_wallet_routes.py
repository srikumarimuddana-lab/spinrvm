# backend/tests/test_corporate_wallet_routes.py
"""Super-admin wallet endpoints: manual top-up + adjustment."""

from unittest.mock import AsyncMock, MagicMock, patch

from backend.tests._factories import corporate_account_row


def test_admin_manual_topup_creates_payment_intent(test_client, admin_override):
    active = corporate_account_row("active", id="c1", stripe_customer_id="cus_A")
    with (
        patch(
            "routes.corporate_wallet.get_corporate_account_by_id",
            AsyncMock(return_value=active),
        ),
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "balance": "0.00", "soft_negative_floor": -50}),
        ),
        patch(
            "routes.corporate_wallet.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
        patch(
            "stripe.PaymentIntent.create",
            return_value=MagicMock(id="pi_x", client_secret="pi_x_secret"),
        ),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_secret"] == "pi_x_secret"
    assert body["payment_intent_id"] == "pi_x"


def test_topup_rejects_below_minimum(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/corporate-accounts/c1/wallet/topup",
        json={"amount": 50},
    )
    assert resp.status_code == 422, resp.text


def test_wallet_router_mounted_at_api_v1(test_client, admin_override):
    """corporate_wallet_router must answer at its canonical /api/v1 path.

    The admin dashboard now calls /api/v1/admin/corporate-accounts/{id}/wallet/...
    to avoid the deprecated /api mount. A below-minimum amount exercises the route
    without external mocks: 422 proves the route is mounted (validation ran); a 404
    would mean the /api/v1 twin is missing.
    """
    resp = test_client.post(
        "/api/v1/admin/corporate-accounts/c1/wallet/topup",
        json={"amount": 50},
    )
    assert resp.status_code == 422, resp.text


def test_topup_rejects_above_maximum(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/corporate-accounts/c1/wallet/topup",
        json={"amount": 25000},
    )
    assert resp.status_code == 422, resp.text


def test_topup_rejects_if_company_not_active(test_client, admin_override):
    pending = corporate_account_row("pending_verification", id="c1")
    with patch(
        "routes.corporate_wallet.get_corporate_account_by_id",
        AsyncMock(return_value=pending),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
        )
    assert resp.status_code == 409, resp.text


def test_topup_rejects_if_no_stripe_customer(test_client, admin_override):
    no_cust = corporate_account_row("active", id="c1", stripe_customer_id=None)
    with patch(
        "routes.corporate_wallet.get_corporate_account_by_id",
        AsyncMock(return_value=no_cust),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
        )
    assert resp.status_code == 409, resp.text
    assert "stripe" in resp.json()["detail"].lower()


def test_topup_404_when_company_missing(test_client, admin_override):
    with patch(
        "routes.corporate_wallet.get_corporate_account_by_id",
        AsyncMock(return_value=None),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
        )
    assert resp.status_code == 404, resp.text


def test_adjust_applies_via_wallet_service(test_client, admin_override):
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "balance": "100.00", "soft_negative_floor": -50}),
        ),
        patch(
            "routes.corporate_wallet.apply_adjustment",
            AsyncMock(return_value={"transaction_id": "t1", "balance_after": "75.00"}),
        ) as m_adjust,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/adjust",
            json={"amount": -25, "notes": "manual correction"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["balance_after"] == "75.00"
    m_adjust.assert_awaited_once()
    kwargs = m_adjust.call_args.kwargs
    assert kwargs["wallet_id"] == "w1"
    assert kwargs["amount"] == -25
    assert kwargs["notes"] == "manual correction"
    assert kwargs["floor"] == -50


def test_adjust_404_when_no_wallet(test_client, admin_override):
    with patch(
        "routes.corporate_wallet.get_corporate_wallet_by_company",
        AsyncMock(return_value=None),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/adjust",
            json={"amount": 50, "notes": "deposit"},
        )
    assert resp.status_code == 404, resp.text


def test_adjust_requires_notes(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/corporate-accounts/c1/wallet/adjust",
        json={"amount": 50},
    )
    assert resp.status_code == 422, resp.text
