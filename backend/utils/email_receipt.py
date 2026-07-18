"""
Receipt generator for Spinr rides.
Generates HTML receipt and sends via email through utils.email_provider
(AWS SES primary, Resend guardrail; logs only when neither is configured).

Line items
----------

Per CLAUDE.md ("rider receipts must show GST (5%) and PST (6% where
applicable) as separate line items") and "every charge on the receipt
maps to a disclosed line item: base fare, distance, time, booking fee,
surge, tax, tip", we render:

    base_fare, distance_fare, time_fare, booking_fee
    [+ each area fee from area_fees_breakdown]   ← airport, night, …
    subtotal
    [+ each tax from tax_breakdown]              ← GST, PST, HST
    [+ tip]
    total

Surge is folded into ``distance_fare`` / ``time_fare`` at fare-calc time
so we surface it as a small "Surge X.X× applied" notice rather than a
recomputed line item — re-deriving the uplift would round-trip through
floats and risk a 1¢ mismatch on the rendered total.
"""

import asyncio
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

import httpx

try:
    from ..repositories.ride_repo import create_route_snapshot_signed_url
    from .datetime_utils import parse_iso_utc
except ImportError:
    from repositories.ride_repo import create_route_snapshot_signed_url  # type: ignore
    from utils.datetime_utils import parse_iso_utc

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")

# Bounded window the receipt-generation path holds for v2 route finalization.
#
# The route finalizer drains its pending queue on a ~5s poll (see
# ``route_finalizer.ROUTE_FINALIZER_INTERVAL_SECONDS``). After completion it
# needs one poll to build route revision 1 and a second, downstream step to
# render + upload the revisioned snapshot, so a matched snapshot realistically
# lands ~5-10s after completion. The automatic receipt is spawned as a
# fire-and-forget task seconds after payment submit, so with the old 4.0s
# deadline it almost always beat finalization and shipped a permanent
# "still processing" email with no map. We hold the RECEIPT path (never fare
# settlement or the completion response — that stays fully non-blocking) for a
# window that covers the finalize + snapshot-publish P95. If the window is
# still exceeded we fall through to the honest "still processing" note rather
# than block delivery, and any later resend re-reads the newest revision.
_ROUTE_FINALIZATION_WAIT_SECONDS = 14.0
_ROUTE_FINALIZATION_POLL_SECONDS = 1.0


def _d(v: Any) -> Decimal:
    """Coerce a number-like value to Decimal via str() to avoid float drift."""
    if v in (None, ""):
        return Decimal("0")
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _fmt(v: Decimal) -> str:
    """Format a Decimal as ``X.XX`` for display."""
    return f"{_q(v):.2f}"


def _line(label: str, amount_html: str, *, label_color: str = "#666", amount_color: str = "#1a1a1a") -> str:
    """One <tr> for the fare breakdown table — keeps the markup uniform."""
    return (
        f'<tr><td style="color:{label_color};padding:4px 0;">{label}</td>'
        f'<td style="text-align:right;color:{amount_color};">{amount_html}</td></tr>'
    )


