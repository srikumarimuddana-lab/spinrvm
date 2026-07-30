"""Smoke tests for utils/driver_statement_pdf.py."""

from __future__ import annotations

from utils.driver_statement_pdf import generate_driver_statement_pdf

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


def test_monthly_title_variant():
    monthly = {**_FIXTURE, "period_type": "monthly", "period_label": "June 2026", "payouts": []}
    pdf = generate_driver_statement_pdf(monthly)
    assert pdf.startswith(b"%PDF")
