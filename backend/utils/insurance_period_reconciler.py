"""Self-heal missed/incorrect driver insurance-period opens (WS-12 §3, C55).

``record_period_transition`` (``utils/insurance_periods.py``) is a deliberate,
documented exception to the no-silent-swallow rule: a DB blip during the
dispatch claim/offer loop or the accept/arrive/start path must never block
the ride, so a failed audit write is logged at ERROR + counted in
``spinr_insurance_period_write_failed_total`` rather than raised. That is the
correct call for the ride state machine, but it leaves a real gap: a dropped
write is a permanent regulatory audit hole for that driver/ride until
*something* notices and corrects it. ``utils/stale_p3_closer.py`` already
covers the Period-3 half of that gap (closing spans that outlived their
ride); this loop covers the open side for every period, by convergence
rather than by watching any one call site.

Every 10 minutes this loop derives the *expected* insurance period for two
driver populations, straight from CLAUDE.md's period-derivation table:

  * a driver named on a pending ``ride_offers`` row, or linked via
    ``rides.driver_id`` to a ride in ``driver_assigned`` / ``driver_accepted``
    / ``driver_arrived`` -> Period 2 (en route), tied to that ride.
  * a driver linked to an ``in_progress`` ride -> Period 3 (passenger
    aboard), tied to that ride.
  * a driver with ``is_online = true`` and neither of the above -> Period 1
    (available, no ride).

...and compares each against that driver's currently-open
``driver_insurance_periods`` row (partial-unique on
``driver_id WHERE ended_at IS NULL``, migration 64). A missing or
wrong-period/wrong-ride open row for an *active* driver (Period 2/3 above) is
corrected unconditionally — this only ever adds or repairs audit coverage,
never removes it, so there is no downside to always self-healing it.

Downgrading a driver from an open Period 2/3 row down to Period 1 is a
different risk class: it is only safe if this loop's ride/offer scan really
saw every active ride and offer. A batch limit or a transient read miss on
those two queries could otherwise make a driver whose ride never appeared in
this tick's own snapshot look "idle" and get a live commercial-coverage
window closed underneath them — an active-ride downgrade error, not merely a
missed-audit-row error. Per CLAUDE.md's additive-over-destructive release
gate this is therefore gated behind the ``insurance_period_reconciler_
downgrade_enabled`` app_settings flag (default OFF, mirroring
``stale_p3_autoclose_enabled``'s alert-first pattern): a mismatch here is
*always* logged + metriced, and only actually corrected when an operator has
turned the flag on.

Scope guarantee, same as ``stale_p3_closer``: this loop only ever calls
``record_period_transition`` (the sanctioned close-open RPC) to add a new
open row; it never mutates or deletes an existing row directly, so the
``driver_insurance_periods`` append-only contract (migration 64's trigger)
is untouched.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from .insurance_periods import record_period_transition
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set_nx
except ImportError:  # pragma: no cover - dual import path
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.insurance_periods import record_period_transition  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 600  # 10 min, per WS-12 §3.
_LOCK_KEY = "spinr:lock:insurance_period_reconciler"
_LOCK_TTL_SECONDS = 590  # just under the interval, re-acquired each tick

# The active-ride/offer scans must not truncate — a driver whose ride falls
# off the edge of a small page would look "idle" and risk the downgrade path
# below. High limit, not a small page size, is the deliberate safety choice
# here (see module docstring).
ACTIVE_SCAN_LIMIT = 1000
# Lower stakes: worst case here is a missed Period-1 self-heal, not a
# wrongful downgrade, so a bounded page is an acceptable v1 limitation.
ONLINE_DRIVER_LIMIT = 500

_ASSIGNED_RIDE_STATUSES = ["driver_assigned", "driver_accepted", "driver_arrived"]

# (period, ride_id) expected for a driver_id.
_Expected = Tuple[int, Optional[str]]


async def _pending_offer_candidates() -> Dict[str, _Expected]:
    rows = (
        await db_supabase.get_rows(
            "ride_offers",
            {"status": "pending"},
            columns="driver_id,ride_id",
            limit=ACTIVE_SCAN_LIMIT,
        )
        or []
    )
    return {r["driver_id"]: (2, r.get("ride_id")) for r in rows if r.get("driver_id")}


async def _assigned_ride_candidates() -> Dict[str, _Expected]:
    rows = (
        await db_supabase.get_rows(
            "rides",
            {"status": {"$in": _ASSIGNED_RIDE_STATUSES}},
            columns="id,driver_id",
            limit=ACTIVE_SCAN_LIMIT,
        )
        or []
    )
    return {r["driver_id"]: (2, r.get("id")) for r in rows if r.get("driver_id")}


async def _in_progress_candidates() -> Dict[str, _Expected]:
    rows = (
        await db_supabase.get_rows(
            "rides",
            {"status": "in_progress"},
            columns="id,driver_id",
            limit=ACTIVE_SCAN_LIMIT,
        )
        or []
    )
    return {r["driver_id"]: (3, r.get("id")) for r in rows if r.get("driver_id")}


async def _online_idle_candidates(exclude: set) -> Dict[str, _Expected]:
    """Online drivers not already accounted for by an active ride/offer above."""
    rows = (
        await db_supabase.get_rows(
            "drivers",
            {"is_online": True},
            columns="id",
            limit=ONLINE_DRIVER_LIMIT,
        )
        or []
    )
    return {r["id"]: (1, None) for r in rows if r.get("id") and r["id"] not in exclude}


async def _open_rows_for_drivers(driver_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    ids = sorted(set(driver_ids))
    if not ids:
        return {}
    rows = (
        await db_supabase.get_rows(
            "driver_insurance_periods",
            {"driver_id": {"$in": ids}, "ended_at": None},
            columns="id,driver_id,period,ride_id",
            limit=len(ids),
        )
        or []
    )
    return {r["driver_id"]: r for r in rows if r.get("driver_id")}


def _mismatched(open_row: Optional[Dict[str, Any]], expected_period: int, expected_ride_id: Optional[str]) -> bool:
    if open_row is None:
        return True
    if open_row.get("period") != expected_period:
        return True
    if expected_period in (2, 3) and open_row.get("ride_id") != expected_ride_id:
        return True
    return False


async def _heal(driver_id: str, period: int, ride_id: Optional[str], action: str, reason: str) -> None:
    logger.warning(
        "insurance_period_reconciler: self-healing driver_id=%s -> period=%s ride_id=%s action=%s reason=%s",
        driver_id,
        period,
        ride_id,
        action,
        reason,
    )
    await record_period_transition(driver_id, period, ride_id=ride_id)
    _metric_inc("spinr_insurance_period_reconciled_total", {"action": action})


async def _tick() -> Dict[str, int]:
    """One convergence pass. Returns counters for tests/observability."""
    settings = await get_app_settings() or {}
    downgrade_enabled = bool(settings.get("insurance_period_reconciler_downgrade_enabled", False))

    active_expected: Dict[str, _Expected] = {}
    # Priority, low to high: a pending offer is provisional evidence: an
    # actual ride assignment/acceptance/arrival or an in_progress trip is
    # stronger, more current evidence and should win if both are present
    # for the same driver (e.g. a stale offer row not yet reaped).
    active_expected.update(await _pending_offer_candidates())
    active_expected.update(await _assigned_ride_candidates())
    active_expected.update(await _in_progress_candidates())

    idle_expected = await _online_idle_candidates(exclude=set(active_expected.keys()))

    open_by_driver = await _open_rows_for_drivers(list(active_expected.keys()) + list(idle_expected.keys()))

    result = {"opened": 0, "corrected": 0, "downgrade_alerted": 0}

    for driver_id, (period, ride_id) in active_expected.items():
        try:
            open_row = open_by_driver.get(driver_id)
            if not _mismatched(open_row, period, ride_id):
                continue
            action = "opened" if open_row is None else "corrected"
            await _heal(driver_id, period, ride_id, action, reason="active_ride_or_offer")
            result[action] += 1
        except Exception:
            logger.error(
                "insurance_period_reconciler: active-driver heal failed driver_id=%s (skipped)",
                driver_id,
                exc_info=True,
            )

    for driver_id, (period, _ride_id) in idle_expected.items():
        try:
            open_row = open_by_driver.get(driver_id)
            if open_row is None:
                await _heal(driver_id, period, None, "opened", reason="online_no_open_row")
                result["opened"] += 1
                continue
            if open_row.get("period") == period:
                continue
            # Downgrade candidate (or an out-of-range value): alert always,
            # correct only when the operator has opted in. See module
            # docstring for why this half is gated and the active half above
            # is not.
            logger.warning(
                "insurance_period_reconciler: driver_id=%s online with no active ride/offer "
                "but open period=%s (expected 1) — %s",
                driver_id,
                open_row.get("period"),
                "correcting"
                if downgrade_enabled
                else "alert-only (insurance_period_reconciler_downgrade_enabled is off)",
            )
            _metric_inc(
                "spinr_insurance_period_reconciler_downgrade_candidate_total",
                {"from_period": str(open_row.get("period"))},
            )
            result["downgrade_alerted"] += 1
            if downgrade_enabled:
                await _heal(driver_id, period, None, "corrected", reason="online_no_ride_downgrade")
                result["corrected"] += 1
        except Exception:
            logger.error(
                "insurance_period_reconciler: idle-driver heal failed driver_id=%s (skipped)",
                driver_id,
                exc_info=True,
            )

    return result


async def insurance_period_reconciler_loop() -> None:
    """Self-heal missed/incorrect driver insurance-period opens every 10 minutes."""
    while True:
        try:
            got_lock = await redis_set_nx(_LOCK_KEY, "1", ttl=_LOCK_TTL_SECONDS)
            if got_lock:
                await _tick()
        except Exception:
            logger.error("insurance_period_reconciler tick failed", exc_info=True)
        # Heartbeat every iteration, lock or not — the watchdog is per-replica.
        _record_heartbeat("insurance_period_reconciler (10min)")
        await asyncio.sleep(INTERVAL_SECONDS)