def _build_fare_rows(ride: Dict[str, Any], tip: Decimal) -> tuple[str, Decimal]:
    """Render the fare breakdown rows + return the grand total used in the header.

    Reads ``tax_breakdown`` and ``area_fees_breakdown`` (migration 46) so
    the per-tax and per-fee amounts shown reconcile to ``grand_total``.
    Falls back to ``base_fare + distance + time + booking + tax + tip``
    for legacy rides written before migration 46.
    """
    base_fare = _d(ride.get("base_fare", 0))
    distance_fare = _d(ride.get("distance_fare", 0))
    time_fare = _d(ride.get("time_fare", 0))
    booking_fee = _d(ride.get("booking_fee", 0))
    distance_km = _d(ride.get("distance_km", 0))
    duration_min = ride.get("duration_minutes", 0) or 0
    surge = _d(ride.get("surge_multiplier", 1.0) or 1.0)

    rows: list[str] = []
    rows.append(_line("Base fare", f"${_fmt(base_fare)}"))
    rows.append(_line(f"Distance ({distance_km:.1f} km)", f"${_fmt(distance_fare)}"))
    rows.append(_line(f"Time ({duration_min} min)", f"${_fmt(time_fare)}"))
    if booking_fee > 0:
        rows.append(_line("Booking fee", f"${_fmt(booking_fee)}"))

    # Itemised area fees (airport, night, custom). Falls back silently when
    # the column is missing on a legacy row — the underlying value is still
    # captured under the per-component fares above for those rides.
    area_fees = ride.get("area_fees_breakdown") or []
    for fee in area_fees:
        if not isinstance(fee, dict):
            continue
        amount = _d(fee.get("calculated_value", fee.get("amount", 0)))
        if amount == 0:
            continue
        name = fee.get("name") or fee.get("type") or "Area fee"
        rows.append(_line(str(name), f"${_fmt(amount)}"))

    # Subtotal divider — fees end here, taxes begin below.
    rows.append('<tr><td colspan="2" style="border-top:1px dashed #eee;padding:0;"></td></tr>')

    # Tax breakdown — REGULATORY REQUIREMENT (CLAUDE.md): GST and PST
    # must appear as separate line items where applicable. Read the
    # persisted breakdown so what we show is exactly what was charged.
    tax_breakdown = ride.get("tax_breakdown") or {}
    tax_total = Decimal("0")
    if isinstance(tax_breakdown, dict) and tax_breakdown:
        for label, payload in tax_breakdown.items():
            if not isinstance(payload, dict):
                continue
            rate = _d(payload.get("rate", 0))
            amount = _d(payload.get("amount", 0))
            tax_total += amount
            rate_str = f" ({rate:.0f}%)" if rate > 0 else ""
            rows.append(_line(f"{label}{rate_str}", f"${_fmt(amount)}"))
    else:
        # Legacy fallback — show a single combined tax line if present.
        tax_amount = _d(ride.get("tax_amount", 0))
        if tax_amount > 0:
            rows.append(_line("Tax", f"${_fmt(tax_amount)}"))
            tax_total = tax_amount

    if tip > 0:
        rows.append(_line("Tip", f"${_fmt(tip)}", label_color="#10b981", amount_color="#10b981"))

    # Surge notice — surge is folded into distance/time fares, so we
    # surface it as a disclosure note instead of as a separate amount.
    if surge > Decimal("1.0"):
        rows.append(
            '<tr><td colspan="2" style="padding:6px 0 0;font-size:11px;color:#b45309;">'
            f"Surge pricing {surge:.2f}× was in effect at booking time."
            "</td></tr>"
        )

    # Grand total — prefer the persisted column (set at completion);
    # otherwise reconstruct from the parts we just listed so the rendered
    # number always equals the sum of visible rows.
    grand_total = ride.get("grand_total")
    if grand_total in (None, "", 0):
        area_fees_total = _d(ride.get("area_fees_total", 0))
        # area_fees_total may be 0 on legacy rows; if we listed individual
        # fees above, sum those instead.
        if area_fees_total == 0:
            area_fees_total = sum(
                (_d(f.get("calculated_value", 0)) for f in area_fees if isinstance(f, dict)),
                Decimal("0"),
            )
        grand_total_d = _q(base_fare + distance_fare + time_fare + booking_fee + area_fees_total + tax_total + tip)
    else:
        # Persisted grand_total includes fees + tax but NOT tip — tip is
        # added at rating time after settlement.
        grand_total_d = _q(_d(grand_total) + tip)

    rows.append('<tr><td colspan="2" style="border-top:1px solid #eee;padding:0;"></td></tr>')
    rows.append(
        '<tr><td style="color:#1a1a1a;padding:8px 0;font-weight:700;font-size:16px;">Total</td>'
        f'<td style="text-align:right;color:#ee2b2b;font-weight:800;font-size:18px;">${_fmt(grand_total_d)}</td></tr>'
    )

    return "".join(rows), grand_total_d


