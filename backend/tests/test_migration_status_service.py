"""Unit tests for backend/services/migration_status_service.py.

Every query in the service module was cross-checked against real
production data (read-only) during development -- see
docs/change-log/2026-08-31-migration-checklist-status-panel.md for the
verification numbers. These tests lock in the *logic* (state transitions,
the shared eligible-driver-population gate, the two manual_check_required
tools, and the saved_addresses missing-column defensive path) against a
fake in-memory store, not live data.
"""

from __future__ import annotations

from backend.services import migration_status_service as svc

# --------------------------------------------------------------------------
# Fake Supabase query builder
# --------------------------------------------------------------------------


def _get_path(row: dict, col: str):
    """Resolve a column expression like 'legacy_import_metadata->>source' or
    'legacy_import_metadata->rider_csv_import->>source' against a row dict,
    matching the same '->'/'->>' path convention every service in this repo
    passes straight through to PostgREST."""
    if "->" not in col:
        return row.get(col)
    parts = col.replace("->>", "->").split("->")
    base = parts[0]
    value = row.get(base) or {}
    for key in parts[1:]:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._raise = table in store.get("__raise_on__", set())

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._predicates.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._predicates.append(("in", col, list(vals)))
        return self

    def filter(self, col, op, val):
        self._predicates.append(("filter", col, op, val))
        return self

    def _row_matches(self, row) -> bool:
        for pred in self._predicates:
            kind = pred[0]
            if kind == "eq":
                _, col, val = pred
                if _get_path(row, col) != val:
                    return False
            elif kind == "in":
                _, col, vals = pred
                if _get_path(row, col) not in vals:
                    return False
            elif kind == "filter":
                _, col, op, val = pred
                actual = _get_path(row, col)
                if op == "not.is" and val == "null":
                    if actual is None:
                        return False
                elif op == "eq":
                    if actual != val:
                        return False
                elif op == "like":
                    prefix = val.rstrip("%")
                    if not (isinstance(actual, str) and actual.startswith(prefix)):
                        return False
                else:
                    return False
        return True

    def execute(self):
        if self._raise:
            raise RuntimeError(f"simulated missing column on {self.table}")
        rows = [r for r in self.store.get(self.table, []) if self._row_matches(r)]
        return _Result([dict(r) for r in rows])


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _fresh_store(**tables):
    base = {
        "drivers": [],
        "users": [],
        "rides": [],
        "driver_vehicle_history": [],
        "saved_addresses": [],
        "wallet_transactions": [],
    }
    base.update(tables)
    return base


def _use(monkeypatch, store):
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))


def _driver(id_, *, source=None, mongo_history=False, extra=None):
    meta = {}
    if source:
        meta["source"] = source
    if mongo_history:
        meta["mongo_driver_history"] = [{"old_driver_id": "abc"}]
    row = {"id": id_, "legacy_import_metadata": meta}
    if extra:
        row.update(extra)
    return row


# --------------------------------------------------------------------------
# Tool 1/2: driver import counts
# --------------------------------------------------------------------------


def test_tool_1_and_2_not_started_when_no_drivers(monkeypatch):
    _use(monkeypatch, _fresh_store())
    report = svc.get_migration_status()
    t1 = next(t for t in report.tools if t.id == "bulk_driver_import")
    t2 = next(t for t in report.tools if t.id == "legacy_driver_import")
    assert t1.state == "not_started"
    assert t2.state == "not_started"


