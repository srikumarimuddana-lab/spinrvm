"""Integration tests for the in-app wallet endpoints.

Routes under test (backend/routes/wallet.py):
  GET  /api/v1/wallet               — get balance
  POST /api/v1/wallet/top-up        — create a Stripe PaymentIntent for top-up
  POST /api/v1/wallet/pay           — pay for a ride
  GET  /api/v1/wallet/transactions  — transaction history

POST /api/v1/wallet/transfer (wallet-to-wallet transfer) was removed
entirely — no route exists in routes/wallet.py — and is not covered here.
"""

import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_USER = {"id": "user_123", "phone": "+1234567890", "role": "rider", "is_driver": False}
SAMPLE_WALLET = {
    "id": "wallet_1",
    "user_id": "user_123",
    "balance": 50.0,
    "currency": "CAD",
    "is_active": True,
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}

SAMPLE_RIDE = {
    "id": "ride_123",
    "rider_id": "user_123",
    "status": "completed",
    "total_fare": 15.0,
}


def make_mock_db():
    """Build a mock db using the flat Supabase-style interface wallet routes use.

    wallet.py calls db.find_one(table, filter), db.insert_one(table, data),
    db.update_one(table, filter, update), and db.get_rows(table, filter, ...).
    """
    mock = MagicMock()
    mock.find_one = AsyncMock(return_value=None)
    mock.insert_one = AsyncMock(return_value=None)
    mock.update_one = AsyncMock(return_value=None)
    mock.get_rows = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def client():
    import dependencies  # same module the routes use (routes use relative '..dependencies')
    from backend.server import app  # ensures server.py sys.path setup runs first

    app.dependency_overrides[dependencies.get_current_user] = lambda: SAMPLE_USER
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestGetWallet:
    """GET /api/v1/wallet"""

    def test_returns_existing_balance(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_WALLET)

        with patch("routes.wallet.db", mock_db):
            resp = client.get("/api/v1/wallet")

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == "50.00"
        assert data["currency"] == "CAD"
        assert data["is_active"] is True

    def test_auto_creates_wallet_for_new_user(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=None)

        with patch("routes.wallet.db", mock_db):
            resp = client.get("/api/v1/wallet")

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == "0.00"
        assert data["currency"] == "CAD"

    def test_unauthenticated_request_rejected(self):
        from fastapi.testclient import TestClient

        from backend.server import app

        with TestClient(app) as c:
            resp = c.get("/api/v1/wallet")
        assert resp.status_code == 401