# ── Canonical route-quality copy (Contract B) ─────────────────────────────
# These strings MUST stay in lockstep with
# ``shared/utils/routeSegments.ts::routeQualityLabel`` so the rider app, driver
# app, admin, and every receipt surface word route quality identically. Rules:
#   - complete  → "Actual route" (a trailing "· revision N" is allowed, but
#                 never imply completeness beyond that — no coverage %).
#   - incomplete → "Route recording incomplete — {pct}% GPS coverage"
#                 ({pct} = round(coverage_ratio*100); "GPS coverage unavailable"
#                 when the ratio is absent).
#   - not yet finalized within the window → "Actual route is still processing".
# Never use the word "verified".
_ROUTE_PROCESSING_NOTE = "Actual route is still processing"


def _complete_route_note(revision: int) -> str:
    return f"Actual route · revision {revision}" if revision > 0 else "Actual route"


def _incomplete_route_note(quality: Dict[str, Any]) -> str:
    coverage = quality.get("coverage_ratio")
    if coverage is None:
        return "Route recording incomplete — GPS coverage unavailable"
    return f"Route recording incomplete — {round(float(coverage) * 100)}% GPS coverage"


def _route_snapshot_presentation(ride: Dict[str, Any]) -> tuple[str, str, bool]:
    """Return ``(url, note, is_actual)`` using the canonical Contract B copy.

    ``is_actual`` is True only when a revision-matched v2 snapshot image exists
    to embed/attach. A legacy (v1) ride-level image was generated before
    completion and is therefore only a planned route — never labelled actual.
    An incomplete recording is always stated as incomplete, and no wording ever
    uses "verified".
    """
    schema_version = int(ride.get("route_schema_version") or 1)
    url = str(ride.get("route_snapshot_url") or "")
    if schema_version < 2:
        return (url, "Planned route" if url else "", False)

    revision = int(ride.get("route_revision") or 0)
    snapshot_revision = int(ride.get("snapshot_revision") or 0)
    quality = ride.get("route_quality") if isinstance(ride.get("route_quality"), dict) else {}
    status = str(ride.get("processing_status") or "")

    # The only image we may show/attach is a revision-matched v2 snapshot.
    snapshot_matched = bool(url) and revision > 0 and snapshot_revision == revision

    # Trust processing_status when the finalizer set it; otherwise infer from
    # persisted quality (a matched snapshot or real coverage/gap data means a
    # revision was produced even if the projected ride dict omits the status).
    if status == "complete":
        finalized, incomplete = True, False
    elif status == "incomplete":
        finalized, incomplete = True, True
    else:
        incomplete = bool(quality.get("missing_tail") or quality.get("incomplete_reason"))
        finalized = snapshot_matched or quality.get("coverage_ratio") is not None or incomplete

    if finalized:
        note = _incomplete_route_note(quality) if incomplete else _complete_route_note(revision)
        return (url if snapshot_matched else ""), note, snapshot_matched

    # Finalization has not landed within the bounded receipt window.
    return "", _ROUTE_PROCESSING_NOTE, False


async def _await_route_receipt_projection(ride: Dict[str, Any]) -> Dict[str, Any]:
    """Boundedly wait for v2 finalization before composing a completed receipt.

    Route rendering remains presentation-only: a database or renderer problem
    is logged loudly but never blocks payment settlement or the rest of the
    receipt email.
    """
    if ride.get("status") != "completed" or not ride.get("id"):
        return dict(ride)

    try:
        try:
            from .. import db_supabase
        except ImportError:
            import db_supabase  # type: ignore

        result = dict(ride)
        deadline = asyncio.get_running_loop().time() + _ROUTE_FINALIZATION_WAIT_SECONDS
        while True:
            rows = await db_supabase.get_rows("ride_routes", {"ride_id": ride["id"]}, limit=1)
            route = rows[0] if rows else None
            if route:
                for key in (
                    "route_schema_version",
                    "route_revision",
                    "processing_status",
                    "route_quality",
                    "snapshot_revision",
                ):
                    if key in route:
                        result[key] = route[key]
                # ``processing_status`` flips to complete/incomplete BEFORE the
                # revisioned snapshot is rendered and uploaded, so we must poll
                # for the published object — not just a terminal status — or the
                # immutable email ships without the map. Exit early only once the
                # snapshot for the CURRENT revision exists.
                object_path = route.get("snapshot_object_path")
                snapshot_ready = (
                    bool(object_path)
                    and int(result.get("snapshot_revision") or 0) > 0
                    and int(result.get("snapshot_revision") or 0) == int(result.get("route_revision") or 0)
                )
                if snapshot_ready:
                    result["route_snapshot_url"] = await create_route_snapshot_signed_url(str(object_path))
                    return result
                if int(result.get("route_schema_version") or 1) >= 2:
                    # A v2 route's only valid image is a freshly-signed,
                    # revision-matched snapshot. Drop any inherited/stale URL so
                    # the honest note (never a mismatched map) is what renders.
                    result.pop("route_snapshot_url", None)
                if route.get("processing_status") == "failed":
                    # Terminal: no snapshot will be published for this revision.
                    return result
            if asyncio.get_running_loop().time() >= deadline:
                return result
            await asyncio.sleep(_ROUTE_FINALIZATION_POLL_SECONDS)
    except Exception:
        logger.error("receipt route projection lookup failed for ride_id=%s", ride.get("id"), exc_info=True)
        return dict(ride)