def test_tool_1_and_2_done_with_mixed_populations(monkeypatch):
    store = _fresh_store(
        drivers=[
            _driver("d1", source="legacy_saskatoon_driver_import"),
            _driver("d2", source="legacy_mongo_driver_import"),
            _driver("d3", mongo_history=True),  # linked/enriched, no top-level source
            _driver("d4"),  # organic, not legacy
        ]
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t1 = next(t for t in report.tools if t.id == "bulk_driver_import")
    t2 = next(t for t in report.tools if t.id == "legacy_driver_import")
    assert t1.state == "done"
    assert "1 driver" in t1.detail
    assert t2.state == "done"
    assert "1 new + 1 linked/enriched" in t2.detail


# --------------------------------------------------------------------------
# Tool 4/5/9: eligible-driver-gated backfills
# --------------------------------------------------------------------------


def test_sin_dob_backfill_not_started_with_no_eligible_drivers(monkeypatch):
    _use(monkeypatch, _fresh_store())
    report = svc.get_migration_status()
    t4 = next(t for t in report.tools if t.id == "sin_dob_backfill")
    assert t4.state == "not_started"
    assert "eligible drivers yet" in t4.detail


def test_sin_dob_backfill_partial_and_done_states(monkeypatch):
    store = _fresh_store(
        drivers=[
            _driver("d1", source="legacy_mongo_driver_import", extra={"sin": "enc123", "date_of_birth": "1990-01-01"}),
            _driver("d2", source="legacy_mongo_driver_import", extra={"sin": None, "date_of_birth": None}),
        ]
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t4 = next(t for t in report.tools if t.id == "sin_dob_backfill")
    assert t4.state == "partial"
    assert "SIN 1/2" in t4.detail and "DOB 1/2" in t4.detail


def test_vehicle_history_backfill_done_when_every_eligible_driver_covered(monkeypatch):
    store = _fresh_store(
        drivers=[_driver("d1", source="legacy_saskatoon_driver_import")],
        driver_vehicle_history=[{"driver_id": "d1"}],
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t5 = next(t for t in report.tools if t.id == "vehicle_history_backfill")
    assert t5.state == "done"


def test_tax_id_import_reports_gst_only_not_sin(monkeypatch):
    """Tool 9 must never double-count SIN with tool 4 -- only gst_bn."""
    store = _fresh_store(
        drivers=[_driver("d1", source="legacy_mongo_driver_import", extra={"sin": "enc123", "gst_bn": None})]
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t9 = next(t for t in report.tools if t.id == "tax_id_import")
    assert t9.state == "not_started"
    assert "SIN" not in t9.detail.split("(")[0]  # headline count is GST only
    assert "reported under #4" in t9.detail


# --------------------------------------------------------------------------
# Tool 6: orphaned accounts (zero is the target, not a spectrum)
# --------------------------------------------------------------------------


def test_orphaned_accounts_done_when_zero(monkeypatch):
    store = _fresh_store(
        users=[{"id": "u1", "is_driver": True}],
        drivers=[{"id": "d1", "user_id": "u1"}],
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t6 = next(t for t in report.tools if t.id == "orphaned_accounts")
    assert t6.state == "done"
    assert t6.warning is None


def test_orphaned_accounts_flags_a_real_orphan(monkeypatch):
    store = _fresh_store(users=[{"id": "u1", "is_driver": True}], drivers=[])
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t6 = next(t for t in report.tools if t.id == "orphaned_accounts")
    assert t6.state == "partial"
    assert t6.warning == "Action needed"
    assert "1 orphaned account" in t6.detail


# --------------------------------------------------------------------------
# Tools 7/12: no Supabase-only signal -- must never fabricate a count
# --------------------------------------------------------------------------


def test_manual_check_tools_never_fabricate_a_count(monkeypatch):
    _use(monkeypatch, _fresh_store())
    report = svc.get_migration_status()
    t7 = next(t for t in report.tools if t.id == "driver_join_date_fix")
    t12 = next(t for t in report.tools if t.id == "rider_join_date_fix")
    assert t7.state == "manual_check_required"
    assert t12.state == "manual_check_required"


# --------------------------------------------------------------------------
# Tool 10: defensive against the un-applied migration 373 schema gap
# --------------------------------------------------------------------------


def test_saved_address_backfill_reports_missing_column_without_crashing_other_tools(monkeypatch):
    store = _fresh_store(drivers=[_driver("d1", source="legacy_saskatoon_driver_import")])
    store["__raise_on__"] = {"saved_addresses"}
    _use(monkeypatch, store)

    report = svc.get_migration_status()  # must not raise
    t10 = next(t for t in report.tools if t.id == "saved_address_backfill")
    assert t10.state == "manual_check_required"
    assert "Migration 373" in t10.detail
    assert t10.warning == "Migration 373 not applied"

    # Every other tool still rendered -- the exception was contained to #10.
    assert len(report.tools) == 16
    t1 = next(t for t in report.tools if t.id == "bulk_driver_import")
    assert t1.state == "done"


# --------------------------------------------------------------------------
# Tools 14/15: route snapshots + backfill, shared imported-ride population
# --------------------------------------------------------------------------


def test_route_tools_not_started_with_no_imported_rides(monkeypatch):
    _use(monkeypatch, _fresh_store())
    report = svc.get_migration_status()
    t14 = next(t for t in report.tools if t.id == "route_snapshots")
    t15 = next(t for t in report.tools if t.id == "route_backfill")
    assert t14.state == "not_started"
    assert t15.state == "not_started"


def test_route_tools_partial_and_done(monkeypatch):
    store = _fresh_store(
        rides=[
            {
                "id": "r1",
                "legacy_import_metadata": {"old_booking_id": "b1"},
                "route_snapshot_url": "http://x",
                "planned_route_polyline": "poly",
            },
            {
                "id": "r2",
                "legacy_import_metadata": {"old_booking_id": "b2"},
                "route_snapshot_url": None,
                "planned_route_polyline": "poly",
            },
        ]
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t14 = next(t for t in report.tools if t.id == "route_snapshots")
    t15 = next(t for t in report.tools if t.id == "route_backfill")
    assert t14.state == "partial"
    assert t15.state == "done"


# --------------------------------------------------------------------------
# Tool 16: pre-launch flag counts both drivers and rides
# --------------------------------------------------------------------------


def test_pre_launch_flag_counts_drivers_and_rides(monkeypatch):
    store = _fresh_store(
        drivers=[_driver("d1", extra={"legacy_import_metadata": {"pre_launch_test": "true"}})],
        rides=[{"id": "r1", "legacy_import_metadata": {"pre_launch_test": "true"}}],
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t16 = next(t for t in report.tools if t.id == "pre_launch_flag")
    assert t16.state == "done"
    assert "1 driver(s), 1 ride(s)" in t16.detail


# --------------------------------------------------------------------------
# Full report shape
# --------------------------------------------------------------------------


def test_report_contains_all_16_tools_in_order(monkeypatch):
    _use(monkeypatch, _fresh_store())
    report = svc.get_migration_status()
    assert len(report.tools) == 16
    assert [t.order for t in report.tools] == list(range(1, 17))
