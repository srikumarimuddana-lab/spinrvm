"""Generate a branded PDF containing vehicle decals for one or more drivers.

Each driver gets a full-page decal layout with their details. The output is
intended for printing — one decal per page, landscape orientation for a
wider decal shape.

Uses the shared report_branding module for Spinr branding consistency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from ..utils.report_branding import (
        BRAND_FONT,
        BRAND_HEX,
        BRAND_RGB,
        COMPANY_LINE,
        INK_RGB,
        LOGO_PATH,
        MUTED_RGB,
        RULE_RGB,
        has_logo_asset,
        pdf_safe,
    )
except ImportError:
    from utils.report_branding import (  # type: ignore[no-redef]
        BRAND_FONT,
        BRAND_RGB,
        COMPANY_LINE,
        INK_RGB,
        LOGO_PATH,
        MUTED_RGB,
        RULE_RGB,
        has_logo_asset,
        pdf_safe,
    )


def _fmt_date(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y")
    except (ValueError, TypeError):
        return str(iso_str)


def _driver_name(driver: dict[str, Any]) -> str:
    first = driver.get("first_name") or ""
    last = driver.get("last_name") or ""
    return f"{first} {last}".strip() or driver.get("name", "Unknown")


def _vehicle_desc(driver: dict[str, Any]) -> str:
    parts = [
        driver.get("vehicle_year"),
        driver.get("vehicle_make"),
        driver.get("vehicle_model"),
    ]
    return " ".join(str(p) for p in parts if p) or "N/A"


def _render_decal_page(pdf, driver: dict[str, Any]) -> None:
    """Render a single decal page for one driver."""

    pdf.add_page()
    page_w = pdf.w
    page_h = pdf.h

    # -- Top accent bar --
    pdf.set_fill_color(*BRAND_RGB)
    pdf.rect(0, 0, page_w, 5, "F")

    # -- Logo / Company name area --
    y_start = 15
    pdf.set_xy(20, y_start)
    if has_logo_asset():
        pdf.image(str(LOGO_PATH), x=20, y=y_start, h=18)
        pdf.set_xy(20, y_start + 22)
    else:
        pdf.set_font(BRAND_FONT, "B", 28)
        pdf.set_text_color(*BRAND_RGB)
        pdf.cell(0, 12, "SPINR", ln=True)
        pdf.set_xy(20, y_start + 16)

    pdf.set_font(BRAND_FONT, "", 9)
    pdf.set_text_color(*MUTED_RGB)
    pdf.cell(0, 5, pdf_safe("Vehicle Identification Decal"), ln=True)

    # -- Divider --
    divider_y = pdf.get_y() + 4
    pdf.set_draw_color(*RULE_RGB)
    pdf.line(20, divider_y, page_w - 20, divider_y)

    # -- Decal number (prominent, right-aligned in header) --
    decal_number = driver.get("decal_number") or "N/A"
    pdf.set_font(BRAND_FONT, "B", 14)
    pdf.set_text_color(*INK_RGB)
    pdf.set_xy(page_w - 120, y_start)
    pdf.cell(100, 8, pdf_safe(f"Decal # {decal_number}"), align="R")

    # Issue date
    generated_at = driver.get("decal_generated_at") or datetime.now(timezone.utc).isoformat()
    pdf.set_font(BRAND_FONT, "", 9)
    pdf.set_text_color(*MUTED_RGB)
    pdf.set_xy(page_w - 120, y_start + 10)
    pdf.cell(100, 5, pdf_safe(f"Issued: {_fmt_date(generated_at)}"), align="R")

    # -- Main content area --
    content_y = divider_y + 10
    left_col_x = 30
    right_col_x = page_w / 2 + 10
    label_w = 50
    value_w = 80

    def _field(x: float, y: float, label: str, value: str) -> float:
        pdf.set_font(BRAND_FONT, "", 9)
        pdf.set_text_color(*MUTED_RGB)
        pdf.set_xy(x, y)
        pdf.cell(label_w, 6, pdf_safe(label))
        pdf.set_font(BRAND_FONT, "B", 11)
        pdf.set_text_color(*INK_RGB)
        pdf.set_xy(x, y + 6)
        pdf.cell(value_w, 7, pdf_safe(value))
        return y + 18

    # Left column
    y = content_y
    y = _field(left_col_x, y, "DRIVER NAME", _driver_name(driver))
    y = _field(left_col_x, y, "DRIVER CODE", driver.get("driver_code") or "N/A")
    y = _field(left_col_x, y, "VEHICLE", _vehicle_desc(driver))
    y = _field(left_col_x, y, "VEHICLE COLOR", driver.get("vehicle_color") or "N/A")

    # Right column
    y = content_y
    y = _field(right_col_x, y, "LICENSE PLATE", driver.get("license_plate") or "N/A")
    y = _field(right_col_x, y, "VEHICLE YEAR", str(driver.get("vehicle_year") or "N/A"))
    y = _field(right_col_x, y, "SERVICE AREA", driver.get("service_area_name") or driver.get("city") or "N/A")
    y = _field(right_col_x, y, "STATUS", "Active")

    # -- Bottom section: legal notice --
    notice_y = max(y, content_y + 76) + 5
    pdf.set_draw_color(*RULE_RGB)
    pdf.line(20, notice_y, page_w - 20, notice_y)

    pdf.set_font(BRAND_FONT, "", 8)
    pdf.set_text_color(*MUTED_RGB)
    pdf.set_xy(20, notice_y + 4)
    pdf.multi_cell(
        page_w - 40,
        4,
        pdf_safe(
            "This decal certifies that the above-named driver and vehicle are registered with "
            "Spinr Technologies Inc. as a Transportation Network Company (TNC) vehicle in accordance "
            "with applicable provincial regulations. This decal must be displayed on the vehicle "
            "at all times while the driver is logged into the Spinr platform."
        ),
    )

    # -- Footer --
    pdf.set_font(BRAND_FONT, "", 7)
    pdf.set_text_color(*MUTED_RGB)
    pdf.set_y(-15)
    pdf.cell(0, 4.5, pdf_safe(COMPANY_LINE), align="C")


def generate_decal_pdf(drivers: list[dict[str, Any]]) -> bytes:
    """Generate a multi-page PDF with one decal per driver.

    Returns the raw PDF bytes suitable for a Response(content=...,
    media_type="application/pdf").
    """
    from fpdf import FPDF  # type: ignore[import-untyped]

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    for driver in drivers:
        _render_decal_page(pdf, driver)

    return pdf.output()
