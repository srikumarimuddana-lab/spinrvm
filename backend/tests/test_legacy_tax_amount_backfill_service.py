"""Pins the legacy_tax_amount_backfill_service (D1) filter/validation logic
against small, synthetic fixtures (never the real export or a real Supabase
connection — matches the pattern in test_legacy_gst_backfill_service.py).

No live Supabase calls anywhere in this module; `supabase.table(...)` is
always mocked here, matching this module's own "read-only, no writes"
contract.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.services import legacy_tax_amount_backfill_service as svc

pytestmark = pytest.mark.unit

SOURCE = "legacy_mongo_booking_import"


def _write_bookings_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["_id", "payout_gst_amount"])
        w.writeheader()
        w.writerows(rows)


def _fake_supabase_table(candidate_rows: list[dict]):
    """A minimal chain mock: .table().select().filter().range().execute().data"""
    query = MagicMock()
    query.select.return_value = query
    query.filter.return_value = query

    calls = {"n": 0}

    def _range(_start, _end):
        calls["n"] += 1
        result = MagicMock()
        result.execute.return_value.data = candidate_rows if calls["n"] == 1 else []
        return result

    query.range.side_effect = _range

    table = MagicMock()
    table.table.return_value = query
    return table


def _ride(ride_id, old_booking_id, old_payout_gst_amount, tax_amount, total_fare, has_backfill=True):
    meta = {"source": SOURCE, "old_booking_id": old_booking_id}
    if has_backfill:
        meta["old_payout_gst_amount"] = old_payout_gst_amount
    return {"id": ride_id, "legacy_import_metadata": meta, "tax_amount": tax_amount, "total_fare": total_fare}


def test_applyable_row_matches_csv_and_gets_validated_new_tax_amount(tmp_path, monkeypatch):
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [{"_id": "old-1", "payout_gst_amount": "1.00"}])
    candidates = [_ride("ride-1", "old-1", 1.00, tax_amount=0.30, total_fare=20.00)]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)

    assert plan.stats["candidate_rides"] == 1
    assert plan.stats["applyable"] == 1
    assert plan.stats["blocked_csv_drift"] == 0
    (row,) = plan.rows
    assert row.applyable is True
    assert row.old_tax_amount == Decimal("0.30")
    assert row.new_tax_amount == Decimal("1.00")
    assert row.delta == Decimal("0.70")
    assert row.sanity_flag is False  # 5% of $20.00 == $1.00, exact match


def test_csv_drift_blocks_the_row(tmp_path, monkeypatch):
    """Stored old_payout_gst_amount no longer matches bookings.csv -- must
    be blocked, not silently applied, per D1's 'validate, don't blind-copy'
    decision."""
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [{"_id": "old-1", "payout_gst_amount": "1.50"}])
    candidates = [_ride("ride-1", "old-1", 1.00, tax_amount=0.30, total_fare=20.00)]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)

    assert plan.stats["applyable"] == 0
    assert plan.stats["blocked_csv_drift"] == 1
    (row,) = plan.rows
    assert row.applyable is False
    assert row.blocked_reason == "csv_drift"
    # A blocked row's proposed value must equal its current value -- never a
    # half-applied delta for a row this plan refused to validate.
    assert row.new_tax_amount == row.old_tax_amount


def test_csv_unresolved_blocks_the_row(tmp_path, monkeypatch):
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [])  # no booking will match
    candidates = [_ride("ride-1", "missing-booking", 1.00, tax_amount=0.30, total_fare=20.00)]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)

    assert plan.stats["applyable"] == 0
    assert plan.stats["blocked_csv_unresolved"] == 1
    (row,) = plan.rows
    assert row.blocked_reason == "csv_unresolved"


def test_not_yet_backfilled_blocks_the_row(tmp_path, monkeypatch):
    """A legacy row that never got old_payout_gst_amount (e.g. this ran
    before the 2026-08-16 backfill, or a future import batch that skipped
    it) must never fall through to being "applyable" with a $0 proposal."""
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [{"_id": "old-1", "payout_gst_amount": "1.00"}])
    candidates = [_ride("ride-1", "old-1", None, tax_amount=0.30, total_fare=20.00, has_backfill=False)]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)

    assert plan.stats["applyable"] == 0
    assert plan.stats["blocked_not_yet_backfilled"] == 1
    (row,) = plan.rows
    assert row.blocked_reason == "not_yet_backfilled"


def test_sanity_outlier_flagged_but_still_applyable(tmp_path, monkeypatch):
    """CSV validation passes (stored == source), but the value is far from
    5% of total_fare -- flagged for a human to eyeball, not blocked, since
    total_fare is only an approximation of the old app's real GST base."""
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [{"_id": "old-1", "payout_gst_amount": "5.00"}])
    candidates = [_ride("ride-1", "old-1", 5.00, tax_amount=0.30, total_fare=20.00)]  # 5% of $20 is $1.00
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)

    assert plan.stats["applyable"] == 1
    assert plan.stats["sanity_flagged"] == 1
    (row,) = plan.rows
    assert row.applyable is True
    assert row.sanity_flag is True
    assert row.new_tax_amount == Decimal("5.00")


def test_report_lists_blocked_and_outlier_rows_and_never_claims_a_write(tmp_path, monkeypatch):
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(
        bookings_csv,
        [{"_id": "old-1", "payout_gst_amount": "1.50"}, {"_id": "old-2", "payout_gst_amount": "5.00"}],
    )
    candidates = [
        _ride("ride-1", "old-1", 1.00, tax_amount=0.30, total_fare=20.00),  # drift -> blocked
        _ride("ride-2", "old-2", 5.00, tax_amount=0.30, total_fare=20.00),  # sanity outlier -> applyable
    ]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)
    report = svc.print_report(plan)

    assert "ride=ride-1" in report and "reason=csv_drift" in report
    assert "ride=ride-2" in report  # listed under sanity-outlier section
    assert "No rides table writes were made." in report
    assert "does NOT itself write to the database" in report


def test_render_update_sql_only_includes_applyable_rows(tmp_path, monkeypatch):
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(
        bookings_csv,
        [{"_id": "old-1", "payout_gst_amount": "1.50"}, {"_id": "old-2", "payout_gst_amount": "1.00"}],
    )
    candidates = [
        _ride("ride-blocked", "old-1", 1.00, tax_amount=0.30, total_fare=20.00),  # drift -> blocked
        _ride("ride-ok", "old-2", 1.00, tax_amount=0.30, total_fare=20.00),  # applyable
    ]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)
    sql = svc.render_update_sql(plan)

    assert "ride-ok" in sql
    assert "ride-blocked" not in sql
    # Optimistic-concurrency guard present: only overwrite if tax_amount is
    # still what this plan observed.
    assert "r.tax_amount = v.old_tax_amount" in sql


def test_render_update_sql_with_no_applyable_rows_is_a_noop_comment(tmp_path, monkeypatch):
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [{"_id": "old-1", "payout_gst_amount": "1.50"}])
    candidates = [_ride("ride-1", "old-1", 1.00, tax_amount=0.30, total_fare=20.00)]  # drift -> blocked
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)
    sql = svc.render_update_sql(plan)

    assert "UPDATE rides" not in sql
    assert "nothing to update" in sql
