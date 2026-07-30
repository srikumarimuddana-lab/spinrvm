"""Smoke tests for utils/driver_statement_pdf.py."""

from __future__ import annotations

import io

from utils.driver_statement_pdf import generate_driver_statement_pdf


def _pdf_text(pdf_bytes: bytes) -> str:
    """Extracted page text. fpdf2 compresses content streams, so asserting on
    raw bytes would silently never match — parse it properly (pypdf is already
    a backend dependency for the SGI AcroForm filler)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(page.extract_text() or "" for page in reader.pages)

_FIXTURE = {
    "driver_name": "Test Driver",
    "period_type": "weekly",
    "period_label": "Jul 20 - 26, 2026",
    "trips": 3,
    "distance_km": 42.5,
    "duration_minutes": 95,
    "earnings": {
        "ride_earnings": "180.00",
        "tips_included": "12.00",
        "incentives": "5.00",
        "bonuses": "25.00",
        "cancellation_fees": "5.00",
        "tax_collected": "9.90",
        "total": "224.90",
    },
    "payouts": [
        {"date": "2026-07-22", "label": "Standard payout", "amount": "100.00", "fee": "0.00", "net": "100.00", "status": "completed"},
        {"date": "2026-07-24", "label": "Instant payout", "amount": "50.00", "fee": "0.75", "net": "49.25", "status": "completed"},
    ],
    "payouts_total": "150.00",
}


def test_generates_pdf_bytes():
    pdf = generate_driver_statement_pdf(_FIXTURE)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1500


def test_handles_empty_statement_without_raising():
    pdf = generate_driver_statement_pdf({})
    assert pdf.startswith(b"%PDF")


def test_payout_row_cap_is_disclosed_not_silent():
    """The table caps at _MAX_PAYOUT_ROWS but 'Total paid out' covers EVERY
    payout — so the overflow must be stated on the document. A silent cap on a
    statement a driver may file for tax would look like the numbers don't add
    up (and CLAUDE.md forbids silent truncation)."""
    from utils.driver_statement_pdf import _MAX_PAYOUT_ROWS

    many = {
        **_FIXTURE,
        "payouts": [
            {"date": "2026-07-01", "label": "Instant payout", "amount": "10.00", "fee": "0.50", "net": "9.50", "status": "completed"}
            for _ in range(_MAX_PAYOUT_ROWS + 5)
        ],
    }
    text = _pdf_text(generate_driver_statement_pdf(many))
    assert "5 more payouts not shown" in text
    assert f"showing the first {_MAX_PAYOUT_ROWS} of {_MAX_PAYOUT_ROWS + 5}" in text


def test_no_overflow_note_when_every_payout_fits():
    text = _pdf_text(generate_driver_statement_pdf(_FIXTURE))
    assert "not shown" not in text


def test_monthly_title_variant():
    monthly = {**_FIXTURE, "period_type": "monthly", "period_label": "June 2026", "payouts": []}
    pdf = generate_driver_statement_pdf(monthly)
    assert pdf.startswith(b"%PDF")
