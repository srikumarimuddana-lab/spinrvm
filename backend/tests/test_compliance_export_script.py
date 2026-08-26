"""Tests for scripts/compliance_export.py — the on-demand trip-record export
for an SGI/regulator subpoena request. Redaction is pure-function tested
(no DB); the scan/audit-write path is tested with get_rows/insert_one mocked,
mirroring test_compliance_reports.py's direct-patch style."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

from compliance_export import redact_row, run_export  # noqa: E402

pytestmark = pytest.mark.unit


def _period_row(
    ride_id="r1",
    driver_id="d1",
    period=3,
    started="2026-07-05T10:00:00Z",
    ended="2026-07-05T10:20:00Z",
    ride=None,
    is_reconstructed=False,
):
    return {
        "id": "p1",
        "driver_id": driver_id,
        "period": period,
        "started_at": started,
        "ended_at": ended,
        "ride_id": ride_id,
        "is_reconstructed": is_reconstructed,
        "rides": ride
        if ride is not None
        else {
            "id": ride_id,
            "status": "completed",
            "created_at": "2026-07-05T09:55:00Z",
            "planned_distance_km": 5.2,
            "actual_distance_km": 5.6,
            "phase_distances": {"trip_in_progress": 5.6},
            "total_fare": "18.75",
        },
    }


class TestRedactRow:
    def test_shapes_expected_fields(self):
        record = redact_row(_period_row())
        assert record["ride_id"] == "r1"
        assert record["driver_id"] == "d1"
        assert record["insurance_period"] == 3
        assert record["ride_status"] == "completed"
        assert record["actual_distance_km"] == 5.6
        assert record["total_fare_cad"] == "18.75"
        assert json.loads(record["phase_distances"]) == {"trip_in_progress": 5.6}
        assert record["is_reconstructed"] is False

    def test_is_reconstructed_true_is_surfaced(self):
        # Migration 332: a legacy-import-backfilled period must not export
        # indistinguishably from a contemporaneously-logged one (audit BLOCKER).
        record = redact_row(_period_row(is_reconstructed=True))
        assert record["is_reconstructed"] is True

    def test_is_reconstructed_defaults_false_when_column_absent(self):
        # Defensive: a row shaped without the column (e.g. an older fixture)
        # must not be misrepresented as reconstructed.
        row = _period_row()
        del row["is_reconstructed"]
        record = redact_row(row)
        assert record["is_reconstructed"] is False

    def test_no_rider_or_address_fields_leak(self):
        record = redact_row(_period_row())
        forbidden = {
            "rider_id",
            "rider_name",
            "pickup_address",
            "dropoff_address",
            "pickup_lat",
            "pickup_lng",
            "dropoff_lat",
            "dropoff_lng",
            "phone",
            "email",
        }
        assert not (forbidden & record.keys())

    def test_handles_missing_embedded_ride(self):
        row = _period_row(ride={})
        record = redact_row(row)
        assert record["ride_status"] is None
        assert record["total_fare_cad"] == "0.00"

    def test_handles_list_shaped_embed(self):
        # Some postgrest-py versions embed a to-one relation as a 1-item list.
        row = _period_row()
        row["rides"] = [row["rides"]]
        record = redact_row(row)
        assert record["ride_status"] == "completed"

    def test_money_rounds_half_up(self):
        row = _period_row()
        row["rides"]["total_fare"] = "18.755"
        record = redact_row(row)
        assert record["total_fare_cad"] == "18.76"


def _patch_get_rows(side_effect):
    return patch("compliance_export.db.get_rows", AsyncMock(side_effect=side_effect))


def _patch_insert_one(side_effect=None):
    return patch("compliance_export.db.insert_one", AsyncMock(side_effect=side_effect or (lambda *a, **kw: None)))


def _table_routed_get_rows(periods_result, corrections_result=None):
    """Build a get_rows side_effect that returns `periods_result` (or its
    pages, in order, if given a list-of-lists) for driver_insurance_periods
    and `corrections_result` (default []) for driver_insurance_period_
    corrections — mirrors _scan()'s two-table read (ACTION_ITEMS.md B34)
    without every existing single-table test having to special-case it."""
    periods_pages = list(periods_result) if periods_result and isinstance(periods_result[0], list) else None

    async def get_rows_side(table, filters=None, **kw):
        if table == "driver_insurance_period_corrections":
            return corrections_result or []
        if periods_pages is not None:
            return periods_pages.pop(0) if periods_pages else []
        return periods_result

    return get_rows_side


class TestScanFilters:
    def test_date_range_and_period_filter_applied(self):
        captured = {}

        async def get_rows_side(table, filters=None, **kw):
            captured["table"] = table
            captured["filters"] = filters
            return []

        with _patch_get_rows(get_rows_side), _patch_insert_one():
            asyncio.run(run_export(start="2026-01-01", end="2026-04-01", requested_by="admin1"))

        assert captured["table"] == "driver_insurance_periods"
        f = captured["filters"]
        assert f["period"] == {"$in": [2, 3]}
        assert {"started_at": {"$gte": "2026-01-01"}} in f["$and"]
        assert {"started_at": {"$lt": "2026-04-01"}} in f["$and"]
        assert "driver_id" not in f
        assert "ride_id" not in f

    def test_driver_and_ride_scoping_added_when_given(self):
        captured = {}

        async def get_rows_side(table, filters=None, **kw):
            captured["filters"] = filters
            return []

        with _patch_get_rows(get_rows_side), _patch_insert_one():
            asyncio.run(
                run_export(
                    start="2026-01-01",
                    end="2026-04-01",
                    requested_by="admin1",
                    driver_id="d1",
                    ride_id="r1",
                )
            )

        assert captured["filters"]["driver_id"] == "d1"
        assert captured["filters"]["ride_id"] == "r1"

    def test_paginates_full_pages(self):
        # 1000-row page followed by a partial page should be fetched as two
        # pages and stop once a short page is seen.
        pages = [[_period_row(ride_id=f"r{i}") for i in range(1000)], [_period_row(ride_id="r-last")]]
        calls = []

        async def get_rows_side(table, filters=None, **kw):
            if table == "driver_insurance_periods":
                calls.append(kw.get("offset"))
                return pages.pop(0) if pages else []
            # ACTION_ITEMS.md B34 correction lookup — none on file here.
            return []

        with _patch_get_rows(get_rows_side), _patch_insert_one():
            summary = asyncio.run(run_export(start="2026-01-01", end="2026-04-01", requested_by="admin1"))

        assert calls == [0, 1000]
        assert summary["row_count"] == 1001


class TestAuditTrail:
    def test_writes_compliance_export_events_row(self):
        insert_calls = []

        async def insert_side(table, doc):
            insert_calls.append((table, doc))

        with _patch_get_rows(_table_routed_get_rows([_period_row()])), _patch_insert_one(insert_side):
            asyncio.run(
                run_export(
                    start="2026-01-01",
                    end="2026-04-01",
                    requested_by="admin1",
                    reference="SGI-2026-0042",
                )
            )

        assert len(insert_calls) == 1
        table, doc = insert_calls[0]
        assert table == "compliance_export_events"
        assert doc["admin_user_id"] == "admin1"
        assert doc["report_type"] == "trip_record_subpoena_export"
        assert doc["row_count"] == 1
        assert doc["params"]["reference"] == "SGI-2026-0042"
        # No PII in the audit params either — only ids/dates/reference.
        assert set(doc["params"].keys()) == {"start", "end", "driver_id", "ride_id", "reference"}


class TestOutputRendering:
    def test_csv_output_written_to_file(self, tmp_path):
        out_file = tmp_path / "export.csv"

        with _patch_get_rows(_table_routed_get_rows([_period_row()])), _patch_insert_one():
            summary = asyncio.run(
                run_export(
                    start="2026-01-01",
                    end="2026-04-01",
                    requested_by="admin1",
                    out_path=str(out_file),
                )
            )

        content = out_file.read_text()
        assert "ride_id" in content.splitlines()[0]
        assert "is_reconstructed" in content.splitlines()[0]
        assert "is_corrected" in content.splitlines()[0]
        assert "r1" in content
        assert summary["output"] == str(out_file)

    def test_json_output_written_to_file(self, tmp_path):
        out_file = tmp_path / "export.json"

        with _patch_get_rows(_table_routed_get_rows([_period_row()])), _patch_insert_one():
            asyncio.run(
                run_export(
                    start="2026-01-01",
                    end="2026-04-01",
                    requested_by="admin1",
                    fmt="json",
                    out_path=str(out_file),
                )
            )

        parsed = json.loads(out_file.read_text())
        assert parsed[0]["ride_id"] == "r1"


class TestCorrectionPreference:
    """ACTION_ITEMS.md B34: when a driver_insurance_period_corrections row
    exists for a period, the export must return the CORRECTED started_at/
    ended_at, not the original — not just that the corrections table can be
    queried."""

    def test_corrected_values_preferred_over_original(self, tmp_path):
        out_file = tmp_path / "export.json"
        original = _period_row(ride_id="r1", started="2026-07-05T10:00:00Z", ended="2026-07-05T10:20:00Z")
        original["id"] = "p1"
        correction = {
            "original_period_id": "p1",
            "corrected_started_at": "2026-07-05T09:45:00Z",  # real GPS start, earlier
            "corrected_ended_at": "2026-07-05T10:20:00Z",
        }

        with (
            _patch_get_rows(_table_routed_get_rows([original], [correction])),
            _patch_insert_one(),
        ):
            asyncio.run(
                run_export(
                    start="2026-01-01",
                    end="2026-04-01",
                    requested_by="admin1",
                    fmt="json",
                    out_path=str(out_file),
                )
            )

        record = json.loads(out_file.read_text())[0]
        assert record["period_started_at"] == "2026-07-05T09:45:00Z"
        assert record["period_ended_at"] == "2026-07-05T10:20:00Z"
        assert record["is_corrected"] is True

    def test_original_values_kept_when_no_correction_on_file(self, tmp_path):
        out_file = tmp_path / "export.json"
        original = _period_row(ride_id="r1", started="2026-07-05T10:00:00Z", ended="2026-07-05T10:20:00Z")
        original["id"] = "p1"

        with (
            _patch_get_rows(_table_routed_get_rows([original], corrections_result=[])),
            _patch_insert_one(),
        ):
            asyncio.run(
                run_export(
                    start="2026-01-01",
                    end="2026-04-01",
                    requested_by="admin1",
                    fmt="json",
                    out_path=str(out_file),
                )
            )

        record = json.loads(out_file.read_text())[0]
        assert record["period_started_at"] == "2026-07-05T10:00:00Z"
        assert record["period_ended_at"] == "2026-07-05T10:20:00Z"
        assert record["is_corrected"] is False

    def test_correction_lookup_scoped_to_scanned_period_ids(self):
        # The $in filter passed to the corrections lookup must be exactly
        # the ids of the rows just scanned — never an unfiltered/empty $in.
        captured_filters = {}
        original = _period_row(ride_id="r1")
        original["id"] = "p1"

        async def get_rows_side(table, filters=None, **kw):
            if table == "driver_insurance_period_corrections":
                captured_filters["filters"] = filters
                return []
            return [original]

        with _patch_get_rows(get_rows_side), _patch_insert_one():
            asyncio.run(run_export(start="2026-01-01", end="2026-04-01", requested_by="admin1"))

        assert captured_filters["filters"] == {"original_period_id": {"$in": ["p1"]}}

    def test_no_correction_lookup_when_no_periods_scanned(self):
        # Empty scan result must short-circuit — never issue an all-empty $in.
        calls = []

        async def get_rows_side(table, filters=None, **kw):
            calls.append(table)
            return []

        with _patch_get_rows(get_rows_side), _patch_insert_one():
            asyncio.run(run_export(start="2026-01-01", end="2026-04-01", requested_by="admin1"))

        assert calls == ["driver_insurance_periods"]
