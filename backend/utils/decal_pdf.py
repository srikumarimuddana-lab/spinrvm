"""Generate a branded PDF welcome letter for one or more drivers.

Each driver gets a full-page marketing-style welcome letter matching the
Spinr driver recruitment template. The output is intended for printing or
emailing — one letter per page, portrait orientation.

Uses the shared report_branding module for Spinr branding consistency.
Company details (name, website, email, phone) are pulled from admin
settings so the letter stays current without code changes.
"""

from __future__ import annotations

from typing import Any

try:
    from ..utils.report_branding import (
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

NAVY_RGB = (0, 32, 96)


def _driver_name(driver: dict[str, Any]) -> str:
    first = driver.get("first_name") or ""
    last = driver.get("last_name") or ""
    return f"{first} {last}".strip() or driver.get("name", "Driver")


def _driver_address(driver: dict[str, Any]) -> str:
    city = driver.get("service_area_name") or driver.get("city") or ""
    meta = driver.get("legacy_import_metadata") or {}
    if isinstance(meta, dict) and meta.get("address"):
        return str(meta["address"])
    if city:
        return city
    return ""


def _render_welcome_page(
    pdf,
    driver: dict[str, Any],
    company: dict[str, Any] | None = None,
) -> None:
    """Render a single marketing-style welcome letter page for one driver."""

    company = company or {}
    company_name = company.get("company_name") or "Spinr"
    website = company.get("company_website") or "www.spinr.ca"
    contact_email = company.get("company_email") or "drivers@spinr.ca"
    contact_phone = company.get("company_phone") or ""

    if website.startswith("https://"):
        website_display = website[8:]
    elif website.startswith("http://"):
        website_display = website[7:]
    else:
        website_display = website

    name = _driver_name(driver)
    address = _driver_address(driver)

    pdf.add_page()
    page_w = pdf.w
    left_margin = 25
    right_margin = 15
    content_w = page_w - left_margin - right_margin

    # -- Top accent bar --
    pdf.set_fill_color(*BRAND_RGB)
    pdf.rect(0, 0, page_w, 4, "F")

    # -- Header: logo (left) + website (right) --
    header_y = 12
    if has_logo_asset():
        pdf.image(str(LOGO_PATH), x=left_margin, y=header_y, h=16)

    pdf.set_font(BRAND_FONT, "", 10)
    pdf.set_text_color(*MUTED_RGB)
    pdf.set_xy(page_w - 60, header_y + 4)
    pdf.cell(45, 6, pdf_safe(website_display), align="R")

    # -- Divider under header --
    divider_y = header_y + 22
    pdf.set_draw_color(*RULE_RGB)
    pdf.line(left_margin, divider_y, page_w - right_margin, divider_y)

    # -- Driver name and address --
    y = divider_y + 8
    pdf.set_font(BRAND_FONT, "B", 11)
    pdf.set_text_color(*INK_RGB)
    pdf.set_xy(left_margin, y)
    pdf.cell(content_w, 6, pdf_safe(name), ln=True)
    if address:
        pdf.set_xy(left_margin, y + 7)
        pdf.cell(content_w, 6, pdf_safe(address), ln=True)
        y = y + 16
    else:
        y = y + 10

    # -- Greeting --
    y += 4
    pdf.set_font(BRAND_FONT, "B", 11)
    pdf.set_text_color(*INK_RGB)
    pdf.set_xy(left_margin, y)
    pdf.cell(content_w, 7, pdf_safe(f"Hi {name}"), ln=True)
    y += 12

    # -- Body paragraphs --
    line_h = 5.5

    def _para(text: str, *, color: tuple = INK_RGB, spacing: float = 8) -> None:
        nonlocal y
        pdf.set_font(BRAND_FONT, "B", 10)
        pdf.set_text_color(*color)
        pdf.set_xy(left_margin, y)
        pdf.multi_cell(content_w, line_h, pdf_safe(text))
        y = pdf.get_y() + spacing

    _para(
        "You know the reality of driving for the big guys: they take a "
        "massive cut of your hard work, and the rules change constantly."
    )

    _para(f"{company_name} is different. We are built in Saskatchewan, for Saskatchewan.")

    _para(
        "We are launching our driver network in Saskatoon, and we are "
        "inviting top-tier drivers to join our founding fleet. Here is "
        "our promise to you:"
    )

    _para(
        "0% Commission: You keep 100% of the net fare. We only charge a small flat subscription fee monthly.",
        color=NAVY_RGB,
    )

    _para(
        "Fully SGI Compliant: We operate completely above board so you are protected on the road.",
        color=NAVY_RGB,
    )

    _para(
        "Your Next Step: Don't wait. We are capping our initial driver "
        "intake to ensure high ride volume for our founding partners.",
    )

    _para(
        "Go to this exact web address on your phone or computer right now: training.spinr.ca",
    )

    _para(
        f"Once your training is complete, future updates, ride requests, "
        f"and SGI compliance checks will be pushed directly through the "
        f"{company_name} App. Keep your app notifications turned ON.",
    )

    _para(
        "Welcome to the revolution. Let us take our streets back.",
        spacing=12,
    )

    # -- Closing signature --
    pdf.set_font(BRAND_FONT, "", 11)
    pdf.set_text_color(*INK_RGB)
    pdf.set_xy(left_margin, y)
    pdf.cell(content_w, 6, pdf_safe("Best"), ln=True)
    y = pdf.get_y() + 2
    pdf.set_xy(left_margin, y)
    pdf.set_font(BRAND_FONT, "B", 11)
    pdf.cell(content_w, 6, pdf_safe(f"Team {company_name}"), ln=True)

    # -- Footer --
    footer_y = pdf.h - 20

    pdf.set_draw_color(*RULE_RGB)
    pdf.line(left_margin, footer_y - 4, page_w - right_margin, footer_y - 4)

    pdf.set_font(BRAND_FONT, "", 8)
    pdf.set_text_color(*MUTED_RGB)

    footer_parts = []
    if contact_email:
        footer_parts.append(contact_email)
    if contact_phone:
        footer_parts.append(contact_phone)
    if website_display:
        footer_parts.append(website_display)

    footer_text = "  |  ".join(footer_parts) if footer_parts else COMPANY_LINE
    pdf.set_xy(left_margin, footer_y)
    pdf.cell(content_w, 4, pdf_safe(footer_text), align="C")

    pdf.set_xy(left_margin, footer_y + 5)
    pdf.set_font(BRAND_FONT, "B", 8)
    pdf.set_text_color(*BRAND_RGB)
    pdf.cell(content_w, 4, pdf_safe(company_name), align="C")


def generate_decal_pdf(
    drivers: list[dict[str, Any]],
    *,
    company: dict[str, Any] | None = None,
) -> bytes:
    """Generate a multi-page PDF with one welcome letter per driver.

    Returns the raw PDF bytes suitable for a Response(content=...,
    media_type="application/pdf").

    ``company`` is an optional dict of admin settings (company_name,
    company_website, company_email, company_phone) — loaded from
    ``get_app_settings()`` in the calling endpoint. Falls back to
    hardcoded defaults when absent.
    """
    from fpdf import FPDF  # type: ignore[import-untyped]

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    for driver in drivers:
        _render_welcome_page(pdf, driver, company=company)

    return pdf.output()
