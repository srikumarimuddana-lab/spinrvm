"""Integration tests for the in-app wallet endpoints.

Routes under test (backend/routes/wallet.py):
  GET  /api/v1/wallet               — get balance
  POST /api/v1/wallet/top-up        — add funds
  POST /api/v1/wallet/pay           — pay for a ride
  GET  /api/v1/wallet/transactions  — transaction history
  POST /api/v1/wallet/transfer      — transfer to another user
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_USER = {"id": "user_123", "phone": "+1234567890", "role": "rider", "is_driver": False}
RECIPIENT_USER = {"id": "user_456", "phone": "+9876543210", "role": "rider"}

SAMPLE_WALLET = {
    "id": "wallet_1",
    "user_id": "user_123",
    "balance": 50.0,
    "currency": "CAD",
    "is_active": True,
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}

RECIPIENT_WALLET = {
    "id": "wallet_2",
    "user_id": "user_456",
    "balance": 10.0,
    "currency": "CAD",
    "is_active": True,
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}


def make_mock_db():
    mock = MagicMock()
    mock.get_rows = AsyncMock(return_value=[])
    for col in ("wallets", "wallet_transactions", "users", "rides"):
        col_mock = MagicMock()
        col_mock.find_one = AsyncMock(return_value=None)
        col_mock.insert_one = AsyncMock(return_value=None)
        col_mock.update_one = AsyncMock(return_value=None)
        setattr(mock, col, col_mock)
    return mock


@pytest.fixture
def client():
    from backend.server import app  # ensures server.py sys.path setup runs first
    import dependencies  # same module the routes use (routes use relative '..dependencies')

    app.dependency_overrides[dependencies.get_current_user] = lambda: SAMPLE_USER
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestGetWallet:
    """GET /api/v1/wallet"""

    def test_returns_existing_balance(self, client):
        mock_db = make_mock_db()
        mock_db.wallets.find_one = AsyncMock(return_value=SAMPLE_WALLET)

        with patch("routes.wallet.db", mock_db):
            resp = client.get("/api/v1/wallet")

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 50.0
        assert data["currency"] == "CAD"
        assert data["is_active"] is True

    def test_auto_creates_wallet_for_new_user(self, client):
        mock_db = make_mock_db()
        mock_db.wallets.find_one = AsyncMock(return_value=None)

        with patch("routes.wallet.db", mock_db):
            resp = client.get("/api/v1/wallet")

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 0.0
        assert data["currency"] == "CAD"

    def test_unauthenticated_request_rejected(self):
        from fastapi.testclient import TestClient
        from backend.server import app

        with TestClient(app) as c:
            resp = c.get("/api/v1/wallet")
        assert resp.status_code == 401


class TestTopUp:
    """POST /api/v1/wallet/top-up"""

    def test_top_up_increases_balance(self, client):
        mock_db = make_mock_db()
        mock_db.wallets.find_one = AsyncMock(return_value=SAMPLE_WALLET)

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 25.0})

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 75.0  # 50 + 25
        assert "transaction_id" in data

    def test_top_up_suspended_wallet_returns_403(self, client):
        suspended = {**SAMPLE_WALLET, "is_active": False}
        mock_db = make_mock_db()
        mock_db.wallets.find_one = AsyncMock(return_value=suspended)

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/top-up", json={"amount": 10.0})

        assert resp.status_code == 403
        assert "suspended" in resp.json()["detail"].lower()

    def test_top_up_exceeds_maximum_returns_422(self, client):
        mock_db = make_mock_db()
        mock_db.wallets.find_one = AsyncMock(return_value=SAMPLE_WALLET)

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
        mock_db.wallets.find_one = AsyncMock(return_value=SAMPLE_WALLET)
        mock_db.rides.update_one = AsyncMock(return_value=None)

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": "ride_123", "amount": 15.0})

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 35.0  # 50 - 15
        assert "transaction_id" in data

    def test_insufficient_balance_returns_400(self, client):
        low_balance = {**SAMPLE_WALLET, "balance": 5.0}
        mock_db = make_mock_db()
        mock_db.wallets.find_one = AsyncMock(return_value=low_balance)

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": "ride_123", "amount": 15.0})

        assert resp.status_code == 400
        assert "insufficient" in resp.json()["detail"].lower()

    def test_pay_from_suspended_wallet_returns_403(self, client):
        suspended = {**SAMPLE_WALLET, "is_active": False}
        mock_db = make_mock_db()
        mock_db.wallets.find_one = AsyncMock(return_value=suspended)

        with patch("routes.wallet.db", mock_db):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": "ride_123", "amount": 10.0})

        assert resp.status_code == 403


class TestGetTransactions:
    """GET /api/v1/wallet/transactions"""

    def test_empty_transaction_history(self, client):
        mock_db = make_mock_db()
        mock_db.wallets.find_one = AsyncMock(return_value=SAMPLE_WALLET)
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
        mock_db.wallets.find_one = AsyncMock(return_value=SAMPLE_WALLET)
        mock_db.get_rows = AsyncMock(return_value=txns)

        with patch("routes.wallet.db", mock_db):
            resp = client.get("/api/v1/wallet/transactions")

        data = resp.json()
        assert data["total"] == 1
        assert data["transactions"][0]["type"] == "top_up"


class TestTransfer:
    """POST /api/v1/wallet/transfer"""

    def test_transfer_to_valid_recipient(self, client):
        mock_db = make_mock_db()
        mock_db.users.find_one = AsyncMock(return_value=RECIPIENT_USER)
        mock_db.wallets.find_one = AsyncMock(
            side_effect=[SAMPLE_WALLET, RECIPIENT_WALLET]
        )

        with patch("routes.wallet.db", mock_db):
            resp = client.post(
                "/api/v1/wallet/transfer",
                json={"recipient_phone": "+9876543210", "amount": 10.0},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 40.0  # 50 - 10
        assert data["success"] is True

    def test_transfer_to_self_returns_400(self, client):
        mock_db = make_mock_db()
        # Recipient has same id as sender
        mock_db.users.find_one = AsyncMock(return_value=SAMPLE_USER)

        with patch("routes.wallet.db", mock_db):
            resp = client.post(
                "/api/v1/wallet/transfer",
                json={"recipient_phone": "+1234567890", "amount": 10.0},
            )

        assert resp.status_code == 400
        assert "yourself" in resp.json()["detail"].lower()

    def test_transfer_recipient_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.users.find_one = AsyncMock(return_value=None)

        with patch("routes.wallet.db", mock_db):
            resp = client.post(
                "/api/v1/wallet/transfer",
                json={"recipient_phone": "+0000000000", "amount": 10.0},
            )

        assert resp.status_code == 404

    def test_transfer_insufficient_balance_returns_400(self, client):
        empty_wallet = {**SAMPLE_WALLET, "balance": 2.0}
        mock_db = make_mock_db()
        mock_db.users.find_one = AsyncMock(return_value=RECIPIENT_USER)
        mock_db.wallets.find_one = AsyncMock(
            side_effect=[empty_wallet, RECIPIENT_WALLET]
        )

        with patch("routes.wallet.db", mock_db):
            resp = client.post(
                "/api/v1/wallet/transfer",
                json={"recipient_phone": "+9876543210", "amount": 10.0},
            )

        assert resp.status_code == 400
        assert "insufficient" in resp.json()["detail"].lower()

    def test_transfer_exceeds_limit_returns_422(self, client):
        mock_db = make_mock_db()

        with patch("routes.wallet.db", mock_db):
            resp = client.post(
                "/api/v1/wallet/transfer",
                json={"recipient_phone": "+9876543210", "amount": 300.0},
            )

        assert resp.status_code == 422