class TestTopUp:
    """POST /api/v1/wallet/top-up

    top_up_wallet no longer credits the balance synchronously -- it creates
    a Stripe PaymentIntent + EphemeralKey and returns the PaymentSheet
    secrets so the client can present Stripe's payment UI. The wallet is
    only credited once Stripe confirms the payment via the
    payment_intent.succeeded webhook (scope=wallet_topup), which is a
    separate code path (routes/webhooks.py) not exercised here.
    """

    _USER = {"id": "user_123", "email": "rider@example.com", "first_name": "Test", "last_name": "Rider"}
    _SETTINGS = {"stripe_secret_key": "sk_test_x", "stripe_publishable_key": "pk_test_x"}

    def test_top_up_creates_payment_intent(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_WALLET)

        mock_intent = MagicMock()
        mock_intent.client_secret = "pi_secret_xxx"
        mock_ephemeral = MagicMock()
        mock_ephemeral.secret = "ek_secret_yyy"

        with (
            patch("routes.wallet.db", mock_db),
            patch("routes.wallet.get_app_settings", AsyncMock(return_value=self._SETTINGS)),
            patch(
                "routes.wallet.db_supabase.get_user_by_id",
                AsyncMock(return_value={**self._USER, "stripe_customer_id": "cus_existing"}),
            ),
            patch("routes.wallet.stripe.EphemeralKey.create", return_value=mock_ephemeral),
            patch("routes.wallet.stripe.PaymentIntent.create", return_value=mock_intent) as mock_pi_create,
        ):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 25.0})

        assert resp.status_code == 200
        data = resp.json()
        assert data["paymentIntent"] == "pi_secret_xxx"
        assert data["ephemeralKey"] == "ek_secret_yyy"
        assert data["customer"] == "cus_existing"
        assert data["publishableKey"] == "pk_test_x"
        # $25.00 -> 2500 cents, tagged for the wallet-topup webhook path.
        assert mock_pi_create.call_args.kwargs["amount"] == 2500
        assert mock_pi_create.call_args.kwargs["metadata"]["scope"] == "wallet_topup"

    def test_top_up_creates_stripe_customer_when_missing(self, client):
        """A user with no stripe_customer_id on file gets one created first."""
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_WALLET)

        mock_customer = MagicMock()
        mock_customer.id = "cus_new"
        mock_intent = MagicMock()
        mock_intent.client_secret = "pi_secret_xxx"
        mock_ephemeral = MagicMock()
        mock_ephemeral.secret = "ek_secret_yyy"

        with (
            patch("routes.wallet.db", mock_db),
            patch("routes.wallet.get_app_settings", AsyncMock(return_value=self._SETTINGS)),
            patch(
                "routes.wallet.db_supabase.get_user_by_id",
                AsyncMock(
                    side_effect=[
                        {**self._USER, "stripe_customer_id": None},
                        {**self._USER, "stripe_customer_id": "cus_new"},
                    ]
                ),
            ),
            patch("routes.wallet.db_supabase.update_one", AsyncMock()),
            patch("routes.wallet.stripe.Customer.create", return_value=mock_customer) as mock_cus_create,
            patch("routes.wallet.stripe.EphemeralKey.create", return_value=mock_ephemeral),
            patch("routes.wallet.stripe.PaymentIntent.create", return_value=mock_intent),
        ):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 25.0})

        assert resp.status_code == 200
        mock_cus_create.assert_called_once()
        assert resp.json()["customer"] == "cus_new"

    def test_top_up_stripe_error_returns_502(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_WALLET)

        with (
            patch("routes.wallet.db", mock_db),
            patch("routes.wallet.get_app_settings", AsyncMock(return_value=self._SETTINGS)),
            patch(
                "routes.wallet.db_supabase.get_user_by_id",
                AsyncMock(return_value={**self._USER, "stripe_customer_id": "cus_existing"}),
            ),
            patch(
                "routes.wallet.stripe.EphemeralKey.create",
                side_effect=stripe.error.StripeError("boom"),
            ),
        ):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 25.0})

        assert resp.status_code == 502

    def test_top_up_no_stripe_key_returns_503(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_WALLET)

        with (
            patch("routes.wallet.db", mock_db),
            patch(
                "routes.wallet.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "", "stripe_publishable_key": ""}),
            ),
        ):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 25.0})

        assert resp.status_code == 503

    def test_top_up_suspended_wallet_returns_403(self, client):
        suspended = {**SAMPLE_WALLET, "is_active": False}
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=suspended)

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 10.0})

        assert resp.status_code == 403
        assert "suspended" in resp.json()["detail"].lower()

    def test_top_up_exceeds_maximum_returns_422(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_WALLET)

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 600.0})

        assert resp.status_code == 422

    def test_top_up_zero_amount_rejected(self, client):
        mock_db = make_mock_db()

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 0.0})

        assert resp.status_code == 422


class TestWalletPay:
    """POST /api/v1/wallet/pay"""

    def test_pay_for_ride_deducts_balance(self, client):
        mock_db = make_mock_db()
        # find_one is called twice: first for wallet, then for ride
        mock_db.find_one = AsyncMock(side_effect=[SAMPLE_WALLET, SAMPLE_RIDE])

        with (
            patch("routes.wallet.db", mock_db),
            patch("routes.wallet.wallet_pay_for_ride", AsyncMock(return_value=Decimal("35.00"))),
        ):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": "ride_123", "amount": 15.0})

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == "35.00"  # 50 - 15
        assert "transaction_id" in data

    def test_insufficient_balance_returns_400(self, client):
        low_balance = {**SAMPLE_WALLET, "balance": 5.0}
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(side_effect=[low_balance, SAMPLE_RIDE])

        with (
            patch("routes.wallet.db", mock_db),
            patch(
                "routes.wallet.wallet_pay_for_ride",
                AsyncMock(side_effect=ValueError("insufficient_funds")),
            ),
        ):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": "ride_123", "amount": 15.0})

        assert resp.status_code == 400
        assert "insufficient" in resp.json()["detail"].lower()

    def test_pay_from_suspended_wallet_returns_403(self, client):
        suspended = {**SAMPLE_WALLET, "is_active": False}
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=suspended)

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": "ride_123", "amount": 10.0})

        assert resp.status_code == 403


class TestGetTransactions:
    """GET /api/v1/wallet/transactions"""

    def test_empty_transaction_history(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_WALLET)
        mock_db.get_rows = AsyncMock(return_value=[])

        with patch("routes.wallet.db", mock_db):
            resp = client.get("/api/v1/wallet/transactions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["transactions"] == []
        assert data["total"] == 0

    def test_transaction_history_with_items(self, client):
        txns = [
            {
                "id": "t1",
                "type": "top_up",
                "amount": 50.0,
                "balance_after": 50.0,
                "description": "Wallet top-up $50.00",
                "reference_id": None,
                "created_at": "2026-01-01T10:00:00",
            }
        ]
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_WALLET)
        mock_db.get_rows = AsyncMock(return_value=txns)

        with patch("routes.wallet.db", mock_db):
            resp = client.get("/api/v1/wallet/transactions")

        data = resp.json()
        assert data["total"] == 1
        assert data["transactions"][0]["type"] == "top_up"
