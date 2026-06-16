"""Unit tests for utils.receipt_pdf.generate_receipt_pdf."""

from __future__ import annotations

from decimal import Decimal

try:
    from utils.receipt_pdf import _fare_lines, generate_receipt_pdf
except ImportError:
    from backend.utils.receipt_pdf import _fare_lines, generate_receipt_pdf  # type: ignore[no-redef]

_RIDE = {
    "id": "ride-abc12345",
    "ride_code": "SPIN1234",
    "base_fare": "5.00",
    "distance_fare": "3.00",
    "time_fare": "2.00",
    "booking_fee": "1.00",
    "total_fare": "11.00",
    "grand_total": "12.21",  # 11.00 + GST 0.55 + PST 0.66
    "tax_breakdown": {"GST": {"rate": 5, "amount": "0.55"}, "PST": {"rate": 6, "amount": "0.66"}},
    "distance_km": "10.5",
    "duration_minutes": 15,
    "pickup_address": "123 Main St",
    "dropoff_address": "456 Elm St",
    "ride_completed_at": "2026-06-15T17:30:00Z",
}
_RIDER = {"id": "rider-1", "email": "r@example.com", "first_name": "Al", "last_name": "R"}
_DRIVER = {"first_name": "Dee", "last_name": "Driver", "driver_vehicle": "Toyota Prius"}


def test_pdf_is_valid_bytes():
    pdf = generate_receipt_pdf(_RIDE, _RIDER, _DRIVER, Decimal("2.00"))
    assert isinstance(pdf, (bytes, bytearray))
    assert bytes(pdf).startswith(b"%PDF")
    assert len(pdf) > 800  # a real document, not an empty stub


def test_grand_total_includes_tax_and_tip():
    rows, grand = _fare_lines(_RIDE, Decimal("2.00"))
    # persisted grand_total 12.21 + tip 2.00
    assert grand == Decimal("14.21")
    labels = [r[0] for r in rows]
    assert "GST (5%)" in labels  # tax shown as separate line items
    assert "PST (6%)" in labels
    assert "Tip" in labels


def test_tax_fallback_from_grand_total_gap():
    ride = {**_RIDE, "tax_breakdown": {}}  # legacy ride: no itemised tax
    rows, grand = _fare_lines(ride, Decimal("0"))
    # gap = 12.21 - 11.00 = 1.21 surfaced as a single Tax line
    assert ("Tax", "$1.21") in rows
    assert grand == Decimal("12.21")


def test_no_driver_phone_or_plate_in_pdf():
    # PIPEDA: even if a driver dict carries a phone, it must not be rendered.
    pdf = bytes(generate_receipt_pdf(_RIDE, _RIDER, {**_DRIVER, "phone": "+13065551212"}))
    assert b"3065551212" not in pdf
