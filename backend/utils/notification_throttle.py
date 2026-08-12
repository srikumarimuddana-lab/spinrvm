"""
Quiet-hours + daily-cap throttling for outbound notifications.

Enforcement point: backend/features.py::send_push_notification, and — in a
follow-up subtask — sms_service.py / email_provider.py. This module has no
concept of notification priority; callers own the decision to skip it
entirely for time-critical sends (dispatch/safety/account), the same way
they already skip the existing push_enabled opt-out for those tiers.

Fails open everywhere: a throttle-check failure (Redis error, malformed
settings) must never suppress a legitimate notification. Every failure
path logs at error level and returns "not throttled" rather than guessing —
mirrors the fail-open precedent already set by the push_enabled preference
lookup in features.py and by _over_mcp_daily_cap in ai/mcp_server.py.

Scope: quiet hours are a single global window (America/Regina), not a
per-user preference — Spinr operates in one timezone today (Saskatchewan,
no DST). See migration 304 for why this is deliberately not per-user yet.
"""

import logging
from datetime import datetime, timezone
from datetime import time as dt_time
from typing import Optional

try:
    from .redis_client import redis_expire, redis_incr
except ImportError:  # pragma: no cover
    from redis_client import redis_expire, redis_incr  # type: ignore

logger = logging.getLogger(__name__)

QUIET_HOURS_TZ = "America/Regina"
_CAP_KEY_PREFIX = "notif:daily_cap"


def _parse_hhmm(value: str) -> Optional[dt_time]:
    try:
        hour_str, minute_str = value.split(":", 1)
        return dt_time(hour=int(hour_str), minute=int(minute_str))
    except (ValueError, AttributeError, TypeError):
        return None


def is_within_quiet_hours(
    quiet_start: str,
    quiet_end: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True if the current time in QUIET_HOURS_TZ falls inside the
    [quiet_start, quiet_end) window. The window may wrap past midnight
    (e.g. "22:00"-"07:00"). Malformed HH:MM config or a timezone-lookup
    failure fails open (returns False — not quiet hours, so nothing gets
    suppressed) and logs at error level rather than guessing.

    `now` is for tests only; production callers always use the real clock.
    """
    start = _parse_hhmm(quiet_start)
    end = _parse_hhmm(quiet_end)
    if start is None or end is None:
        logger.error(
            "notification_throttle: malformed quiet-hours config "
            f"(start={quiet_start!r}, end={quiet_end!r}) — failing open"
        )
        return False

    if now is not None:
        current = now.time()
    else:
        try:
            from zoneinfo import ZoneInfo

            current = datetime.now(ZoneInfo(QUIET_HOURS_TZ)).time()
        except Exception:
            logger.error("notification_throttle: zoneinfo lookup failed — failing open", exc_info=True)
            return False

    if start <= end:
        return start <= current < end
    # Wraps past midnight.
    return current >= start or current < end


async def is_over_daily_cap(user_id: str, daily_cap: int) -> bool:
    """Per-user calendar-day (UTC) cap on non-critical notifications via
    Redis INCR, mirroring _over_mcp_daily_cap in ai/mcp_server.py. cap<=0
    means "no cap" (always False, no Redis round-trip). Fails open on any
    Redis error.
    """
    if daily_cap <= 0:
        return False
    key = f"{_CAP_KEY_PREFIX}:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    try:
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, 86400)
        return count > daily_cap
    except Exception:
        logger.error(
            "notification_throttle: daily-cap check failed — failing open",
            exc_info=True,
            extra={"user_id": user_id},
        )
        return False


async def should_throttle(
    user_id: str,
    quiet_hours_start: str,
    quiet_hours_end: str,
    daily_cap: int,
) -> bool:
    """Single entry point for callers: True if this non-critical
    notification should be suppressed. Quiet hours are checked first and
    short-circuit — a notification suppressed for being inside quiet
    hours does not also consume a slot of the daily cap.
    """
    if is_within_quiet_hours(quiet_hours_start, quiet_hours_end):
        return True
    return await is_over_daily_cap(user_id, daily_cap)
