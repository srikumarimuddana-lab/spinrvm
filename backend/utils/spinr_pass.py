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
    ZoneInfo = None  # type: ignore[assignment]
    REGINA_TZ = timezone(timedelta(hours=-6))  # SK fixed offset, no DST

logger = logging.getLogger(__name__)

UNLIMITED = -1

# A timezone is either an IANA name (e.g. "America/Edmonton") resolved from the
# service area, a tzinfo, or None → America/Regina (Spinr's SK home). Resolving
# per service area keeps the quota "calendar day" correct under DST (Alberta is
# Mountain Time with DST; Saskatchewan is UTC-6 year-round).
TzArg = Optional[Any]


def _coerce_tz(tz: TzArg):
    """Return a tzinfo for ``tz`` (IANA name / tzinfo / None), Regina as default.

    Falls back to Regina on an unknown name or when zoneinfo is unavailable, so
    a misconfigured ``service_areas.timezone`` never breaks the day boundary.
    """
    if tz is None:
        return REGINA_TZ
    if isinstance(tz, str):
        if not tz or ZoneInfo is None:
            return REGINA_TZ
        try:
            return ZoneInfo(tz)
        except Exception:
            logger.warning("Unknown service-area timezone %r — falling back to Regina", tz)
            return REGINA_TZ
    return tz  # already a tzinfo


def quota_day_bounds_utc(now: Optional[datetime] = None, tz: TzArg = None) -> Tuple[datetime, datetime]:
    """``[start, end)`` UTC datetimes for the local calendar day of ``now``.

    ``start`` is the most recent local midnight; ``end`` is the next local
    midnight (the moment the allowance refills). ``tz`` selects the locale
    (defaults to America/Regina); DST transitions are handled by zoneinfo, so
    both bounds land on true local midnight even when the day isn't 24h long.

    Note: ``replace(hour=0, ...)`` assumes local midnight is a real, unambiguous
    wall-clock time. True for every Canadian zone (DST flips at 02:00). If the
    service-area set ever includes a locale that transitions at midnight,
    revisit this with a fold-aware construction.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    zone = _coerce_tz(tz)
    local = now.astimezone(zone)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def compute_quota(
    rides_per_day: Any, used_today: Any, now: Optional[datetime] = None, tz: TzArg = None
) -> Dict[str, Any]:
    """Derive quota state from a plan's ``rides_per_day`` and today's usage.

    Returns a dict with the remaining count, whether the allowance is
    exhausted, and the countdown to the next reset. ``rides_remaining`` is the
    string ``"unlimited"`` for unlimited plans (matches the API/UI contract).
    ``tz`` selects the local calendar day (defaults to Regina).
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    _, day_end = quota_day_bounds_utc(now, tz=tz)
    try:
        rpd = int(rides_per_day)
    except (TypeError, ValueError):
        rpd = UNLIMITED
    # Any negative cap means "no daily limit" (-1 is the canonical sentinel, but
    # a misconfigured -2/-5 must not read as 0-remaining and block everyone).
    # 0 is a real cap (no rides) and stays exhausted.
    unlimited = rpd < 0
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


async def area_timezone(area_id: Optional[str]) -> Optional[str]:
    """IANA timezone name for a service area, or ``None`` if not configured.

    Mirrors the area-tz pattern used by earnings, quests, and onboarding
    reminders (``service_areas.timezone``, migration 105). Returns ``None`` only
    when the area genuinely has no timezone set — a *lookup failure* is allowed
    to **propagate** so the calling quota helper's fail-open/fail-closed handling
    decides what to do, rather than silently enforcing on the wrong (Regina)
    calendar day. (Outside Regina, e.g. Edmonton in winter, the boundary differs
    by an hour, so a wrong fallback near midnight could mis-gate a driver.)
    """
    if not area_id:
        return None
    db = _db()
    area = await db.find_one("service_areas", {"id": area_id})
    return (area or {}).get("timezone")


