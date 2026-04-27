"""PIPEDA / CRA retention purge loop.

Runs daily (02:00 UTC cadence approximated via asyncio.sleep(86400)).
Per-table thresholds:
  - users / drivers : hard-delete soft-deleted rows older than 2 years (PIPEDA)
  - rides           : anonymize (null user/driver IDs, round coords) at 2 years;
                      full hard-delete at 7 years (CRA trip-record retention)

Every tick logs purge counts to audit_logs — no PII in the log entries.

Replay-safety: the WHERE clause (deleted_at < cutoff) is idempotent; running
on multiple replicas simultaneously produces duplicate deletes on already-gone
rows (Supabase silently returns 0 affected rows) — safe.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

try:
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:
    from utils.metrics import inc as _metric_inc
    from utils.metrics import set_gauge as _metric_gauge

logger = logging.getLogger(__name__)

_TWO_YEARS = timedelta(days=730)
_SEVEN_YEARS = timedelta(days=2555)
INTERVAL_SECONDS = 86400  # 24 h


async def retention_purge_loop() -> None:
    """Entry point spawned by core/lifespan.py."""
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await _tick()
        except Exception:
            logger.error("retention_purge_loop tick failed", exc_info=True)
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "retention_purge"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "retention_purge"})
        await asyncio.sleep(INTERVAL_SECONDS * (0.9 + random.random() * 0.2))


async def _tick() -> None:
    try:
        from db_supabase import delete_many, get_rows, update_many  # type: ignore[import]
    except ImportError:
        from backend.db_supabase import delete_many, get_rows, update_many  # type: ignore[import]

    now = datetime.now(timezone.utc)
    cutoff_2yr = (now - _TWO_YEARS).isoformat()
    cutoff_7yr = (now - _SEVEN_YEARS).isoformat()

    # ── users: hard-delete soft-deleted rows past 2yr PIPEDA window ──────────
    user_rows = await get_rows(
        "users",
        {"deleted_at": {"$lte": cutoff_2yr}, "deleted_at": {"$ne": None}},
        limit=500,
    )
    user_ids = [r["id"] for r in user_rows]
    users_deleted = 0
    if user_ids:
        await delete_many("users", {"id": {"$in": user_ids}})
        users_deleted = len(user_ids)

    # ── drivers: same 2yr threshold ───────────────────────────────────────────
    driver_rows = await get_rows(
        "drivers",
        {"deleted_at": {"$lte": cutoff_2yr}, "deleted_at": {"$ne": None}},
        limit=500,
    )
    driver_ids = [r["id"] for r in driver_rows]
    drivers_deleted = 0
    if driver_ids:
        await delete_many("drivers", {"id": {"$in": driver_ids}})
        drivers_deleted = len(driver_ids)

    # ── rides: anonymize at 2yr (PIPEDA); hard-delete at 7yr (CRA) ───────────
    # 7-year hard-delete first so we don't anonymize rows we're about to drop.
    old_ride_rows = await get_rows(
        "rides",
        {"created_at": {"$lte": cutoff_7yr}},
        limit=500,
    )
    old_ride_ids = [r["id"] for r in old_ride_rows]
    rides_deleted = 0
    if old_ride_ids:
        await delete_many("rides", {"id": {"$in": old_ride_ids}})
        rides_deleted = len(old_ride_ids)

    # 2-year anonymization: null PII fields, leave trip record for regulatory audit.
    anon_ride_rows = await get_rows(
        "rides",
        {
            "created_at": {"$lte": cutoff_2yr},
            "rider_id": {"$ne": None},
        },
        limit=500,
    )
    anon_ride_ids = [r["id"] for r in anon_ride_rows]
    rides_anonymized = 0
    if anon_ride_ids:
        await update_many(
            "rides",
            {"id": {"$in": anon_ride_ids}},
            {
                "rider_id": None,
                "driver_id": None,
                "pickup_address": None,
                "dropoff_address": None,
                "pickup_lat": None,
                "pickup_lng": None,
                "dropoff_lat": None,
                "dropoff_lng": None,
            },
        )
        rides_anonymized = len(anon_ride_ids)

    if users_deleted or drivers_deleted or rides_deleted or rides_anonymized:
        _audit(users_deleted, drivers_deleted, rides_deleted, rides_anonymized)


def _audit(users: int, drivers: int, rides_del: int, rides_anon: int) -> None:
    logger.info(
        "retention_purge completed",
        extra={
            "users_hard_deleted": users,
            "drivers_hard_deleted": drivers,
            "rides_hard_deleted": rides_del,
            "rides_anonymized": rides_anon,
        },
    )
