"""Detect (and optionally close) stale open Period-3 insurance spans.

A driver's Period-3 span (passenger aboard — full TNC commercial coverage)
opens when the trip starts and normally closes when the completion path fires
``record_period_transition``. Two failure classes leave it open forever:

  A. ``ride_terminal`` — the ride reached completed/cancelled but the
     compliance-grade period write failed (it swallows errors by design,
     see utils/insurance_periods.py). Unambiguous: the ride row itself
     proves when Period 3 ended.
  B. ``ride_abandoned`` — the ride is still ``in_progress`` but the app
     died mid-trip and nobody ever completed it. Here the last accepted
     breadcrumb is the only evidence of when coverage exposure ended.

An open P3 span misstates commercial-insurance exposure to SGI, so this
loop ALWAYS alerts (ERROR log + metric) when it finds one. Actually
closing the span is gated behind ``stale_p3_autoclose_enabled`` (default
off — alert-first) because class B has a dangerous edge: SPR-PE7TTB showed
a trip can genuinely continue after recording dies. The thresholds are
therefore far beyond any plausible Regina trip (12 h in_progress, 6 h
since last evidence) before class B is even reported.

Scope guarantee: this loop touches ONLY ``driver_insurance_periods``
``ended_at`` on the open row — the sanctioned closing mechanism, same
column the transition RPC writes. It never mutates ride state, fares, or
closed period rows (append-only rule intact), and it never opens a
synthetic row: "no open row" is a valid state that the next go_online
transition handles.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from .datetime_utils import parse_iso_utc
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set_nx
    from .route_gap_monitor import _latest_capture_time
except ImportError:  # pragma: no cover - dual import path
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore
    from utils.route_gap_monitor import _latest_capture_time  # type: ignore

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 900  # 15 min — stale spans are hours old; no rush, low load.
_LOCK_KEY = "spinr:lock:stale_p3_closer"
_LOCK_TTL_SECONDS = 890  # just under the interval, re-acquired each tick
BATCH_LIMIT = 50

# Class A: ride already terminal — span must close within this grace before
# it is called stale (covers the normal fire-and-forget write racing us).
TERMINAL_GRACE_HOURS = 1
# Class B: ride still in_progress. BOTH must hold before we even alert —
# far beyond any real Regina trip, because SPR-PE7TTB proved a live trip
# can outlast its own GPS recording.
ABANDONED_RIDE_HOURS = 12
ABANDONED_EVIDENCE_HOURS = 6

_TERMINAL_STATUSES = ("completed", "cancelled")


async def _open_p3_spans() -> List[Dict[str, Any]]:
    return (
        await db_supabase.get_rows(
            "driver_insurance_periods",
            {"period": 3, "ended_at": None},
            order="started_at",
            desc=False,
            limit=BATCH_LIMIT,
            columns="id,driver_id,ride_id,started_at",
        )
        or []
    )


async def _close_span(span: Dict[str, Any], ended_at: datetime, reason: str) -> bool:
    """Close one open span at an evidence-based timestamp.

    Conditional on ``ended_at`` still being NULL so a concurrent transition
    (driver's app came back, admin force-completed) always wins the race —
    zero rows updated means someone else closed it first.
    """
    supabase = db_supabase.supabase
    if supabase is None:
        return False

    def _update() -> int:
        res = (
            supabase.table("driver_insurance_periods")
            .update({"ended_at": ended_at.isoformat()})
            .eq("id", span["id"])
            .is_("ended_at", "null")
            .execute()
        )
        return len(getattr(res, "data", None) or [])

    closed = await db_supabase.run_sync(_update, retry_policy="idempotent_write")
    if closed:
        logger.warning(
            "stale_p3_closer: closed span id=%s driver=%s ride=%s ended_at=%s reason=%s",
            span.get("id"),
            span.get("driver_id"),
            span.get("ride_id"),
            ended_at.isoformat(),
            reason,
        )
        _metric_inc("spinr_insurance_stale_p3_closed_total", {"class": reason})
    return bool(closed)


def _ride_end_time(ride: Dict[str, Any]) -> Optional[datetime]:
    for field in ("ride_completed_at", "completed_at", "cancelled_at", "updated_at"):
        parsed = parse_iso_utc(ride.get(field))
        if parsed is not None:
            return parsed
    return None


async def _classify(span: Dict[str, Any], now: datetime) -> Optional[tuple[str, datetime]]:
    """Return (class, evidence_end_time) when the span is stale, else None."""
    ride_id = span.get("ride_id")
    started_at = parse_iso_utc(span.get("started_at"))
    if not ride_id or started_at is None:
        # P3 without a ride_id violates the state-machine contract — surface it.
        logger.error(
            "stale_p3_closer: open P3 span with no ride_id/started_at (span=%s driver=%s) — contract violation",
            span.get("id"),
            span.get("driver_id"),
        )
        return None

    rows = await db_supabase.get_rows("rides", {"id": ride_id}, limit=1)
    ride = rows[0] if rows else None
    if not ride:
        logger.error(
            "stale_p3_closer: open P3 span references missing ride %s (span=%s)",
            ride_id,
            span.get("id"),
        )
        return None

    status = ride.get("status")
    if status in _TERMINAL_STATUSES:
        end_time = _ride_end_time(ride)
        if end_time is None:
            end_time = now
        if (now - end_time) < timedelta(hours=TERMINAL_GRACE_HOURS):
            return None  # normal completion write may still be in flight
        return ("ride_terminal", end_time)

    if status == "in_progress":
        if (now - started_at) < timedelta(hours=ABANDONED_RIDE_HOURS):
            return None
        last_evidence = await _latest_capture_time(ride_id)
        effective = last_evidence or started_at
        if (now - effective) < timedelta(hours=ABANDONED_EVIDENCE_HOURS):
            return None  # breadcrumbs still arriving — trip may genuinely be alive
        return ("ride_abandoned", effective)

    # Any other status (searching / driver_assigned / …) with an open P3 span
    # is a contract violation worth seeing, but there is no safe close time.
    logger.error(
        "stale_p3_closer: open P3 span but ride %s is in status=%r (span=%s) — contract violation",
        ride_id,
        status,
        span.get("id"),
    )
    return None


async def _alert_orphaned_in_progress_rides(now: datetime) -> int:
    """Alert on in_progress rides whose driver has NO open period row at all.

    This is the state a class-B autoclose leaves behind when the abandoned
    ride never gets completed: the span is closed, the ride row still says
    in_progress, and nothing else watches that combination — an insurance
    classification hole that must stay loudly visible every tick until an
    admin resolves the ride. Alert-only; never mutates anything."""
    cutoff = (now - timedelta(hours=ABANDONED_RIDE_HOURS)).isoformat()
    rides = (
        await db_supabase.get_rows(
            "rides",
            {"status": "in_progress", "ride_started_at": {"$lt": cutoff}},
            columns="id,driver_id,ride_started_at",
            order="ride_started_at",
            desc=False,
            limit=BATCH_LIMIT,
        )
        or []
    )
    orphaned = 0
    for ride in rides:
        driver_id = ride.get("driver_id")
        if not driver_id:
            continue
        try:
            open_rows = await db_supabase.get_rows(
                "driver_insurance_periods",
                {"driver_id": driver_id, "ended_at": None},
                columns="id,period",
                limit=1,
            )
        except Exception:
            logger.error(
                "stale_p3_closer: open-row check failed for driver=%s (skipped)",
                driver_id,
                exc_info=True,
            )
            continue
        if open_rows:
            continue
        orphaned += 1
        logger.error(
            "stale_p3_closer: ORPHANED in_progress ride %s — driver %s has no open "
            "insurance-period row (started=%s); force-complete the ride via admin",
            ride.get("id"),
            driver_id,
            ride.get("ride_started_at"),
        )
        _metric_inc("spinr_insurance_stale_p3_detected_total", {"class": "orphaned_no_open_row"})
    return orphaned


async def _tick() -> Dict[str, int]:
    """One detection pass. Alerts always; closes only when the flag is on."""
    settings = await get_app_settings() or {}
    autoclose = bool(settings.get("stale_p3_autoclose_enabled", False))
    now = datetime.now(timezone.utc)
    result = {"detected": 0, "closed": 0, "orphaned": 0}

    for span in await _open_p3_spans():
        try:
            stale = await _classify(span, now)
            if stale is None:
                continue
            reason, end_time = stale
            result["detected"] += 1
            # Alert-first: this fires whether or not autoclose is enabled, so
            # an admin can force-complete the ride through the normal path.
            logger.error(
                "stale_p3_closer: STALE open Period-3 span (class=%s) driver=%s ride=%s "
                "span_started=%s evidence_end=%s autoclose=%s",
                reason,
                span.get("driver_id"),
                span.get("ride_id"),
                span.get("started_at"),
                end_time.isoformat(),
                autoclose,
            )
            _metric_inc("spinr_insurance_stale_p3_detected_total", {"class": reason})
            if autoclose:
                if await _close_span(span, end_time, reason):
                    result["closed"] += 1
        except Exception:
            logger.error(
                "stale_p3_closer: span %s failed (skipped)",
                span.get("id"),
                exc_info=True,
            )

    try:
        result["orphaned"] = await _alert_orphaned_in_progress_rides(now)
    except Exception:
        logger.error("stale_p3_closer: orphan detection failed", exc_info=True)
    return result


async def stale_p3_closer_loop() -> None:
    """Sweep for stale open Period-3 insurance spans every 15 minutes."""
    while True:
        try:
            got_lock = await redis_set_nx(_LOCK_KEY, "1", ttl=_LOCK_TTL_SECONDS)
            if got_lock:
                await _tick()
        except Exception:
            logger.error("stale_p3_closer tick failed", exc_info=True)
        # Heartbeat every iteration, lock or not — the watchdog is per-replica.
        _record_heartbeat("stale_p3_closer (15min)")
        await asyncio.sleep(INTERVAL_SECONDS)
