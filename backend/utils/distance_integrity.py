"""Writer for ride distance/route integrity signals (detection only).

A single, never-fatal entry point used by the booking guard, the completion
milestone check, and the nightly reconciliation loop to append to
``ride_distance_integrity_events`` (migration 246). These are DETECTION signals
for ops — they never change a fare or a displayed distance, and a write failure
must never surface to a rider or block a booking/completion, so every error is
logged and swallowed.

PIPEDA: ``details`` must carry only distances/ratios/ids — never coordinates,
addresses, or other PII.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - dual import path
    import db_supabase  # type: ignore

logger = logging.getLogger(__name__)

VALID_KINDS = frozenset(
    {
        "booked_distance_suspect",
        "coverage_fallback",
        "measured_below_straight_line",
        "trip_window_compressed",
        "quote_measured_divergence",
    }
)


async def record_integrity_event(
    ride_id: str,
    kind: str,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Append one integrity signal. Returns True on write, False on any failure.

    Never raises: a detection-signal write must not fail or slow the caller
    (booking, completion, reconciliation). ``kind`` must be one of
    ``VALID_KINDS`` (mirrors the table CHECK); an unknown kind is refused
    locally so a typo can't wedge on the DB constraint.
    """
    if kind not in VALID_KINDS:
        logger.error("refusing to record unknown integrity event kind=%s (ride_id=%s)", kind, ride_id)
        return False
    try:
        await db_supabase.insert_one(
            "ride_distance_integrity_events",
            {"ride_id": ride_id, "kind": kind, "details": details or {}},
        )
        return True
    except Exception:
        logger.error("integrity event write failed (kind=%s ride_id=%s)", kind, ride_id, exc_info=True)
        return False
