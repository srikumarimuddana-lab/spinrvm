"""Unit tests for backend/services/migration_data_quality_service.py.

Critical correctness properties locked in here:
- a ride with more than one anomaly (e.g. missing driver AND zero fare)
  produces ONE candidate carrying both issues, not two separate ones
- an issue a ride is already flagged for is never re-detected by a fresh
  plan (safe to re-run after a partial apply), but a genuinely new issue on
  an already-partially-flagged ride still is
- apply merges into whatever data_quality.issues already exists rather than
  clobbering it, mirroring pre_launch_flag_service's read-merge-write guard
"""

import json
import re

from backend.services import migration_data_quality_service as svc

BATCH = "20260831000000"


class _Result:
    def __init__(self, data):
        self.data = data


def _walk_json_path(row: dict, col: str):
    """Resolve a `base->key1->key2` / `base->>key` PostgREST-style path
    against a plain dict. Mirrors the real ->/->> chaining regardless of
    which arrow is used at each step -- only the final predicate (is/not.is
    null) cares about presence, not text-vs-jsonb typing."""
    parts = re.split(r"->>|->", col)
    current = row.get(parts[0])
    for key in parts[1:]:
        current = (current or {}).get(key)
    return current


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._update_payload = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._predicates.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self._predicates.append(("is_", col, val))
        return self

    def filter(self, col, op, val):
        self._predicates.append(("filter", col, op, val))
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def _row_matches(self, row) -> bool:
        for pred in self._predicates:
            kind = pred[0]
            if kind == "eq":
                _, col, val = pred
                if row.get(col) != val:
                    return False
            elif kind == "is_":
                _, col, val = pred
                actual = row.get(col)
                if val == "null" and actual is not None:
                    return False
            elif kind == "filter":
                _, col, op, val = pred
                if col == "legacy_import_metadata" and op == "eq":
                    if json.dumps(row.get(col) or {}, sort_keys=True, default=str) != val:
                        return False
                    continue
                actual = _walk_json_path(row, col)
                if op == "not.is" and val == "null":
                    if actual is None:
                        return False
                elif op == "is" and val == "null":
                    if actual is not None:
                        return False
                else:
                    return False
        return True

    def execute(self):
        rows = [r for r in self.store.get(self.table, []) if self._row_matches(r)]
        if self._update_payload is not None:
            for r in rows:
                r.update(self._update_payload)
        return _Result([dict(r) for r in rows])


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _ride(ride_id, **fields):
    row = {
        "id": ride_id,
        "status": "completed",
        "driver_id": "driver-1",
        "rider_id": "rider-1",
        "pickup_address": "123 Real St",
        "dropoff_address": "456 Real Ave",
        "grand_total": 12.50,
        "legacy_import_metadata": {},
    }
    row.update(fields)
    return row


def _store(rides):
    return {"rides": rides}


def _patch(monkeypatch, store):
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))


