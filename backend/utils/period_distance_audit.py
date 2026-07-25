"""Per-insurance-period distance audit (GPS-measured, NOT billing).

Records how far the driver actually drove in each insurance period, from the
driver's GPS coordinates + timestamps, into the append-only
``driver_period_distances`` table (migration 249) for the SGI / Saskatchewan
Transportation Act commercial-coverage audit.

This is deliberately independent of billing: the rider is charged the road
distance quoted before the ride (fare_distance_basis='road', quote-locked). The
figures here are measured driven distance and must never feed a fare.

Insurance-period ↔ breadcrumb-phase mapping (see CLAUDE.md, migration 64):
    Period 2 (en route, TNC primary)      = phase ``navigating_to_pickup``
    Period 3 (passenger, TNC primary full) = phase ``trip_in_progress``
Period 0/1 are not ride-scoped and are recorded elsewhere.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

try:
    from .. import db_supabase
except ImportError:  # python -m backend.server vs top-level
    import db_supabase  # type: ignore

logger = logging.getLogger(__name__)


async def record_ride_period_distances(
    *,
    driver_id: str,
    ride_id: str,
    phases: Iterable[Dict[str, Any]],
) -> int:
    """Append one audit row per insurance period for a completed ride.

    ``phases`` is an iterable of ``{"period", "distance_km", "started_at",
    "ended_at", "source"?}``. Returns the number of rows written.

    Contract:
      * Best-effort — an audit-write failure must NEVER fail ride settlement.
        Failures are logged at ERROR (never silently swallowed), and the driver
        still gets paid.
      * Replay-safe — periods already recorded for this ride are skipped, and the
        ``(ride_id, period)`` unique index is the atomic backstop against a race.
      * PII-safe — only ids and distances are logged, never coordinates.
    """
    if not driver_id or not ride_id:
        logger.error("period-distance audit: missing driver_id/ride_id (ride=%s)", ride_id)
        return 0

    try:
        existing = await db_supabase.get_rows("driver_period_distances", {"ride_id": ride_id})
        seen = {int(r["period"]) for r in (existing or []) if r.get("period") is not None}
    except Exception:
        # A read failure must not block the write attempt; the unique index still
        # guards against a duplicate. Log loudly — this is a regulatory audit.
        logger.error("period-distance audit: pre-check read failed (ride=%s)", ride_id, exc_info=True)
        seen = set()

    written = 0
    for phase in phases:
        period = _coerce_period(phase.get("period"))
        if period is None or period in seen:
            continue
        distance_km = _coerce_distance(phase.get("distance_km"))
        if distance_km is None:
            continue
        row = {
            "driver_id": driver_id,
            "ride_id": ride_id,
            "period": period,
            "distance_km": distance_km,
            "started_at": phase.get("started_at"),
            "ended_at": phase.get("ended_at"),
            "source": phase.get("source") or "gps_measured",
        }
        try:
            await db_supabase.insert_one("driver_period_distances", row)
            written += 1
            seen.add(period)
        except Exception:
            # Includes the unique-violation on a concurrent re-run — the row is
            # already recorded, so this is benign, but we surface it at error for
            # the audit trail rather than hiding a real DB failure.
            logger.error(
                "period-distance audit: insert failed (ride=%s period=%s)",
                ride_id,
                period,
                exc_info=True,
            )
    if written:
        logger.info("period-distance audit: recorded %s period rows (ride=%s)", written, ride_id)
    return written


def _coerce_period(value: Any) -> Optional[int]:
    try:
        period = int(value)
    except (TypeError, ValueError):
        return None
    return period if period in (0, 1, 2, 3) else None


def _coerce_distance(value: Any) -> Optional[float]:
    try:
        km = round(float(value), 3)
    except (TypeError, ValueError):
        return None
    return km if km >= 0 else None
