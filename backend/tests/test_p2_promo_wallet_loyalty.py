"""
P2-15: Promo / wallet / loyalty E2E (R8, R9, R16)

Implemented endpoints:
  POST /promo/validate  — multi-rule validation (expiry, usage, fare min, targeting)
  POST /promo/apply     — server-side fare used (client-fare manipulation guard)
  GET  /wallet          — balance read
  POST /wallet/top-up   — adds funds
  POST /wallet/pay      — deducts; fare-manipulation guard; insufficient guard
  GET  /loyalty         — tier + points status
  POST /loyalty/earn    — points from ride fare; tier multiplier; dedup guard
  POST /loyalty/redeem  — points → wallet credit; min-redemption guard

Run:
    pytest backend/tests/test_p2_promo_wallet_loyalty.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


USER_ID = "user_p2_15"
RIDE_ID = "ride_p2_15_001"


def _ride(total_fare: float = 20.00, status: str = "completed") -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": USER_ID,
        "total_fare": total_fare,
        "status": status,
    }


def _promo(code: str = "SAVE5", **extra) -> dict:
    base = {
        "id": "promo-001",
        "code": code,
        "is_active": True,
        "discount_type": "flat",
        "discount_value": 5.00,
        "max_uses": 100,
        "uses": 0,
        "max_uses_per_user": 1,
        "expiry_date": (datetime.utcnow() + timedelta(days=30)).isoformat(),
        "assigned_user_ids": [],
        "first_ride_only": False,
        "new_user_days": 0,
        "inactive_days": 0,
        "min_total_rides": 0,
        "max_total_rides": 0,
        "total_budget": 0,
        "min_ride_fare": 0,
        "budget_used": 0,
    }
    base.update(extra)
    return base


def _wallet(balance: float = 50.00, active: bool = True) -> dict:
    return {
        "id": "wallet-001",
        "user_id": USER_ID,
        "balance": balance,
        "is_active": active,
        "updated_at": datetime.utcnow().isoformat(),
    }


def _loyalty_account(points: int = 200, lifetime: int = 700, tier: str = "silver") -> dict:
    return {
        "id": "loyal-001",
        "user_id": USER_ID,
        "points": points,
        "lifetime_points": lifetime,
        "tier": tier,
        "updated_at": datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /promo/validate
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestValidatePromo:
    """Pins promo validation rules.

    Code under test: backend/routes/promotions.py::validate_promo (~line 69).
    """

    async def _call(self, code: str, ride_fare: float, promo_row: dict,
                    user_uses: int = 0):
        from backend.routes.promotions import validate_promo, ValidatePromoRequest

        req = ValidatePromoRequest(code=code, ride_fare=Decimal(str(ride_fare)))
        mock_request = MagicMock()

        with (
            patch("backend.routes.promotions.db_supabase.get_rows",
                  AsyncMock(return_value=[promo_row] if promo_row else [])),
            patch("backend.routes.promotions.db_supabase.count_documents",
                  AsyncMock(return_value=user_uses)),
            patch("backend.routes.promotions.db_supabase.get_user_by_id",
                  AsyncMock(return_value={"id": USER_ID, "created_at": "2020-01-01T00:00:00"})),
        ):
            return await validate_promo(mock_request, req, {"id": USER_ID})

    async def test_valid_flat_promo_returns_discount(self):
        promo = _promo(discount_type="flat", discount_value=5.00)
        result = await self._call("SAVE5", 20.00, promo)

        assert result["valid"] is True
        assert float(result["discount_amount"]) == 5.00
        assert result["discount_type"] == "flat"

    async def test_percentage_promo_applies_correct_discount(self):
        promo = _promo(
            discount_type="percentage",
            discount_value=10.0,
            max_discount=None,
        )
        result = await self._call("PCT10", 20.00, promo)

        assert result["valid"] is True
        # 10% of $20 = $2.00
        assert float(result["discount_amount"]) == pytest.approx(2.00, abs=0.01)

    async def test_percentage_promo_caps_at_max_discount(self):
        promo = _promo(
            discount_type="percentage",
            discount_value=50.0,
            max_discount=Decimal("8.00"),
        )
        result = await self._call("PCT50", 50.00, promo)

        # 50% of $50 = $25, but capped at $8
        assert float(result["discount_amount"]) <= 8.00

    async def test_expired_promo_raises_400(self):
        from fastapi import HTTPException

        promo = _promo(expiry_date=(datetime.utcnow() - timedelta(days=1)).isoformat())

        with pytest.raises(HTTPException) as exc_info:
            await self._call("EXPIRED", 20.00, promo)

        assert exc_info.value.status_code == 400
        assert "expired" in exc_info.value.detail.lower()

    async def test_inactive_promo_raises_400(self):
        from fastapi import HTTPException

        promo = _promo(is_active=False)

        with pytest.raises(HTTPException) as exc_info:
            await self._call("INACTIVE", 20.00, promo)

        assert exc_info.value.status_code == 400

    async def test_max_uses_reached_raises_400(self):
        from fastapi import HTTPException

        promo = _promo(max_uses=10, uses=10)

        with pytest.raises(HTTPException) as exc_info:
            await self._call("USED_UP", 20.00, promo)

        assert exc_info.value.status_code == 400
        assert "usage limit" in exc_info.value.detail.lower()

    async def test_per_user_limit_raises_400(self):
        from fastapi import HTTPException

        promo = _promo(max_uses_per_user=1)

        with pytest.raises(HTTPException) as exc_info:
            await self._call("ONCE_ONLY", 20.00, promo, user_uses=1)

        assert exc_info.value.status_code == 400

    async def test_minimum_fare_not_met_raises_400(self):
        from fastapi import HTTPException

        promo = _promo(min_ride_fare=25.00)

        with pytest.raises(HTTPException) as exc_info:
            await self._call("MIN_FARE", 15.00, promo)

        assert exc_info.value.status_code == 400
        assert "minimum" in exc_info.value.detail.lower()

    async def test_unknown_code_raises_404(self):
        from fastapi import HTTPException

        from backend.routes.promotions import validate_promo, ValidatePromoRequest

        req = ValidatePromoRequest(code="GHOST", ride_fare=Decimal("20.00"))
        mock_request = MagicMock()

        with patch("backend.routes.promotions.db_supabase.get_rows",
                   AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await validate_promo(mock_request, req, {"id": USER_ID})

        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# POST /promo/apply
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestApplyPromo:
    """Pins apply_promo: server-fare used, application recorded.

    Code under test: backend/routes/promotions.py::apply_promo (~line 208).
    """

    async def test_apply_uses_server_fare_not_client_fare(self):
        """Server-stored fare is passed to validate; client value is ignored."""
        from backend.routes.promotions import apply_promo, ApplyPromoRequest

        req = ApplyPromoRequest(code="SAVE5", ride_id=RIDE_ID)

        validate_calls = []

        async def _fake_validate(req_inner, user):
            validate_calls.append(req_inner.ride_fare)
            return {
                "valid": True,
                "code": "SAVE5",
                "discount_type": "flat",
                "discount_value": 5.00,
                "discount_amount": Decimal("5.00"),
                "promo_id": "promo-001",
                "description": "",
                "max_discount": None,
            }

        with (
            patch("backend.routes.promotions.db_supabase.find_one",
                  AsyncMock(return_value=_ride(total_fare=20.00))),
            patch("backend.routes.promotions.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.promotions.db_supabase.get_rows",
                  AsyncMock(return_value=[_promo()])),
            patch("backend.routes.promotions.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.promotions.validate_promo",
                  AsyncMock(side_effect=_fake_validate)),
        ):
            result = await apply_promo(req=req, current_user={"id": USER_ID})

        assert result["success"] is True
        # Server fare ($20) was used, not anything from the client
        assert validate_calls[0] == Decimal("20.00")

    async def test_non_owner_apply_raises_403(self):
        from backend.routes.promotions import apply_promo, ApplyPromoRequest
        from fastapi import HTTPException

        req = ApplyPromoRequest(code="SAVE5", ride_id=RIDE_ID)

        with patch("backend.routes.promotions.db_supabase.find_one",
                   AsyncMock(return_value=_ride())):
            with pytest.raises(HTTPException) as exc_info:
                await apply_promo(req=req, current_user={"id": "other-user"})

        assert exc_info.value.status_code == 403

    async def test_ride_not_found_raises_404(self):
        from backend.routes.promotions import apply_promo, ApplyPromoRequest
        from fastapi import HTTPException

        req = ApplyPromoRequest(code="SAVE5", ride_id="ghost-ride")

        with patch("backend.routes.promotions.db_supabase.find_one",
                   AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await apply_promo(req=req, current_user={"id": USER_ID})

        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# POST /wallet/pay
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestWalletPay:
    """Pins wallet_pay: balance deduction, guards, and state updates.

    Code under test: backend/routes/wallet.py::wallet_pay (~line 147).
    """

    async def _pay(self, amount: float, wallet_balance: float = 50.00,
                   ride_fare: float = 20.00):
        from backend.routes.wallet import wallet_pay, WalletPayRequest

        req = WalletPayRequest(ride_id=RIDE_ID, amount=Decimal(str(amount)))
        updated = []
        txns = []

        async def _update(table, query, data):
            updated.append((table, data))

        async def _insert(table, row):
            txns.append(row)
            return row

        with (
            patch("backend.routes.wallet.db.find_one", AsyncMock(side_effect=[
                _wallet(balance=wallet_balance),
                _ride(total_fare=ride_fare),
            ])),
            patch("backend.routes.wallet.db.update_one", AsyncMock(side_effect=_update)),
            patch("backend.routes.wallet.db.insert_one", AsyncMock(side_effect=_insert)),
        ):
            result = await wallet_pay(req=req, current_user={"id": USER_ID})

        return result, updated, txns

    async def test_wallet_pay_deducts_balance(self):
        result, _, _ = await self._pay(amount=15.00, wallet_balance=50.00,
                                       ride_fare=20.00)

        assert "balance" in result
        assert float(result["balance"]) == pytest.approx(35.00, abs=0.01)

    async def test_wallet_pay_marks_ride_paid(self):
        _, updated, _ = await self._pay(amount=15.00, wallet_balance=50.00)

        ride_updates = [(t, d) for t, d in updated if t == "rides"]
        assert ride_updates, "Ride was not updated after payment"
        data = ride_updates[0][1]
        assert data.get("$set", data).get("payment_status") == "paid"

    async def test_insufficient_balance_raises_400(self):
        from backend.routes.wallet import wallet_pay, WalletPayRequest
        from fastapi import HTTPException

        req = WalletPayRequest(ride_id=RIDE_ID, amount=Decimal("100.00"))

        with (
            patch("backend.routes.wallet.db.find_one", AsyncMock(side_effect=[
                _wallet(balance=10.00),
                _ride(total_fare=100.00),
            ])),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await wallet_pay(req=req, current_user={"id": USER_ID})

        assert exc_info.value.status_code == 400
        assert "insufficient" in exc_info.value.detail.lower()

    async def test_amount_exceeds_fare_raises_400(self):
        """Client cannot pay more than the server-stored fare (fare-inflation guard)."""
        from backend.routes.wallet import wallet_pay, WalletPayRequest
        from fastapi import HTTPException

        req = WalletPayRequest(ride_id=RIDE_ID, amount=Decimal("50.00"))

        with (
            patch("backend.routes.wallet.db.find_one", AsyncMock(side_effect=[
                _wallet(balance=100.00),
                _ride(total_fare=20.00),  # fare is only $20
            ])),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await wallet_pay(req=req, current_user={"id": USER_ID})

        assert exc_info.value.status_code == 400
        assert "fare" in exc_info.value.detail.lower() or "exceeded" in exc_info.value.detail.lower()

    async def test_suspended_wallet_raises_403(self):
        from backend.routes.wallet import wallet_pay, WalletPayRequest
        from fastapi import HTTPException

        req = WalletPayRequest(ride_id=RIDE_ID, amount=Decimal("15.00"))

        with (
            patch("backend.routes.wallet.db.find_one", AsyncMock(return_value=_wallet(active=False))),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await wallet_pay(req=req, current_user={"id": USER_ID})

        assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# POST /wallet/top-up
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestWalletTopUp:
    """Pins top_up_wallet: balance increment and transaction record.

    Code under test: backend/routes/wallet.py::top_up_wallet (~line 115).
    """

    async def test_top_up_increases_balance(self):
        from backend.routes.wallet import top_up_wallet, TopUpRequest

        req = TopUpRequest(amount=Decimal("25.00"))
        updated = []
        txns = []

        with (
            patch("backend.routes.wallet.db.find_one", AsyncMock(return_value=_wallet(balance=10.00))),
            patch("backend.routes.wallet.db.update_one", AsyncMock(side_effect=lambda t, q, d: updated.append(d))),
            patch("backend.routes.wallet.db.insert_one", AsyncMock(side_effect=lambda t, r: txns.append(r) or r)),
        ):
            result = await top_up_wallet(req=req, current_user={"id": USER_ID})

        assert float(result["balance"]) == pytest.approx(35.00, abs=0.01)
        assert txns, "Transaction not recorded"
        assert txns[0]["txn_type"] == "top_up"

    async def test_suspended_wallet_blocks_top_up(self):
        from backend.routes.wallet import top_up_wallet, TopUpRequest
        from fastapi import HTTPException

        req = TopUpRequest(amount=Decimal("10.00"))

        with patch("backend.routes.wallet.db.find_one", AsyncMock(return_value=_wallet(active=False))):
            with pytest.raises(HTTPException) as exc_info:
                await top_up_wallet(req=req, current_user={"id": USER_ID})

        assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# POST /loyalty/earn
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestLoyaltyEarn:
    """Pins earn_points_for_ride: points calculation, tier upgrade, dedup.

    Code under test: backend/routes/loyalty.py::earn_points_for_ride (~line 112).
    """

    async def _earn(self, account=None, fare: float = 20.00, already_awarded: bool = False):
        from backend.routes.loyalty import earn_points_for_ride

        acc = account or _loyalty_account(points=0, lifetime=0, tier="bronze")

        async def _find_one(table, query):
            if table == "loyalty_accounts":
                return acc
            if table == "loyalty_transactions" and already_awarded:
                return {"id": "txn-existing"}
            return None

        updated = []
        inserted = []

        with (
            patch("backend.routes.loyalty.db.find_one", AsyncMock(side_effect=_find_one)),
            patch("backend.routes.loyalty.db.update_one",
                  AsyncMock(side_effect=lambda t, q, d: updated.append(d))),
            patch("backend.routes.loyalty.db.insert_one",
                  AsyncMock(side_effect=lambda t, r: inserted.append(r) or r)),
        ):
            result = await earn_points_for_ride(
                ride_id=RIDE_ID,
                current_user={"id": USER_ID},
            )

        return result, updated, inserted

    async def test_earn_points_from_fare(self):
        # Needs ride lookup too
        from backend.routes.loyalty import earn_points_for_ride

        acc = _loyalty_account(points=0, lifetime=0, tier="bronze")

        async def _find_one(table, query):
            if table == "rides":
                return _ride(total_fare=20.00)
            if table == "loyalty_accounts":
                return acc
            return None

        with (
            patch("backend.routes.loyalty.db.find_one", AsyncMock(side_effect=_find_one)),
            patch("backend.routes.loyalty.db.update_one", AsyncMock()),
            patch("backend.routes.loyalty.db.insert_one", AsyncMock(side_effect=lambda t, r: r)),
        ):
            result = await earn_points_for_ride(
                ride_id=RIDE_ID,
                current_user={"id": USER_ID},
            )

        assert result["points_earned"] >= 20  # at least $1/point

    async def test_silver_tier_multiplier_awards_bonus_points(self):
        from backend.routes.loyalty import earn_points_for_ride

        acc = _loyalty_account(points=100, lifetime=700, tier="silver")  # 1.25x

        async def _find_one(table, query):
            if table == "rides":
                return _ride(total_fare=20.00)
            if table == "loyalty_accounts":
                return acc
            return None

        with (
            patch("backend.routes.loyalty.db.find_one", AsyncMock(side_effect=_find_one)),
            patch("backend.routes.loyalty.db.update_one", AsyncMock()),
            patch("backend.routes.loyalty.db.insert_one", AsyncMock(side_effect=lambda t, r: r)),
        ):
            result = await earn_points_for_ride(
                ride_id=RIDE_ID,
                current_user={"id": USER_ID},
            )

        # Silver = 1.25x: 20 base + 5 bonus = 25 total
        assert result["bonus_points"] > 0
        assert result["points_earned"] == result["base_points"] + result["bonus_points"]

    async def test_duplicate_award_returns_already_awarded(self):
        from backend.routes.loyalty import earn_points_for_ride

        async def _find_one(table, query):
            if table == "rides":
                return _ride(total_fare=20.00)
            if table == "loyalty_transactions":
                return {"id": "existing-txn"}
            return _loyalty_account()

        with patch("backend.routes.loyalty.db.find_one", AsyncMock(side_effect=_find_one)):
            result = await earn_points_for_ride(
                ride_id=RIDE_ID,
                current_user={"id": USER_ID},
            )

        assert result.get("already_awarded") is True

    async def test_non_owner_earn_raises_403(self):
        from backend.routes.loyalty import earn_points_for_ride
        from fastapi import HTTPException

        with patch("backend.routes.loyalty.db.find_one",
                   AsyncMock(return_value=_ride(total_fare=20.00))):
            with pytest.raises(HTTPException) as exc_info:
                await earn_points_for_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": "other-user"},
                )

        assert exc_info.value.status_code == 403

    async def test_non_completed_ride_raises_400(self):
        from backend.routes.loyalty import earn_points_for_ride
        from fastapi import HTTPException

        with patch("backend.routes.loyalty.db.find_one",
                   AsyncMock(return_value=_ride(status="in_progress"))):
            with pytest.raises(HTTPException) as exc_info:
                await earn_points_for_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": USER_ID},
                )

        assert exc_info.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# POST /loyalty/redeem
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestLoyaltyRedeem:
    """Pins redeem_points: points deducted, wallet credited, guards.

    Code under test: backend/routes/loyalty.py::redeem_points (~line 187).
    """

    async def test_redeem_deducts_points_and_credits_wallet(self):
        from backend.routes.loyalty import redeem_points, RedeemRequest

        req = RedeemRequest(points=200)
        acc = _loyalty_account(points=500, lifetime=700, tier="silver")
        wallet_row = _wallet(balance=10.00)

        with (
            patch("backend.routes.loyalty.db.find_one", AsyncMock(return_value=acc)),
            patch("backend.routes.loyalty.db.update_one", AsyncMock()),
            patch("backend.routes.loyalty.db.insert_one", AsyncMock(return_value={"id": "txn-001"})),
            # wallet.get_or_create_wallet / _record_transaction imported locally inside fn
            patch("backend.routes.wallet.get_or_create_wallet",
                  AsyncMock(return_value=wallet_row)),
            patch("backend.routes.wallet._record_transaction", AsyncMock(return_value={"id": "t1"})),
        ):
            result = await redeem_points(req=req, current_user={"id": USER_ID})

        # 200 points / 100 = $2.00 credit
        assert float(result["credit_amount"]) == pytest.approx(2.00, abs=0.01)
        assert result["redeemed_points"] == 200
        assert result["remaining_points"] == 300

    async def test_insufficient_points_raises_400(self):
        from backend.routes.loyalty import redeem_points, RedeemRequest
        from fastapi import HTTPException

        req = RedeemRequest(points=500)
        acc = _loyalty_account(points=100, lifetime=700, tier="silver")

        with patch("backend.routes.loyalty.db.find_one", AsyncMock(return_value=acc)):
            with pytest.raises(HTTPException) as exc_info:
                await redeem_points(req=req, current_user={"id": USER_ID})

        assert exc_info.value.status_code == 400
        assert "insufficient" in exc_info.value.detail.lower()

    async def test_below_minimum_redemption_raises_400(self):
        from backend.routes.loyalty import redeem_points, RedeemRequest, REDEMPTION_RATE
        from fastapi import HTTPException

        # Request fewer points than the minimum (100 points = $1)
        req = RedeemRequest(points=REDEMPTION_RATE - 1)

        with pytest.raises(HTTPException) as exc_info:
            await redeem_points(req=req, current_user={"id": USER_ID})

        assert exc_info.value.status_code == 400
        assert "minimum" in exc_info.value.detail.lower()
