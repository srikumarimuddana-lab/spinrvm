"""Nightly quote-vs-measured distance reconciliation.

The haversine fare bug survived because nothing ever compared the numbers. This
loop closes that gap: once a day it compares each completed ride's QUOTED
distance (what the fare was priced on) against its MEASURED distance (from GPS /
reconstruction), emits a ratio histogram, opens an integrity event for per-ride
outliers, and — the point of the whole thing — logs at ERROR (→ Sentry) when the
day's *aggregate* ratio is systematically off 1.0. A platform-wide bias like
"every ride measures 2.6× its quote" then trips an alert within a day instead of
surviving to a screenshot.

Detection only: it never changes a fare or a displayed distance.

Replay-safe: runs on one replica via a Redis leader lock, and each ride is
claimed via ``rides.distance_reconciled_at`` (migration 247) so it is evaluated
exactly once even across restarts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from .. import db_supabase
    from ..utils import metrics
    from ..utils.distance_integrity import record_integrity_event
    from ..utils.redis_client import redis_set_nx
except ImportError:  # pragma: no cover - dual import path
    import db_supabase  # type: ignore
    from utils import metrics  # type: ignore
    from utils.distance_integrity import record_integrity_event  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

# Per-ride outlier band: outside [0.6, 1.8]× the quote, the measured distance is
# far enough from what was priced to warrant a per-ride integrity event.
RATIO_LOW = 0.6
RATIO_HIGH = 1.8

# Systematic-bias alert: with at least this many samples, a mean ratio more than
# BIAS_THRESHOLD off 1.0 is platform-wide skew, not ride-level noise.
AGG_MIN_SAMPLES = 20
BIAS_THRESHOLD = 0.15

RATIO_BUCKETS = (0.5, 0.7, 0.85, 0.95, 1.05, 1.2, 1.5, 2.0, 3.0, 5.0)

# Give completion + the deferred route finalizer time to settle the measured
# distance before comparing (skip rides completed within this window).
SETTLE_DELAY_HOURS = 2
BATCH_LIMIT = 500

_RUN_HOUR_UTC = 4  # 04:00 UTC — after stripe_reconcile (02:00) and retention (03:00)
_LOCK_KEY = "spinr:lock:distance_reconcile"
_LOCK_TTL_SECONDS = 23 * 3600


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _seconds_until(target_hour_utc: int) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=target_hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_reconciliation(
    rides: List[Dict[str, Any]],
) -> Tuple[List[float], List[Tuple[str, Dict[str, Any]]], Dict[str, Any]]:
    """Pure: (ratios, divergences, aggregate) for a batch of completed rides.

    ratios       — measured/quoted for every ride with usable positive distances.
    divergences  — (ride_id, details) for ratios outside the per-ride band.
    aggregate    — {count, mean_ratio, biased}; ``biased`` is True only with
                   enough samples AND a mean more than BIAS_THRESHOLD off 1.0.
    """
    ratios: List[float] = []
    divergences: List[Tuple[str, Dict[str, Any]]] = []
    for ride in rides:
        ride_id = str(ride.get("id") or "")
        quoted = _num(ride.get("planned_distance_km")) or _num(ride.get("distance_km"))
        measured = _num(ride.get("actual_distance_km"))
        if not quoted or not measured or quoted <= 0 or measured <= 0:
            continue
        ratio = measured / quoted
        ratios.append(ratio)
        if ratio < RATIO_LOW or ratio > RATIO_HIGH:
            basis = (((ride.get("ride_metrics") or {}).get("phases") or {}).get("trip_in_progress") or {}).get(
                "distance_basis"
            )
            divergences.append(
                (
                    ride_id,
                    {
                        "quoted_km": round(quoted, 3),
                        "measured_km": round(measured, 3),
                        "ratio": round(ratio, 3),
                        "measured_distance_basis": basis,
                    },
                )
            )
    mean_ratio = round(sum(ratios) / len(ratios), 4) if ratios else None
    biased = mean_ratio is not None and len(ratios) >= AGG_MIN_SAMPLES and abs(mean_ratio - 1.0) > BIAS_THRESHOLD
    return ratios, divergences, {"count": len(ratios), "mean_ratio": mean_ratio, "biased": biased}


async def _run_reconciliation_tick() -> None:
    now = datetime.now(timezone.utc)
    settle_cutoff = (now - timedelta(hours=SETTLE_DELAY_HOURS)).isoformat()
    rides = await db_supabase.get_rows(
        "rides",
        {"status": "completed", "distance_reconciled_at": None, "ride_completed_at": {"$lte": settle_cutoff}},
        order="ride_completed_at",
        limit=BATCH_LIMIT,
    )
    if not rides:
        return

    ratios, divergences, aggregate = evaluate_reconciliation(rides)
    for ratio in ratios:
        metrics.observe("spinr_fare_quote_vs_measured_ratio", ratio, None, RATIO_BUCKETS)
    for ride_id, details in divergences:
        metrics.inc("spinr_fare_quote_measured_divergence_total")
        await record_integrity_event(ride_id, "quote_measured_divergence", details)

    if aggregate["biased"]:
        # ERROR → Sentry: platform-wide under/over-charge, the class of bug the
        # haversine fare defect was. Ratio only — no PII.
        logger.error(
            "distance reconciliation: SYSTEMATIC quote-vs-measured bias mean_ratio=%s over %d rides",
            aggregate["mean_ratio"],
            aggregate["count"],
        )

    # Claim the batch so the next run skips it (single UPDATE ... WHERE id IN).
    ids = [str(r.get("id")) for r in rides if r.get("id")]
    if ids:
        await db_supabase.update_one(
            "rides",
            {"id": {"$in": ids}},
            {"distance_reconciled_at": now.isoformat()},
        )
    logger.info(
        "distance reconciliation tick: %d rides, %d divergences, mean_ratio=%s",
        len(rides),
        len(divergences),
        aggregate["mean_ratio"],
    )


async def distance_reconciliation_loop(target_hour_utc: int = _RUN_HOUR_UTC) -> None:
    """Daily loop — sleeps until target_hour_utc then runs one reconciliation tick.

    Redis SET NX EX leader lock keeps it to one replica per ~23 h window.
    """
    first_sleep = _seconds_until(target_hour_utc)
    logger.info("distance_reconciliation_loop: first run in %.0fs (target %02d:00 UTC)", first_sleep, target_hour_utc)
    await asyncio.sleep(first_sleep)

    while True:
        try:
            if await redis_set_nx(_LOCK_KEY, _pod_id(), _LOCK_TTL_SECONDS):
                await _run_reconciliation_tick()
            else:
                logger.info("distance_reconciliation_loop: another replica holds the lock, skipping")
        except Exception:
            logger.error("distance_reconciliation_loop: tick raised", exc_info=True)
        await asyncio.sleep(86400)
