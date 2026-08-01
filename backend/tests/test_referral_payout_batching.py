"""Batched per-referee lookups in the referral payout tick (utils/referral_payout).

_prefetch_chunk replaces the per-referee N+1 (count_documents per referee,
get_driver_by_id per referrer, drivers row per referee, area query per rider)
with one drivers query, one referrer-drivers query, and one completed-rides
query per chunk. The claim/credit logic is untouched: these tests pin that the
batched path reaches the same claims the per-referee path did, and that
truncation falls back to exact server-side counting.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import utils.referral_payout as rp
import utils.referral_terms as rt


def _run(coro):
    return asyncio.run(coro)


# Referral deadlines are computed against real wall-clock time
# (`datetime.now(timezone.utc)` in utils/referral_payout.py's expiry check),
# so fixture dates must stay relative to "now", not hardcoded absolute
# calendar dates. A hardcoded "2026-07-01" applied_at + the global 30-day
# window silently crossed into the past once real time caught up to
# 2026-07-31, which flipped these tests from "referral still pending" to
# "referral expired" (an extra `insert_one` expiry-claim call the tests
# don't expect) with zero code change — a time-bomb, not a real regression.
# See issue #2981.
_NOW = datetime.now(timezone.utc)


def _iso(delta_days: float) -> str:
    """ISO-8601 timestamp `delta_days` from now (negative = past)."""
    return (_NOW + timedelta(days=delta_days)).isoformat()


def _db(*, users, rides_by_table_filters=None, drivers=None, service_areas=None):
    """db_supabase mock dispatching get_rows by table; records every call."""
    drivers = drivers or []
    service_areas = service_areas or []

    async def _get_rows(table, filters=None, order=None, desc=False, limit=None, offset=None, columns="*"):
        filters = filters or {}
        if table == "referral_payouts" and filters.get("status") == "processing":
            return []  # stale-claim sweep
        if table == "users":
            return users
        if table == "referral_payouts":
            return []  # nothing claimed yet
        if table == "drivers":
            if "$in" in filters.get("id", {}):
                wanted = set(filters["id"]["$in"])
                return [d for d in drivers if d["id"] in wanted]
            if "$in" in filters.get("user_id", {}):
                wanted = set(filters["user_id"]["$in"])
                return [d for d in drivers if d["user_id"] in wanted]
            if "user_id" in filters:  # legacy per-referee path
                return [d for d in drivers if d["user_id"] == filters["user_id"]]
            raise AssertionError(f"unexpected drivers filters {filters}")
        if table == "rides":
            rider_in = filters.get("rider_id", {})
            driver_in = filters.get("driver_id", {})
            rows = rides_by_table_filters or []
            if "$in" in rider_in:
                return [r for r in rows if r.get("rider_id") in set(rider_in["$in"])][: limit or None]
            if "$in" in driver_in:
                return [r for r in rows if r.get("driver_id") in set(driver_in["$in"])][: limit or None]
            raise AssertionError(f"unexpected rides filters {filters}")
        if table == "service_areas":
            wanted = filters.get("id")
            return [a for a in service_areas if a["id"] == wanted]
        raise AssertionError(f"unexpected table {table}")

    db = MagicMock()
    db.get_rows = AsyncMock(side_effect=_get_rows)
    db.update_one = AsyncMock()
    db.count_documents = AsyncMock(return_value=0)
    db.get_driver_by_id = AsyncMock(return_value=None)
    db.insert_one = AsyncMock()
    return db


def _rider(uid, referred_by="referrer_1", applied_at=None):
    if applied_at is None:
        applied_at = _iso(-10)  # 10 days ago: well inside the 30-day window, deadline still in the future
    return {"id": uid, "referral_code_used": "RIDEX", "referred_by": referred_by, "referral_applied_at": applied_at}


def test_batched_tick_pays_qualified_rider_without_per_referee_queries():
    users = [_rider("u1"), _rider("u2")]
    # u1 has a qualifying completed ride inside the window; u2 has none.
    rides = [{"rider_id": "u1", "created_at": _iso(-9), "service_area_id": None}]
    db = _db(users=users, rides_by_table_filters=rides)
    credit = AsyncMock()

    with patch.object(rp, "db_supabase", db), patch.object(rp, "_credit", credit):
        _run(rp._tick())

    # The qualifying referee was claimed and credited (global default: 1 ride,
    # $5/$5 both sides — 2 credits); the other was not.
    db.insert_one.assert_awaited_once()
    claim = db.insert_one.await_args.args[1]
    assert claim["referee_user_id"] == "u1"
    assert credit.await_count == 2
    # And the per-referee N+1 never ran.
    db.count_documents.assert_not_awaited()
    db.get_driver_by_id.assert_not_awaited()


def test_batched_counts_respect_per_referee_bounds():
    # One ride BEFORE the referral applied, one AFTER the deadline: neither
    # counts, so no claim is opened (deadline in the future → no expiry either).
    applied_at = _iso(-10)
    users = [_rider("u1", applied_at=applied_at)]
    rides = [
        {"rider_id": "u1", "created_at": _iso(-10.001), "service_area_id": None},
        {"rider_id": "u1", "created_at": "2099-01-01T00:00:00+00:00", "service_area_id": None},
    ]
    areas = [{"id": "area1", "rider_referral_window_days": 36500}]  # far-future deadline
    db = _db(users=users, rides_by_table_filters=rides, service_areas=areas)
    credit = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rt, "db_supabase", db),
        patch.object(rp, "_credit", credit),
    ):
        _run(rp._tick())

    db.insert_one.assert_not_awaited()
    credit.assert_not_awaited()


def test_truncated_prefetch_falls_back_to_exact_counts(monkeypatch):
    monkeypatch.setattr(rp, "_RIDES_FETCH_LIMIT", 1)
    users = [_rider("u1"), _rider("u2")]
    rides = [
        {"rider_id": "u1", "created_at": _iso(-9), "service_area_id": None},
        {"rider_id": "u2", "created_at": _iso(-9), "service_area_id": None},
    ]
    db = _db(users=users, rides_by_table_filters=rides)
    db.count_documents = AsyncMock(return_value=0)  # exact counts say: not qualified
    credit = AsyncMock()

    with patch.object(rp, "db_supabase", db), patch.object(rp, "_credit", credit):
        _run(rp._tick())

    # The 1-row cap marks the chunk truncated → every rider referee is counted
    # server-side instead of trusting a possibly-partial prefetch.
    assert db.count_documents.await_count == 2
    db.insert_one.assert_not_awaited()


def test_driver_kind_uses_batched_driver_lookups():
    users = [
        {
            "id": "u_drv",
            "referral_code_used": "DRV99",
            "referred_by": "drv_referrer",
            "referral_applied_at": None,
        }
    ]
    drivers = [
        {"id": "drv_referrer", "user_id": "referrer_user", "service_area_id": None},
        {"id": "drv_referee", "user_id": "u_drv", "service_area_id": None},
    ]
    # Global driver threshold is 10 rides; give exactly 10.
    rides = [{"driver_id": "drv_referee", "created_at": f"2026-07-0{(i % 9) + 1}T00:00:00+00:00"} for i in range(10)]
    db = _db(users=users, rides_by_table_filters=rides, drivers=drivers)
    credit = AsyncMock()

    with patch.object(rp, "db_supabase", db), patch.object(rp, "_credit", credit):
        _run(rp._tick())

    db.insert_one.assert_awaited_once()
    claim = db.insert_one.await_args.args[1]
    assert claim["referee_user_id"] == "u_drv"
    assert claim["referrer_user_id"] == "referrer_user"
    # Referrer resolution came from the batched map, not get_driver_by_id.
    db.get_driver_by_id.assert_not_awaited()
    db.count_documents.assert_not_awaited()


def test_terms_resolved_once_per_area_kind_for_the_whole_tick():
    users = [_rider("u1"), _rider("u2"), _rider("u3")]
    rides = [{"rider_id": uid, "created_at": _iso(-9), "service_area_id": "area1"} for uid in ("u1", "u2", "u3")]
    areas = [{"id": "area1", "rider_referrer_reward": 7, "rider_referee_reward": 0}]
    db = _db(users=users, rides_by_table_filters=rides, service_areas=areas)
    credit = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rt, "db_supabase", db),
        patch.object(rp, "_credit", credit),
    ):
        _run(rp._tick())

    area_lookups = [c for c in db.get_rows.await_args_list if c.args[0] == "service_areas"]
    assert len(area_lookups) == 1  # memoized across all three referees
    assert db.insert_one.await_count == 3


def test_area_pick_replicates_newest_20_rides_rule():
    # 21 area-less newer rides push the only area-bearing ride out of the
    # newest-20 window — the standalone helper would return None (global terms),
    # so the batched path must too.
    rides = [
        {"rider_id": "u1", "created_at": f"2026-07-10T00:00:{i:02d}+00:00", "service_area_id": None} for i in range(21)
    ]
    rides.append({"rider_id": "u1", "created_at": "2026-07-09T00:00:00+00:00", "service_area_id": "areaX"})
    area = rp._area_from_prefetched_rides(rides, "2026-07-01T00:00:00+00:00")
    assert area is None

    # Inside the newest-20 window the area IS picked.
    area2 = rp._area_from_prefetched_rides(rides[:5] + [rides[-1]], None)
    assert area2 == "areaX"
