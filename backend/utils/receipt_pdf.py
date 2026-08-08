"""
Server-side ride-receipt PDF (fpdf2) for emailing as an attachment.

Mirrors the line items of the HTML/text email receipt — base/distance/time/
booking, itemised area fees, subtotal, GST/PST as separate line items
(Saskatchewan regulatory), tip, and a tax-inclusive grand total — in a
branded layout that matches the red Spinr email receipt.

PIPEDA: this is the rider's own receipt. The driver block is name + vehicle
only — never the driver's phone or plate.

Decimal-only money math (CLAUDE.md): never float.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

try:
    from .company_details import to_latin1
    from .datetime_utils import parse_iso_utc
except ImportError:
    from utils.company_details import to_latin1  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore

_CENT = Decimal("0.01")
_BRAND = (238, 43, 43)  # #ee2b2b
logger = logging.getLogger(__name__)


def _d(v: Any) -> Decimal:
    if v in (None, ""):
        return Decimal("0")
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_UP)


def _money(v: Any) -> str:
    return f"${_q(_d(v)):.2f}"


def _fare_lines(ride: Dict[str, Any], tip: Decimal) -> tuple[list[tuple[str, str]], Decimal]:
    """Build (label, amount) rows + the grand total, mirroring email_receipt."""
    base = _d(ride.get("base_fare"))
    dist = _d(ride.get("distance_fare"))
    time_ = _d(ride.get("time_fare"))
    booking = _d(ride.get("booking_fee"))
    distance_km = _d(ride.get("distance_km"))
    duration_min = ride.get("duration_minutes") or 0

    rows: list[tuple[str, str]] = [
        ("Base fare", _money(base)),
        (f"Distance ({distance_km:.1f} km)", _money(dist)),
        (f"Time ({int(duration_min)} min)", _money(time_)),
    ]
    if booking > 0:
        rows.append(("Booking fee", _money(booking)))

    # Airport surcharge is inside total_fare (calculate_fare) but is a distinct
    # column from area_fees_breakdown — itemise it or the fare rows under-sum.
    airport = _d(ride.get("airport_fee"))
    if airport > 0:
        rows.append(("Airport surcharge", _money(airport)))

    area_fees = ride.get("area_fees_breakdown") or []
    area_total = Decimal("0")
    for fee in area_fees:
        if not isinstance(fee, dict):
            continue
        amount = _d(fee.get("calculated_value", fee.get("amount", 0)))
        if amount == 0:
            continue
        area_total += amount
        rows.append((str(fee.get("name") or fee.get("type") or "Area fee"), _money(amount)))

    # Minimum-fare adjustment: the clamp in calculate_fare floors
    # base+dist+time+booking+airport up to the minimum, and the driver keeps
    # the uplift (0% commission). Itemise it so the fare rows reconcile to the
    # Subtotal — every charge maps to a disclosed line (CLAUDE.md).
    core = base + dist + time_ + booking + airport
    total_fare_val = _d(ride.get("total_fare"))
    min_fare_uplift = max(Decimal("0"), total_fare_val - core) if total_fare_val > 0 else Decimal("0")
    if min_fare_uplift > 0:
        rows.append(("Minimum fare adjustment", _money(min_fare_uplift)))

    subtotal = total_fare_val or (core + area_total)
    rows.append(("__rule__", ""))
    rows.append(("Subtotal", _money(subtotal)))

    # GST/PST as separate line items from the persisted breakdown; fall back to
    # the grand_total gap so the lines reconcile to what was actually charged.
    tax_breakdown = ride.get("tax_breakdown") or {}
    tax_total = Decimal("0")
    had_tax = False
    if isinstance(tax_breakdown, dict):
        for label, payload in tax_breakdown.items():
            if not isinstance(payload, dict):
                continue
            amount = _d(payload.get("amount"))
            rate = _d(payload.get("rate"))
            if amount == 0:
                continue
            had_tax = True
            tax_total += amount
            rate_str = f" ({rate:.0f}%)" if rate > 0 else ""
            rows.append((f"{label}{rate_str}", _money(amount)))

    persisted_grand = ride.get("grand_total")
    if not had_tax and persisted_grand not in (None, "", 0):
        gap = _d(persisted_grand) - subtotal
        if gap > Decimal("0.005"):
            tax_total = gap
            rows.append(("Tax", _money(gap)))

    if tip > 0:
        rows.append(("Tip", _money(tip)))

    if persisted_grand not in (None, "", 0):
        grand = _q(_d(persisted_grand) + tip)
    else:
        grand = _q(subtotal + area_total + tax_total + tip)
    return rows, grand


def generate_receipt_pdf(
    ride: Dict[str, Any],
    rider: Dict[str, Any],
    driver: Optional[Dict[str, Any]] = None,
    tip: Any = Decimal(0),
    route_snapshot_bytes: Optional[bytes] = None,
    route_snapshot_note: Optional[str] = None,
    route_snapshot_is_actual: bool = False,
    company: Optional[Any] = None,
) -> bytes:
    """Return a branded ride-receipt PDF as raw bytes (starts with b'%PDF').

    ``company`` carries the identity resolved from the admin Settings page. It
    is threaded in rather than loaded here because fpdf2 is synchronous and the
    settings read is not — and because the PDF is attached to the receipt
    email, so the two must show the same company or the pair contradicts
    itself, which is worse than either being stale alone. None keeps the
    pre-retrofit lines.
    """
    from fpdf import FPDF  # type: ignore[import-untyped]

    tip_d = _d(tip)
    rows, grand = _fare_lines(ride, tip_d)

    ride_ref = ride.get("ride_code") or (str(ride.get("id", ""))[:8].upper() or "—")
    dt = parse_iso_utc(ride.get("ride_completed_at") or ride.get("created_at") or "")
    date_str = dt.strftime("%B %d, %Y at %I:%M %p") if dt else ""

    driver_name = ""
    vehicle = ""
    driver_code = ""
    if driver:
        driver_name = (
            f"{driver.get('first_name', '')} {driver.get('last_name', '')}".strip() or driver.get("name") or ""
        )
        vehicle = driver.get("driver_vehicle") or driver.get("vehicle") or ""
        driver_code = driver.get("driver_code") or ""

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    left, W = 15, 180

    # Header band
    pdf.set_fill_color(*_BRAND)
    pdf.rect(0, 0, 210, 28, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(left, 8)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 8, "Spinr", ln=True)
    pdf.set_x(left)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, "Ride Receipt", ln=True)
    pdf.set_y(36)
    pdf.set_text_color(0, 0, 0)

    # Amount + meta
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(*_BRAND)
    pdf.cell(W, 11, f"${grand:.2f} CAD", align="C", ln=True)
    pdf.set_text_color(120, 120, 120)
    pdf.set_font("Helvetica", "", 9)
    if date_str:
        pdf.cell(W, 5, date_str, align="C", ln=True)
    pdf.cell(W, 5, f"Ride {ride_ref}", align="C", ln=True)
    pdf.ln(3)

    # The image is supplied only from a revision-matched route snapshot. A
    # planned fallback must never be captioned as an actual trip trace.
    if route_snapshot_bytes:
        try:
            import io

            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(30, 30, 30)
            pdf.cell(W, 6, "Actual route" if route_snapshot_is_actual else "Planned route", ln=True)
            pdf.image(io.BytesIO(route_snapshot_bytes), x=left, w=W)
            pdf.ln(2)
        except Exception:
            # Image is supplemental; the printable text note below remains
            # available even if corrupt image bytes cannot be embedded.
            logger.error("receipt PDF route snapshot embedding failed", exc_info=True)
    if route_snapshot_note:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(120, 80, 20)
        pdf.multi_cell(W, 4, route_snapshot_note.replace("—", "-"))
        pdf.ln(2)

    # Route
    pdf.set_text_color(0, 0, 0)

    def _route(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(W, 5, label.upper(), ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(W, 5, value or "—")
        pdf.ln(1)

    _route("Pickup", ride.get("pickup_address", ""))
    _route("Dropoff", ride.get("dropoff_address", ""))
    pdf.ln(2)

    # Fare breakdown
    pdf.set_draw_color(225, 225, 225)
    for label, amount in rows:
        if label == "__rule__":
            y = pdf.get_y() + 1
            pdf.line(left, y, left + W, y)
            pdf.ln(3)
            continue
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(W * 0.7, 6, label, border=0)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(W * 0.3, 6, amount, border=0, align="R", ln=True)

    # Total box
    pdf.ln(2)
    y = pdf.get_y()
    pdf.set_fill_color(*_BRAND)
    pdf.rect(left, y, W, 11, "F")
    pdf.set_xy(left + 3, y)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(W * 0.5 - 3, 11, "Total Paid", border=0)
    pdf.cell(W * 0.5 - 3, 11, f"${grand:.2f} CAD", border=0, align="R")
    pdf.ln(15)
    pdf.set_text_color(0, 0, 0)

    # Driver (name + vehicle only — PIPEDA)
    if driver_name:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(W, 6, driver_name, ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(150, 150, 150)
        detail = " - ".join(p for p in (driver_code, vehicle) if p) or "Your driver"
        pdf.cell(W, 5, detail, ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

    # Footer
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(150, 150, 150)
    pdf.ln(4)
    identity = company.identity_line if company is not None else "Spinr Technologies Inc. - Saskatoon, SK"
    contact = company.contact_line if company is not None else "support@spinr.ca - www.spinr.ca"
    # fpdf2 core fonts are latin-1; the settings-driven lines can contain an
    # em dash or a middot, which would raise on output. Fold them to ASCII
    # rather than dropping the line.
    pdf.cell(W, 5, to_latin1(identity), align="C", ln=True)
    pdf.cell(W, 5, to_latin1(contact), align="C", ln=True)

    out = pdf.output()
    return bytes(out)