class TestBuildPlanDetection:
    def test_detects_missing_driver(self, monkeypatch):
        store = _store([_ride("r1", driver_id=None)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.stats["missing_driver"] == 1
        assert plan.candidates[0].issues == [svc.ISSUE_MISSING_DRIVER]

    def test_detects_missing_rider(self, monkeypatch):
        store = _store([_ride("r1", rider_id=None)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.stats["missing_rider"] == 1

    def test_detects_placeholder_pickup_address(self, monkeypatch):
        store = _store([_ride("r1", pickup_address=svc.PLACEHOLDER_ADDRESS)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.stats["placeholder_address"] == 1

    def test_detects_placeholder_dropoff_address(self, monkeypatch):
        store = _store([_ride("r1", dropoff_address=svc.PLACEHOLDER_ADDRESS)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.stats["placeholder_address"] == 1

    def test_detects_zero_fare(self, monkeypatch):
        store = _store([_ride("r1", grand_total=0)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.stats["zero_fare"] == 1

    def test_detects_null_fare_as_zero_fare(self, monkeypatch):
        store = _store([_ride("r1", grand_total=None)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.stats["zero_fare"] == 1

    def test_non_completed_ride_never_flagged_for_driver_or_rider(self, monkeypatch):
        # A cancelled ride missing a driver is expected, not an anomaly --
        # only completed rides are scanned for missing_driver/missing_rider.
        store = _store([_ride("r1", status="cancelled", driver_id=None)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.stats["missing_driver"] == 0
        assert plan.candidates == []

    def test_clean_ride_produces_no_candidate(self, monkeypatch):
        store = _store([_ride("r1")])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.candidates == []
        assert plan.stats["rides_affected"] == 0


class TestMultiIssueMerging:
    def test_ride_with_two_issues_gets_one_candidate_with_both(self, monkeypatch):
        store = _store([_ride("r1", driver_id=None, grand_total=0)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert len(plan.candidates) == 1
        assert set(plan.candidates[0].issues) == {svc.ISSUE_MISSING_DRIVER, svc.ISSUE_ZERO_FARE}
        assert plan.stats["rides_affected"] == 1

    def test_already_flagged_issue_not_redetected(self, monkeypatch):
        store = _store(
            [
                _ride(
                    "r1",
                    driver_id=None,
                    grand_total=0,
                    legacy_import_metadata={"data_quality": {"issues": [svc.ISSUE_MISSING_DRIVER]}},
                )
            ]
        )
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        # missing_driver already flagged -> only the new zero_fare issue surfaces
        assert len(plan.candidates) == 1
        assert plan.candidates[0].issues == [svc.ISSUE_ZERO_FARE]

    def test_ride_fully_flagged_for_its_issue_produces_no_candidate(self, monkeypatch):
        store = _store(
            [
                _ride(
                    "r1",
                    driver_id=None,
                    legacy_import_metadata={"data_quality": {"issues": [svc.ISSUE_MISSING_DRIVER]}},
                )
            ]
        )
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        assert plan.candidates == []


class TestApplyDataQualityFlags:
    def test_apply_writes_issues_and_metadata(self, monkeypatch):
        store = _store([_ride("r1", driver_id=None)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        conflicts = svc.apply_data_quality_flags(plan, batch=BATCH)
        assert conflicts == []
        row = store["rides"][0]
        dq = row["legacy_import_metadata"]["data_quality"]
        assert dq["issues"] == [svc.ISSUE_MISSING_DRIVER]
        assert dq["data_quality_flag"]["batch"] == BATCH

    def test_apply_merges_with_existing_unrelated_metadata(self, monkeypatch):
        store = _store([_ride("r1", driver_id=None, legacy_import_metadata={"source": "legacy_mongo_booking_import"})])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        svc.apply_data_quality_flags(plan, batch=BATCH)
        row = store["rides"][0]
        assert row["legacy_import_metadata"]["source"] == "legacy_mongo_booking_import"
        assert row["legacy_import_metadata"]["data_quality"]["issues"] == [svc.ISSUE_MISSING_DRIVER]

    def test_apply_unions_issues_rather_than_clobbering(self, monkeypatch):
        # Row already has missing_driver flagged (from a prior run); this
        # apply is for a newly-detected zero_fare issue on the same row.
        store = _store(
            [
                _ride(
                    "r1",
                    driver_id=None,
                    grand_total=0,
                    legacy_import_metadata={"data_quality": {"issues": [svc.ISSUE_MISSING_DRIVER]}},
                )
            ]
        )
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        svc.apply_data_quality_flags(plan, batch=BATCH)
        row = store["rides"][0]
        assert set(row["legacy_import_metadata"]["data_quality"]["issues"]) == {
            svc.ISSUE_MISSING_DRIVER,
            svc.ISSUE_ZERO_FARE,
        }

    def test_apply_absorbs_concurrent_write_between_plan_and_apply(self, monkeypatch):
        """apply_data_quality_flags always re-reads immediately before
        writing (not the plan-time snapshot), so a writer that lands between
        plan and apply -- but before apply's own internal read -- is simply
        the fresh baseline it merges onto, not a conflict. Mirrors
        pre_launch_flag_service's test_apply_survives_concurrent_write."""
        store = _store([_ride("r1", driver_id=None)])
        _patch(monkeypatch, store)
        plan = svc.build_data_quality_scan_plan()
        store["rides"][0]["legacy_import_metadata"]["some_other_writer_key"] = "concurrent-value"
        conflicts = svc.apply_data_quality_flags(plan, batch=BATCH)
        assert conflicts == []
        meta = store["rides"][0]["legacy_import_metadata"]
        assert meta["some_other_writer_key"] == "concurrent-value"
        assert meta["data_quality"]["issues"] == [svc.ISSUE_MISSING_DRIVER]

    def test_apply_flag_to_row_guard_refuses_a_stale_snapshot(self, monkeypatch):
        """The narrow race _apply_flag_to_row itself guards against: a write
        that lands in the instant between its own read and its own write.
        Exercised directly since that window can't be forced through the
        public apply_data_quality_flags entry point (see the test above) --
        same reasoning and pattern as pre_launch_flag_service's equivalent
        test."""
        store = _store([_ride("r1", driver_id=None)])
        _patch(monkeypatch, store)
        stale_read_meta = dict(store["rides"][0]["legacy_import_metadata"])  # snapshot taken "earlier"

        # A write that lands after the snapshot was taken but before the
        # guarded update executes.
        store["rides"][0]["legacy_import_metadata"]["some_other_writer_key"] = "concurrent-value"

        conflict = svc._apply_flag_to_row(
            "r1", stale_read_meta, [svc.ISSUE_MISSING_DRIVER], batch=BATCH, now_iso="2026-08-31T00:00:00Z"
        )
        assert conflict == "r1"
        meta = store["rides"][0]["legacy_import_metadata"]
        assert meta["some_other_writer_key"] == "concurrent-value"
        assert "data_quality" not in meta


class TestFetchNeedsReviewRideIds:
    def test_returns_ids_with_issues(self, monkeypatch):
        store = _store(
            [
                _ride("r1", legacy_import_metadata={"data_quality": {"issues": [svc.ISSUE_ZERO_FARE]}}),
                _ride("r2"),
            ]
        )
        _patch(monkeypatch, store)
        assert svc.fetch_needs_review_ride_ids() == {"r1"}

    def test_empty_when_nothing_flagged(self, monkeypatch):
        store = _store([_ride("r1"), _ride("r2")])
        _patch(monkeypatch, store)
        assert svc.fetch_needs_review_ride_ids() == set()
