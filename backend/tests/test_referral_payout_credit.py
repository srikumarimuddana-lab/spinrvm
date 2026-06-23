"""Referral reward credit routing (utils/referral_payout._credit).

Driver referrals are PAYABLE driver earnings — they must land in the
driver_bonuses ledger (folds into payable_balance + Stripe payout), NOT the
rider wallet the driver app can't see. Rider referrals keep the wallet credit.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import utils.referral_payout as rp  # noqa: F401


@pytest.mark.asyncio
async def test_driver_referral_credits_driver_bonuses_not_wallet():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[{"id": "drv_1", "user_id": "user_1"}])
    db.insert_one = AsyncMock(return_value={"id": "b1"})
    db.wallet_increment_balance = AsyncMock()

    with patch.object(rp, "db_supabase", db):
        await rp._credit("user_1", Decimal("50.00"), "driver", "referee_1", "referral_reward", {})

    # A payable driver_bonuses row was written...
    db.insert_one.assert_awaited_once()
    table, doc = db.insert_one.await_args.args[0], db.insert_one.await_args.args[1]
    assert table == "driver_bonuses"
    assert doc["kind"] == "referral"
    assert doc["driver_id"] == "drv_1"
    assert doc["user_id"] == "user_1"
    assert doc["amount"] == "50.00"
    # ...and NOT the rider wallet.
    db.wallet_increment_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_driver_referral_missing_driver_row_raises():
    """No driver row for the user → raise so the caller marks the claim 'failed'
    (manual reconciliation) instead of silently dropping the reward."""
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[])
    db.insert_one = AsyncMock()

    with patch.object(rp, "db_supabase", db):
        with pytest.raises(RuntimeError):
            await rp._credit("ghost_user", Decimal("50.00"), "driver", "referee_1", "referral_reward", {})
    db.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_rider_referral_credits_wallet_not_bonuses():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[])
    db.insert_one = AsyncMock()
    db.wallet_increment_balance = AsyncMock(return_value=Decimal("50.00"))

    with (
        patch.object(rp, "db_supabase", db),
        patch("routes.wallet.get_or_create_wallet", AsyncMock(return_value={"id": "w1"})),
        patch("routes.wallet._record_transaction", AsyncMock()),
    ):
        await rp._credit("rider_1", Decimal("50.00"), "rider", "referee_1", "referral_reward", {})

    db.wallet_increment_balance.assert_awaited_once()
    # No driver_bonuses insert for a rider referral.
    inserted_tables = [c.args[0] for c in db.insert_one.await_args_list]
    assert "driver_bonuses" not in inserted_tables
