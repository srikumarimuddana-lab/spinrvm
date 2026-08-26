"""Unit tests for backend/scripts/reconcile_legacy_driver_earnings.py.

Read-only diagnostic script -- no commit_plan/write path to test. Covers the
two pieces of real logic: driverearnings.csv grouping (must mirror
booking_import_service._earnings_by_booking's own rules exactly) and the
match/fallback/mismatch bucketing against rides already in the DB (mocked).
"""

from decimal import Decimal

import pytest

from backend.scripts import reconcile_legacy_driver_earnings as script
from backend.services.booking_import_service import IMPORT_SOURCE

pytestmark = pytest.mark.unit


def _write_csv(tmp_path, rows, fieldnames=("_id", "booking_id", "amount", "earning_type")):
    import csv

    path = tmp_path / "driverearnings.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return str(path)


def _ride(*, old_booking_id, driver_earnings, status="completed", source=IMPORT_SOURCE):
    return {
        "id": f"ride-{old_booking_id}",
        "driver_earnings": driver_earnings,
        "status": status,
        "legacy_import_metadata": {"source": source, "old_booking_id": old_booking_id},
    }


# --------------------------------------------------------------------------
# _load_earnings_by_booking — CSV grouping
# --------------------------------------------------------------------------


def test_groups_and_sums_by_booking_id(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            {"_id": "e1", "booking_id": "bk-1", "amount": "10.00", "earning_type": "salary"},
            {"_id": "e2", "booking_id": "bk-1", "amount": "2.50", "earning_type": "salary"},
        ],
    )
    sums, counts, unparseable = script._load_earnings_by_booking(path)
    assert sums["bk-1"] == Decimal("12.50")
    assert counts["bk-1"] == 2
    assert unparseable == []


def test_blank_booking_id_rows_are_ignored_same_as_importer(tmp_path):
    """Referral-bonus rows carry an empty booking_id -- must be excluded,
    mirroring booking_import_service._earnings_by_booking exactly."""
    path = _write_csv(
        tmp_path,
        [
            {"_id": "e1", "booking_id": "", "amount": "20.00", "earning_type": "refer"},
            {"_id": "e2", "booking_id": "bk-1", "amount": "5.00", "earning_type": "salary"},
        ],
    )
    sums, counts, _ = script._load_earnings_by_booking(path)
    assert "bk-1" in sums
    assert len(sums) == 1
    assert sums["bk-1"] == Decimal("5.00")


def test_unparseable_amount_is_flagged_not_silently_zeroed(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            {"_id": "e1", "booking_id": "bk-1", "amount": "not-a-number", "earning_type": "salary"},
        ],
    )
    sums, counts, unparseable = script._load_earnings_by_booking(path)
    assert sums["bk-1"] == Decimal("0")
    assert unparseable == ["e1"]


def test_blank_amount_is_a_real_zero_not_flagged(tmp_path):
    path = _write_csv(tmp_path, [{"_id": "e1", "booking_id": "bk-1", "amount": "", "earning_type": "salary"}])
    sums, counts, unparseable = script._load_earnings_by_booking(path)
    assert sums["bk-1"] == Decimal("0")
    assert unparseable == []


# --------------------------------------------------------------------------
# reconcile() — bucketing against DB rows
# --------------------------------------------------------------------------


async def _reconcile_with(monkeypatch, tmp_path, csv_rows, rides):
    path = _write_csv(tmp_path, csv_rows)

    async def _fake_get_rows(*args, **kwargs):
        return rides

    monkeypatch.setattr(script.db_supabase, "get_rows", _fake_get_rows)
    return await script.reconcile(path, tolerance=Decimal("0.01"), limit=None)


