"""Pins the legacy_gst_backfill_service filter/join logic against small,
synthetic fixtures (never the real export or a real Supabase connection —
this repo's CSVs are PII and stay out of git; the mocked Supabase client
matches the pattern in test_legacy_payout_correction_service.py).

No live Supabase/Stripe calls anywhere in this module; `supabase.table(...)`
is always mocked here, matching this module's own "read-only, no writes"
contract.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.services import legacy_gst_backfill_service as svc

pytestmark = pytest.mark.unit


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

    # First .range() call returns all candidates (page 1); every subsequent
    # call returns empty, so the pagination loop in _fetch_rows_missing_field
    # terminates after one page — mirrors "fewer than `page` rows returned".
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


def test_resolvable_row_gets_the_real_source_value(tmp_path, monkeypatch):
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [{"_id": "old-booking-1", "payout_gst_amount": "4.50"}])

    candidates = [
        {
            "id": "ride-1",
            "legacy_import_metadata": {"source": "legacy_mongo_booking_import", "old_booking_id": "old-booking-1"},
        }
    ]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)

    assert plan.stats["candidate_rides_missing_field"] == 1
    assert plan.stats["resolvable_against_source"] == 1
    assert plan.stats["unresolvable_no_source_match"] == 0
    # Exact Decimal equality, not pytest.approx — plan.stats now carries the
    # real Decimal (build_backfill_plan no longer coerces to float), so
    # there's no binary-float drift left to tolerate.
    assert plan.stats["sum_old_payout_gst_amount"] == Decimal("4.50")
    (row,) = plan.rows
    assert row.ride_id == "ride-1"
    assert row.old_payout_gst_amount == Decimal("4.50")
    assert row.found_in_source is True


def test_row_already_carrying_the_field_is_never_a_candidate(tmp_path, monkeypatch):
    """The Supabase filter/query mock here represents what the real query
    already excludes server-side (rows WITH the key) -- this test locks in
    that a row with the key present in its metadata dict is never re-added
    to the plan even if it somehow reached this function, so re-running the
    backfill after a partial application can't double up a row."""
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [{"_id": "old-booking-1", "payout_gst_amount": "4.50"}])

    candidates = [
        {
            "id": "ride-1",
            "legacy_import_metadata": {
                "source": "legacy_mongo_booking_import",
                "old_booking_id": "old-booking-1",
                "old_payout_gst_amount": 4.50,  # already backfilled
            },
        }
    ]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)

    assert plan.rows == []
    assert plan.stats["candidate_rides_missing_field"] == 0


def test_no_source_match_is_flagged_not_silently_dropped(tmp_path, monkeypatch):
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [])  # empty -- no booking will match

    candidates = [
        {
            "id": "ride-1",
            "legacy_import_metadata": {"source": "legacy_mongo_booking_import", "old_booking_id": "missing-booking"},
        }
    ]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)

    assert plan.stats["candidate_rides_missing_field"] == 1
    assert plan.stats["resolvable_against_source"] == 0
    assert plan.stats["unresolvable_no_source_match"] == 1
    (row,) = plan.rows
    assert row.found_in_source is False
    assert row.old_payout_gst_amount == svc.ZERO


def test_report_never_touches_tax_amount(tmp_path, monkeypatch):
    """Documentation-level guardrail: the printed report must say plainly
    that tax_amount is untouched, so a reader can't mistake this for the
    D1 tax-value decision being resolved."""
    bookings_csv = tmp_path / "bookings.csv"
    _write_bookings_csv(bookings_csv, [{"_id": "old-booking-1", "payout_gst_amount": "4.50"}])
    candidates = [
        {
            "id": "ride-1",
            "legacy_import_metadata": {"source": "legacy_mongo_booking_import", "old_booking_id": "old-booking-1"},
        }
    ]
    monkeypatch.setattr(svc, "supabase", _fake_supabase_table(candidates))

    plan = svc.build_backfill_plan(bookings_csv)
    report = svc.print_report(plan)

    assert "does NOT change tax_amount" in report
    assert "No rides table writes were made." in report
