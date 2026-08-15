"""Pins the legacy_payout_correction_service filter chain against small,
synthetic CSVs (never the real export — that's PII and stays out of git).

No live Supabase/Stripe calls anywhere in this module; `_fetch_already_imported`
is always mocked here, matching the module's own "read-only, no writes" contract.
"""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services import legacy_payout_correction_service as svc

pytestmark = pytest.mark.unit


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def csv_set(tmp_path):
    payments = [
        # kept: due, resolvable booking, real (non-test) tenant
        {
            "_id": "p1",
            "booking_id": "b1",
            "driver_id": "d1",
            "customer_id": "c1",
            "payout_amount": "10.00",
            "pending_amount_status": "due",
        },
        # dropped: not 'due'
        {
            "_id": "p2",
            "booking_id": "b2",
            "driver_id": "d1",
            "customer_id": "c1",
            "payout_amount": "5.00",
            "pending_amount_status": "paid",
        },
        # dropped: booking_id has no matching row in bookings.csv (unresolved)
        {
            "_id": "p3",
            "booking_id": "does-not-exist",
            "driver_id": "d1",
            "customer_id": "c1",
            "payout_amount": "7.00",
            "pending_amount_status": "due",
        },
        # dropped: test-tenant driver (country_code 91)
        {
            "_id": "p4",
            "booking_id": "b4",
            "driver_id": "d-test",
            "customer_id": "c1",
            "payout_amount": "3.00",
            "pending_amount_status": "due",
        },
        # dropped: test-tenant customer (yopmail)
        {
            "_id": "p5",
            "booking_id": "b5",
            "driver_id": "d1",
            "customer_id": "c-test",
            "payout_amount": "4.00",
            "pending_amount_status": "due",
        },
        # kept: second real due row, different driver — goes to group B (not imported)
        {
            "_id": "p6",
            "booking_id": "b6",
            "driver_id": "d2",
            "customer_id": "c1",
            "payout_amount": "2.50",
            "pending_amount_status": "due",
        },
    ]
    bookings = [{"_id": bid} for bid in ("b1", "b4", "b5", "b6")]
    drivers = [
        {"_id": "d1", "country_code": "1", "email": "real@example.com"},
        {"_id": "d2", "country_code": "1", "email": "real2@example.com"},
        {"_id": "d-test", "country_code": "91", "email": "vendor-test@example.com"},
    ]
    customers = [
        {"_id": "c1", "country_code": "1", "email": "rider@example.com"},
        {"_id": "c-test", "country_code": "1", "email": "someone@yopmail.com"},
    ]

    paths = {}
    for name, rows in (
        ("payments", payments),
        ("bookings", bookings),
        ("drivers", drivers),
        ("customers", customers),
    ):
        p = tmp_path / f"{name}.csv"
        _write_csv(p, rows)
        paths[name] = p
    return paths


def test_filter_chain_keeps_only_due_resolvable_non_test_rows(csv_set):
    with patch.object(svc, "_fetch_already_imported", return_value={}):
        plan = svc.build_correction_plan(
            csv_set["payments"], csv_set["bookings"], csv_set["drivers"], csv_set["customers"]
        )
    # p1 and p6 survive; p2 (not due), p3 (unresolved), p4/p5 (test tenant) don't.
    kept_ids = {r.old_booking_id for r in plan.rows}
    assert kept_ids == {"b1", "b6"}
    assert plan.unresolved_row_count == 1
    assert plan.stats["kept_after_filters"] == 2


def test_group_a_vs_group_b_split_on_already_imported(csv_set):
    with patch.object(svc, "_fetch_already_imported", return_value={"b1": ("ride-1", "spinr-driver-1")}):
        plan = svc.build_correction_plan(
            csv_set["payments"], csv_set["bookings"], csv_set["drivers"], csv_set["customers"]
        )
    a_ids = {r.old_booking_id for r in plan.group_a}
    b_ids = {r.old_booking_id for r in plan.group_b}
    assert a_ids == {"b1"}
    assert b_ids == {"b6"}
    assert plan.group_a[0].spinr_ride_id == "ride-1"
    assert plan.group_a[0].spinr_driver_id == "spinr-driver-1"


def test_totals_match_sum_of_kept_rows(csv_set):
    with patch.object(svc, "_fetch_already_imported", return_value={}):
        plan = svc.build_correction_plan(
            csv_set["payments"], csv_set["bookings"], csv_set["drivers"], csv_set["customers"]
        )
    assert plan.stats["total_amount"] == pytest.approx(12.50)  # 10.00 + 2.50


def test_print_report_makes_no_writes_and_is_human_readable(csv_set):
    with patch.object(svc, "_fetch_already_imported", return_value={}) as mock_fetch:
        plan = svc.build_correction_plan(
            csv_set["payments"], csv_set["bookings"], csv_set["drivers"], csv_set["customers"]
        )
        report = svc.print_report(plan)
    mock_fetch.assert_called_once()
    assert "DRY RUN (no writes performed)" in report
    assert "No payouts table writes, no ride inserts, no Stripe calls were made." in report
    assert "$12.50" in report
