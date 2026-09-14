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
    db.get_user_by_id = AsyncMock(return_value={"id": "rider_1", "role": "rider"})

    with (
        patch.object(rp, "db_supabase", db),
        patch("routes.wallet.get_or_create_wallet", AsyncMock(return_value={"id": "w1"})),
        patch("routes.wallet._record_transaction", AsyncMock()),
        patch.object(rp, "send_push_notification", AsyncMock()),
    ):
        await rp._credit("rider_1", Decimal("50.00"), "rider", "referee_1", "referral_reward", {})

    db.wallet_increment_balance.assert_awaited_once()
    # No driver_bonuses insert for a rider referral.
    inserted_tables = [c.args[0] for c in db.insert_one.await_args_list]
    assert "driver_bonuses" not in inserted_tables


@pytest.mark.asyncio
async def test_rider_referral_credit_sends_push_notification():
    """R31 follow-up: a rider referral wallet credit must notify the rider —
    previously silent (ACTION_ITEMS.md)."""
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[])
    db.insert_one = AsyncMock()
    db.wallet_increment_balance = AsyncMock(return_value=Decimal("55.00"))
    db.get_user_by_id = AsyncMock(return_value={"id": "rider_1", "role": "rider"})
    push = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch("routes.wallet.get_or_create_wallet", AsyncMock(return_value={"id": "w1"})),
        patch("routes.wallet._record_transaction", AsyncMock()),
        patch.object(rp, "send_push_notification", push),
    ):
        await rp._credit("rider_1", Decimal("5.00"), "rider", "referee_1", "referral_reward", {})

    push.assert_awaited_once()
    args, kwargs = push.await_args
    assert args[0] == "rider_1"
    assert "$5.00" in args[2]
    assert kwargs["target_app"] == "rider"
    assert kwargs["data"]["type"] == "referral_payout"
    assert kwargs["data"]["referral_payout_id"] == "referee_1"


@pytest.mark.asyncio
async def test_rider_referral_credit_routes_push_to_driver_app_for_driver_account():
    """`kind` is the referral-CODE type (rider-referral program), not the
    recipient's account role -- routes/users.py's apply_rider_referral
    resolves the referrer from the shared `users` table with no role filter,
    so a driver account can end up as referrer_user_id on a kind='rider'
    payout. The push must route to whichever app that user actually has,
    mirroring routes/admin/wallet.py's _wallet_target_app -- not assume
    rider just because this is the rider-referral program."""
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[])
    db.insert_one = AsyncMock()
    db.wallet_increment_balance = AsyncMock(return_value=Decimal("5.00"))
    db.get_user_by_id = AsyncMock(return_value={"id": "driver_acct_1", "role": "driver"})
    push = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch("routes.wallet.get_or_create_wallet", AsyncMock(return_value={"id": "w1"})),
        patch("routes.wallet._record_transaction", AsyncMock()),
        patch.object(rp, "send_push_notification", push),
    ):
        await rp._credit("driver_acct_1", Decimal("5.00"), "rider", "referee_1", "referral_reward", {})

    push.assert_awaited_once()
    assert push.await_args.kwargs["target_app"] == "driver"


@pytest.mark.asyncio
async def test_rider_referral_credit_push_title_distinguishes_referrer_vs_referee():
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[])
    db.insert_one = AsyncMock()
    db.wallet_increment_balance = AsyncMock(return_value=Decimal("5.00"))
    db.get_user_by_id = AsyncMock(return_value={"role": "rider"})
    push = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch("routes.wallet.get_or_create_wallet", AsyncMock(return_value={"id": "w1"})),
        patch("routes.wallet._record_transaction", AsyncMock()),
        patch.object(rp, "send_push_notification", push),
    ):
        await rp._credit("referrer_1", Decimal("5.00"), "rider", "referee_1", "referral_reward", {})
        await rp._credit("referee_1", Decimal("5.00"), "rider", "referee_1", "referral_bonus", {})

    titles = [c.args[1] for c in push.await_args_list]
    assert titles == ["Referral reward earned!", "Referral bonus earned!"]


@pytest.mark.asyncio
async def test_rider_referral_credit_push_failure_does_not_fail_the_credit():
    """Best-effort: the wallet credit + ledger entry already committed, so a
    push failure must be swallowed, not raised."""
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[])
    db.insert_one = AsyncMock()
    db.wallet_increment_balance = AsyncMock(return_value=Decimal("5.00"))
    db.get_user_by_id = AsyncMock(return_value={"role": "rider"})

    with (
        patch.object(rp, "db_supabase", db),
        patch("routes.wallet.get_or_create_wallet", AsyncMock(return_value={"id": "w1"})),
        patch("routes.wallet._record_transaction", AsyncMock()),
        patch.object(rp, "send_push_notification", AsyncMock(side_effect=RuntimeError("push down"))),
    ):
        # Must not raise.
        await rp._credit("rider_1", Decimal("5.00"), "rider", "referee_1", "referral_reward", {})

    db.wallet_increment_balance.assert_awaited_once()


@pytest.mark.asyncio
async def test_rider_referral_credit_user_lookup_failure_does_not_fail_the_credit():
    """The target-app resolution lookup is itself best-effort -- if it fails,
    the credit must still stand (same guarantee as a push-send failure)."""
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[])
    db.insert_one = AsyncMock()
    db.wallet_increment_balance = AsyncMock(return_value=Decimal("5.00"))
    db.get_user_by_id = AsyncMock(side_effect=RuntimeError("db down"))
    push = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch("routes.wallet.get_or_create_wallet", AsyncMock(return_value={"id": "w1"})),
        patch("routes.wallet._record_transaction", AsyncMock()),
        patch.object(rp, "send_push_notification", push),
    ):
        # Must not raise.
        await rp._credit("rider_1", Decimal("5.00"), "rider", "referee_1", "referral_reward", {})

    db.wallet_increment_balance.assert_awaited_once()
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_driver_referral_credit_never_sends_rider_push():
    """Driver referrals pay driver_bonuses, not the rider wallet -- no push."""
    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[{"id": "drv_1", "user_id": "user_1"}])
    db.insert_one = AsyncMock(return_value={"id": "b1"})
    db.wallet_increment_balance = AsyncMock()
    push = AsyncMock()

    with patch.object(rp, "db_supabase", db), patch.object(rp, "send_push_notification", push):
        await rp._credit("user_1", Decimal("10.00"), "driver", "referee_1", "referral_reward", {})

    push.assert_not_awaited()
