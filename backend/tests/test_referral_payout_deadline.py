"""Referral completion-deadline enforcement (utils/referral_payout._process_one).

A referred driver must complete the ride threshold WITHIN window_days of
referral_applied_at:
  - met in time      → a 'processing' claim is opened and the reward is credited
  - missed deadline  → a terminal 'expired' claim (rewards 0) is recorded, never paid
  - still in window  → nothing happens yet; the next tick re-checks
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import utils.referral_payout as rp

_DRIVER_TERMS = {
    "rides": 10,
    "referrer": Decimal("10.00"),
    "referee": Decimal("0.00"),
    "window_days": 30,
    "terms": None,
}


def _driver_db(completed: int) -> MagicMock:
    db = MagicMock()
    # Resolve the referrer's user id from their driver id.
    db.get_driver_by_id = AsyncMock(return_value={"user_id": "referrer_u"})
    # The referee's own driver row (service area for per-area terms).
    db.get_rows = AsyncMock(return_value=[{"id": "referee_drv", "service_area_id": "area1"}])
    db.count_documents = AsyncMock(return_value=completed)
    db.insert_one = AsyncMock(return_value={"id": "claim1"})
    db.update_one = AsyncMock()
    return db


def _referee(applied_at: str) -> dict:
    return {
        "id": "referee_u",
        "referred_by": "drv_referrer",
        "referral_applied_at": applied_at,
        "referral_code_used": "DRV99",
    }


def _run(db, referee):
    import asyncio

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=dict(_DRIVER_TERMS))),
        patch.object(rp, "_credit", AsyncMock()) as credit,
    ):
        asyncio.run(rp._process_one(referee, "DRV99"))
    return credit


def test_missed_deadline_records_expired_and_does_not_pay():
    # Applied long ago, under threshold → window closed → expire, never pay.
    db = _driver_db(completed=3)
    credit = _run(db, _referee("2020-01-01T00:00:00+00:00"))

    db.insert_one.assert_awaited_once()
    table, doc = db.insert_one.await_args.args[0], db.insert_one.await_args.args[1]
    assert table == "referral_payouts"
    assert doc["status"] == "expired"
    assert doc["referrer_reward"] == "0.00" and doc["referee_reward"] == "0.00"
    credit.assert_not_awaited()  # no money moved


def test_under_threshold_within_window_waits():
    # Applied yesterday, under threshold, deadline still in the future → no claim
    # of any kind yet; the loop re-checks next tick.
    applied = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db = _driver_db(completed=3)
    credit = _run(db, _referee(applied))

    db.insert_one.assert_not_awaited()
    credit.assert_not_awaited()


def test_threshold_met_in_time_opens_claim_and_credits():
    # Applied yesterday, threshold met before the deadline → normal payout path.
    applied = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db = _driver_db(completed=10)
    credit = _run(db, _referee(applied))

    db.insert_one.assert_awaited_once()
    doc = db.insert_one.await_args.args[1]
    assert doc["status"] == "processing"
    assert doc["referrer_reward"] == "10.00"
    credit.assert_awaited()  # referrer credited


def test_no_deadline_when_window_days_zero():
    # window_days == 0 disables the deadline: an ancient referral still pays once
    # the threshold is reached (original "count forever" behaviour).
    db = _driver_db(completed=10)
    terms = dict(_DRIVER_TERMS, window_days=0)
    import asyncio

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=terms)),
        patch.object(rp, "_credit", AsyncMock()),
    ):
        asyncio.run(rp._process_one(_referee("2019-01-01T00:00:00+00:00"), "DRV99"))

    doc = db.insert_one.await_args.args[1]
    assert doc["status"] == "processing"  # paid despite being years old
