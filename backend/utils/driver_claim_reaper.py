"""Orphaned driver-claim reaper (C3).

Dispatch claims a driver (is_available=false via claim_driver_atomic) and only
then inserts the ride_offers row + schedules the offer-timeout task. A crash or
restart in that window leaves the driver is_available=false with no offer and no
timeout handler — silently dropped from dispatch. The stuck-ride sweeper
(`utils/stuck_ride_sweeper.py`) recovers the ride ONLY when it is in
`searching` (its atomic claim filters `.eq("status", "searching")`) — it does
not recover the driver flag, and it does nothing at all for a ride already
past `searching` (e.g. `driver_assigned`/`in_progress`). A stuck `in_progress`
ride has no automated recovery today; `utils/stale_in_progress_ride_alerter.py`
alerts (never mutates) on that gap.

This loop asks a Postgres RPC to recover each candidate. The RPC uses DB time,
locks the current claim, checks offer/ride obligations, and releases it while
closing or opening the current insurance period in the same transaction.

Replay-safety (CLAUDE.md, Background loops):
  - Redis leader lock (best-effort throttle).
  - Driver claim identity + the DB-side driver-row lock are the correctness
    guard; duplicate ticks are harmless and cannot release a newer claim.
"""

import asyncio
import logging
import os
import random
import socket

try:
    from ..db import db
    from .insurance_periods import reap_stale_driver_claim
    from .redis_client import redis_set_nx
except ImportError:
    from db import db  # type: ignore
    from utils.insurance_periods import reap_stale_driver_claim  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


logger = logging.getLogger(__name__)

RECLAIM_THRESHOLD_SECONDS = 90  # well past the ~15s offer window
REAP_INTERVAL_SECONDS = 60
_LOOP_NAME = "driver_claim_reaper (60s)"


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def _reap_tick() -> None:
    """Ask Postgres to recover stale claims without unscoped availability writes."""
    try:
        drivers = await db.get_rows(
            "drivers",
            {"is_online": True, "is_available": False},
            limit=200,
            columns="id,user_id,is_online,is_available,availability_claimed_at,availability_claim_id",
        )
    except Exception as e:
        logger.error("[claim-reaper] failed to fetch candidate drivers: %s", e, exc_info=True)
        return

    for driver in drivers:
        driver_id = driver["id"]
        try:
            result = await reap_stale_driver_claim(
                driver_id,
                driver.get("availability_claim_id"),
                driver.get("availability_claimed_at"),
            )
        except Exception as e:
            logger.error("[claim-reaper] recovery RPC failed for driver %s: %s", driver_id, e, exc_info=True)
            continue
        if isinstance(result, dict) and result.get("status") == "released":
            logger.warning(
                "[claim-reaper] released orphaned claim for driver %s (claim_id=%s, claimed %s)",
                driver_id,
                driver.get("availability_claim_id"),
                driver.get("availability_claimed_at"),
            )
        elif isinstance(result, dict):
            status = result.get("status")
            if status not in {
                "claim_too_recent",
                "offer_active",
                "ride_active",
                "offer_or_ride_active",
                "not_claimed",
                "driver_missing",
                "legacy_claim_changed",
                "claim_identity_changed",
            }:
                logger.error("[claim-reaper] recovery skipped for driver %s reason=%s", driver_id, status)


async def driver_claim_reaper_loop() -> None:
    """Background loop: release orphaned driver claims every interval."""
    logger.info(
        f"Driver claim reaper started (interval={REAP_INTERVAL_SECONDS}s, threshold={RECLAIM_THRESHOLD_SECONDS}s)"
    )
    while True:
        # TTL must be SHORTER than the minimum possible sleep below (interval *
        # 0.9, worst-case jitter), or the pod that ran the last tick wakes to
        # find its OWN key still alive, fails SET NX, and sleeps another full
        # interval — halving the documented cadence. `interval * 2` doesn't
        # (2x > 0.9x). Matches ledger_projection.py's `_LOCK_TTL_SECONDS`
        # formula (ACTION_ITEMS B21): 0.05 headroom under the 0.9 floor.
        lock_ttl = int(REAP_INTERVAL_SECONDS * 0.85)
        try:
            got_lock = await redis_set_nx("spinr:driver:claim_reaper:lock", _pod_id(), lock_ttl)
        except Exception as lock_err:
            # redis_set_nx now raises on a real (Redis-configured-but-
            # unavailable) error instead of silently falling back per-replica
            # (2026-08-11 P1 fix). This lock is a throttle only — the atomic
            # DB claim inside _reap_tick is the real correctness guard — so
            # proceed with the tick rather than skip it; every replica
            # running redundant idempotent work during a Redis blip is safe,
            # silently going dark every tick until Redis recovers is not.
            logger.error(f"driver_claim_reaper: leader lock unavailable ({lock_err}), proceeding without it")
            got_lock = True
        if not got_lock:
            _record_heartbeat(_LOOP_NAME)
            await asyncio.sleep(REAP_INTERVAL_SECONDS)
            continue
        try:
            await _reap_tick()
        except Exception as e:
            logger.error(f"Driver claim reaper loop error: {e}", exc_info=True)
        _record_heartbeat(_LOOP_NAME)
        delta = REAP_INTERVAL_SECONDS * 0.1
        await asyncio.sleep(REAP_INTERVAL_SECONDS + random.uniform(-delta, delta))
