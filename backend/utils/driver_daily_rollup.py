"""Scheduled Regina-day rollup of driver activity into driver_daily_stats.

Shared core for the manual admin endpoint (routes/admin/maintenance.py) and
the 30-minute background loop below. One day definition everywhere:
America/Regina calendar days (Saskatchewan business day, UTC-6 year-round),
stamped ``day_tz='regina'`` — legacy endpoint-written rows carry 'utc'.

Driver discovery comes from driver_insurance_periods overlapping the window
(plus drivers with rides that day) — the previous approach scanned up to
10 k raw breadcrumbs just to learn who was online, which both missed
drivers past the cap and paid for coordinates it never used.

Replay-safe: idempotent upsert keyed on the existing UNIQUE
(driver_id, stat_date); single leader via Redis lock; recomputing a day is
always safe (the GPS truth is re-derived from driver_location_history via
the v2 SQL function each time).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase
    from .driver_activity import REGINA_TZ, regina_day_bounds_utc
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set_nx
except ImportError:  # pragma: no cover - dual import path
    import db_supabase  # type: ignore
    from utils.driver_activity import REGINA_TZ, regina_day_bounds_utc  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 1800  # 30 min — keeps closed-day stats fresh for admin views
_LOCK_KEY = "spinr:lock:driver_daily_rollup"
_LOCK_TTL_SECONDS = 1790
_ROW_LIMIT = 10000
# Regina hour after which the nightly 7-day sweep may run (02:00 local —
# quiet hours; catches late GPS tails and any missed intermediate days).
_SWEEP_AFTER_LOCAL_HOUR = 2
_SWEEP_DAYS = 7

_last_sweep_for: Optional[date_cls] = None  # module state; reset on restart is harmless


async def _drivers_active_in_window(win_start_iso: str, win_end_iso: str) -> set:
    """Driver ids with any insurance-period overlap of [win_start, win_end)."""
    rows = (
        await db_supabase.get_rows(
            "driver_insurance_periods",
            {
                "period": {"$in": [1, 2, 3]},
                "started_at": {"$lt": win_end_iso},
                "$or": [
                    {"ended_at": {"$notnull": False}},
                    {"ended_at": {"$gte": win_start_iso}},
                ],
            },
            columns="driver_id",
            limit=_ROW_LIMIT,
        )
        or []
    )
    return {r["driver_id"] for r in rows if r.get("driver_id")}


async def _phase_stats(driver_id: str, win_start_iso: str, win_end_iso: str) -> Dict[str, Any]:
    """One-driver GPS aggregate via the v2 SQL function (11 scalars, no rows)."""
    supabase = db_supabase.supabase
    if supabase is None:
        return {}

    def _rpc():
        return supabase.rpc(
            "compute_driver_phase_distances",
            {
                "p_driver_id": driver_id,
                "p_day_start": win_start_iso,
                "p_day_end": win_end_iso,
            },
        ).execute()

    resp = await db_supabase.run_sync(_rpc, retry_policy="read")
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else {}


async def rollup_driver_day(stat_date: date_cls) -> Dict[str, Any]:
    """Recompute one Regina calendar day for every active driver. Idempotent."""
    win_start, win_end = regina_day_bounds_utc(stat_date)
    win_start_iso = win_start.isoformat()
    win_end_iso = win_end.isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    day_rides = (
        await db_supabase.get_rows(
            "rides",
            {"created_at": {"$gte": win_start_iso, "$lt": win_end_iso}},
            limit=_ROW_LIMIT,
        )
        or []
    )
    rides_by_driver: Dict[str, List[dict]] = {}
    for r in day_rides:
        did = r.get("driver_id")
        if did:
            rides_by_driver.setdefault(did, []).append(r)

    decline_logs = (
        await db_supabase.get_rows(
            "audit_logs",
            {
                "action": "ride_declined",
                "created_at": {"$gte": win_start_iso, "$lt": win_end_iso},
            },
            limit=_ROW_LIMIT,
        )
        or []
    )
    declines_by_driver: Dict[str, int] = {}
    for entry in decline_logs:
        did = entry.get("actor_id") or entry.get("user_email")
        if did:
            declines_by_driver[did] = declines_by_driver.get(did, 0) + 1

    all_driver_ids = await _drivers_active_in_window(win_start_iso, win_end_iso)
    all_driver_ids |= set(rides_by_driver.keys())

    created = updated = failed = 0
    for driver_id in all_driver_ids:
        try:
            gps = await _phase_stats(driver_id, win_start_iso, win_end_iso)
        except Exception:
            logger.error(
                "daily_rollup: compute_driver_phase_distances failed driver=%s date=%s",
                driver_id,
                stat_date,
                exc_info=True,
            )
            failed += 1
            continue

        rides = rides_by_driver.get(driver_id, [])
        rides_completed = sum(1 for r in rides if r.get("status") == "completed")
        rides_cancelled = sum(1 for r in rides if r.get("status") == "cancelled")
        total_earnings = float(
            sum(Decimal(str(r.get("driver_earnings") or 0)) for r in rides if r.get("status") == "completed")
        )
        total_tips = float(sum(Decimal(str(r.get("tip_amount") or 0)) for r in rides if r.get("status") == "completed"))

        drv = await db_supabase.get_driver_by_id(driver_id)
        idle_km = float(gps.get("idle_km") or 0)
        navigating_km = float(gps.get("navigating_km") or 0)
        trip_km = float(gps.get("trip_km") or 0)

        stat_row = {
            "id": f"{driver_id}_{stat_date.isoformat()}",
            "driver_id": driver_id,
            "stat_date": stat_date.isoformat(),
            "day_tz": "regina",
            "service_area_id": drv.get("service_area_id") if drv else None,
            "online_minutes": int(gps.get("online_minutes") or 0),
            "idle_km": round(idle_km, 2),
            "navigating_km": round(navigating_km, 2),
            "trip_km": round(trip_km, 2),
            "total_km": round(idle_km + navigating_km + trip_km, 2),
            "idle_seconds": int(gps.get("idle_seconds") or 0),
            "navigating_seconds": int(gps.get("navigating_seconds") or 0),
            "trip_seconds": int(gps.get("trip_seconds") or 0),
            "first_online_at": gps.get("first_online_at"),
            "last_online_at": gps.get("last_online_at"),
            "rides_completed": rides_completed,
            "rides_cancelled": rides_cancelled,
            "rides_declined": declines_by_driver.get(driver_id, 0),
            "total_earnings": round(total_earnings, 2),
            "total_tips": round(total_tips, 2),
            "updated_at": now_iso,
        }

        try:
            existing = await db_supabase.get_rows("driver_daily_stats", {"id": stat_row["id"]}, limit=1)
            if existing:
                await db_supabase.update_one("driver_daily_stats", {"id": stat_row["id"]}, stat_row)
                updated += 1
            else:
                stat_row["created_at"] = now_iso
                await db_supabase.insert_one("driver_daily_stats", stat_row)
                created += 1
        except Exception:
            logger.error("daily_rollup: upsert failed driver=%s date=%s", driver_id, stat_date, exc_info=True)
            failed += 1

    result = {
        "stat_date": stat_date.isoformat(),
        "day_tz": "regina",
        "drivers_processed": len(all_driver_ids),
        "created": created,
        "updated": updated,
        "failed": failed,
    }
    logger.info("daily_rollup: %s", result)
    _metric_inc("spinr_drivers_daily_rollup_days_total", {"outcome": "failed" if failed else "ok"})
    return result


def _completed_regina_days(now_utc: datetime, count: int) -> List[date_cls]:
    """The last ``count`` COMPLETED Regina days, newest first. Never today:
    a partial-day row would advance MAX(stat_date) and make the leaderboard
    freshness top-up (routes/drivers/referrals.py) skip rides completed
    after the rollup ran."""
    today_regina = now_utc.astimezone(REGINA_TZ).date()
    return [today_regina - timedelta(days=i) for i in range(1, count + 1)]


async def _tick(now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Recompute the last 2 completed Regina days; once nightly, the last 7."""
    global _last_sweep_for
    now_utc = now_utc or datetime.now(timezone.utc)
    now_regina = now_utc.astimezone(REGINA_TZ)

    days = _completed_regina_days(now_utc, 2)
    if now_regina.hour >= _SWEEP_AFTER_LOCAL_HOUR and _last_sweep_for != now_regina.date():
        days = _completed_regina_days(now_utc, _SWEEP_DAYS)
        _last_sweep_for = now_regina.date()

    results = []
    for d in days:
        try:
            results.append(await rollup_driver_day(d))
        except Exception:
            logger.error("daily_rollup: day %s failed entirely", d, exc_info=True)
    return {"days": [r["stat_date"] for r in results]}


async def driver_daily_rollup_loop() -> None:
    """Keep driver_daily_stats current on Regina business days, every 30 min."""
    while True:
        try:
            got_lock = await redis_set_nx(_LOCK_KEY, "1", ttl=_LOCK_TTL_SECONDS)
            if got_lock:
                await _tick()
        except Exception:
            logger.error("driver_daily_rollup tick failed", exc_info=True)
        # Heartbeat every iteration, lock or not — the watchdog is per-replica.
        _record_heartbeat("driver_daily_rollup (30min)")
        await asyncio.sleep(INTERVAL_SECONDS)
