"""Finalize Period-1 (deadhead) distance accumulators into the append-only audit.

The location-batch path accumulates a running scalar (drivers.period1_accum_km)
while a driver is online with no active ride. This loop drains that scalar into
driver_period_distances (period=1) once the driver LEAVES Period 1 — i.e. they
picked up a ride (now Period 2/3) or went offline (Period 0). It never runs while
the span is still open, so the total is complete before it is recorded.

Replay-safe: one replica via a Redis leader lock; each accumulator is CLAIMED by
an atomic conditional reset (period1_accum_km = 0 WHERE period1_accum_km = value)
before the audit row is written, so a restart or a second replica never
double-counts. Claim-before-write means a crash in the tiny window loses a span
rather than double-recording it — the safe direction for an audit total.

Off unless period1_distance_tracking_enabled is set (same flag that gates
accumulation).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from ..utils.breadcrumbs import resolve_active_ride
    from ..utils.redis_client import redis_set_nx
except ImportError:  # pragma: no cover - dual import path
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.breadcrumbs import resolve_active_ride  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # 5 min — deadhead spans end often; keep the audit fresh.
BATCH_LIMIT = 500
_LOCK_KEY = "spinr:lock:period1_finalizer"
_LOCK_TTL_SECONDS = 290  # just under the interval, re-acquired each tick.


async def _driver_left_period1(driver_row: Dict[str, Any]) -> bool:
    """True when the driver is no longer in Period 1 (offline, or on a ride).

    Only then is the accumulated span complete and safe to finalize. While the
    driver is still online with no active ride, the span is open — leave it.
    """
    if not driver_row.get("is_online"):
        return True  # offline → Period 0
    try:
        active = await resolve_active_ride(driver_row["id"])
    except Exception:
        # Can't confirm they left Period 1 — don't finalize yet.
        logger.error("period1 finalizer: active-ride check failed (driver=%s)", driver_row.get("id"), exc_info=True)
        return False
    return active is not None  # on a ride → Period 2/3


async def _finalize_one(driver_row: Dict[str, Any], now_iso: str) -> bool:
    """Claim and record one driver's Period-1 accumulator. Returns True if written."""
    driver_id = driver_row["id"]
    value = float(driver_row.get("period1_accum_km") or 0)
    if value <= 0:
        return False

    supabase = db_supabase.supabase
    if supabase is None:
        return False

    # Atomic claim: reset only if the value is still exactly what we read. A
    # concurrent finalizer (or a resumed accumulation) changes it → 0 rows → skip.
    def _claim() -> int:
        res = (
            supabase.table("drivers")
            .update({"period1_accum_km": 0, "period1_accum_since": None})
            .eq("id", driver_id)
            .eq("period1_accum_km", value)
            .execute()
        )
        return len(getattr(res, "data", None) or [])

    claimed = await db_supabase.run_sync(_claim, retry_policy="idempotent_write")
    if not claimed:
        return False

    await db_supabase.insert_one(
        "driver_period_distances",
        {
            "driver_id": driver_id,
            "ride_id": None,
            "period": 1,
            "distance_km": round(value, 3),
            "started_at": driver_row.get("period1_accum_since"),
            "ended_at": now_iso,
            "source": "gps_scalar_p1",
        },
    )
    logger.info("period1 finalizer: recorded %.3f km deadhead (driver=%s)", value, driver_id)
    return True


async def _pending_accumulators() -> List[Dict[str, Any]]:
    supabase = db_supabase.supabase
    if supabase is None:
        return []

    def _q():
        return (
            supabase.table("drivers")
            .select("id,is_online,period1_accum_km,period1_accum_since")
            .gt("period1_accum_km", 0)
            .limit(BATCH_LIMIT)
            .execute()
        )

    res = await db_supabase.run_sync(_q, retry_policy="read")
    return getattr(res, "data", None) or []


async def _tick() -> int:
    """One finalization pass. Returns the number of accumulators recorded."""
    settings = await get_app_settings() or {}
    if not settings.get("period1_distance_tracking_enabled"):
        return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    recorded = 0
    for driver_row in await _pending_accumulators():
        try:
            if not await _driver_left_period1(driver_row):
                continue
            if await _finalize_one(driver_row, now_iso):
                recorded += 1
        except Exception:
            logger.error(
                "period1 finalizer: failed for driver=%s (skipped)",
                driver_row.get("id"),
                exc_info=True,
            )
    return recorded


async def period1_distance_finalizer_loop() -> None:
    """Drain completed Period-1 accumulators into the audit every 5 minutes."""
    while True:
        try:
            got_lock = await redis_set_nx(_LOCK_KEY, "1", ttl=_LOCK_TTL_SECONDS)
            if got_lock:
                await _tick()
        except Exception:
            logger.error("period1_distance_finalizer tick failed", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)
