"""Spinr Pass daily ride-quota helpers.

A driver subscription ("Spinr Pass") grants a per-**calendar-day** ride
allowance (``rides_per_day``; ``-1`` == unlimited). The *pass* itself may last
1 / 7 / 30 / 365 days (``duration_days``), but the ride allowance is always
counted per calendar day and unused rides do **not** roll over — a monthly pass
with ``rides_per_day = 4`` gives 4 rides today, 4 tomorrow, and so on.

The allowance resets at midnight in the driver's local calendar day. We use
America/Regina because Spinr is Saskatchewan-first and SK is UTC-6 year-round
(no DST), so "calendar day" is unambiguous and stable. Every enforcement gate
(go-online, dispatch candidate filter, accept-ride, force-offline on
completion) and the ``/subscription/current`` display read the window from this
module so the number a driver *sees* is exactly the number that's *enforced*.

Pure functions (window math, ``compute_quota``) take already-fetched values and
are unit-testable without a DB; the async helpers do the counting.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Set, Tuple

try:
    from .datetime_utils import parse_iso_utc
except ImportError:  # pragma: no cover - top-level import path
    from utils.datetime_utils import parse_iso_utc  # type: ignore

try:
    from zoneinfo import ZoneInfo

    REGINA_TZ: Any = ZoneInfo("America/Regina")
except Exception:  # pragma: no cover - zoneinfo/tzdata unavailable
    REGINA_TZ = timezone(timedelta(hours=-6))  # SK fixed offset, no DST

logger = logging.getLogger(__name__)

UNLIMITED = -1


def quota_day_bounds_utc(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """``[start, end)`` UTC datetimes for the Regina calendar day of ``now``.

    ``start`` is the most recent local midnight; ``end`` is the next local
    midnight (the moment the allowance refills).
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(REGINA_TZ)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def compute_quota(rides_per_day: Any, used_today: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Derive quota state from a plan's ``rides_per_day`` and today's usage.

    Returns a dict with the remaining count, whether the allowance is
    exhausted, and the countdown to the next reset. ``rides_remaining`` is the
    string ``"unlimited"`` for unlimited plans (matches the API/UI contract).
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    _, day_end = quota_day_bounds_utc(now)
    try:
        rpd = int(rides_per_day)
    except (TypeError, ValueError):
        rpd = UNLIMITED
    unlimited = rpd == UNLIMITED
    used = max(0, int(used_today or 0))
    remaining = None if unlimited else max(0, rpd - used)
    exhausted = (not unlimited) and remaining == 0
    secs = max(0, int((day_end - now).total_seconds()))
    return {
        "rides_per_day": rpd,
        "unlimited": unlimited,
        "used_today": used,
        "rides_remaining": "unlimited" if unlimited else remaining,
        "can_accept_rides": unlimited or used < rpd,
        "exhausted": exhausted,
        "quota_resets_at": day_end.isoformat(),
        "seconds_until_reset": secs,
        "hours_until_reset": round(secs / 3600, 1),
    }


def hours_until(value: Any, now: Optional[datetime] = None) -> Optional[float]:
    """Hours from ``now`` until ``value`` (ISO string or datetime).

    Returns ``None`` for a missing/unparseable value and ``0`` when already
    past — never a negative number (the UI shows a non-negative countdown).
    """
    target = parse_iso_utc(value) if value is not None else None
    if target is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    secs = (target - now).total_seconds()
    return round(max(0.0, secs) / 3600, 1)


def _db():
    try:
        from .. import db_supabase  # type: ignore
    except ImportError:  # pragma: no cover - top-level import path
        import db_supabase  # type: ignore
    return db_supabase


async def completed_today(driver_id: str, now: Optional[datetime] = None) -> int:
    """Count a driver's rides completed within the current quota day."""
    db = _db()
    day_start, _ = quota_day_bounds_utc(now)
    try:
        from ..schemas import RideStatus  # type: ignore
    except ImportError:  # pragma: no cover
        from schemas import RideStatus  # type: ignore
    return await db.count_documents(
        "rides",
        {
            "driver_id": driver_id,
            "status": RideStatus.COMPLETED,
            "ride_completed_at": {"$gte": day_start.isoformat()},
        },
    )


async def active_subscription(driver_id: str) -> Optional[Dict[str, Any]]:
    """Return the driver's active Spinr Pass row, or ``None``."""
    db = _db()
    rows = await db.get_rows(
        "driver_subscriptions",
        {"driver_id": driver_id, "status": "active"},
        limit=1,
    )
    return rows[0] if rows else None


async def quota_status(
    driver_id: str,
    sub: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Full quota state for a driver, or ``None`` when there's no active pass.

    Pass ``sub`` to reuse an already-fetched active subscription row and avoid a
    second lookup at call sites that have it in hand.
    """
    if sub is None:
        sub = await active_subscription(driver_id)
    if not sub:
        return None
    used = await completed_today(driver_id, now)
    status = compute_quota(sub.get("rides_per_day", UNLIMITED), used, now)
    status["subscription_id"] = sub.get("id")
    return status


async def is_quota_exhausted(
    driver_id: str,
    sub: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """True only when the driver has an active finite pass with 0 rides left.

    Fails **closed-open**: on any error, returns ``False`` (do not block the
    driver because of a transient read failure — the other gates still apply).
    """
    try:
        status = await quota_status(driver_id, sub=sub, now=now)
    except Exception:
        logger.error("is_quota_exhausted lookup failed for driver=%s", driver_id, exc_info=True)
        return False
    return bool(status and status.get("exhausted"))


async def exhausted_driver_ids(
    subs: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Set[str]:
    """Batch: of the given active subs, which drivers are out of rides today.

    ``subs`` rows must include ``driver_id`` and ``rides_per_day``. Unlimited
    plans (``rides_per_day == -1``) are skipped. Uses a single batched
    ``rides`` read (no N+1) to tally today's completions across all finite-quota
    drivers, then compares each driver's count to their own allowance.
    """
    finite = {
        s["driver_id"]: int(s.get("rides_per_day", UNLIMITED))
        for s in subs
        if s.get("driver_id") is not None and _as_int(s.get("rides_per_day", UNLIMITED)) != UNLIMITED
    }
    if not finite:
        return set()

    db = _db()
    day_start, _ = quota_day_bounds_utc(now)
    try:
        from ..schemas import RideStatus  # type: ignore
    except ImportError:  # pragma: no cover
        from schemas import RideStatus  # type: ignore

    ids = list(finite.keys())
    rows = await db.get_rows(
        "rides",
        {
            "driver_id": {"$in": ids},
            "status": RideStatus.COMPLETED,
            "ride_completed_at": {"$gte": day_start.isoformat()},
        },
        columns="driver_id",
        limit=10000,
    )
    counts: Dict[str, int] = {}
    for r in rows or []:
        did = r.get("driver_id")
        if did is not None:
            counts[did] = counts.get(did, 0) + 1
    return {did for did, cap in finite.items() if counts.get(did, 0) >= cap}


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return UNLIMITED


async def force_offline_if_exhausted(
    driver: Any,
    sub: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Set the driver offline when today's pass allowance is now used up.

    Call this right after a ride completes. ``driver`` may be the driver row
    (dict with ``id``) or a bare driver id. Returns the quota status dict when
    it forced the driver offline (so the caller — which owns the socket manager
    — can notify the driver + admins), else ``None``.

    Side effects (all best-effort, logged on failure): flip ``is_online`` /
    ``is_available`` to False, append the SGI period-0 transition, clear Redis
    presence, and write a ``driver_activity_log`` row. The append-only insurance
    timeline is never mutated — only a new period row is added.
    """
    driver_id = driver.get("id") if isinstance(driver, dict) else driver
    if not driver_id:
        return None
    status = await quota_status(driver_id, sub=sub, now=now)
    if not status or not status.get("exhausted"):
        return None

    now = now or datetime.now(timezone.utc)
    iso = now.isoformat()
    db = _db()
    try:
        await db.update_one(
            "drivers",
            {"id": driver_id},
            {
                "is_online": False,
                "is_available": False,
                "updated_at": iso,
                "last_status_changed_at": iso,
            },
        )
    except Exception:
        logger.error("force_offline_if_exhausted: failed to flip driver=%s offline", driver_id, exc_info=True)
        return None

    # SGI insurance period audit: online (P1) -> offline (P0). Append-only.
    try:
        try:
            from .insurance_periods import record_period_transition  # type: ignore
        except ImportError:
            from utils.insurance_periods import record_period_transition  # type: ignore
        await record_period_transition(driver_id, 0)
    except Exception:
        logger.error("force_offline_if_exhausted: period transition failed driver=%s", driver_id, exc_info=True)

    try:
        try:
            from .driver_presence import clear_presence  # type: ignore
        except ImportError:
            from utils.driver_presence import clear_presence  # type: ignore
        await clear_presence(driver_id)
    except Exception:
        logger.error("force_offline_if_exhausted: clear_presence failed driver=%s", driver_id, exc_info=True)

    try:
        await db.insert_one(
            "driver_activity_log",
            {
                "id": str(uuid.uuid4()),
                "driver_id": driver_id,
                "event_type": "went_offline",
                "title": "Went offline (daily ride limit reached)",
                "description": "System set driver offline — used all Spinr Pass rides for the day.",
                "metadata": {
                    "reason": "quota_exhausted",
                    "source": "ride_completion",
                    "rides_per_day": status.get("rides_per_day"),
                    "quota_resets_at": status.get("quota_resets_at"),
                },
                "actor": "system",
                "created_at": iso,
            },
        )
    except Exception:
        logger.error("force_offline_if_exhausted: activity log failed driver=%s", driver_id, exc_info=True)

    return status