async def _download_route_snapshot(url: str) -> Optional[bytes]:
    """Fetch a previously-published image for the PDF attachment only."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
        if response.status_code == 200 and response.headers.get("content-type", "").startswith("image/"):
            return response.content
        logger.error("receipt route snapshot download failed with status=%s", response.status_code)
    except Exception:
        logger.error("receipt route snapshot download failed", exc_info=True)
    return None


def generate_receipt_html(
    ride: dict,
    rider: dict,
    driver: dict = None,
    tip: Decimal = Decimal(0),
    *,
    include_route_snapshot: bool = True,
    route_map_attached: bool = False,
) -> str:
    """Generate HTML receipt for a completed ride.

    ``route_map_attached`` gates the "A permanent map copy is attached" line so
    it only appears when the caller actually embedded the snapshot bytes as an
    attachment (see :func:`send_receipt_email`) — never merely because a URL was
    available.
    """
    tip_d = _d(tip)
    fare_rows, total_d = _build_fare_rows(ride, tip_d)
    total_str = _fmt(total_d)

    rider_name = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or "Rider"
    driver_name = "Unknown"
    driver_subtitle = "Your driver"
    if driver:
        driver_name = f"{driver.get('first_name', '')} {driver.get('last_name', '')}".strip() or driver.get(
            "name", "Driver"
        )
        # PIPEDA-safe driver reference (+ vehicle) instead of personal contact.
        _bits = [b for b in (driver.get("driver_code", ""), driver.get("driver_vehicle", "")) if b]
        if _bits:
            driver_subtitle = " · ".join(_bits)

    ride_date_raw = ride.get("ride_completed_at") or ride.get("created_at") or ""
    dt = parse_iso_utc(ride_date_raw)
    ride_date = dt.strftime("%B %d, %Y at %I:%M %p") if dt else str(ride_date_raw)

    # Prefer the human-readable ride_code (migration 40); fall back to a
    # truncated UUID for rides that predate the code column. Shown in the
    # receipt so the rider can quote it to support.
    ride_ref = ride.get("ride_code") or (str(ride.get("id", ""))[:8].upper() or "—")

    route_snapshot_url, route_snapshot_note, _route_snapshot_is_actual = _route_snapshot_presentation(ride)
    route_snapshot_html = ""
    if route_snapshot_url and include_route_snapshot:
        route_snapshot_html = f"""
        <tr><td style="padding:0 24px 16px;">
          <p style="font-size:12px;color:#666;margin:0 0 6px;">{route_snapshot_note}</p>
          <img src="{route_snapshot_url}" alt="{route_snapshot_note}" width="472"
               style="width:100%;max-width:472px;height:auto;border-radius:12px;display:block;" />
        </td></tr>
        """
    elif route_snapshot_note:
        attached_copy_note = ""
        if route_map_attached:
            attached_copy_note = " A permanent map copy is attached to this receipt."
        route_snapshot_html = f"""
        <tr><td style="padding:0 24px 16px;">
          <p style="font-size:12px;color:#8a3412;margin:0;">{route_snapshot_note}{attached_copy_note}</p>
        </td></tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;margin:20px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <!-- Header -->
        <tr><td style="background:#ee2b2b;padding:28px 24px;text-align:center;">
        <h1 style="color:#fff;margin:0;font-size:28px;font-weight:800;letter-spacing:-0.5px;">Spinr</h1>
          <p style="color:rgba(255,255,255,0.8);margin:6px 0 0;font-size:14px;">Ride Receipt</p>
        </td></tr>

        <!-- Greeting -->
        <tr><td style="padding:24px 24px 0;">
        <p style="color:#1a1a1a;font-size:16px;margin:0;">Hi {rider_name},</p>
          <p style="color:#888;font-size:14px;margin:6px 0 0;">Thanks for riding with Spinr. Here's your receipt.</p>
        </td></tr>

        <!-- Amount -->
        <tr><td style="padding:20px 24px;text-align:center;">
        <p style="color:#ee2b2b;font-size:42px;font-weight:800;margin:0;">${total_str} CAD</p>
          <p style="color:#999;font-size:12px;margin:4px 0 0;">{ride_date}</p>
          <p style="color:#999;font-size:11px;margin:6px 0 0;letter-spacing:0.5px;">Ride <strong style="color:#1a1a1a;font-weight:700;">{ride_ref}</strong></p>
        </td></tr>

        <!-- Route map (only rendered when snapshot exists) -->
        {route_snapshot_html}
        <!-- Route -->
        <tr><td style="padding:0 24px 16px;">
        <table width="100%" style="background:#f9f9f9;border-radius:12px;padding:16px;">
            <tr>
            <td style="width:24px;vertical-align:top;padding:4px 12px 4px 0;">
                <div style="width:10px;height:10px;border-radius:5px;background:#10b981;margin:4px auto;"></div>
                <div style="width:2px;height:24px;background:#ddd;margin:0 auto;"></div>
                <div style="width:10px;height:10px;border-radius:5px;background:#ee2b2b;margin:0 auto;"></div>
                </td>
              <td>
                <p style="color:#999;font-size:10px;margin:0;text-transform:uppercase;letter-spacing:0.5px;">Pickup</p>
                <p style="color:#1a1a1a;font-size:14px;margin:2px 0 16px;font-weight:500;">{ride.get("pickup_address", "N/A")}</p>
                <p style="color:#999;font-size:10px;margin:0;text-transform:uppercase;letter-spacing:0.5px;">Dropoff</p>
                <p style="color:#1a1a1a;font-size:14px;margin:2px 0 0;font-weight:500;">{ride.get("dropoff_address", "N/A")}</p>
                </td>
            </tr>
            </table>
        </td></tr>

        <!-- Fare Breakdown -->
        <tr><td style="padding:0 24px 16px;">
        <table width="100%" style="font-size:14px;">
            {fare_rows}
            </table>
        </td></tr>

        <!-- Driver -->
        <tr><td style="padding:0 24px 16px;">
        <table width="100%" style="background:#f9f9f9;border-radius:12px;padding:12px 16px;">
            <tr>
            <td style="width:40px;"><div style="width:36px;height:36px;border-radius:18px;background:#e8e8e8;text-align:center;line-height:36px;color:#888;font-weight:700;">{driver_name[0] if driver_name else "?"}</div></td>
              <td style="padding-left:12px;">
                <p style="margin:0;font-size:14px;font-weight:600;color:#1a1a1a;">{driver_name}</p>
                <p style="margin:2px 0 0;font-size:12px;color:#999;">{driver_subtitle}</p>
                </td>
            </tr>
            </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:16px 24px 24px;text-align:center;border-top:1px solid #f0f0f0;">
        <p style="color:#bbb;font-size:12px;margin:0;">Spinr Technologies Inc. · Saskatoon, SK</p>
          <p style="color:#bbb;font-size:11px;margin:4px 0 0;">support@spinr.ca · www.spinr.ca</p>
        </td></tr>
        </table>
    </body>
    </html>
    """


def _receipt_total(ride: dict, tip: float = 0) -> Decimal:
    """Compute the rider-visible total without rendering HTML.

    Used by send_receipt_email for the subject line and log fallback so
    the figure on the email subject matches the body. Prefers the
    persisted ``grand_total`` (set at completion) over re-summing parts.
    """
    tip_d = _d(tip)
    grand_total = ride.get("grand_total")
    if grand_total not in (None, "", 0):
        return _q(_d(grand_total) + tip_d)

    fare = _d(ride.get("total_fare", 0))
    fees = _d(ride.get("area_fees_total", 0))
    tax = _d(ride.get("tax_amount", 0))
    return _q(fare + fees + tax + tip_d)


async def send_receipt_email(
    ride: dict, rider: dict, driver: dict = None, tip: float = 0, recipient_email: Optional[str] = None
):
    """Send an HTML receipt email: AWS SES primary, Resend guardrail.

    Delegates to utils.email_provider.send_transactional_email, which tries
    AWS SES first and falls back to Resend when SES is unconfigured or fails.
    Returns True if either provider accepted the message, False otherwise.

    ``recipient_email`` overrides the destination address (admin "send to a
    different email"). When omitted, the receipt goes to the rider on file.
    The receipt body still reflects the rider — only the To: address changes.
    """
    ride = await _await_route_receipt_projection(ride)
    email = (recipient_email or rider.get("email") or "").strip()
    if not email:
        logger.warning(f"No email for rider {rider.get('id')} — skipping receipt")
        return False

    snapshot_url, snapshot_note, snapshot_is_actual = _route_snapshot_presentation(ride)
    snapshot_bytes = await _download_route_snapshot(snapshot_url) if snapshot_url else None
    # The map is a permanent attachment only if we actually pulled its bytes
    # while the signed URL was valid; the "map attached" line must not over-claim
    # when the download failed.
    route_map_attached = bool(snapshot_bytes and snapshot_is_actual)
    # Private Storage URLs expire. The email body must remain valid long after
    # delivery, so it contains only the quality note; the PDF and PNG contain
    # the immutable bytes downloaded while the signed URL was valid.
    html = generate_receipt_html(
        ride, rider, driver, tip, include_route_snapshot=False, route_map_attached=route_map_attached
    )
    total = _receipt_total(ride, tip)

    try:
        from .email_provider import send_transactional_email
    except ImportError:
        from utils.email_provider import send_transactional_email  # type: ignore

    # Attach a PDF copy of the receipt. Best-effort: a PDF-generation failure
    # must never block the receipt email itself.
    attachments = []
    try:
        try:
            from .receipt_pdf import generate_receipt_pdf
        except ImportError:
            from utils.receipt_pdf import generate_receipt_pdf  # type: ignore
        pdf_bytes = generate_receipt_pdf(
            ride,
            rider,
            driver,
            tip,
            route_snapshot_bytes=snapshot_bytes,
            route_snapshot_note=snapshot_note,
            route_snapshot_is_actual=snapshot_is_actual,
        )
        ref = ride.get("ride_code") or str(ride.get("id", ""))[:8].upper() or "receipt"
        attachments.append({"filename": f"Spinr-receipt-{ref}.pdf", "content": pdf_bytes, "mime": "application/pdf"})
    except Exception:
        logger.error("Receipt PDF generation failed — sending receipt without attachment", exc_info=True)

    if route_map_attached:
        ref = ride.get("ride_code") or str(ride.get("id", ""))[:8].upper() or "receipt"
        attachments.append({"filename": f"Spinr-route-{ref}.png", "content": snapshot_bytes, "mime": "image/png"})

    recipient_user_id = rider.get("id") or ride.get("rider_id")
    return await send_transactional_email(
        to=email,
        subject=f"Your Spinr ride receipt — ${total:.2f}",
        html=html,
        default_from="receipts@spinr.ca",
        log_id=str(recipient_user_id or "-"),
        email_type="receipt",
        recipient_user_id=str(recipient_user_id) if recipient_user_id else None,
        attachments=attachments or None,
    )