async def completed_today(driver_id: str, now: Optional[datetime] = None, tz: TzArg = None) -> int:
    """Count a driver's rides completed within the current quota day (``tz``)."""
    db = _db()
    day_start, _ = quota_day_bounds_utc(now, tz=tz)
    try:
        from ..models.ride_status import RideStatus  # type: ignore
    except ImportError:  # pragma: no cover - top-level import path
        from models.ride_status import RideStatus  # type: ignore
    return await db.count_documents(
        "rides",
        {
            "driver_id": driver_id,
            "status": RideStatus.COMPLETED.value,
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
    area_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Full quota state for a driver, or ``None`` when there's no active pass.

    Pass ``sub`` to reuse an already-fetched active subscription row and avoid a
    second lookup at call sites that have it in hand. ``area_id`` (the driver's
    service area) selects the calendar-day timezone — Regina when omitted.
    """
    if sub is None:
        sub = await active_subscription(driver_id)
    if not sub:
        return None
    # An active row past its expiry is moot for quota — the expiry gates (go
    # online / accept / sweeper) own that case. Treating it as "no quota" avoids
    # a misleading "rides used up" message when the real reason is expiry.
    exp = parse_iso_utc(sub.get("expires_at")) if sub.get("expires_at") else None
    if exp is not None and exp <= (now or datetime.now(timezone.utc)):
        return None
    tz = await area_timezone(area_id) if area_id else None
    used = await completed_today(driver_id, now, tz=tz)
    status = compute_quota(sub.get("rides_per_day", UNLIMITED), used, now, tz=tz)
    status["subscription_id"] = sub.get("id")
    return status


async def is_quota_exhausted(
    driver_id: str,
    sub: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    area_id: Optional[str] = None,
) -> bool:
    """True only when the driver has an active finite pass with 0 rides left.

    Fails **closed-open**: on any error, returns ``False`` (do not block the
    driver because of a transient read failure — the other gates still apply).
    """
    try:
        status = await quota_status(driver_id, sub=sub, now=now, area_id=area_id)
    except Exception:
        logger.error("is_quota_exhausted lookup failed for driver=%s", driver_id, exc_info=True)
        return False
    return bool(status and status.get("exhausted"))


async def assert_quota_available(
    driver_id: str,
    sub: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    area_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Raise if the driver's finite Spinr Pass allowance is used up for the day.

    The single enforcement entry point for go-online and accept-ride. No-op
    (returns the quota status or ``None``) when there's no active finite pass,
    the pass is unlimited, or rides remain. Raises ``SpinrException`` (403,
    ``DRIVER_QUOTA_EXCEEDED``) when exhausted. ``area_id`` (the driver's service
    area) selects the calendar-day timezone — Regina when omitted.

    Fails **open**: a lookup error (e.g. ``driver_subscriptions`` absent
    pre-launch) returns ``None`` rather than blocking — the subscription-present
    and expiry gates are the hard gates; quota is an additional limit and the
    completion force-offline still backstops it.
    """
    try:
        status = await quota_status(driver_id, sub=sub, now=now, area_id=area_id)
    except Exception:
        logger.error("assert_quota_available lookup failed for driver=%s", driver_id, exc_info=True)
        return None
    if not status or not status.get("exhausted"):
        return status

    try:
        from .error_handling import ErrorCode, SpinrException  # type: ignore
    except ImportError:  # pragma: no cover - top-level import path
        from utils.error_handling import ErrorCode, SpinrException  # type: ignore
    try:
        from .error_keys import ErrorKeys  # type: ignore
    except ImportError:  # pragma: no cover
        from utils.error_keys import ErrorKeys  # type: ignore

    reset_h = round(status.get("hours_until_reset") or 0)
    raise SpinrException(
        message=(
            f"You've used all {status['rides_per_day']} of today's Spinr Pass rides. "
            f"Your allowance resets in about {reset_h}h. "
            "Enjoy the rest of your day — you can go online again then."
        ),
        error_code=ErrorCode.DRIVER_QUOTA_EXCEEDED,
        status_code=403,
        message_key=ErrorKeys.DRIVER_QUOTA_EXHAUSTED,
        action_hint="Resets at midnight",
        details={
            "rides_per_day": status["rides_per_day"],
            "used_today": status["used_today"],
            "quota_resets_at": status["quota_resets_at"],
            "hours_until_reset": status["hours_until_reset"],
        },
    )


async def exhausted_driver_ids(
    subs: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
    tz: TzArg = None,
) -> Set[str]:
    """Batch: of the given active subs, which drivers are out of rides today.

    ``subs`` rows must include ``driver_id`` and ``rides_per_day`` (and may
    include ``expires_at``). Self-contained: skips unlimited plans (any negative
    ``rides_per_day``) and lapsed passes, so callers can pass raw active-sub
    rows. Uses a single batched ``rides`` read (no N+1) to tally today's
    completions across all finite-quota drivers, then compares each driver's
    count to their own allowance. ``tz`` selects the calendar-day window — pass
    the ride's service-area timezone (Regina when omitted).
    """
    now_dt = now or datetime.now(timezone.utc)
    finite: Dict[str, int] = {}
    for s in subs:
        did = s.get("driver_id")
        if did is None:
            continue
        cap = _as_int(s.get("rides_per_day", UNLIMITED))
        if cap < 0:
            continue  # unlimited (any negative sentinel)
        exp = parse_iso_utc(s.get("expires_at")) if s.get("expires_at") else None
        if exp is not None and exp <= now_dt:
            continue  # lapsed pass — quota is moot
        finite[did] = cap
    if not finite:
        return set()

    db = _db()
    day_start, _ = quota_day_bounds_utc(now_dt, tz=tz)
    try:
        from ..models.ride_status import RideStatus  # type: ignore
    except ImportError:  # pragma: no cover - top-level import path
        from models.ride_status import RideStatus  # type: ignore

    ids = list(finite.keys())
    rows = await db.get_rows(
        "rides",
        {
            "driver_id": {"$in": ids},
            "status": RideStatus.COMPLETED.value,
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
    area_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Set the driver offline when today's pass allowance is now used up.

    Call this right after a ride completes. ``driver`` may be the driver row
    (dict with ``id``) or a bare driver id. Returns the quota status dict when
    it forced the driver offline (so the caller — which owns the socket manager
    — can notify the driver + admins), else ``None``. The calendar-day timezone
    is taken from ``area_id`` (or the driver row's ``service_area_id``), Regina
    when neither is available.

    Side effects (all best-effort, logged on failure): flip ``is_online`` /
    ``is_available`` to False, append the SGI period-0 transition, clear Redis
    presence, and write a ``driver_activity_log`` row. The append-only insurance
    timeline is never mutated — only a new period row is added.
    """
    driver_id = driver.get("id") if isinstance(driver, dict) else driver
    if not driver_id:
        return None
    if area_id is None and isinstance(driver, dict):
        area_id = driver.get("service_area_id")
    status = await quota_status(driver_id, sub=sub, now=now, area_id=area_id)
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
                # Stamp durable offline intent (migration 97). intent_online()
                # reads went_online_at/went_offline_at ("more recent wins"), and
                # the live dispatcher's presence filter uses it — without this the
                # driver could still read as intent-online and keep getting
                # offers. Matches update_driver_status's go-offline write.
                "went_offline_at": iso,
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
