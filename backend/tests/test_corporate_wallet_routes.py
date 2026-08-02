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


def test_manual_topup_writes_audit_log(test_client, admin_override):
    """Corporate module lifecycle audit Finding 9: manual_topup moves real
    money (creates a Stripe PaymentIntent) but never wrote to the admin
    audit trail — only the wallet ledger."""
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
        patch("routes.corporate_wallet.log_admin_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/topup",
            json={"amount": 500},
        )
    assert resp.status_code == 200, resp.text
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["details"]["payment_intent_id"] == "pi_x"


def test_manual_adjust_writes_audit_log(test_client, admin_override):
    """Same Finding 9 gap — manual_adjust also moved money silently
    (audit-log-wise)."""
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "balance": "100.00", "soft_negative_floor": -50}),
        ),
        patch(
            "routes.corporate_wallet.apply_adjustment",
            AsyncMock(return_value={"transaction_id": "t1", "balance_after": "75.00"}),
        ),
        patch("routes.corporate_wallet.log_admin_action", AsyncMock()) as mock_audit,
        patch("routes.corporate_wallet.get_rows", AsyncMock(return_value=[])),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/adjust",
            json={"amount": -25, "notes": "manual correction"},
        )
    assert resp.status_code == 200, resp.text
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["details"]["notes"] == "manual correction"


def test_adjust_blocked_when_daily_cap_exceeded(test_client, admin_override):
    """Corporate + admin portal review, "$100k/minute" finding:
    /wallet/adjust accepted up to $100,000 per call with no limit on
    repeated calls by the same admin. A daily cumulative cap now blocks a
    call that would push the day's total over the configured limit."""
    prior_rows = [
        {"details": {"amount": "40000.00"}},
        {"details": {"amount": "-5000.00"}},  # abs() summed — direction doesn't matter
    ]
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "balance": "100000.00", "soft_negative_floor": -50}),
        ),
        patch(
            "routes.corporate_wallet.get_app_settings",
            AsyncMock(return_value={"corporate_wallet_admin_adjust_daily_cap": 50000.0}),
        ),
        patch("routes.corporate_wallet.get_rows", AsyncMock(return_value=prior_rows)),
        patch("routes.corporate_wallet.apply_adjustment", AsyncMock()) as m_adjust,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/adjust",
            json={"amount": 6000, "notes": "another top-up"},
        )
    # 45000 already moved today + 6000 this call = 51000 > 50000 cap
    assert resp.status_code == 429, resp.text
    assert "cap" in resp.json()["detail"].lower()
    m_adjust.assert_not_awaited()


def test_adjust_allowed_under_configured_daily_cap(test_client, admin_override):
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "balance": "100000.00", "soft_negative_floor": -50}),
        ),
        patch(
            "routes.corporate_wallet.get_app_settings",
            AsyncMock(return_value={"corporate_wallet_admin_adjust_daily_cap": 50000.0}),
        ),
        patch(
            "routes.corporate_wallet.get_rows",
            AsyncMock(return_value=[{"details": {"amount": "40000.00"}}]),
        ),
        patch(
            "routes.corporate_wallet.apply_adjustment",
            AsyncMock(return_value={"transaction_id": "t1", "balance_after": "104000.00"}),
        ) as m_adjust,
        patch("routes.corporate_wallet.log_admin_action", AsyncMock()),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/adjust",
            json={"amount": 4000, "notes": "within cap"},
        )
    # 40000 + 4000 = 44000, under the 50000 cap
    assert resp.status_code == 200, resp.text
    m_adjust.assert_awaited_once()


def test_adjust_daily_cap_defaults_when_unconfigured(test_client, admin_override):
    """No app_settings value set -> falls back to the built-in default cap
    rather than silently allowing an unbounded amount."""
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "balance": "1000000.00", "soft_negative_floor": -50}),
        ),
        patch("routes.corporate_wallet.get_app_settings", AsyncMock(return_value={})),
        patch(
            "routes.corporate_wallet.get_rows",
            AsyncMock(return_value=[{"details": {"amount": "49999.00"}}]),
        ),
        patch("routes.corporate_wallet.apply_adjustment", AsyncMock()) as m_adjust,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/wallet/adjust",
            # Max per-call amount (100000) pushed on top of 49999 already moved
            # today blows past the $50,000 default cap even before this call.
            json={"amount": 100000, "notes": "large top-up"},
        )
    assert resp.status_code == 429, resp.text
    m_adjust.assert_not_awaited()
