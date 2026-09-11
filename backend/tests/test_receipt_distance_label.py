"""Receipts label the fare line with the distance it was PRICED on (ADR 016, Phase 0).

Ride SPR-T9NYPB (2026-09-11): quoted and fare-locked on 6.89 km; completion
wrote the GPS-measured 12.24 km into rides.distance_km. The in-app receipt
already showed 6.9 km; the emailed HTML and the PDF read distance_km and would
have said "Distance (12.2 km)" beside a fare that never changed.
"""

from __future__ import annotations

try:
    from utils.email_receipt import _build_fare_rows
    from utils.receipt_distance import fare_basis_distance_km
    from utils.receipt_pdf import _fare_lines
except ImportError:  # pragma: no cover
    from backend.utils.email_receipt import _build_fare_rows  # type: ignore[no-redef]
    from backend.utils.receipt_distance import fare_basis_distance_km  # type: ignore[no-redef]
    from backend.utils.receipt_pdf import _fare_lines  # type: ignore[no-redef]

from decimal import Decimal


def _locked_ride(**over):
    ride = {
        "id": "ride-t9nypb",
        "base_fare": "0.02",
        "distance_fare": "0.14",
        "time_fare": "0",
        "booking_fee": "0",
        "total_fare": "0.16",
        "grand_total": "2.93",
        "planned_distance_km": 6.89,
        "distance_km": 12.24,  # GPS-measured, written at completion
        "duration_minutes": 18,
        "fare_breakdown_snapshot": {
            "locked_at": "2026-09-11T13:51:27Z",
            "lines": [{"type": "ride", "label": "Ride fare (6.9 km)", "amount": 0.16}],
        },
    }
    ride.update(over)
    return ride


def test_locked_ride_reports_the_planned_distance():
    assert fare_basis_distance_km(_locked_ride()) == 6.89


def test_unlocked_ride_reports_the_measured_distance():
    ride = _locked_ride()
    del ride["fare_breakdown_snapshot"]
    assert fare_basis_distance_km(ride) == 12.24


def test_locked_ride_without_planned_distance_falls_back_to_measured():
    assert fare_basis_distance_km(_locked_ride(planned_distance_km=None)) == 12.24


def test_lock_is_read_from_the_snapshot_not_a_live_flag():
    """A snapshot with lines but no locked_at (older writer) still counts."""
    ride = _locked_ride()
    ride["fare_breakdown_snapshot"] = {"lines": [{"type": "ride", "amount": 0.16}]}
    assert fare_basis_distance_km(ride) == 6.89


def test_email_receipt_labels_the_quoted_distance_under_fare_lock():
    rows_html, _total = _build_fare_rows(_locked_ride(), Decimal("0"))
    assert "Distance (6.9 km)" in rows_html
    assert "12.2 km" not in rows_html


def test_email_receipt_labels_the_measured_distance_when_not_locked():
    ride = _locked_ride()
    del ride["fare_breakdown_snapshot"]
    rows_html, _total = _build_fare_rows(ride, Decimal("0"))
    assert "Distance (12.2 km)" in rows_html


def test_pdf_receipt_labels_the_quoted_distance_under_fare_lock():
    rows, _grand = _fare_lines(_locked_ride(), Decimal("0"))
    labels = [label for label, _amount in rows]
    assert any(label == "Distance (6.9 km)" for label in labels), labels
    assert not any("12.2" in label for label in labels)


def test_pdf_amounts_are_unchanged_by_the_relabel():
    """Only the label moves; the locked amount is printed as stored."""
    rows, grand = _fare_lines(_locked_ride(), Decimal("0"))
    amount = dict(rows)["Distance (6.9 km)"]
    assert amount == "$0.14"
    assert grand == Decimal("2.93")
