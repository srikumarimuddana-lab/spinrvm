"""Fraud guards on the rider-referral payout path (utils/referral_payout).

Ranked blocker #6 / audit finding N2 (2026-08-19): a $0-cost
first_ride_only/free_ride promo ride otherwise satisfied rider-referral
qualification on its own, making the referrer_reward farmable at zero
marginal cost via throwaway phone numbers, with no cap on how many times one
referrer could cash in. Two independent, complementary fixes are pinned here:

  1. a completed ride only counts toward the rider-referral threshold when
     grand_total > 0 (both the prefetched-rows path, _count_prefetched_rides,
     and the exact per-referee count_documents filter in _process_one);
  2. a rolling-window velocity cap on referrer_reward payouts per referrer
     (_referrer_velocity_capped / _record_referrer_payout_velocity), backed by
     the same in-process Redis fallback used everywhere else in this repo when
     REDIS_URL is unset.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import utils.redis_client as redis_client_mod
import utils.referral_payout as rp

# ── _count_prefetched_rides(exclude_zero_fare=...) — pure-function coverage ──


class TestCountPrefetchedRidesExcludeZeroFare:
    def test_zero_fare_ride_excluded_when_flag_set(self):
        rides = [{"created_at": "2026-01-02T00:00:00+00:00", "grand_total": 0}]
        assert rp._count_prefetched_rides(rides, None, None, exclude_zero_fare=True) == 0

    def test_zero_fare_ride_counts_when_flag_not_set(self):
        # Driver-kind callers never pass exclude_zero_fare — behaviour must stay
        # exactly as before this fix for them.
        rides = [{"created_at": "2026-01-02T00:00:00+00:00", "grand_total": 0}]
        assert rp._count_prefetched_rides(rides, None, None) == 1

    def test_real_fare_ride_counts_when_flag_set(self):
        rides = [{"created_at": "2026-01-02T00:00:00+00:00", "grand_total": 12.5}]
        assert rp._count_prefetched_rides(rides, None, None, exclude_zero_fare=True) == 1

    def test_mixed_rides_only_real_fare_ones_count(self):
        rides = [
            {"created_at": "2026-01-01T00:00:00+00:00", "grand_total": 0},
            {"created_at": "2026-01-02T00:00:00+00:00", "grand_total": "0.00"},
            {"created_at": "2026-01-03T00:00:00+00:00", "grand_total": 8.75},
        ]
        assert rp._count_prefetched_rides(rides, None, None, exclude_zero_fare=True) == 1


# ── _process_one end-to-end (ctx=None / exact per-referee count path) ───────


def _filter_aware_db(rides: list) -> MagicMock:
    """db_supabase stand-in whose count_documents actually applies the
    grand_total filter _process_one builds, instead of returning a fixed
    value regardless of the filter passed — so these tests exercise the real
    fix, not just a mocked-away threshold."""
    db = MagicMock()

    async def count_documents(table, filters):
        assert table == "rides"
        min_gt = None
        gt_filter = filters.get("grand_total")
        if isinstance(gt_filter, dict) and "$gt" in gt_filter:
            min_gt = Decimal(str(gt_filter["$gt"]))
        n = 0
        for r in rides:
            if min_gt is not None and Decimal(str(r["grand_total"])) <= min_gt:
                continue
            n += 1
        return n

    db.count_documents = AsyncMock(side_effect=count_documents)
    db.insert_one = AsyncMock(return_value={"id": "claim1"})
    db.update_one = AsyncMock()
    return db


def _referee(uid: str = "referee_u") -> dict:
    return {
        "id": uid,
        "referred_by": "referrer_u",
        "referral_applied_at": None,
        "referral_code_used": "RIDEABCD1234",
    }


def _run_process_one(db, terms, credit, referee=None):
    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "area_id_for_rider", AsyncMock(return_value=None)),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=terms)),
        patch.object(rp, "_credit", credit),
    ):
        asyncio.run(rp._process_one(referee or _referee(), "RIDEABCD1234"))


def test_zero_fare_ride_alone_does_not_satisfy_qualification():
    terms = {"rides": 1, "referrer": Decimal("5.00"), "referee": Decimal("5.00"), "window_days": 0, "terms": None}
    db = _filter_aware_db([{"grand_total": "0.00"}])
    credit = AsyncMock()

    _run_process_one(db, terms, credit)

    db.insert_one.assert_not_awaited()
    credit.assert_not_awaited()


def test_real_fare_ride_still_qualifies_normally():
    """No-regression check: a ride with a genuine non-zero fare must still
    satisfy qualification and pay both sides exactly as before this fix."""
    terms = {"rides": 1, "referrer": Decimal("5.00"), "referee": Decimal("5.00"), "window_days": 0, "terms": None}
    db = _filter_aware_db([{"grand_total": "12.50"}])
    credit = AsyncMock()

    _run_process_one(db, terms, credit)

    db.insert_one.assert_awaited_once()
    doc = db.insert_one.await_args.args[1]
    assert doc["status"] == "processing"
    assert credit.await_count == 2
    paid = {c.args[0]: c.args[1] for c in credit.await_args_list}
    assert paid == {"referrer_u": Decimal("5.00"), "referee_u": Decimal("5.00")}


def test_one_zero_fare_and_one_real_fare_ride_qualifies_on_the_real_one():
    # A referee who took a $0 promo ride AND a later real-fare ride qualifies
    # once the real ride lands, not before.
    terms = {"rides": 1, "referrer": Decimal("5.00"), "referee": Decimal("0.00"), "window_days": 0, "terms": None}
    db = _filter_aware_db([{"grand_total": "0.00"}, {"grand_total": "9.00"}])
    credit = AsyncMock()

    _run_process_one(db, terms, credit)

    db.insert_one.assert_awaited_once()
    credit.assert_awaited_once()
    assert credit.await_args.args[0] == "referrer_u"


# ── Velocity cap (_referrer_velocity_capped / _record_referrer_payout_velocity) ──


def _reset_redis(monkeypatch) -> None:
    """Fresh in-process Redis fallback store per test (mirrors conftest.py's
    mock_redis fixture, but targets utils.redis_client directly — this test
    module imports `utils.referral_payout`/`utils.redis_client` top-level, the
    same module objects referral_payout.py's non-package import binds its
    redis_get/redis_incr/redis_expire names from, per CLAUDE.md's dual-import
    convention)."""
    monkeypatch.setattr(redis_client_mod, "_local", {})


def _rider_db_always_qualified() -> MagicMock:
    db = MagicMock()
    db.count_documents = AsyncMock(return_value=1)
    db.insert_one = AsyncMock(return_value={"id": "claim1"})
    db.update_one = AsyncMock()
    return db


def _run_process_one_for_velocity(db, terms, credit, referee_id, cap):
    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "area_id_for_rider", AsyncMock(return_value=None)),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=terms)),
        patch.object(rp, "get_app_settings", AsyncMock(return_value={"referral_payout_velocity_cap_per_day": cap})),
        patch.object(rp, "_credit", credit),
    ):
        asyncio.run(rp._process_one(_referee(referee_id), "RIDEABCD1234"))


def test_velocity_cap_allows_payout_n_but_rejects_payout_n_plus_1(monkeypatch):
    _reset_redis(monkeypatch)
    terms = {"rides": 1, "referrer": Decimal("5.00"), "referee": Decimal("0.00"), "window_days": 0, "terms": None}
    db = _rider_db_always_qualified()
    credit = AsyncMock()
    cap = 2

    # referee_1 and referee_2: within cap (N=1, N=2) -> both credit the referrer.
    _run_process_one_for_velocity(db, terms, credit, "referee_1", cap)
    _run_process_one_for_velocity(db, terms, credit, "referee_2", cap)
    # referee_3: N+1 -> capped, deferred (no claim opened, no credit).
    _run_process_one_for_velocity(db, terms, credit, "referee_3", cap)

    assert credit.await_count == 2
    assert db.insert_one.await_count == 2
    claimed_referees = {c.args[1]["referee_user_id"] for c in db.insert_one.await_args_list}
    assert claimed_referees == {"referee_1", "referee_2"}


def test_velocity_capped_referee_opens_no_claim_so_it_stays_payable_later():
    """A capped referral must NOT burn the UNIQUE(referee_user_id) claim —
    otherwise the referrer would permanently lose that reward instead of just
    having it deferred to a later tick once the window clears."""
    terms = {"rides": 1, "referrer": Decimal("5.00"), "referee": Decimal("0.00"), "window_days": 0, "terms": None}
    db = _rider_db_always_qualified()
    credit = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "area_id_for_rider", AsyncMock(return_value=None)),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=terms)),
        patch.object(rp, "_referrer_velocity_capped", AsyncMock(return_value=True)),
        patch.object(rp, "_credit", credit),
    ):
        asyncio.run(rp._process_one(_referee("referee_1"), "RIDEABCD1234"))

    db.insert_one.assert_not_awaited()
    credit.assert_not_awaited()


def test_velocity_cap_zero_disables_the_cap(monkeypatch):
    """<= 0 is a documented, explicit admin opt-out, not a bug."""
    _reset_redis(monkeypatch)
    terms = {"rides": 1, "referrer": Decimal("5.00"), "referee": Decimal("0.00"), "window_days": 0, "terms": None}
    db = _rider_db_always_qualified()
    credit = AsyncMock()

    for i in range(5):
        _run_process_one_for_velocity(db, terms, credit, f"referee_{i}", cap=0)

    assert credit.await_count == 5
    assert db.insert_one.await_count == 5


def test_referrer_velocity_capped_read_only_does_not_increment(monkeypatch):
    """Checking the cap (without a successful credit following) must not by
    itself advance the counter — otherwise a still-capped referrer would never
    recover once the window rolls over, since every check would look like a
    fresh payout."""
    _reset_redis(monkeypatch)
    with patch.object(rp, "get_app_settings", AsyncMock(return_value={"referral_payout_velocity_cap_per_day": 1})):
        capped_before = asyncio.run(rp._referrer_velocity_capped("referrer_x", "rider"))
        capped_after_recheck = asyncio.run(rp._referrer_velocity_capped("referrer_x", "rider"))
    assert capped_before is False
    assert capped_after_recheck is False  # still not capped -- the read never incremented


def test_record_referrer_payout_velocity_then_cap_trips(monkeypatch):
    _reset_redis(monkeypatch)
    with patch.object(rp, "get_app_settings", AsyncMock(return_value={"referral_payout_velocity_cap_per_day": 1})):
        asyncio.run(rp._record_referrer_payout_velocity("referrer_y", "rider"))
        capped = asyncio.run(rp._referrer_velocity_capped("referrer_y", "rider"))
    assert capped is True


def test_referrer_velocity_capped_redis_error_fails_open(monkeypatch):
    """A Redis outage must not freeze every referral payout -- the velocity
    cap is a fraud throttle layered on top of the UNIQUE(referee_user_id)
    claim, not a substitute for it."""

    async def _boom(_key):
        raise ConnectionError("redis unavailable")

    with (
        patch.object(rp, "get_app_settings", AsyncMock(return_value={"referral_payout_velocity_cap_per_day": 1})),
        patch.object(rp, "redis_get", _boom),
    ):
        capped = asyncio.run(rp._referrer_velocity_capped("referrer_z", "rider"))
    assert capped is False


def test_record_referrer_payout_velocity_redis_error_does_not_raise(monkeypatch):
    """Must never raise into the caller's credit try/except -- the money has
    already moved by the time this runs, so a Redis hiccup here must not risk
    mis-marking a successful payout as 'failed'."""

    async def _boom(_key):
        raise ConnectionError("redis unavailable")

    with patch.object(rp, "redis_incr", _boom):
        asyncio.run(rp._record_referrer_payout_velocity("referrer_w", "rider"))  # must not raise
