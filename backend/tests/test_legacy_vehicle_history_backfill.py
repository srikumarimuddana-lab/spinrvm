"""Tests for driver_import_service.py's legacy vehicle-history backfill
(vehicle_details.csv -> driver_vehicle_history, Oct 30 checklist item #4).

Companion to test_legacy_sin_dob_import_service.py — mirrors its fake-
supabase harness (this module reads `svc.supabase` directly, not through
`db_supabase`/`repositories`, so the shared `mock_supabase_client` conftest
fixture does not intercept it; a local fake is the established pattern here).

Covers: the vehicle_details.csv -> drivers.csv -> phone crosswalk, the
legacy-driver-only safety gate, the multi-vehicle before/after chain
reconstruction, idempotency against already-committed rows, and the
apply path's append-only insert behavior.
"""

from __future__ import annotations

from backend.services import driver_import_service as svc

IMPORT_SOURCE = svc.IMPORT_SOURCE


def _vehicle_row(**overrides):
    row = {
        "_id": "veh-1",
        "driver_id": "mongo-driver-1",
        "name": "Toyota",
        "model": "Corolla",
        "color": "White",
        "year": "2022",
        "number": "ABC123",
        "vin": "1HGCM82633A004352",
        "created_at": "1700000000000",
        "status": "active",
    }
    row.update(overrides)
    return row


def _mongo_driver_row(**overrides):
    row = {"_id": "mongo-driver-1", "phone": "3065551234"}
    row.update(overrides)
    return row


def _spinr_driver(**overrides):
    row = {
        "id": "spinr-driver-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "42"},
    }
    row.update(overrides)
    return row


