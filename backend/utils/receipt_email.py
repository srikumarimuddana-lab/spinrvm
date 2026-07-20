"""
Send ride receipt emails with Canadian GST/PST tax line items.

Delivery goes through utils.email_provider.send_transactional_email, which
sends via AWS SES (primary) and falls back to Resend (guardrail) when SES is
unconfigured or fails. Provider credentials live in the app_settings Supabase
table (loaded via settings_loader.get_app_settings()).

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
    airport_fee = _d(ride.get("airport_fee"))
    surge_multiplier = _d(ride.get("surge_multiplier") or "1.0")
    distance_km = _d(ride.get("distance_km"))
    duration_min = ride.get("duration_minutes") or 0

    # Subtotal before tax (what the fare engine recorded as total_fare)
    subtotal = _d(ride.get("total_fare"))
    if subtotal == Decimal("0"):
        subtotal = base_fare + distance_fare + time_fare + booking_fee + airport_fee

    # Minimum-fare adjustment: when total_fare was clamped up to the floor at
    # booking, the amount above the itemised components is the driver's uplift
    # (0% commission). Disclose it (and the airport surcharge, which sits inside
    # total_fare) so the fare lines reconcile to the Subtotal.
    min_fare_uplift = max(
        Decimal("0"),
        subtotal - (base_fare + distance_fare + time_fare + booking_fee + airport_fee),
    )

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

    if airport_fee > Decimal("0"):
        lines.append(f"  Airport surcharge {_fmt(airport_fee)}")
    if min_fare_uplift > Decimal("0"):
        lines.append(f"  Minimum fare adjustment {_fmt(min_fare_uplift)}")

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
        plain_body = _build_plain_text(ride, rider)
        subtotal = _d(ride.get("total_fare"))
        gst = (subtotal * _GST_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
        pst = (subtotal * _PST_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
        grand_total = _d(ride.get("grand_total")) if ride.get("grand_total") is not None else subtotal + gst + pst
        subject = f"Your Spinr ride receipt — {_fmt(grand_total)} CAD"

        try:
            from .email_provider import send_transactional_email
        except ImportError:
            from utils.email_provider import send_transactional_email  # type: ignore

        sent = await send_transactional_email(
            to=email,
            subject=subject,
            text=plain_body,
            default_from="receipts@spinr.ca",
            log_id=str(rider_id),
            email_type="receipt",
            recipient_user_id=str(rider_id) if rider_id and rider_id != "unknown" else None,
        )

        if sent:
            logger.info(
                "[receipt_email] Receipt sent successfully for rider %s (ride %s)",
                rider_id,
                ride.get("id"),
            )
        else:
            logger.error(
                "[receipt_email] No email provider sent receipt for rider %s (ride %s)",
                rider_id,
                ride.get("id"),
            )

    except Exception:
        logger.error(
            "[receipt_email] Unexpected error sending receipt for rider %s",
            rider_id,
            exc_info=True,
        )
