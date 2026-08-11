"""Orphaned driver-claim reaper (C3).

Dispatch claims a driver (is_available=false via claim_driver_atomic) and only
then inserts the ride_offers row + schedules the offer-timeout task. A crash or
restart in that window leaves the driver is_available=false with no offer and no
timeout handler — silently dropped from dispatch. The stuck-ride sweeper
recovers the ride, not the driver flag.

This loop releases such orphans: a driver that is online, unavailable, was
claimed more than RECLAIM_THRESHOLD_SECONDS ago, and has NEITHER a pending offer
NOR an active ride. The age threshold (well past the ~15s offer window) plus the
dedicated availability_claimed_at stamp (which heartbeats never bump) guarantee
we never race a legitimate in-flight claim.

Replay-safety (CLAUDE.md, Background loops):
  - Redis leader lock (best-effort throttle).
  - The release (set_driver_available True) is idempotent and clamps to
    is_online, so a double-release across replicas is harmless.
  - A driver with a pending offer or active ride is skipped, so a legitimately
    busy driver is never reaped.
"""

import asyncio
import logging
import os
import random
import socket
from datetime import datetime, timedelta, timezone

try:
    from ..db import db
    from ..repositories.driver_repo import set_driver_available
    from .datetime_utils import parse_iso_utc
    from .redis_client import redis_set_nx
except ImportError:
    from db import db  # type: ignore
    from repositories.driver_repo import set_driver_available  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
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
_ACTIVE_RIDE_STATUSES = ["driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def _has_pending_offer(driver_id: str) -> bool:
    """True iff the driver has a pending offer — i.e. is mid-dispatch and busy.

    Offer expiry — including a stale offer whose in-process timeout task died —
    is owned by ``offer_expiry_reaper``, which expires it via
    ``process_expired_offer`` with the full miss-streak / acceptance-rate /
    insurance-period / re-dispatch side-effects. The claim reaper therefore no
    longer expires offers itself: a bare ``pending -> expired`` flip here would
    win the atomic claim WITHOUT those side-effects (e.g. leaving no insurance
    Period-1 audit row and not re-dispatching the ride). It only checks whether
    an offer exists so it never releases a driver who is mid-offer. A stale
    pending offer is transient — the offer reaper clears it (and releases the
    driver with correct accounting) within one interval.
    """
    rows = await db.get_rows(
        "ride_offers",
        {"driver_id": driver_id, "status": "pending"},
        limit=1,
        columns="id",
    )
    return bool(rows)


async def _has_active_ride(driver_id: str) -> bool:
    rows = await db.get_rows(
        "rides",
        {"driver_id": driver_id, "status": {"$in": _ACTIVE_RIDE_STATUSES}},
        limit=1,
        columns="id",
    )
    return bool(rows)


async def _reap_tick() -> None:
    """Find and release drivers whose dispatch claim never produced an offer."""
    try:
        drivers = await db.get_rows(
            "drivers",
            {"is_online": True, "is_available": False},
            limit=200,
            columns="id,user_id,is_online,is_available,availability_claimed_at",
        )
    except Exception as e:
        logger.error("[claim-reaper] failed to fetch candidate drivers: %s", e, exc_info=True)
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=RECLAIM_THRESHOLD_SECONDS)

    for driver in drivers:
        claimed_at = parse_iso_utc(driver.get("availability_claimed_at"))
        # No stamp, or claimed too recently → not a confirmed orphan (a NULL
        # stamp means the unavailability didn't come from a dispatch claim).
        if claimed_at is None or claimed_at > cutoff:
            continue

        driver_id = driver["id"]
        # Legitimately busy? A pending offer or active ride → leave alone.
        try:
            if await _has_pending_offer(driver_id) or await _has_active_ride(driver_id):
                continue
        except Exception as e:
            # Isolated per-driver, matching the release-failure guard below —
            # one driver's lookup failure must not abort the rest of this
            # tick's batch (an unguarded raise here previously propagated
            # out of _reap_tick entirely, skipping every other already-
            # fetched, otherwise-reapable driver too).
            logger.error("[claim-reaper] offer/ride check failed for driver %s: %s", driver_id, e, exc_info=True)
            continue

        try:
            released = await set_driver_available(driver_id, available=True)
        except Exception as e:
            logger.error("[claim-reaper] release failed for driver %s: %s", driver_id, e, exc_info=True)
            continue
        if isinstance(released, dict) and released.get("is_available"):
            logger.warning(
                "[claim-reaper] released orphaned claim for driver %s (claimed %s, no offer/ride)",
                driver_id,
                driver.get("availability_claimed_at"),
            )


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
