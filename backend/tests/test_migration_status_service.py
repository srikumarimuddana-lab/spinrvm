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

    def is_(self, col, val):
        self._predicates.append(("is_", col, val))
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
            elif kind == "is_":
                _, col, val = pred
                if val == "null" and _get_path(row, col) is not None:
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
        "legacy_id_crosswalk": [],
    }
    base.update(tables)
    return base


def _use(monkeypatch, store):
    fake = _FakeSupabase(store)
    monkeypatch.setattr(svc, "supabase", fake)
    # Tool 17 calls into migration_data_quality_service.py, which has its
    # own module-level `supabase` binding -- patch it to the same fake store
    # so the two modules see one consistent picture, same reasoning as
    # every dual-binding conftest patch this repo already uses.
    from backend.services import migration_data_quality_service as dq_svc

    monkeypatch.setattr(dq_svc, "supabase", fake)

    # Tool 18 calls into migration_driver_repair_service.py -- same
    # dual-binding patch, same reasoning.
    from backend.services import migration_driver_repair_service as dr_svc

    monkeypatch.setattr(dr_svc, "supabase", fake)


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
    assert len(report.tools) == 19
    t1 = next(t for t in report.tools if t.id == "bulk_driver_import")
    assert t1.state == "done"


# --------------------------------------------------------------------------
# Generalized isolation: a query failure in ANY tool (not just #10) must
# never take down the other tools' statuses -- the bug that made the whole
# Migration Checklist panel render "An unexpected error occurred" instead of
# a partial report.
# --------------------------------------------------------------------------


def test_a_query_failure_in_a_normally_unguarded_tool_does_not_crash_the_whole_report(monkeypatch):
    """The `drivers` table is read by many tools (1, 2, 4, 5, 6, 8, 9, 16,
    18) either directly or via the shared eligible_ids/driver-repair
    lookups. Simulating a failure there must degrade every one of them
    individually, not raise out of get_migration_status()."""
    store = _fresh_store(
        rides=[{"id": "r1", "status": "completed", "driver_id": "d1", "rider_id": "u1"}],
        # is_driver=True gives orphaned_accounts a candidate to check, so it
        # actually reaches its own `drivers` lookup instead of early-returning
        # "done" on an empty candidate list.
        users=[{"id": "u1", "is_driver": True}],
    )
    store["__raise_on__"] = {"drivers"}
    _use(monkeypatch, store)

    report = svc.get_migration_status()  # must not raise
    assert len(report.tools) == 19

    for tool_id in ("bulk_driver_import", "orphaned_accounts", "stripe_mapping_import"):
        t = next(t for t in report.tools if t.id == tool_id)
        assert t.state == "manual_check_required"
        assert t.warning == "Status check error"

    # Tools that don't touch `drivers` at all still compute normally.
    t11 = next(t for t in report.tools if t.id == "legacy_booking_import")
    assert t11.state == "not_started"


def test_eligible_driver_lookup_failure_reports_unknown_not_zero(monkeypatch):
    """A failed eligible_ids lookup must not be silently treated as an empty
    population -- tools 2/4/5/9 have their own honest "no eligible drivers
    yet" message for the genuine-zero case, so a query failure needs a
    distinct message rather than reusing it and implying a false zero."""
    store = _fresh_store()
    store["__raise_on__"] = {"drivers"}
    _use(monkeypatch, store)

    report = svc.get_migration_status()
    for tool_id in ("sin_dob_backfill", "vehicle_history_backfill", "tax_id_import"):
        t = next(t for t in report.tools if t.id == tool_id)
        assert t.state == "manual_check_required"
        assert "Could not determine the eligible-driver population" in t.detail
        assert "eligible drivers yet" not in t.detail