@pytest.mark.anyio
async def test_matching_amounts_are_bucketed_match(monkeypatch, tmp_path):
    results = await _reconcile_with(
        monkeypatch,
        tmp_path,
        [{"_id": "e1", "booking_id": "bk-1", "amount": "16.72", "earning_type": "salary"}],
        [_ride(old_booking_id="bk-1", driver_earnings=16.72)],
    )
    (r,) = results
    assert r.bucket == "match"


@pytest.mark.anyio
async def test_within_tolerance_still_matches(monkeypatch, tmp_path):
    results = await _reconcile_with(
        monkeypatch,
        tmp_path,
        [{"_id": "e1", "booking_id": "bk-1", "amount": "16.72", "earning_type": "salary"}],
        [_ride(old_booking_id="bk-1", driver_earnings=16.725)],
    )
    (r,) = results
    assert r.bucket == "match"


@pytest.mark.anyio
async def test_no_ledger_rows_is_fallback_not_mismatch(monkeypatch, tmp_path):
    """The 4-row you_earn-fallback case: no driverearnings.csv row exists at
    all for this booking -- expected, must not be reported as a mismatch."""
    results = await _reconcile_with(
        monkeypatch,
        tmp_path,
        [],
        [_ride(old_booking_id="bk-1", driver_earnings=7.54)],
    )
    (r,) = results
    assert r.bucket == "fallback"


@pytest.mark.anyio
async def test_real_discrepancy_is_a_mismatch(monkeypatch, tmp_path):
    results = await _reconcile_with(
        monkeypatch,
        tmp_path,
        [{"_id": "e1", "booking_id": "bk-1", "amount": "16.72", "earning_type": "salary"}],
        [_ride(old_booking_id="bk-1", driver_earnings=10.00)],
    )
    (r,) = results
    assert r.bucket == "mismatch"
    assert r.ledger_sum == Decimal("16.72")
    assert r.stored_driver_earnings == Decimal("10.00")


@pytest.mark.anyio
async def test_cancelled_rides_are_skipped(monkeypatch, tmp_path):
    results = await _reconcile_with(
        monkeypatch,
        tmp_path,
        [{"_id": "e1", "booking_id": "bk-1", "amount": "16.72", "earning_type": "salary"}],
        [_ride(old_booking_id="bk-1", driver_earnings=0.0, status="cancelled")],
    )
    assert results == []


@pytest.mark.anyio
async def test_rides_from_a_different_import_source_are_skipped(monkeypatch, tmp_path):
    results = await _reconcile_with(
        monkeypatch,
        tmp_path,
        [{"_id": "e1", "booking_id": "bk-1", "amount": "16.72", "earning_type": "salary"}],
        [_ride(old_booking_id="bk-1", driver_earnings=16.72, source="legacy_mongo_driver_import")],
    )
    assert results == []


@pytest.mark.anyio
async def test_main_returns_nonzero_exit_code_when_mismatches_found(monkeypatch, tmp_path, capsys):
    path = _write_csv(tmp_path, [{"_id": "e1", "booking_id": "bk-1", "amount": "16.72", "earning_type": "salary"}])

    async def _fake_get_rows(*args, **kwargs):
        return [_ride(old_booking_id="bk-1", driver_earnings=1.00)]

    monkeypatch.setattr(script.db_supabase, "get_rows", _fake_get_rows)

    exit_code = await script.main(path, tolerance=Decimal("0.01"), limit=None)
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "MISMATCHES" in out
    assert "bk-1" in out


@pytest.mark.anyio
async def test_main_returns_zero_when_clean(monkeypatch, tmp_path):
    path = _write_csv(tmp_path, [{"_id": "e1", "booking_id": "bk-1", "amount": "16.72", "earning_type": "salary"}])

    async def _fake_get_rows(*args, **kwargs):
        return [_ride(old_booking_id="bk-1", driver_earnings=16.72)]

    monkeypatch.setattr(script.db_supabase, "get_rows", _fake_get_rows)

    exit_code = await script.main(path, tolerance=Decimal("0.01"), limit=None)
    assert exit_code == 0
