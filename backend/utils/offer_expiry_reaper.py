"""Durable, restart-safe offer-expiry reaper (issue #9).

Offer/search timeouts fire from an in-process asyncio task; a pod restart or
deploy drops every in-flight timer, so an offer counting down at that moment
never fires its expire / miss-streak / acceptance-rate / insurance-period /
re-dispatch side-effects. The ride-level stuck-ride sweeper recovers the ride
but not the per-offer/driver accounting.

This loop is the durable backstop: it finds pending ride_offers whose persisted
``expires_at`` (migration 224) has passed and runs the SAME idempotent
``process_expired_offer`` the in-process handler uses, then re-dispatches any
affected ride that is still searching.

Replay-safety (CLAUDE.md, Background loops):
  - ``process_expired_offer``'s atomic ``pending -> expired`` claim is the true
    gate: across replicas — and against the in-process handler — only one
    actor's claim wins per offer, so side-effects run exactly once.
  - A Redis leader lock throttles how often a replica scans (best-effort).

Layering note: this utils module imports two functions from
``routes.rides.matching`` (``process_expired_offer`` for the shared per-offer
logic and ``match_driver_to_ride`` for re-dispatch). That is a deliberate
inversion; there is no import cycle (nothing in that chain imports this reaper —
only core/lifespan does, to register the loop).
"""

import asyncio
import logging
import os
import random
import socket
from datetime import datetime, timezone

try:
    from ..db import db
    from ..routes.rides.matching import match_driver_to_ride, process_expired_offer
    from ..settings_loader import get_app_settings
    from .redis_client import redis_set_nx
except ImportError:
    from db import db  # type: ignore
    from routes.rides.matching import match_driver_to_ride, process_expired_offer  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover
    try:
        from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    except ImportError:

        def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
            pass


logger = logging.getLogger(__name__)

REAP_INTERVAL_SECONDS = 10
_LOOP_NAME = "offer_expiry_reaper (10s)"
_CANDIDATE_LIMIT = 200


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def _reap_tick() -> None:
    """Expire pending offers past their deadline and re-dispatch affected rides.

    Idempotent by construction: every expire goes through process_expired_offer's
    atomic claim, so running on multiple replicas (or racing the in-process
    handler) processes each offer at most once.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        expired = await db.get_rows(
            "ride_offers",
            {"status": "pending", "expires_at": {"$lt": now_iso}},
            order="expires_at",  # oldest-overdue first, so a truncated batch reaps the most stale
            limit=_CANDIDATE_LIMIT,
            columns="ride_id,driver_id",
        )
    except Exception as e:
        logger.error("[offer-reaper] failed to fetch expired offers: %s", e, exc_info=True)
        return
    if not expired:
        return
    if len(expired) >= _CANDIDATE_LIMIT:
        # More than one tick's worth of overdue offers — a sustained backlog
        # (frequent restarts, or the in-process path is stalling). The rest are
        # picked up next tick; log so the backlog isn't silently invisible.
        logger.warning(
            "[offer-reaper] hit the %d-offer scan cap — overdue-offer backlog; remainder deferred to next tick",
            _CANDIDATE_LIMIT,
        )

    try:
        settings = await get_app_settings()
        miss_threshold = int(settings.get("auto_offline_miss_threshold", 3))
    except Exception as e:
        logger.error("[offer-expiry-reaper] settings fetch failed, defaulting miss_threshold=3: %s", e, exc_info=True)
        miss_threshold = 3

    results = await asyncio.gather(
        *(process_expired_offer(o["ride_id"], o["driver_id"], miss_threshold) for o in expired),
        return_exceptions=True,
    )
    won = sum(1 for r in results if r is True)
    if won:
        # These are offers whose in-process timer was lost (restart/crash) — the
        # backstop did the work. A steady non-zero count points at frequent
        # restarts or a stuck in-process path.
        logger.warning("[offer-reaper] expired %d orphaned offer(s) via the durable backstop", won)

    # Re-dispatch each affected ride still in 'searching' (its offers just
    # expired). match_driver_to_ride is the recovery shell; the status guard
    # avoids re-dispatching a ride accepted/cancelled between dispatch and now.
    for ride_id in {o["ride_id"] for o in expired}:
        try:
            ride = await db.get_rows("rides", {"id": ride_id}, limit=1, columns="status")
            if ride and ride[0].get("status") == "searching":
                await match_driver_to_ride(ride_id)
        except Exception as e:
            logger.error("[offer-reaper] re-dispatch failed for ride %s: %s", ride_id, e, exc_info=True)


async def offer_expiry_reaper_loop() -> None:
    """Background loop: durable backstop for offer-timeout timers lost on restart."""
    logger.info(f"Offer expiry reaper started (interval={REAP_INTERVAL_SECONDS}s)")
    while True:
        # TTL must be SHORTER than the minimum possible sleep below (interval *
        # 0.9, worst-case jitter), or the pod that ran the last tick wakes to
        # find its OWN key still alive, fails SET NX, and sleeps another full
        # interval — halving the documented cadence. `interval * 2` doesn't
        # (2x > 0.9x). Matches ledger_projection.py's `_LOCK_TTL_SECONDS`
        # formula (ACTION_ITEMS B21): 0.05 headroom under the 0.9 floor.
        lock_ttl = int(REAP_INTERVAL_SECONDS * 0.85)
        try:
            got_lock = await redis_set_nx("spinr:offer_expiry_reaper:lock", _pod_id(), lock_ttl)
        except Exception as lock_err:
            # redis_set_nx now raises on a real (Redis-configured-but-
            # unavailable) error instead of silently falling back per-replica
            # (2026-08-11 P1 fix). This is a durable backstop loop — going
            # dark on a Redis blip defeats its whole purpose — so proceed
            # with the tick rather than skip it.
            logger.error(f"offer_expiry_reaper: leader lock unavailable ({lock_err}), proceeding without it")
            got_lock = True
        if not got_lock:
            _record_heartbeat(_LOOP_NAME)
            await asyncio.sleep(REAP_INTERVAL_SECONDS)
            continue
        try:
            await _reap_tick()
        except Exception as e:
            logger.error(f"Offer expiry reaper loop error: {e}", exc_info=True)
        _record_heartbeat(_LOOP_NAME)
        delta = REAP_INTERVAL_SECONDS * 0.1
        await asyncio.sleep(REAP_INTERVAL_SECONDS + random.uniform(-delta, delta))
