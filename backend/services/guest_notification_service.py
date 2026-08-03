"""Guest-ride notification lifecycle (SMS is the guest's app).

Corporate guest bookings notify the CUSTOMER, who may have no Spinr app:

  * is_guest users  → SMS at booking / driver-assigned / arrived / cancelled,
    carrying the pickup OTP and the public tracking link (PII-scrubbed
    resolver, 24h expiry).
  * app-holders     → the booking gets a dedicated `corporate_ride_booked`
    push (rider-app routes it like scheduled_ride_dispatched); every later
    transition already reaches them through the standard ride pushes + WS,
    so no SMS is sent.

A guest who installs the app mid-ride flips is_guest=False at OTP login
(routes/auth.py) — from that moment these helpers see a non-guest row and
stop texting; the standard push/WS path takes over seamlessly.

PIPEDA: the SMS body goes TO the data subject (their own trip, their own
phone) — that's fine. LOGS never carry the body, name, addresses, or full
phone: ids + redact_phone() only. All entry points are designed to be
spawn()ed off the hot path and never raise into their caller.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

try:
    from .. import db_supabase
    from ..core.config import settings as app_config
    from ..features import send_push_notification
    from ..settings_loader import get_app_settings
    from ..sms_service import send_sms
    from ..utils.pii import redact_phone
except ImportError:
    import db_supabase  # type: ignore
    from core.config import settings as app_config  # type: ignore
    from features import send_push_notification  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from sms_service import send_sms  # type: ignore
    from utils.pii import redact_phone  # type: ignore

logger = logging.getLogger(__name__)

_DISPLAY_TZ = "America/Regina"  # Saskatchewan-first product; no DST year-round


def _short(addr: Optional[str], limit: int = 60) -> str:
    addr = (addr or "").strip()
    return addr if len(addr) <= limit else addr[: limit - 1] + "…"


def _local_time(dt_iso: Any) -> str:
    try:
        dt = dt_iso if isinstance(dt_iso, datetime) else datetime.fromisoformat(str(dt_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(_DISPLAY_TZ)).strftime("%b %d, %I:%M %p")
    except Exception:
        return str(dt_iso)


async def _send_guest_sms(ride_id: str, phone: str, body: str, context: str) -> None:
    try:
        sms_settings = await get_app_settings() or {}
        result = await send_sms(
            phone,
            body,
            twilio_sid=sms_settings.get("twilio_account_sid", ""),
            twilio_token=sms_settings.get("twilio_auth_token", ""),
            twilio_from=sms_settings.get("twilio_from_number", ""),
        )
        if not result.get("success"):
            # send_sms guarantees 'error' is a PII-free type+code string.
            logger.error(
                "guest SMS failed (%s) ride=%s phone=%s: %s",
                context,
                ride_id,
                redact_phone(phone),
                result.get("error"),
            )
    except Exception as exc:
        logger.error(
            "guest SMS crashed (%s) ride=%s phone=%s: %s",
            context,
            ride_id,
            redact_phone(phone),
            type(exc).__name__,
            exc_info=True,
        )


async def _guest_recipient(ride: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ride's customer row iff they should receive SMS (still a guest)."""
    rider_id = ride.get("rider_id")
    if not rider_id:
        return None
    try:
        user = await db_supabase.get_user_by_id(rider_id)
    except Exception:
        # Per the module docstring, every entry point here must never raise
        # into its caller — unlike every other DB/network call in this
        # file, this one was unguarded, so a transient DB blip propagated
        # straight out of notify_guest_driver_assigned/_arrived/_cancelled.
        logger.error("guest SMS: recipient lookup failed for rider %s", rider_id, exc_info=True)
        return None
    if not user or not user.get("is_guest") or not user.get("phone"):
        return None
    return user


async def _company_name(company_id: Optional[str]) -> str:
    if not company_id:
        return "Your company"
    try:
        rows = await db_supabase.get_rows("corporate_accounts", {"id": company_id}, columns="id,name", limit=1)
        return (rows[0].get("name") if rows else None) or "Your company"
    except Exception:
        logger.error("guest SMS: company name lookup failed for %s", company_id, exc_info=True)
        return "Your company"