def test_imported_ride_lookup_failure_degrades_route_tools_without_crashing(monkeypatch):
    store = _fresh_store()
    store["__raise_on__"] = {"rides"}
    _use(monkeypatch, store)

    report = svc.get_migration_status()  # must not raise
    assert len(report.tools) == 19
    for tool_id in ("route_snapshots", "route_backfill"):
        t = next(t for t in report.tools if t.id == tool_id)
        assert t.state == "manual_check_required"
        assert "imported-ride population" in t.detail


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
    assert "1 driver(s), 0 rider(s), 1 ride(s)" in t16.detail


def test_pre_launch_flag_counts_riders_too(monkeypatch):
    store = _fresh_store(users=[{"id": "u1", "legacy_import_metadata": {"pre_launch_test": "true"}}])
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t16 = next(t for t in report.tools if t.id == "pre_launch_flag")
    assert t16.state == "done"
    assert "0 driver(s), 1 rider(s), 0 ride(s)" in t16.detail


# --------------------------------------------------------------------------
# Full report shape
# --------------------------------------------------------------------------


def test_report_contains_all_18_tools_in_order(monkeypatch):
    _use(monkeypatch, _fresh_store())
    report = svc.get_migration_status()
    assert len(report.tools) == 19
    assert [t.order for t in report.tools] == list(range(1, 20))


# --------------------------------------------------------------------------
# Tool 17: migration data quality scan
# --------------------------------------------------------------------------


def test_data_quality_scan_clean_is_done(monkeypatch):
    """No issues found at all, nothing flagged -- genuinely clean, not
    "not started"."""
    _use(
        monkeypatch,
        _fresh_store(
            rides=[
                {
                    "id": "r1",
                    "status": "completed",
                    "driver_id": "d1",
                    "rider_id": "u1",
                    "pickup_address": "123 St",
                    "dropoff_address": "456 Ave",
                    "grand_total": 12.5,
                }
            ]
        ),
    )
    report = svc.get_migration_status()
    t17 = next(t for t in report.tools if t.id == "data_quality_scan")
    assert t17.state == "done"
    assert "No missing-driver" in t17.detail


def test_data_quality_scan_unflagged_issue_is_not_started(monkeypatch):
    _use(
        monkeypatch,
        _fresh_store(
            rides=[
                {
                    "id": "r1",
                    "status": "completed",
                    "driver_id": None,
                    "rider_id": "u1",
                    "pickup_address": "123 St",
                    "dropoff_address": "456 Ave",
                    "grand_total": 12.5,
                }
            ]
        ),
    )
    report = svc.get_migration_status()
    t17 = next(t for t in report.tools if t.id == "data_quality_scan")
    assert t17.state == "not_started"
    assert "1 row(s) found needing review" in t17.detail


def test_data_quality_scan_all_flagged_is_done(monkeypatch):
    _use(
        monkeypatch,
        _fresh_store(
            rides=[
                {
                    "id": "r1",
                    "status": "completed",
                    "driver_id": None,
                    "rider_id": "u1",
                    "pickup_address": "123 St",
                    "dropoff_address": "456 Ave",
                    "grand_total": 12.5,
                    "legacy_import_metadata": {"data_quality": {"issues": ["missing_driver"]}},
                }
            ]
        ),
    )
    report = svc.get_migration_status()
    t17 = next(t for t in report.tools if t.id == "data_quality_scan")
    assert t17.state == "done"
    assert "1 row(s) flagged, 0 pending" in t17.detail


def test_data_quality_scan_mixed_is_partial(monkeypatch):
    _use(
        monkeypatch,
        _fresh_store(
            rides=[
                {
                    "id": "r1",
                    "status": "completed",
                    "driver_id": None,
                    "rider_id": "u1",
                    "pickup_address": "123 St",
                    "dropoff_address": "456 Ave",
                    "grand_total": 12.5,
                    "legacy_import_metadata": {"data_quality": {"issues": ["missing_driver"]}},
                },
                {
                    "id": "r2",
                    "status": "completed",
                    "driver_id": "d1",
                    "rider_id": None,
                    "pickup_address": "123 St",
                    "dropoff_address": "456 Ave",
                    "grand_total": 12.5,
                },
            ]
        ),
    )
    report = svc.get_migration_status()
    t17 = next(t for t in report.tools if t.id == "data_quality_scan")
    assert t17.state == "partial"
    assert "1 flagged, 1 more pending" in t17.detail