# ── fake supabase (select/in_/insert only — this module never updates or RPCs) ──


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert_rows = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def insert(self, rows):
        self._insert_rows = rows
        return self

    def _matched(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
        return rows

    def execute(self):
        if self._insert_rows is not None:
            self.store.setdefault(self.table, []).extend(self._insert_rows)
            return _FakeExecute(self._insert_rows)
        return _FakeExecute(self._matched())


class _FakeSupabase:
    def __init__(self, drivers=None, vehicle_history=None):
        self.store = {
            "drivers": drivers if drivers is not None else [],
            "driver_vehicle_history": vehicle_history if vehicle_history is not None else [],
        }

    def table(self, name):
        return _FakeQuery(name, self.store)


def _install(monkeypatch, drivers=None, vehicle_history=None):
    fake = _FakeSupabase(drivers=drivers, vehicle_history=vehicle_history)
    monkeypatch.setattr(svc, "supabase", fake)
    return fake


# ── join_legacy_vehicle_details ────────────────────────────────────────


def test_join_resolves_phone_via_mongo_driver_id():
    joined = svc.join_legacy_vehicle_details([_vehicle_row()], [_mongo_driver_row()])
    assert joined[0]["phone"] == "+13065551234"
    assert joined[0]["old_driver_id"] == "mongo-driver-1"
    assert joined[0]["old_vehicle_id"] == "veh-1"
    assert joined[0]["fields"]["vehicle_make"] == "Toyota"
    assert joined[0]["fields"]["license_plate"] == "ABC123"


def test_join_unmatched_driver_id_yields_none_phone():
    joined = svc.join_legacy_vehicle_details([_vehicle_row(driver_id="ghost")], [_mongo_driver_row()])
    assert joined[0]["phone"] is None


# ── plan_legacy_vehicle_history_backfill ───────────────────────────────


def test_plan_matches_legacy_driver_and_stages_all_tracked_fields(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_vehicle_history_backfill([_vehicle_row()], [_mongo_driver_row()])

    assert not plan.warnings
    assert not plan.errors
    fields = {r["field"]: r["new_value"] for r in plan.rows_to_insert}
    assert fields == {
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "vehicle_color": "White",
        "vehicle_year": "2022",
        "license_plate": "ABC123",
        "vehicle_vin": "1HGCM82633A004352",
    }
    for row in plan.rows_to_insert:
        assert row["driver_id"] == "spinr-driver-1"
        assert row["changed_by_role"] == "system"
        assert row["changed_by_user_id"] is None
        assert row["old_value"] is None  # first-ever row for each field


def test_plan_skips_unmatched_phone(monkeypatch):
    _install(monkeypatch, drivers=[])
    plan = svc.plan_legacy_vehicle_history_backfill([_vehicle_row()], [_mongo_driver_row()])

    assert plan.rows_to_insert == []
    assert plan.skipped_unmatched == 1
    assert plan.warnings[0].field == "phone"
    assert "3065551234" not in plan.warnings[0].message


def test_plan_skips_unresolvable_driver_id(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_vehicle_history_backfill([_vehicle_row(driver_id="no-such-id")], [_mongo_driver_row()])

    assert plan.rows_to_insert == []
    assert plan.skipped_unmatched == 1


def test_plan_skips_driver_without_legacy_import_source(monkeypatch):
    organic_driver = _spinr_driver(legacy_import_metadata={})
    _install(monkeypatch, drivers=[organic_driver])
    plan = svc.plan_legacy_vehicle_history_backfill([_vehicle_row()], [_mongo_driver_row()])

    assert plan.rows_to_insert == []
    assert plan.skipped_not_legacy_driver == 1


def test_plan_unparseable_created_at_is_an_error(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_vehicle_history_backfill([_vehicle_row(created_at="not-a-number")], [_mongo_driver_row()])

    assert plan.rows_to_insert == []
    assert any(e.field == "created_at" for e in plan.errors)


def test_plan_blank_field_is_not_staged(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_vehicle_history_backfill([_vehicle_row(vin="")], [_mongo_driver_row()])

    fields = {r["field"] for r in plan.rows_to_insert}
    assert "vehicle_vin" not in fields
    assert "vehicle_make" in fields  # other fields still staged


def test_plan_multiple_vehicles_builds_before_after_chain(monkeypatch):
    """A driver with two vehicle_details.csv rows (24/330 in the real
    export) gets a real change chain: the second row's old_value is the
    first row's value, sorted by the legacy row's own created_at -- not
    import time, and not CSV row order."""
    _install(monkeypatch, drivers=[_spinr_driver()])
    older = _vehicle_row(_id="veh-1", color="White", created_at="1700000000000")
    newer = _vehicle_row(_id="veh-2", color="Red", created_at="1700100000000")
    # Feed them out of chronological order to prove sorting is by
    # created_at, not input order.
    plan = svc.plan_legacy_vehicle_history_backfill([newer, older], [_mongo_driver_row()])

    color_rows = sorted(
        (r for r in plan.rows_to_insert if r["field"] == "vehicle_color"), key=lambda r: r["created_at"]
    )
    assert len(color_rows) == 2
    assert color_rows[0]["old_value"] is None
    assert color_rows[0]["new_value"] == "White"
    assert color_rows[1]["old_value"] == "White"
    assert color_rows[1]["new_value"] == "Red"


def test_plan_multiple_vehicles_unchanged_field_not_duplicated(monkeypatch):
    """Same value across two legacy rows for a field must not produce a
    second history row -- matches the live writer's own "old == new ->
    skip" rule (utils/vehicle_history.record_vehicle_changes)."""
    _install(monkeypatch, drivers=[_spinr_driver()])
    row_a = _vehicle_row(_id="veh-1", color="White", created_at="1700000000000")
    row_b = _vehicle_row(_id="veh-2", color="White", created_at="1700100000000")  # same colour, later re-registration
    plan = svc.plan_legacy_vehicle_history_backfill([row_a, row_b], [_mongo_driver_row()])

    color_rows = [r for r in plan.rows_to_insert if r["field"] == "vehicle_color"]
    assert len(color_rows) == 1


def test_plan_idempotent_against_already_committed_rows(monkeypatch):
    fake = _install(monkeypatch, drivers=[_spinr_driver()])
    plan1 = svc.plan_legacy_vehicle_history_backfill([_vehicle_row()], [_mongo_driver_row()])
    svc.apply_legacy_vehicle_history_backfill(plan1)
    assert len(fake.store["driver_vehicle_history"]) == 6  # one per tracked field

    plan2 = svc.plan_legacy_vehicle_history_backfill([_vehicle_row()], [_mongo_driver_row()])
    assert plan2.rows_to_insert == []
    assert plan2.skipped_already_backfilled == 6


def test_plan_idempotent_survives_postgres_timestamp_fraction_trimming(monkeypatch):
    """spinr-migration-reviewer finding, 2026-08-20: Postgres/PostgREST
    trims trailing zero fractional digits on a timestamptz's text output
    (".123000" -> ".123"), which never string-matches Python's zero-padded
    isoformat() for the same instant. The fake store used by every other
    test in this file just echoes back the literal Python dict it was
    given, so it can't catch this -- this test manually rewrites the
    already-committed row's created_at into Postgres's trimmed form before
    re-planning, proving the dedup survives real round-trip serialization,
    not just an exact Python-string match against itself."""
    fake = _install(monkeypatch, drivers=[_spinr_driver()])
    plan1 = svc.plan_legacy_vehicle_history_backfill([_vehicle_row()], [_mongo_driver_row()])
    svc.apply_legacy_vehicle_history_backfill(plan1)
    assert len(fake.store["driver_vehicle_history"]) == 6

    for row in fake.store["driver_vehicle_history"]:
        assert row["created_at"].endswith("+00:00")
        head, _, _tz = row["created_at"].partition("+")
        base, _, frac = head.partition(".")
        # 6-digit zero-padded microseconds -> Postgres's trimmed form.
        trimmed = frac.rstrip("0") or "0"
        row["created_at"] = f"{base}.{trimmed}+00:00"

    plan2 = svc.plan_legacy_vehicle_history_backfill([_vehicle_row()], [_mongo_driver_row()])
    assert plan2.rows_to_insert == []
    assert plan2.skipped_already_backfilled == 6


def test_plan_same_created_at_tiebreaks_deterministically_on_old_vehicle_id(monkeypatch):
    """Two legacy vehicle rows sharing an identical created_at (plausible
    for a bulk-seeded old-app dataset) must not fall back to arbitrary
    input-order tiebreaking -- old_vehicle_id (the legacy Mongo ObjectId,
    which embeds its own creation order) is a meaningful, deterministic
    secondary sort key."""
    _install(monkeypatch, drivers=[_spinr_driver()])
    row_a = _vehicle_row(_id="veh-aaa", color="White", created_at="1700000000000")
    row_b = _vehicle_row(_id="veh-bbb", color="Red", created_at="1700000000000")  # identical timestamp

    plan_forward = svc.plan_legacy_vehicle_history_backfill([row_a, row_b], [_mongo_driver_row()])
    plan_reversed = svc.plan_legacy_vehicle_history_backfill([row_b, row_a], [_mongo_driver_row()])

    for plan in (plan_forward, plan_reversed):
        color_rows = [r for r in plan.rows_to_insert if r["field"] == "vehicle_color"]
        assert len(color_rows) == 2
        # "veh-aaa" sorts before "veh-bbb" lexicographically -- deterministic
        # regardless of which order the CSV rows were fed in.
        assert color_rows[0]["new_value"] == "White"
        assert color_rows[0]["old_value"] is None
        assert color_rows[1]["new_value"] == "Red"
        assert color_rows[1]["old_value"] == "White"


# ── apply_legacy_vehicle_history_backfill ──────────────────────────────


def test_apply_inserts_rows_and_strips_report_only_keys(monkeypatch):
    fake = _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_vehicle_history_backfill([_vehicle_row()], [_mongo_driver_row()])

    svc.apply_legacy_vehicle_history_backfill(plan)

    inserted = fake.store["driver_vehicle_history"]
    assert len(inserted) == 6
    for row in inserted:
        assert "_old_driver_id" not in row
        assert "_old_vehicle_id" not in row
        assert row["driver_id"] == "spinr-driver-1"
    # the CLI-facing plan object still carries the report-only keys for its
    # own post-apply logging loop
    assert plan.rows_to_insert[0]["_old_driver_id"] == "mongo-driver-1"


def test_apply_refuses_when_plan_has_errors(monkeypatch):
    fake = _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.VehicleHistoryBackfillPlan(errors=[svc.ImportErrorItem("x", "created_at", "boom")])

    try:
        svc.apply_legacy_vehicle_history_backfill(plan)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    assert fake.store["driver_vehicle_history"] == []


def test_apply_is_a_noop_with_no_rows(monkeypatch):
    fake = _install(monkeypatch, drivers=[])
    svc.apply_legacy_vehicle_history_backfill(svc.VehicleHistoryBackfillPlan())
    assert fake.store["driver_vehicle_history"] == []


# ── print_vehicle_history_report ───────────────────────────────────────


def test_print_vehicle_history_report_smoke(capsys):
    plan = svc.VehicleHistoryBackfillPlan(
        rows_to_insert=[{"driver_id": "d1", "field": "vehicle_make"}],
        warnings=[svc.ImportErrorItem("43", "phone", "no Spinr driver with this phone number")],
    )
    svc.print_vehicle_history_report(plan, dry_run=True)
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "history rows to insert: 1" in out