def _tracking_url(token: Optional[str]) -> Optional[str]:
    return f"{app_config.TRACKING_BASE_URL}/{token}" if token else None


async def _ensure_tracking_token(ride: Dict[str, Any]) -> Optional[str]:
    """Return the ride's share token, minting one if absent (scheduled rides
    defer minting to driver-accept so the 24h expiry covers the actual trip)."""
    token = ride.get("shared_trip_token")
    if token:
        return token
    token = secrets.token_urlsafe(32)
    try:
        await db_supabase.update_ride(
            ride["id"],
            {
                "shared_trip_token": token,
                "shared_trip_token_created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.error("guest SMS: tracking token mint failed for ride %s", ride.get("id"), exc_info=True)
        return None
    ride["shared_trip_token"] = token
    return token


# ── Lifecycle entry points (spawn() these) ──────────────────────────


async def notify_guest_booking_created(
    ride: Dict[str, Any],
    guest_user: Dict[str, Any],
    customer_has_app: bool,
    tracking_url: Optional[str],
) -> None:
    company = await _company_name(ride.get("corporate_account_id"))

    if customer_has_app:
        # Existing rider: in-app surfacing (rides/active poll + WS) plus a
        # dedicated push so it appears instantly, mirroring
        # scheduled_ride_dispatched's handler in rider-app/_layout.tsx.
        await send_push_notification(
            guest_user["id"],
            f"{company} booked you a ride",
            "Open Spinr to see your trip details and track your driver.",
            data={"type": "corporate_ride_booked", "ride_id": str(ride.get("id"))},
        )
        return

    if not guest_user.get("phone"):
        logger.error("guest SMS: booking for ride %s has no customer phone", ride.get("id"))
        return

    otp = ride.get("pickup_otp") or ""
    if ride.get("is_scheduled") and ride.get("scheduled_time"):
        body = (
            f"Spinr: {company} booked you a ride for {_local_time(ride['scheduled_time'])} "
            f"from {_short(ride.get('pickup_address'))} to {_short(ride.get('dropoff_address'))}. "
            f"Your pickup code is {otp}. You'll get driver details by text."
        )
    else:
        body = (
            f"Spinr: {company} booked you a ride from {_short(ride.get('pickup_address'))} "
            f"to {_short(ride.get('dropoff_address'))}. Your pickup code is {otp}."
        )
        if tracking_url:
            body += f" Track your driver: {tracking_url}"
    await _send_guest_sms(str(ride.get("id")), guest_user["phone"], body, "booking")


async def notify_guest_driver_assigned(ride: Dict[str, Any], driver: Dict[str, Any]) -> None:
    guest = await _guest_recipient(ride)
    if not guest:
        return
    token = await _ensure_tracking_token(ride)
    url = _tracking_url(token)
    vehicle_bits = " ".join(
        str(driver.get(k)) for k in ("vehicle_color", "vehicle_make", "vehicle_model") if driver.get(k)
    )
    plate = driver.get("license_plate")
    first = (driver.get("name") or "").split(" ")[0] or "your driver"
    body = f"Spinr: {first} is on the way"
    if vehicle_bits:
        body += f" — {vehicle_bits}"
    if plate:
        body += f", plate {plate}"
    body += f". Pickup code: {ride.get('pickup_otp') or ''}."
    if url:
        body += f" Track: {url}"
    await _send_guest_sms(str(ride.get("id")), guest["phone"], body, "driver_assigned")


async def notify_guest_driver_arrived(ride: Dict[str, Any]) -> None:
    guest = await _guest_recipient(ride)
    if not guest:
        return
    body = f"Spinr: your driver has arrived. Give them code {ride.get('pickup_otp') or ''} to start your trip."
    await _send_guest_sms(str(ride.get("id")), guest["phone"], body, "driver_arrived")


async def notify_guest_cancelled(ride: Dict[str, Any]) -> None:
    guest = await _guest_recipient(ride)
    if not guest:
        return
    company = await _company_name(ride.get("corporate_account_id"))
    body = f"Spinr: your ride booked by {company} has been cancelled."
    await _send_guest_sms(str(ride.get("id")), guest["phone"], body, "cancelled")
