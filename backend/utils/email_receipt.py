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
    [+ Surge line, when surge_multiplier > 1]     ← real dollar delta, see below
    [+ each area fee from area_fees_breakdown]   ← airport, night, …
    subtotal
    [+ each tax from tax_breakdown]              ← GST, PST, HST
    [+ tip]
    total

Surge is folded into ``distance_fare`` / ``time_fare`` at fare-calc time.
Ranked #26 / audit N14 (2026-08-19): this used to surface only as a text
footnote with no dollar figure — a 7-year-retained financial record that
never showed surge as money, unlike the in-app fare breakdown. We now split
the persisted (already-surged) distance/time fares back into pre-surge
display amounts + a real "Surge (X.XX×)" dollar line, using the exact same
Decimal formula as the in-app breakdown
(``routes/rides/_shared.py::_build_fare_breakdown``): the split is
constructed as a plug (``distance_display + time_display + surge_delta ==
distance_fare + time_fare`` exactly), so the rendered total is unchanged —
only the disclosure improves. The footnote stays as supplementary
explanation of the multiplier alongside the new line, not a replacement
for it.
"""

import asyncio
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

import httpx

try:
    from ..repositories.ride_repo import create_route_snapshot_signed_url
    from ..utils.company_details import CompanyDetails, load_company_details
    from ..utils.email_layout import BRAND_RED as _LAYOUT_BRAND_RED
    from ..utils.email_layout import footer_html as _layout_footer_html
    from ..utils.email_layout import header_html as _layout_header_html
    from .datetime_utils import parse_iso_utc
except ImportError:
    from repositories.ride_repo import create_route_snapshot_signed_url  # type: ignore
    from utils.company_details import CompanyDetails, load_company_details  # type: ignore
    from utils.datetime_utils import parse_iso_utc
    from utils.email_layout import BRAND_RED as _LAYOUT_BRAND_RED  # type: ignore
    from utils.email_layout import footer_html as _layout_footer_html  # type: ignore
    from utils.email_layout import header_html as _layout_header_html  # type: ignore

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")
_ROUTE_FINALIZATION_WAIT_SECONDS = 4.0
_ROUTE_FINALIZATION_POLL_SECONDS = 0.5

# ── Pre-retrofit shell ──────────────────────────────────────────────────────
# Kept verbatim so `branded_receipt_enabled = false` restores exactly what
# riders were receiving, with no reconstruction from memory. Pinned by
# tests/test_receipt_shell_snapshot.py. Delete these once the retrofit has
# been seen in real inboxes and the flag is retired.
_LEGACY_BRAND_RED = "#ee2b2b"
_LEGACY_HEADER = (
    '<tr><td style="background:#ee2b2b;padding:28px 24px;text-align:center;">\n'
    '        <h1 style="color:#fff;margin:0;font-size:28px;font-weight:800;letter-spacing:-0.5px;">Spinr</h1>\n'
    '          <p style="color:rgba(255,255,255,0.8);margin:6px 0 0;font-size:14px;">Ride Receipt</p>\n'
    "        </td></tr>"
)
_LEGACY_FOOTER = (
    '<tr><td style="padding:16px 24px 24px;text-align:center;border-top:1px solid #f0f0f0;">\n'
    '        <p style="color:#bbb;font-size:12px;margin:0;">Spinr Technologies Inc. · Saskatoon, SK</p>\n'
    '          <p style="color:#bbb;font-size:11px;margin:4px 0 0;">support@spinr.ca · www.spinr.ca</p>\n'
    "        </td></tr>"
)


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


def _build_fare_rows(
    ride: Dict[str, Any],
    tip: Decimal,
    accent: str = _LEGACY_BRAND_RED,
) -> tuple[str, Decimal]:
    """Render the fare breakdown rows + return the grand total used in the header.

    ``accent`` colours the grand-total row. It is threaded through rather than
    hardcoded so the total does not stay legacy-red inside an otherwise
    rebranded receipt — a mismatch that would read as a rendering bug.

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

    airport_fee = _d(ride.get("airport_fee") or 0)

    # Minimum-fare adjustment: when total_fare was clamped up to the floor at
    # booking, the amount above the itemised components (which the driver keeps
    # — 0% commission) must appear as its own disclosed line so the rendered
    # rows reconcile to the charged total. Every charge maps to a line item
    # (CLAUDE.md: "not a hidden-fee operator"). Legacy rows without total_fare
    # yield 0. Excludes taxes/area fees, which are added below the subtotal.
    # Computed before the surge split below so the split knows whether the
    # floor already absorbed the surge uplift.
    total_fare = _d(ride.get("total_fare") or 0)
    min_fare_uplift = max(
        Decimal("0"),
        total_fare - (base_fare + distance_fare + time_fare + booking_fee + airport_fee),
    )

    # Surge as a real dollar line item (ranked #26 / audit N14), not just a
    # footnote. Surge multiplies only distance+time (see fare_service.calculate_fare),
    # so — matching the exact formula the in-app fare breakdown uses
    # (routes/rides/_shared.py::_build_fare_breakdown) — split the persisted
    # (already-surged) distance/time fares back into pre-surge display amounts
    # + the surge delta. distance_display + time_display + surge_delta always
    # equals distance_fare + time_fare exactly, so the split never changes the
    # reconciled total. When the minimum-fare floor already absorbed the
    # surge, the delta is $0.00 (still shown) rather than double-billed via
    # the Minimum fare adjustment line above.
    surge_delta = Decimal("0")
    distance_display = distance_fare
    time_display = time_fare
    if surge > Decimal("1.0"):
        surged_dt = distance_fare + time_fare
        if min_fare_uplift <= Decimal("0.005"):
            unsurged_dt = _q(surged_dt / surge) if surge > 0 else surged_dt
            distance_display = _q(distance_fare / surge) if surged_dt > 0 and surge > 0 else distance_fare
            time_display = unsurged_dt - distance_display
            surge_delta = max(Decimal("0"), _q(surged_dt - unsurged_dt))

    rows: list[str] = []
    rows.append(_line("Base fare", f"${_fmt(base_fare)}"))
    rows.append(_line(f"Distance ({distance_km:.1f} km)", f"${_fmt(distance_display)}"))
    rows.append(_line(f"Time ({duration_min} min)", f"${_fmt(time_display)}"))
    if booking_fee > 0:
        rows.append(_line("Booking fee", f"${_fmt(booking_fee)}"))
    # Airport surcharge is inside total_fare (calculate_fare) but is a distinct
    # column from area_fees_breakdown — itemise it or the rows under-sum the
    # header on airport rides.
    if airport_fee > 0:
        rows.append(_line("Airport surcharge", f"${_fmt(airport_fee)}"))

    if surge > Decimal("1.0"):
        rows.append(
            _line(f"Surge ({surge:.2f}×)", f"${_fmt(surge_delta)}", label_color="#b45309", amount_color="#b45309")
        )

    if min_fare_uplift > 0:
        rows.append(_line("Minimum fare adjustment", f"${_fmt(min_fare_uplift)}"))

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

    # Surge footnote — supplementary context alongside the Surge dollar line
    # item above (not a replacement for it; ranked #26 / audit N14).
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
        grand_total_d = _q(
            base_fare
            + distance_fare
            + time_fare
            + booking_fee
            + airport_fee
            + min_fare_uplift
            + area_fees_total
            + tax_total
            + tip
        )
    else:
        # Persisted grand_total includes fees + tax but NOT tip — tip is
        # added at rating time after settlement.
        grand_total_d = _q(_d(grand_total) + tip)

    rows.append('<tr><td colspan="2" style="border-top:1px solid #eee;padding:0;"></td></tr>')
    rows.append(
        '<tr><td style="color:#1a1a1a;padding:8px 0;font-weight:700;font-size:16px;">Total</td>'
        f'<td style="text-align:right;color:{accent};font-weight:800;font-size:18px;">${_fmt(grand_total_d)}</td></tr>'
    )

    return "".join(rows), grand_total_d


