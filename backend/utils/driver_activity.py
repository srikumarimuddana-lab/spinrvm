"""Driver daily activity report — per-phase km + empty/riding time.

Source of truth:
  * ``driver_insurance_periods`` — the append-only period timeline gives time in
    each insurance period. Riding = P3 (passenger aboard); Empty = P1+P2 (online,
    no passenger: idle waiting + driving to pickup); Online = P1+P2+P3.
  * ``rides`` — per-ride milestones (driver_accepted_at / driver_arrived_at /
    ride_started_at / ride_completed_at) + ``phase_distances`` give each ride's
    per-phase km and the timestamps.

Day boundaries are in America/Regina (Saskatchewan is UTC-6 year-round, no DST).
The aggregation below is pure (takes already-fetched rows) so it is unit-testable
without the DB; the admin endpoint does the fetching.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from .datetime_utils import parse_iso_utc
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore

try:
    from zoneinfo import ZoneInfo

    REGINA_TZ = ZoneInfo("America/Regina")
except Exception:  # pragma: no cover - zoneinfo/tzdata unavailable
    REGINA_TZ = timezone(timedelta(hours=-6))  # SK fixed offset, no DST

PHASE_KEYS: Tuple[str, ...] = ("navigating_to_pickup", "arrived_at_pickup", "trip_in_progress")
_RIDING_PERIODS = {3}
_EMPTY_PERIODS = {1, 2}


def regina_day_bounds_utc(d: date_cls) -> Tuple[datetime, datetime]:
    """[start, end) UTC datetimes for the Regina calendar day ``d``."""
    start_local = datetime(d.year, d.month, d.day, tzinfo=REGINA_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _overlap_seconds(s: Optional[datetime], e: Optional[datetime], win_s: datetime, win_e: datetime) -> float:
    """Seconds of [s, e] that fall inside [win_s, win_e]."""
    if s is None:
        return 0.0
    lo = max(s, win_s)
    hi = min(e or win_e, win_e)
    return max(0.0, (hi - lo).total_seconds())


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build_daily_activity(
    periods: List[Dict[str, Any]],
    rides: List[Dict[str, Any]],
    win_start: datetime,
    win_end: datetime,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Aggregate one Regina day of a driver's periods + rides into a report."""
    now = now or datetime.now(timezone.utc)

    # ── Time per insurance period (clipped to the day) ──
    period_seconds: Dict[int, float] = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    for p in periods:
        try:
            period = int(p.get("period"))
        except (TypeError, ValueError):
            continue
        started = parse_iso_utc(p.get("started_at"))
        ended = parse_iso_utc(p.get("ended_at")) or now  # open period → up to now
        period_seconds[period] = period_seconds.get(period, 0.0) + _overlap_seconds(started, ended, win_start, win_end)

    riding_seconds = sum(period_seconds.get(p, 0.0) for p in _RIDING_PERIODS)
    empty_seconds = sum(period_seconds.get(p, 0.0) for p in _EMPTY_PERIODS)
    online_seconds = riding_seconds + empty_seconds

    # ── Per-ride breakdown (sorted by when the trip began) ──
    def _ride_sort_key(r: Dict[str, Any]) -> str:
        return str(r.get("ride_started_at") or r.get("driver_accepted_at") or r.get("created_at") or "")

    rides_sorted = sorted(rides, key=_ride_sort_key)

    phase_km_total: Dict[str, float] = {k: 0.0 for k in PHASE_KEYS}
    total_fare = 0.0
    ride_out: List[Dict[str, Any]] = []
    prev_completed: Optional[datetime] = None

    for r in rides_sorted:
        accepted = parse_iso_utc(r.get("driver_accepted_at"))
        arrived = parse_iso_utc(r.get("driver_arrived_at"))
        started = parse_iso_utc(r.get("ride_started_at"))
        completed = parse_iso_utc(r.get("ride_completed_at"))
        pd = r.get("phase_distances") or {}

        nav_km = _num(pd.get("navigating_to_pickup"))
        trip_km = _num(pd.get("trip_in_progress")) or _num(r.get("actual_distance_km"))
        arrived_km = _num(pd.get("arrived_at_pickup"))

        phase_km_total["navigating_to_pickup"] += nav_km
        phase_km_total["arrived_at_pickup"] += arrived_km
        phase_km_total["trip_in_progress"] += trip_km
        total_fare += _num(r.get("total_fare"))

        # Empty gap before this ride = prior ride's completion → this accept.
        empty_before = None
        if prev_completed and accepted:
            empty_before = max(0.0, (accepted - prev_completed).total_seconds())

        ride_out.append(
            {
                "ride_id": r.get("id"),
                "status": r.get("status"),
                "total_fare": round(_num(r.get("total_fare")), 2),
                "empty_before_seconds": empty_before,
                "phases": [
                    {
                        "phase": "navigating_to_pickup",
                        "km": round(nav_km, 2),
                        "seconds": int(round((arrived - accepted).total_seconds())) if accepted and arrived else None,
                        "from": r.get("driver_accepted_at"),
                        "to": r.get("driver_arrived_at"),
                    },
                    {
                        "phase": "arrived_at_pickup",
                        "km": round(arrived_km, 2),
                        "seconds": int(round((started - arrived).total_seconds())) if arrived and started else None,
                        "from": r.get("driver_arrived_at"),
                        "to": r.get("ride_started_at"),
                    },
                    {
                        "phase": "trip_in_progress",
                        "km": round(trip_km, 2),
                        "seconds": int(round((completed - started).total_seconds())) if started and completed else None,
                        "from": r.get("ride_started_at"),
                        "to": r.get("ride_completed_at"),
                    },
                ],
            }
        )
        if completed:
            prev_completed = completed

    total_km = round(sum(phase_km_total.values()), 2)

    return {
        "summary": {
            "online_seconds": int(round(online_seconds)),
            "riding_seconds": int(round(riding_seconds)),
            "empty_seconds": int(round(empty_seconds)),
            "utilization": round(riding_seconds / online_seconds, 3) if online_seconds > 0 else 0.0,
            "phase_km": {k: round(v, 2) for k, v in phase_km_total.items()},
            "total_km": total_km,
            "ride_count": len(rides_sorted),
            "total_fare": round(total_fare, 2),
        },
        "rides": ride_out,
    }