# --------------------------------------------------------------------------
# Tool 18: driver-repair pass
# --------------------------------------------------------------------------


def test_driver_repair_clean_is_done(monkeypatch):
    """No old_driver_id-bearing missing_driver rows at all -- genuinely
    clean, not "not started"."""
    _use(monkeypatch, _fresh_store())
    report = svc.get_migration_status()
    t18 = next(t for t in report.tools if t.id == "driver_repair")
    assert t18.state == "done"
    assert "No old_driver_id" in t18.detail


def test_driver_repair_repairable_is_not_started(monkeypatch):
    store = _fresh_store(
        rides=[
            {
                "id": "r1",
                "status": "completed",
                "driver_id": None,
                "legacy_import_metadata": {"old_driver_id": "old-d1"},
            }
        ],
        drivers=[_driver("d1", extra={"legacy_import_metadata": {"old_driver_id": "old-d1"}})],
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t18 = next(t for t in report.tools if t.id == "driver_repair")
    assert t18.state == "not_started"
    assert "1 ride(s) repairable now" in t18.detail


def test_driver_repair_no_match_is_manual_check_required(monkeypatch):
    store = _fresh_store(
        rides=[
            {
                "id": "r1",
                "status": "completed",
                "driver_id": None,
                "legacy_import_metadata": {"old_driver_id": "old-d1"},
            }
        ],
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t18 = next(t for t in report.tools if t.id == "driver_repair")
    assert t18.state == "manual_check_required"
    assert "1 still unmatched" in t18.detail


# --------------------------------------------------------------------------
# Tool 19: legacy ID crosswalk backfill (driver-side coverage ratio)
# --------------------------------------------------------------------------


def test_id_crosswalk_backfill_not_started_with_no_eligible_drivers(monkeypatch):
    _use(monkeypatch, _fresh_store())
    report = svc.get_migration_status()
    t19 = next(t for t in report.tools if t.id == "id_crosswalk_backfill")
    assert t19.state == "not_started"
    assert "eligible drivers yet" in t19.detail


def test_id_crosswalk_backfill_partial_and_done_states(monkeypatch):
    store = _fresh_store(
        drivers=[
            _driver("d1", source="legacy_mongo_driver_import"),
            _driver("d2", source="legacy_mongo_driver_import"),
        ],
        legacy_id_crosswalk=[{"spinr_user_id": "d1", "entity_type": "driver"}],
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t19 = next(t for t in report.tools if t.id == "id_crosswalk_backfill")
    assert t19.state == "partial"
    assert "1/2" in t19.detail


def test_id_crosswalk_backfill_done_when_every_eligible_driver_covered(monkeypatch):
    store = _fresh_store(
        drivers=[_driver("d1", source="legacy_saskatoon_driver_import")],
        legacy_id_crosswalk=[{"spinr_user_id": "d1", "entity_type": "driver"}],
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t19 = next(t for t in report.tools if t.id == "id_crosswalk_backfill")
    assert t19.state == "done"


def test_id_crosswalk_backfill_ignores_rider_rows_in_the_driver_ratio(monkeypatch):
    """A rider-entity crosswalk row must never count toward the driver
    coverage ratio (wrong entity_type)."""
    store = _fresh_store(
        drivers=[_driver("d1", source="legacy_saskatoon_driver_import")],
        legacy_id_crosswalk=[{"spinr_user_id": "d1", "entity_type": "rider"}],
    )
    _use(monkeypatch, store)
    report = svc.get_migration_status()
    t19 = next(t for t in report.tools if t.id == "id_crosswalk_backfill")
    assert t19.state == "not_started"


# --------------------------------------------------------------------------
# Oversized `.in_()` regression -- see backend/tests/test_oversized_in_batching.py
# for the production incidents (2026-08-31, 2026-09-03) this class of bug
# already caused elsewhere. Found live on the Bulk Operations checklist panel
# on 2026-09-08 once the eligible-driver population crossed _IN_BATCH_SIZE:
# tools #4, #5, #6, #9 and #19 each built a single unbatched
# `.in_(id_column, eligible_ids_or_similar)` call, which is exactly the shape
# the edge proxy rejects at fleet scale.
# --------------------------------------------------------------------------


def _track_in_chunk_sizes(monkeypatch) -> list[int]:
    chunk_sizes: list[int] = []
    original_in_ = _Query.in_

    def _tracking_in_(self, col, vals):
        vals = list(vals)
        chunk_sizes.append(len(vals))
        return original_in_(self, col, vals)

    monkeypatch.setattr(_Query, "in_", _tracking_in_)
    return chunk_sizes


def test_eligible_gated_tools_batch_in_queries_above_threshold(monkeypatch):
    """Tools #4, #5, #9, #19 all key an `.in_()` lookup off the shared
    eligible-driver population. At a fleet size above _IN_BATCH_SIZE, every
    such call must be split into batches -- and the batched results must
    still merge into a correct, undropped total."""
    n = svc._IN_BATCH_SIZE * 2 + 20  # 320: forces 3 batches (150 + 150 + 20)
    drivers = [
        _driver(
            f"d{i}",
            source="legacy_saskatoon_driver_import",
            extra={"sin": "enc", "date_of_birth": "1990-01-01", "gst_bn": "123"},
        )
        for i in range(n)
    ]
    store = _fresh_store(
        drivers=drivers,
        driver_vehicle_history=[{"driver_id": f"d{i}"} for i in range(n)],
        legacy_id_crosswalk=[{"spinr_user_id": f"d{i}", "entity_type": "driver"} for i in range(n)],
    )
    _use(monkeypatch, store)
    chunk_sizes = _track_in_chunk_sizes(monkeypatch)

    report = svc.get_migration_status()

    assert chunk_sizes, "expected at least one .in_() call to have run"
    assert max(chunk_sizes) <= svc._IN_BATCH_SIZE, "a single .in_() call exceeded the batch ceiling"
    assert chunk_sizes.count(svc._IN_BATCH_SIZE) >= 4  # 4 tools x 2 full batches each at n=320

    for tool_id, expect_ratio in (
        ("sin_dob_backfill", f"SIN {n}/{n}"),
        ("vehicle_history_backfill", f"{n}/{n} eligible"),
        ("tax_id_import", f"GST/HST BN {n}/{n}"),
        ("id_crosswalk_backfill", f"{n}/{n} eligible"),
    ):
        tool = next(t for t in report.tools if t.id == tool_id)
        assert tool.state == "done", f"{tool_id}: {tool.detail}"
        assert expect_ratio in tool.detail, f"{tool_id}: {tool.detail}"


def test_orphaned_accounts_batches_in_queries_above_threshold(monkeypatch):
    """Tool #6 keys its `.in_()` lookup off the flagged-driver `users`
    population, not the shared eligible-driver list -- a separate call site
    with the same failure shape, so it needs its own batching proof."""
    n = svc._IN_BATCH_SIZE * 2 + 20  # 320
    store = _fresh_store(
        users=[{"id": f"u{i}", "is_driver": True} for i in range(n)],
        drivers=[{"id": f"d{i}", "user_id": f"u{i}", "legacy_import_metadata": {}} for i in range(n)],
    )
    _use(monkeypatch, store)
    chunk_sizes = _track_in_chunk_sizes(monkeypatch)

    report = svc.get_migration_status()

    assert chunk_sizes
    assert max(chunk_sizes) <= svc._IN_BATCH_SIZE
    t6 = next(t for t in report.tools if t.id == "orphaned_accounts")
    assert t6.state == "done"
    assert "Clean" in t6.detail