def _route_snapshot_presentation(ride: Dict[str, Any]) -> tuple[str, str, bool]:
    """Return ``(url, note, is_actual)`` without ever mislabeling a map.

    A legacy ride-level image was generated before completion and is therefore
    only a planned route. V2 images may be called actual only if their stored
    revision equals the finalized route revision.
    """
    schema_version = int(ride.get("route_schema_version") or 1)
    url = str(ride.get("route_snapshot_url") or "")
    if schema_version < 2:
        return (url, "Planned route" if url else "", False)

    revision = int(ride.get("route_revision") or 0)
    snapshot_revision = int(ride.get("snapshot_revision") or 0)
    quality = ride.get("route_quality") if isinstance(ride.get("route_quality"), dict) else {}
    coverage = quality.get("coverage_ratio")
    coverage_text = (
        f"{round(float(coverage) * 100)}% GPS coverage" if coverage is not None else "GPS coverage unavailable"
    )
    if url and revision > 0 and snapshot_revision == revision:
        return url, f"Actual route (revision {revision}) — {coverage_text}", True
    if quality.get("missing_tail") or quality.get("incomplete_reason"):
        coverage_note = f"{round(float(coverage) * 100)}% coverage" if coverage is not None else "coverage unavailable"
        return "", f"Route snapshot unavailable — GPS capture was incomplete ({coverage_note}).", False
    return "", "Route snapshot unavailable — actual route processing is still pending.", False


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
                if route.get("processing_status") in {"complete", "incomplete", "failed"}:
                    object_path = route.get("snapshot_object_path")
                    if object_path and int(result.get("snapshot_revision") or 0) == int(
                        result.get("route_revision") or 0
                    ):
                        result["route_snapshot_url"] = await create_route_snapshot_signed_url(str(object_path))
                    else:
                        result.pop("route_snapshot_url", None)
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
    company: Optional["CompanyDetails"] = None,
    show_pickup_leg: bool = False,
) -> str:
    """Generate HTML receipt for a completed ride.

    Args:
        company: Resolved company identity. When supplied, the receipt renders
            with the shared branded shell — real logo, documented brand red, and
            the company name and address from the admin Settings page. When
            None it falls back to the original bespoke shell, byte-for-byte.

    Stays synchronous so the fare-row tests can drive it directly; the async
    caller (:func:`send_receipt_email`) resolves the identity and passes it,
    gated on ``branded_receipt_enabled``.
    """
    tip_d = _d(tip)
    accent = _LAYOUT_BRAND_RED if company is not None else _LEGACY_BRAND_RED
    fare_rows, total_d = _build_fare_rows(ride, tip_d, accent)
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
        if route_snapshot_url and _route_snapshot_is_actual:
            attached_copy_note = " A permanent map copy is attached to this receipt."
        route_snapshot_html = f"""
        <tr><td style="padding:0 24px 16px;">
          <p style="font-size:12px;color:#8a3412;margin:0;">{route_snapshot_note}{attached_copy_note}</p>
        </td></tr>
        """

    # Pickup-leg context (flag-gated): the driver's approach distance, shown
    # as plain information NEXT TO the map — deliberately outside the fare
    # table, and explicitly marked not charged, so it can never read as a
    # hidden fee (CLAUDE.md: every charged amount maps to a fare line; this
    # is not one). Fare math above is untouched.
    if show_pickup_leg:
        _pd = ride.get("phase_distances") or {}
        pickup_leg_km = _d(_pd.get("navigating_to_pickup") or 0) + _d(_pd.get("arrived_at_pickup") or 0)
        if pickup_leg_km > 0:
            route_snapshot_html += f"""
        <tr><td style="padding:0 24px 12px;">
          <p style="font-size:12px;color:#666;margin:0;">Driver's approach to pickup: {pickup_leg_km:.1f} km — not charged.</p>
        </td></tr>
        """

    # Shell only. Everything below the header — amount, route, fare rows,
    # driver card — is identical either way apart from the accent colour; the
    # flag governs the wrapper, not the tax-bearing content. See migration 288.
    if company is not None:
        header = _layout_header_html(company, "Ride Receipt")
        footer = _layout_footer_html(company)
        brand_sentence = company.name_sentence
    else:
        header = _LEGACY_HEADER
        footer = _LEGACY_FOOTER
        brand_sentence = "Spinr."

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;margin:20px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <!-- Header -->
        {header}

        <!-- Greeting -->
        <tr><td style="padding:24px 24px 0;">
        <p style="color:#1a1a1a;font-size:16px;margin:0;">Hi {rider_name},</p>
          <p style="color:#888;font-size:14px;margin:6px 0 0;">Thanks for riding with {brand_sentence} Here's your receipt.</p>
        </td></tr>

        <!-- Amount -->
        <tr><td style="padding:20px 24px;text-align:center;">
        <p style="color:{accent};font-size:42px;font-weight:800;margin:0;">${total_str} CAD</p>
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
                <div style="width:10px;height:10px;border-radius:5px;background:{accent};margin:0 auto;"></div>
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
        {footer}
        </table>
    </body>
    </html>
    """


async def _branded_company() -> Optional[CompanyDetails]:
    """Resolved company identity, or None to keep the pre-retrofit shell.

    Never raises: a settings failure falls back to the legacy shell rather than
    dropping a receipt. The receipt is the one email a rider may actually need
    later, so "send the old-looking one" always beats "send nothing".
    """
    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    try:
        settings = await get_app_settings()
        if not bool(settings.get("branded_receipt_enabled", True)):
            return None
        return await load_company_details()
    except Exception as exc:
        logger.warning("receipt branding: falling back to the legacy shell: %s", exc)
        return None


def generate_receipt_text(
    ride: dict,
    rider: dict,
    driver: dict = None,
    tip: Decimal = Decimal(0),
    *,
    company: Optional["CompanyDetails"] = None,
) -> str:
    """Plain-text receipt, carrying the same disclosed line items as the HTML.

    Not a courtesy copy: on a tax-bearing document the text part has to show
    the same GST/PST breakdown, or a recipient reading it has an incomplete
    record. Rows are derived from the same ``_build_fare_rows`` output the HTML
    uses, so the two cannot list different charges.
    """
    tip_d = _d(tip)
    fare_rows_html, total_d = _build_fare_rows(ride, tip_d)
    rider_name = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or "Rider"
    ride_ref = ride.get("ride_code") or (str(ride.get("id", ""))[:8].upper() or "—")
    dt = parse_iso_utc(ride.get("ride_completed_at") or ride.get("created_at") or "")
    ride_date = dt.strftime("%B %d, %Y at %I:%M %p") if dt else ""

    lines = [
        f"Hi {rider_name},",
        "",
        f"Thanks for riding with {company.name_sentence if company else 'Spinr.'} Here's your receipt.",
        "",
        f"Total: ${_fmt(total_d)} CAD",
    ]
    if ride_date:
        lines.append(ride_date)
    lines += [
        f"Ride {ride_ref}",
        "",
        f"Pickup:  {ride.get('pickup_address', 'N/A')}",
        f"Dropoff: {ride.get('dropoff_address', 'N/A')}",
        "",
        "Fare breakdown",
    ]
    lines += [f"  {label}  {amount}" for label, amount in _fare_rows_as_pairs(fare_rows_html)]
    if driver:
        driver_name = f"{driver.get('first_name', '')} {driver.get('last_name', '')}".strip() or driver.get(
            "name", "Driver"
        )
        bits = [b for b in (driver.get("driver_code", ""), driver.get("driver_vehicle", "")) if b]
        lines += ["", f"Driver: {driver_name}" + (f" ({' · '.join(bits)})" if bits else "")]
    lines += ["", "--"]
    if company is not None:
        lines += [company.identity_line, company.contact_line]
    else:
        lines += ["Spinr Technologies Inc. · Saskatoon, SK", "support@spinr.ca · www.spinr.ca"]
    return "\n".join(lines).strip() + "\n"


def _fare_rows_as_pairs(fare_rows_html: str) -> list[tuple[str, str]]:
    """Recover (label, amount) pairs from the rendered fare rows.

    Reading back the HTML rather than re-deriving the rows is deliberate: it
    makes it structurally impossible for the text part to list a different set
    of charges than the HTML part of the same receipt.
    """
    import re as _re

    pairs: list[tuple[str, str]] = []
    for row in _re.findall(r"<tr>(.*?)</tr>", fare_rows_html, _re.S):
        cells = [_re.sub(r"<[^>]+>", "", c).strip() for c in _re.findall(r"<td[^>]*>(.*?)</td>", row, _re.S)]
        cells = [c for c in cells if c]
        if len(cells) == 2:
            pairs.append((cells[0], cells[1]))
    return pairs


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
    company = await _branded_company()
    # Pickup-leg context line (flag-gated, informational — never a fare row).
    show_pickup_leg = False
    try:
        try:
            from ..settings_loader import get_app_settings  # type: ignore
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        show_pickup_leg = bool(((await get_app_settings()) or {}).get("rider_show_pickup_leg_enabled", False))
    except Exception:
        # Fail-safe direction: omit the informational line. Still ERROR with
        # the underlying exception — a settings read failing on the receipt
        # path must surface loudly (CLAUDE.md: never silently swallow).
        logger.error("pickup-leg receipt flag read failed; omitting the line", exc_info=True)
    # Private Storage URLs expire. The email body must remain valid long after
    # delivery, so it contains only the quality note; the PDF and PNG contain
    # the immutable bytes downloaded while the signed URL was valid.
    html = generate_receipt_html(
        ride,
        rider,
        driver,
        tip,
        include_route_snapshot=False,
        company=company,
        show_pickup_leg=show_pickup_leg,
    )
    total = _receipt_total(ride, tip)
    # The receipt shipped HTML-only, so a text-only client, a screen reader or a
    # blocked-image view got nothing at all — on the one email that doubles as a
    # tax record. Both parts also let the provider build a real
    # multipart/alternative, which helps inbox placement.
    text = generate_receipt_text(ride, rider, driver, tip, company=company)

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
            # Same identity as the email body. If the attachment and the mail it
            # arrives in named different companies, that would be worse than
            # either being stale on its own.
            company=company,
        )
        ref = ride.get("ride_code") or str(ride.get("id", ""))[:8].upper() or "receipt"
        attachments.append({"filename": f"Spinr-receipt-{ref}.pdf", "content": pdf_bytes, "mime": "application/pdf"})
    except Exception:
        logger.error("Receipt PDF generation failed — sending receipt without attachment", exc_info=True)

    if snapshot_bytes and snapshot_is_actual:
        ref = ride.get("ride_code") or str(ride.get("id", ""))[:8].upper() or "receipt"
        attachments.append({"filename": f"Spinr-route-{ref}.png", "content": snapshot_bytes, "mime": "image/png"})

    recipient_user_id = rider.get("id") or ride.get("rider_id")
    return await send_transactional_email(
        to=email,
        subject=f"Your Spinr ride receipt — ${total:.2f}",
        html=html,
        text=text,
        default_from="receipts@spinr.ca",
        log_id=str(recipient_user_id or "-"),
        email_type="receipt",
        recipient_user_id=str(recipient_user_id) if recipient_user_id else None,
        attachments=attachments or None,
    )
