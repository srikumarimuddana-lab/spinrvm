"""GPS capture-gap assessment for active trips.

The monitor deliberately works with timestamps only. It can identify that a
ride stopped reporting location without retaining, logging, or emitting a raw
coordinate outside the durable breadcrumb store.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from .datetime_utils import parse_iso_utc
    from .loop_monitor import record_heartbeat as _record_heartbeat
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:  # pragma: no cover - supports running backend modules top-level
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import set_gauge as _metric_gauge  # type: ignore


logger = logging.getLogger(__name__)

ROUTE_GAP_MONITOR_INTERVAL_SECONDS = 15
DEFAULT_GAP_ALERT_SECONDS = 30
MAX_ACTIVE_RIDES_PER_TICK = 500

GapState = Literal["healthy", "gap", "unknown"]


@dataclass(frozen=True)
class GapDecision:
    """Timestamp-only result of one active-trip GPS health assessment."""

    state: GapState
    gap_started_at: Optional[datetime]
    gap_seconds: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def assess_location_gap(
    *,
    now: datetime,
    trip_started_at: Optional[datetime],
    last_captured_at: Optional[datetime],
    threshold_seconds: int,
) -> GapDecision:
    """Classify the latest location-reporting interval for one in-progress ride.

    When there is no accepted breadcrumb yet, the ride start time is the
    truthful beginning of the missing interval. Clock skew cannot produce a
    negative duration, and a non-positive threshold is clamped to one second
    so a bad setting never disables monitoring silently.
    """
    threshold = max(1, int(threshold_seconds))
    gap_started_at = last_captured_at or trip_started_at
    if gap_started_at is None:
        return GapDecision(state="unknown", gap_started_at=None, gap_seconds=0)

    gap_seconds = max(0, int((now - gap_started_at).total_seconds()))
    return GapDecision(
        state="gap" if gap_seconds >= threshold else "healthy",
        gap_started_at=gap_started_at,
        gap_seconds=gap_seconds,
    )


def _configured_threshold_seconds(settings: dict) -> int:
    """Read the configured threshold without silently accepting bad settings."""
    raw = settings.get("route_location_gap_alert_seconds", DEFAULT_GAP_ALERT_SECONDS)
    try:
        threshold = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("route_location_gap_alert_seconds must be an integer") from exc
    if threshold <= 0:
        raise ValueError("route_location_gap_alert_seconds must be positive when the monitor is enabled")
    return threshold


async def _latest_capture_time(ride_id: str) -> Optional[datetime]:
    rows = await db_supabase.get_rows(
        "driver_location_history",
        {"ride_id": ride_id},
        order="captured_at",
        desc=True,
        limit=1,
        columns="captured_at",
    )
    return parse_iso_utc(rows[0].get("captured_at")) if rows else None


async def _open_gap_event(ride: dict, decision: GapDecision, threshold_seconds: int, now: datetime) -> bool:
    """Create the one durable alert for this gap start. Unique index makes replay safe."""
    if decision.gap_started_at is None:
        return False
    inserted = await db_supabase.insert_many_ignore_conflicts(
        "ride_location_gap_events",
        [
            {
                "ride_id": ride["id"],
                "driver_id": ride.get("driver_id"),
                "gap_started_at": decision.gap_started_at,
                "detected_at": now,
                "threshold_seconds": threshold_seconds,
                "gap_seconds": decision.gap_seconds,
                "status": "open",
                "source": "active_trip_monitor",
            }
        ],
        "ride_id,gap_started_at",
    )
    return bool(inserted)


async def _resolve_open_gap_event(ride_id: str, now: datetime) -> bool:
    """Close any current outage after a fresh accepted capture arrives."""
    updated = await db_supabase.update_one(
        "ride_location_gap_events",
        {"ride_id": ride_id, "status": "open"},
        {"status": "resolved", "gap_resolved_at": now},
    )
    return updated is not None


async def route_gap_monitor_tick() -> dict[str, int]:
    """Assess active rides and durably record only timestamp-based GPS gaps.

    Database failures intentionally propagate to the loop wrapper. A partial
    scan would make an operational outage look healthy, so this monitor never
    soft-handles persistence errors.
    """
    settings = await get_app_settings()
    threshold_seconds = _configured_threshold_seconds(settings)
    now = _now()
    rides = await db_supabase.get_rows(
        "rides",
        {"status": "in_progress"},
        order="ride_started_at",
        limit=MAX_ACTIVE_RIDES_PER_TICK,
        columns="id,driver_id,ride_started_at",
    )
    result = {"scanned": 0, "opened": 0, "resolved": 0, "unknown": 0}
    current_open_gaps = 0

    for ride in rides:
        ride_id = ride.get("id")
        if not ride_id:
            logger.error("route gap monitor received an active ride without an id")
            continue
        result["scanned"] += 1
        decision = assess_location_gap(
            now=now,
            trip_started_at=parse_iso_utc(ride.get("ride_started_at")),
            last_captured_at=await _latest_capture_time(str(ride_id)),
            threshold_seconds=threshold_seconds,
        )
        if decision.state == "unknown":
            result["unknown"] += 1
            continue
        if decision.state == "gap":
            current_open_gaps += 1
            if await _open_gap_event(ride, decision, threshold_seconds, now):
                result["opened"] += 1
                _metric_inc("spinr.routes.gps_gap_detected.count")
                logger.warning(
                    "active trip GPS gap detected ride_id=%s gap_seconds=%s threshold_seconds=%s",
                    ride_id,
                    decision.gap_seconds,
                    threshold_seconds,
                )
            continue
        if await _resolve_open_gap_event(str(ride_id), now):
            result["resolved"] += 1
            _metric_inc("spinr.routes.gps_gap_resolved.count")

    _metric_gauge("spinr.routes.gps_gap_open.count", current_open_gaps)
    return result


async def route_gap_monitor_loop(interval_seconds: int = ROUTE_GAP_MONITOR_INTERVAL_SECONDS) -> None:
    """Replay-safe active-trip GPS-gap monitor. Runs every 15 seconds."""
    while True:
        try:
            await route_gap_monitor_tick()
            _record_heartbeat("route_gap_monitor (15s)")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("route gap monitor tick failed", exc_info=True)
            _metric_inc("spinr.bgloop.errors.count", {"loop": "route_gap_monitor"})
        await asyncio.sleep(interval_seconds)
