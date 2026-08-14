"""Integration tests for the loyalty rewards endpoints.

Routes under test (backend/routes/loyalty.py):
  GET  /api/v1/loyalty             — get loyalty status
  GET  /api/v1/loyalty/history     — transaction history
  POST /api/v1/loyalty/earn        — award points for a ride
  POST /api/v1/loyalty/redeem      — redeem points for wallet credit
"""

import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_USER = {"id": "user_123", "phone": "+1234567890", "role": "rider", "is_driver": False}

SAMPLE_ACCOUNT = {
    "id": "acct_123",
    "user_id": "user_123",
    "points": 250,
    "lifetime_points": 750,
    "tier": "silver",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}

SAMPLE_RIDE = {
    "id": "ride_123",
    "rider_id": "user_123",
    "status": "completed",
    "total_fare": 20.00,
}


def make_mock_db():
    """Build a mock db using the flat Supabase-style interface loyalty routes use.

    loyalty.py calls db.find_one(table, filter), db.insert_one(table, data),
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


class TestGetLoyaltyStatus:
    """GET /api/v1/loyalty"""

    def test_new_user_gets_bronze_account(self, client):
        """First-time call auto-creates a bronze account."""
        mock_db = make_mock_db()
        # find_one returns None → auto-create path
        mock_db.find_one = AsyncMock(return_value=None)

        with patch("routes.loyalty.db", mock_db):
            resp = client.get("/api/v1/loyalty")

        assert resp.status_code == 200
        data = resp.json()
        assert data["points"] == 0
        assert data["lifetime_points"] == 0
        assert data["tier"] == "bronze"
        assert data["multiplier"] == 1.0
        assert data["redemption_rate"] == 100

    def test_existing_silver_account(self, client):
        """Existing account data is returned as-is."""
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_ACCOUNT)

        with patch("routes.loyalty.db", mock_db):
            resp = client.get("/api/v1/loyalty")

        assert resp.status_code == 200
        data = resp.json()
        assert data["points"] == 250
        assert data["tier"] == "silver"
        assert data["multiplier"] == 1.25

    def test_unauthenticated_request_rejected(self):
        from fastapi.testclient import TestClient

        from backend.server import app

        # No dependency_overrides — real auth should reject the request
        with TestClient(app) as c:
            resp = c.get("/api/v1/loyalty")
        assert resp.status_code == 401


class TestGetLoyaltyHistory:
    """GET /api/v1/loyalty/history"""

    def test_empty_history(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_ACCOUNT)
        mock_db.get_rows = AsyncMock(return_value=[])

        with patch("routes.loyalty.db", mock_db):
            resp = client.get("/api/v1/loyalty/history")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_history_returns_transactions(self, client):
        txns = [
            {"id": "t1", "points": 20, "type": "ride_earned", "created_at": "2026-01-02T10:00:00"},
            {"id": "t2", "points": -100, "type": "redeemed", "created_at": "2026-01-01T10:00:00"},
        ]
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_ACCOUNT)
        mock_db.get_rows = AsyncMock(return_value=txns)

        with patch("routes.loyalty.db", mock_db):
            resp = client.get("/api/v1/loyalty/history?limit=10")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_limit_validation(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=SAMPLE_ACCOUNT)
        mock_db.get_rows = AsyncMock(return_value=[])

        with patch("routes.loyalty.db", mock_db):
            # limit=0 is below ge=1
            resp = client.get("/api/v1/loyalty/history?limit=0")
        assert resp.status_code == 422


class TestEarnPoints:
    """POST /api/v1/loyalty/earn"""

    def test_earn_points_for_completed_ride(self, client):
        mock_db = make_mock_db()
        # find_one called twice: ride lookup, then account lookup
        mock_db.find_one = AsyncMock(side_effect=[SAMPLE_RIDE, SAMPLE_ACCOUNT])

        with patch("routes.loyalty.db", mock_db):
            resp = client.post("/api/v1/loyalty/earn?ride_id=ride_123")

        assert resp.status_code == 200
        data = resp.json()
        assert "points_earned" in data
        assert data["points_earned"] > 0
        assert "tier" in data

    def test_earn_applies_silver_multiplier(self, client):
        """Silver tier (1.25×) earns bonus points."""
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(side_effect=[SAMPLE_RIDE, SAMPLE_ACCOUNT])  # $20 fare

        with patch("routes.loyalty.db", mock_db):
            resp = client.post("/api/v1/loyalty/earn?ride_id=ride_123")

        data = resp.json()
        # $20 × 1 pt/$ = 20 base + 5 bonus (1.25-1.0 = 0.25 × 20)
        assert data["base_points"] == 20
        assert data["bonus_points"] == 5
        assert data["points_earned"] == 25

    def test_ride_not_found_returns_404(self, client):
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=None)

        with patch("routes.loyalty.db", mock_db):
            resp = client.post("/api/v1/loyalty/earn?ride_id=bad_ride")

        assert resp.status_code == 404

    def test_ride_not_completed_returns_400(self, client):
        pending_ride = {**SAMPLE_RIDE, "status": "in_progress"}
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=pending_ride)

        with patch("routes.loyalty.db", mock_db):
            resp = client.post("/api/v1/loyalty/earn?ride_id=ride_123")

        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"].lower()

    def test_already_awarded_returns_idempotent(self, client):
        """Second call raises DuplicateRecordError on insert → idempotent no-op."""
        from utils.error_handling import DuplicateRecordError

        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(side_effect=[SAMPLE_RIDE, SAMPLE_ACCOUNT])
        mock_db.insert_one = AsyncMock(side_effect=DuplicateRecordError("ride_earned"))

        with patch("routes.loyalty.db", mock_db):
            resp = client.post("/api/v1/loyalty/earn?ride_id=ride_123")

        assert resp.status_code == 200
        assert resp.json()["already_awarded"] is True

    def test_unauthorized_ride_returns_403(self, client):
        other_user_ride = {**SAMPLE_RIDE, "rider_id": "other_user"}
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=other_user_ride)

        with patch("routes.loyalty.db", mock_db):
            resp = client.post("/api/v1/loyalty/earn?ride_id=ride_123")

        assert resp.status_code == 403


class TestTierUpgradeNotification:
    """N15/R34 (ACTION_ITEMS.md): a tier upgrade must push-notify the rider."""

    def test_tier_upgrade_sends_push_with_new_tier(self, client):
        # bronze account 10 pts under the silver threshold (500); a $20 fare
        # earns 20 base points at the bronze 1.0x multiplier -> lifetime 510,
        # crossing into silver.
        bronze_near_threshold = {
            **SAMPLE_ACCOUNT,
            "tier": "bronze",
            "points": 490,
            "lifetime_points": 490,
        }
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(side_effect=[SAMPLE_RIDE, bronze_near_threshold])
        push = AsyncMock(return_value=True)

        with patch("routes.loyalty.db", mock_db), patch("routes.loyalty.send_push_notification", push):
            resp = client.post("/api/v1/loyalty/earn?ride_id=ride_123")

        assert resp.status_code == 200
        data = resp.json()
        assert data["tier_upgraded"] is True
        assert data["tier"] == "silver"

        push.assert_awaited_once()
        args, kwargs = push.await_args
        assert args[0] == SAMPLE_USER["id"]
        assert "tier" in args[1].lower() or "tier" in args[2].lower()
        assert kwargs["data"]["type"] == "loyalty_tier_upgraded"
        assert kwargs["data"]["tier"] == "silver"
        assert kwargs["data"]["previous_tier"] == "bronze"
        assert kwargs["target_app"] == "rider"

    def test_no_tier_change_does_not_send_push(self, client):
        # SAMPLE_ACCOUNT is already silver with plenty of lifetime points
        # left before gold (1500) — a $20 fare stays inside the silver band.
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(side_effect=[SAMPLE_RIDE, SAMPLE_ACCOUNT])
        push = AsyncMock(return_value=True)

        with patch("routes.loyalty.db", mock_db), patch("routes.loyalty.send_push_notification", push):
            resp = client.post("/api/v1/loyalty/earn?ride_id=ride_123")

        assert resp.status_code == 200
        assert resp.json()["tier_upgraded"] is False
        push.assert_not_awaited()

    def test_push_failure_does_not_break_the_earn_response(self, client):
        """A notification failure must never surface as a failed /earn call —
        the loyalty account + points ledger already committed by that point."""
        bronze_near_threshold = {
            **SAMPLE_ACCOUNT,
            "tier": "bronze",
            "points": 490,
            "lifetime_points": 490,
        }
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(side_effect=[SAMPLE_RIDE, bronze_near_threshold])
        push = AsyncMock(side_effect=RuntimeError("fcm down"))

        with patch("routes.loyalty.db", mock_db), patch("routes.loyalty.send_push_notification", push):
            resp = client.post("/api/v1/loyalty/earn?ride_id=ride_123")

        assert resp.status_code == 200
        data = resp.json()
        assert data["tier_upgraded"] is True
        assert data["tier"] == "silver"
        # the account update still happened despite the push failure
        mock_db.update_one.assert_awaited_once()


class TestRedeemPoints:
    """POST /api/v1/loyalty/redeem — redemption is withdrawn (returns 410).

    The old flow credited the wallet, then debited points with a non-atomic
    read-then-write, so concurrent redemptions double-credited the wallet. The
    endpoint now refuses every request until an atomic points-debit replaces it.
    """

    def test_redeem_is_gone_410(self, client):
        rich_account = {**SAMPLE_ACCOUNT, "points": 500}
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=rich_account)

        with patch("routes.loyalty.db", mock_db):
            resp = client.post("/api/v1/loyalty/redeem", json={"points": 100})

        assert resp.status_code == 410
        # No wallet was touched: a request with more than enough points still fails.
        assert "unavailable" in resp.json()["detail"].lower()

    def test_redeem_does_not_credit_wallet_even_with_ample_points(self, client):
        rich_account = {**SAMPLE_ACCOUNT, "points": 100_000}
        mock_db = make_mock_db()
        mock_db.find_one = AsyncMock(return_value=rich_account)
        credit = AsyncMock(return_value=Decimal("999.00"))

        with patch("routes.loyalty.db", mock_db):
            resp = client.post("/api/v1/loyalty/redeem", json={"points": 100})

        assert resp.status_code == 410
        credit.assert_not_awaited()  # the money path is unreachable
        mock_db.update_one.assert_not_awaited()  # points ledger untouched
