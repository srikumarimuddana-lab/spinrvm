"""
Send ride receipt emails with Canadian GST/PST tax line items.

Uses the SendGrid REST API via httpx (same pattern as features.send_email and
utils.email_receipt.send_receipt_email). Settings are loaded from the
app_settings Supabase table via settings_loader.get_app_settings().

PIPEDA: rider email address is never written to logs. Use rider_id only.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal

logger = logging.getLogger(__name__)

# GST is mandatory Canada-wide; PST (SK) applies to most ride-share fares.
_GST_RATE = Decimal("0.05")
_PST_RATE = Decimal("0.06")

_CENT = Decimal("0.01")


def _d(value) -> Decimal:
    """Coerce any numeric value to Decimal without float arithmetic."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _fmt(value: Decimal) -> str:
    return f"${value.quantize(_CENT, rounding=ROUND_HALF_UP):.2f}"


def _build_plain_text(ride: dict, rider: dict) -> str:
    """Build a plain-text receipt body with GST/PST line items."""
    rider_name = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or "Rider"
    ride_ref = ride.get("ride_code") or (str(ride.get("id", ""))[-8:].upper() or "—")

    base_fare = _d(ride.get("base_fare"))
    distance_fare = _d(ride.get("distance_fare"))
    time_fare = _d(ride.get("time_fare"))
    booking_fee = _d(ride.get("booking_fee"))
    surge_multiplier = _d(ride.get("surge_multiplier") or "1.0")
    distance_km = _d(ride.get("distance_km"))
    duration_min = ride.get("duration_minutes") or 0

    # Subtotal before tax (what the fare engine recorded as total_fare)
    subtotal = _d(ride.get("total_fare"))
    if subtotal == Decimal("0"):
        subtotal = base_fare + distance_fare + time_fare + booking_fee

    gst = (subtotal * _GST_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
    pst = (subtotal * _PST_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)

    # Use grand_total from DB when present (already includes tax); otherwise derive it.
    grand_total_raw = ride.get("grand_total")
    if grand_total_raw is not None:
        grand_total = _d(grand_total_raw)
    else:
        grand_total = subtotal + gst + pst

    lines = [
        f"Hi {rider_name},",
        "",
        "Thanks for riding with Spinr. Here is your receipt.",
        "",
        f"Ride reference : {ride_ref}",
        f"Distance       : {distance_km:.1f} km",
        f"Duration       : {duration_min} min",
        "",
        "── Fare breakdown ──────────────────",
        f"  Base fare        {_fmt(base_fare)}",
        f"  Distance charge  {_fmt(distance_fare)}",
        f"  Time charge      {_fmt(time_fare)}",
        f"  Booking fee      {_fmt(booking_fee)}",
    ]

    if surge_multiplier > Decimal("1.0"):
        lines.append(f"  Surge ({surge_multiplier}×)     (applied to fare above)")

    lines += [
        f"  Subtotal         {_fmt(subtotal)}",
        f"  GST (5%)         {_fmt(gst)}",
        f"  PST (6%)         {_fmt(pst)}",
        "────────────────────────────────────",
        f"  Grand total      {_fmt(grand_total)} CAD",
        "────────────────────────────────────",
        "",
        "Pickup  : " + (ride.get("pickup_address") or "—"),
        "Dropoff : " + (ride.get("dropoff_address") or "—"),
        "",
        "Questions? support@spinr.ca",
        "",
        "Spinr Technologies Inc. · Saskatoon, SK",
        "www.spinr.ca",
    ]
    return "\n".join(lines)


async def send_ride_receipt_email(ride: dict, rider: dict) -> None:
    """Fire-and-forget receipt email for a completed ride.

    Designed to be called via asyncio.create_task() after ride completion.
    Any failure is logged but never propagated — receipt email failure must
    never affect ride completion.

    PIPEDA: rider_id is used in all log messages; the email address is never
    written to any log or structured event.
    """
    rider_id = rider.get("id") or ride.get("rider_id") or "unknown"
    email = rider.get("email", "")

    if not email:
        logger.warning(
            "[receipt_email] No email address for rider %s — skipping receipt",
            rider_id,
        )
        return

    try:
        try:
            from ..settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings

        settings = await get_app_settings()
        sendgrid_key = settings.get("sendgrid_api_key", "")
        from_email = settings.get("sendgrid_from_email") or settings.get("email_from") or "receipts@spinr.ca"

        if not sendgrid_key:
            logger.warning(
                "[receipt_email] sendgrid_api_key not configured — receipt not sent for rider %s",
                rider_id,
            )
            return

        plain_body = _build_plain_text(ride, rider)
        subtotal = _d(ride.get("total_fare"))
        gst = (subtotal * _GST_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
        pst = (subtotal * _PST_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
        grand_total = _d(ride.get("grand_total")) if ride.get("grand_total") is not None else subtotal + gst + pst
        subject = f"Your Spinr ride receipt — {_fmt(grand_total)} CAD"

        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {sendgrid_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": email}]}],
                    "from": {"email": from_email, "name": "Spinr"},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": plain_body}],
                },
            )

        if response.status_code in (200, 201, 202):
            logger.info(
                "[receipt_email] Receipt sent successfully for rider %s (ride %s)",
                rider_id,
                ride.get("id"),
            )
        else:
            logger.error(
                "[receipt_email] SendGrid returned %s for rider %s — body: %s",
                response.status_code,
                rider_id,
                response.text[:200],
            )

    except Exception:
        logger.error(
            "[receipt_email] Unexpected error sending receipt for rider %s",
            rider_id,
            exc_info=True,
        )
