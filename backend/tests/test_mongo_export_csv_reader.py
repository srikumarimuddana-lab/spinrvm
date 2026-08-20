"""Regression tests for driver_import_service.read_mongo_export_csv.

Added 2026-08-20 alongside the fix it locks in: read_csv's header
normalization (normalize_header) is tuned for the bespoke Saskatoon driver
recruitment CSV and silently corrupts a raw MongoDB export's own column
names when reused for banks.csv/drivers.csv/vehicle_details.csv --
_id -> id (breaking every ObjectId-keyed join, since join_legacy_bank_sin_dob
and join_legacy_vehicle_details both key off row["_id"]) and, for
vehicle_details.csv specifically, its "name" column (the vehicle's make)
colliding with the alias table's "name" -> "full_name" rewrite (meant for a
person's name). Confirmed against the real cached export: the SIN/DOB
backfill's phone crosswalk silently resolved 0/157 rows via the broken
read_csv path, vs 157/157 via read_mongo_export_csv.
See docs/change-log/2026-08-20-mongo-export-header-normalization-bug.md.
"""

from __future__ import annotations

import csv

from backend.services import driver_import_service as svc


def _write_csv(tmp_path, name: str, header: list[str], rows: list[list[str]]):
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_read_mongo_export_csv_preserves_leading_underscore_columns(tmp_path):
    path = _write_csv(tmp_path, "drivers.csv", ["_id", "__v", "phone"], [["abc123", "0", "3065551234"]])
    rows = svc.read_mongo_export_csv(path)
    assert rows[0]["_id"] == "abc123"
    assert rows[0]["__v"] == "0"
    assert rows[0]["phone"] == "3065551234"


def test_read_mongo_export_csv_does_not_apply_saskatoon_csv_aliases(tmp_path):
    """ "name" on a raw Mongo export must stay "name" -- read_csv's alias
    table would rewrite it to "full_name", which is correct for the
    Saskatoon CSV's person-name column but wrong for vehicle_details.csv's
    vehicle-make column of the same name."""
    path = _write_csv(tmp_path, "vehicle_details.csv", ["_id", "name"], [["veh1", "Toyota"]])
    rows = svc.read_mongo_export_csv(path)
    assert rows[0]["_id"] == "veh1"
    assert rows[0]["name"] == "Toyota"
    assert "full_name" not in rows[0]


def test_read_csv_is_the_wrong_function_for_a_mongo_export_regression_guard(tmp_path):
    """Locks in *why* read_mongo_export_csv exists: read_csv (correct for
    the Saskatoon CSV) demonstrably mangles the exact columns the Mongo-
    export join functions depend on. If this test ever starts failing
    because read_csv stopped mangling these columns, read_mongo_export_csv's
    own docstring should be revisited, not deleted -- the two readers exist
    for genuinely different CSV dialects, not as a historical accident."""
    path = _write_csv(tmp_path, "vehicle_details.csv", ["_id", "name"], [["veh1", "Toyota"]])
    rows = svc.read_csv(path)
    assert "_id" not in rows[0]
    assert rows[0].get("id") == "veh1"
    assert rows[0].get("full_name") == "Toyota"  # the corrupting rewrite this bug fix avoids


def test_read_mongo_export_csv_strips_whitespace_like_read_csv(tmp_path):
    path = _write_csv(tmp_path, "banks.csv", ["_id", "sin"], [["  bank1  ", "  130692544  "]])
    rows = svc.read_mongo_export_csv(path)
    assert rows[0]["_id"] == "bank1"
    assert rows[0]["sin"] == "130692544"
