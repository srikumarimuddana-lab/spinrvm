"""Per-area 0 reward = "don't pay this side" (utils/referral_payout._process_one).

Referral reward amounts are admin-controlled per service area. There is no
global on/off flag: setting an area's reward to 0 is how an admin says "don't
pay". This module pins that contract:
  - both sides 0      → no claim is opened and no money moves (the referee stays
                        unclaimed so a later admin raise above 0 can still pay)
  - referrer 0, referee > 0 → claim opens, ONLY the referee is credited
  - referrer > 0, referee 0 → claim opens, ONLY the referrer is credited

The driver-kind tests below pin the same contract for DRIVER referrals now
that an admin can opt a service area into a driver_referee_reward > 0
(migration 201) — previously the driver referee side was hardcoded to 0 and
this path was unreachable.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import utils.referral_payout as rp


def _rider_db() -> MagicMock:
    db = MagicMock()
    # Threshold met: one completed ride (rider referral requires 1).
    db.count_documents = AsyncMock(return_value=1)
    db.insert_one = AsyncMock(return_value={"id": "claim1"})
    db.update_one = AsyncMock()
    return db


def _referee() -> dict:
    return {
        "id": "referee_u",
        "referred_by": "referrer_u",  # rider referral stores the referrer USER id
        "referral_applied_at": "2026-01-01T00:00:00+00:00",
        "referral_code_used": "RIDEABCD1234",
    }


def _run(db, terms):
    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "area_id_for_rider", AsyncMock(return_value="area1")),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=terms)),
        patch.object(rp, "_credit", AsyncMock()) as credit,
    ):
        asyncio.run(rp._process_one(_referee(), "RIDEABCD1234"))
    return credit


def test_both_zero_opens_no_claim_and_pays_nothing():
    terms = {"rides": 1, "referrer": Decimal("0.00"), "referee": Decimal("0.00"), "window_days": 0, "terms": None}
    db = _rider_db()
    credit = _run(db, terms)

    db.insert_one.assert_not_awaited()  # claim NOT burned — re-payable if admin raises it
    credit.assert_not_awaited()  # no money moved


def test_referrer_zero_credits_only_referee():
    terms = {"rides": 1, "referrer": Decimal("0.00"), "referee": Decimal("5.00"), "window_days": 0, "terms": None}
    db = _rider_db()
    credit = _run(db, terms)

    db.insert_one.assert_awaited_once()  # claim opened for the payable side
    credit.assert_awaited_once()
    user_id, amount = credit.await_args.args[0], credit.await_args.args[1]
    assert user_id == "referee_u"
    assert amount == Decimal("5.00")


def test_referee_zero_credits_only_referrer():
    terms = {"rides": 1, "referrer": Decimal("5.00"), "referee": Decimal("0.00"), "window_days": 0, "terms": None}
    db = _rider_db()
    credit = _run(db, terms)

    db.insert_one.assert_awaited_once()
    credit.assert_awaited_once()
    user_id, amount = credit.await_args.args[0], credit.await_args.args[1]
    assert user_id == "referrer_u"
    assert amount == Decimal("5.00")


def _driver_db() -> MagicMock:
    db = MagicMock()

    def get_rows(table, filters=None, **kw):
        if table == "drivers":
            # referee's own driver row (resolves area + ride filter).
            return [{"id": "referee_driver_1", "user_id": "referee_u", "service_area_id": "area1"}]
        return []

    db.get_rows = AsyncMock(side_effect=get_rows)
    db.get_driver_by_id = AsyncMock(return_value={"id": "referrer_driver_1", "user_id": "referrer_u"})
    # Threshold met: 10 completed rides (driver referral requires 10).
    db.count_documents = AsyncMock(return_value=10)
    db.insert_one = AsyncMock(return_value={"id": "claim1"})
    db.update_one = AsyncMock()
    return db


def _driver_referee() -> dict:
    return {
        "id": "referee_u",
        "referred_by": "referrer_driver_1",  # driver referral stores the referrer DRIVER id
        "referral_applied_at": "2026-01-01T00:00:00+00:00",
        "referral_code_used": "DRV-ABCDEF",
    }


def _run_driver(db, terms):
    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=terms)),
        patch.object(rp, "_credit", AsyncMock()) as credit,
    ):
        asyncio.run(rp._process_one(_driver_referee(), "DRV-ABCDEF"))
    return credit


def test_driver_both_zero_opens_no_claim_and_pays_nothing():
    terms = {"rides": 10, "referrer": Decimal("0.00"), "referee": Decimal("0.00"), "window_days": 0, "terms": None}
    db = _driver_db()
    credit = _run_driver(db, terms)

    db.insert_one.assert_not_awaited()
    credit.assert_not_awaited()


def test_driver_referee_reward_credits_both_sides_once_opted_in():
    # Admin set service_areas.driver_referee_reward > 0 for this area: both the
    # referrer ($10) and the referred driver ($5 signup bonus) get credited.
    terms = {"rides": 10, "referrer": Decimal("10.00"), "referee": Decimal("5.00"), "window_days": 0, "terms": None}
    db = _driver_db()
    credit = _run_driver(db, terms)

    db.insert_one.assert_awaited_once()
    assert credit.await_count == 2
    paid = {c.args[0]: c.args[1] for c in credit.await_args_list}
    assert paid == {"referrer_u": Decimal("10.00"), "referee_u": Decimal("5.00")}


def test_driver_referrer_zero_credits_only_referee():
    terms = {"rides": 10, "referrer": Decimal("0.00"), "referee": Decimal("5.00"), "window_days": 0, "terms": None}
    db = _driver_db()
    credit = _run_driver(db, terms)

    db.insert_one.assert_awaited_once()
    credit.assert_awaited_once()
    user_id, amount = credit.await_args.args[0], credit.await_args.args[1]
    assert user_id == "referee_u"
    assert amount == Decimal("5.00")
