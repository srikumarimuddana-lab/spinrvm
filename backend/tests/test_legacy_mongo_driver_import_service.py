"""Unit tests for backend/services/driver_import_service.py's Mongo
driver-profile import section (Phase 1, docs/migration/2026-08-27-legacy-
data-full-migration-approach.md).

Companion to test_driver_import_service.py / test_driver_import_service_
coverage.py, which cover the Saskatoon-CSV build_plan()/commit_plan() this
section deliberately does NOT touch. Fakes are a local, minimal copy of the
same in-memory Supabase pattern those files use (select/eq/in_/insert/rpc),
not shared — matches this repo's existing per-test-file fake convention.
"""

from __future__ import annotations

from backend.services import driver_import_service as svc

MONGO_IMPORT_SOURCE = svc.MONGO_IMPORT_SOURCE

SERVICE_AREA = {"id": "sa-1", "name": "Saskatoon", "province": "SK"}


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def limit(self, _n):
        return self

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
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
        if self._insert is not None:
            self.store.setdefault(self.table, []).extend(self._insert)
            return _FakeExecute(list(self._insert))
        return _FakeExecute(self._matched())


class _FakeRpc:
    def __init__(self, name, params, recorder):
        self.name = name
        self.params = params
        self.recorder = recorder

    def execute(self):
        self.recorder.setdefault("rpc_calls", []).append((self.name, self.params))
        return _FakeExecute(f"enc::{self.params.get('plaintext')}")


class _FakeSupabase:
    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.recorder: dict = {}

    def table(self, name):
        return _FakeQuery(name, self.store)

    def rpc(self, name, params):
        return _FakeRpc(name, params, self.recorder)


def _install(monkeypatch, **kwargs):
    fake = _FakeSupabase(**kwargs)
    monkeypatch.setattr(svc, "supabase", fake)
    return fake


def _mongo_row(**overrides):
    row = {
        "_id": "6923ea32d1bde481895439f4",
        "name": "Jane Doe",
        "phone": "3065551234",
        "email": "jane@example.com",
        "license_number": "41626417",
        "ratings": "4.5",
        "created_at": "1700000000000",
        "is_deleted": "false",
        "is_block": "false",
        "status": "offline",
    }
    row.update(overrides)
    return row


# ── validate_required_mongo_driver_columns ──────────────────────────────


def test_validate_required_columns_empty_rows():
    plan = svc.MongoDriverImportPlan()
    svc.validate_required_mongo_driver_columns([], plan)
    assert len(plan.errors) == 1
    assert "empty" in plan.errors[0].message


def test_validate_required_columns_missing_column():
    plan = svc.MongoDriverImportPlan()
    svc.validate_required_mongo_driver_columns([{"_id": "x", "name": "y"}], plan)
    assert any(e.field == "phone" for e in plan.errors)


# ── build_mongo_driver_import_plan: happy path ──────────────────────────


def test_happy_path_creates_needs_review_offline_driver(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert len(plan.users_to_insert) == 1
    assert len(plan.drivers_to_insert) == 1

    user = plan.users_to_insert[0]
    assert user["phone"] == "+13065551234"
    assert user["first_name"] == "Jane"
    assert user["last_name"] == "Doe"
    assert user["role"] == "driver"
    assert user["is_driver"] is True

    driver = plan.drivers_to_insert[0]
    # The core safety invariant of this section: no CSV field can ever
    # promote a row past needs_review/offline/unverified.
    assert driver["status"] == "needs_review"
    assert driver["is_verified"] is False
    assert driver["is_online"] is False
    assert driver["is_available"] is False
    assert driver["service_area_id"] == "sa-1"
    assert driver["legacy_import_metadata"]["source"] == MONGO_IMPORT_SOURCE
    assert driver["legacy_import_metadata"]["old_driver_id"] == "6923ea32d1bde481895439f4"
    assert driver["legacy_import_metadata"]["was_deleted_in_source"] is False
    assert driver["legacy_import_metadata"]["was_blocked_in_source"] is False
    assert driver["rating"] == 4.5
    assert driver["_plain_license_number"] == "41626417"
    # No vehicle data in Phase 1 -- that's vehicle_details.csv's job.
    assert "vehicle_make" not in driver
    assert driver.get("vehicle_type_id") is None or "vehicle_type_id" not in driver


def test_created_at_backdated_from_csv_epoch_ms(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(created_at="1700000000000")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    # 1700000000000 ms -> 2023-11-14T22:13:20+00:00
    assert plan.drivers_to_insert[0]["created_at"].startswith("2023-11-14")
    assert plan.users_to_insert[0]["created_at"].startswith("2023-11-14")


def test_created_at_falls_back_to_now_when_unparseable(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(created_at="not-a-timestamp")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    # Just assert it's a real ISO string, not the literal garbage input.
    assert "not-a-timestamp" not in plan.drivers_to_insert[0]["created_at"]


# ── build_mongo_driver_import_plan: rejections ──────────────────────────


def test_missing_id_is_error(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(_id="")], service_area=SERVICE_AREA, import_batch="b1")
    assert any(e.field == "_id" for e in plan.errors)
    assert not plan.drivers_to_insert


def test_duplicate_id_in_same_batch_is_error(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(), _mongo_row(phone="3065559999")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert any(e.field == "_id" and "duplicate" in e.message for e in plan.errors)
    # First occurrence still processed once (the error is on the second).
    assert len(plan.drivers_to_insert) == 1


def test_invalid_phone_is_error(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(phone="123")], service_area=SERVICE_AREA, import_batch="b1")
    assert any(e.field == "phone" for e in plan.errors)
    assert not plan.drivers_to_insert


def test_missing_name_is_error(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(name="")], service_area=SERVICE_AREA, import_batch="b1")
    assert any(e.field == "name" for e in plan.errors)
    assert not plan.drivers_to_insert


def test_malformed_email_is_warning_not_error_row_still_imports(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(email="not-an-email")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    assert any(w.field == "email" for w in plan.warnings)
    assert len(plan.drivers_to_insert) == 1
    assert plan.users_to_insert[0]["email"] is None


# ── build_mongo_driver_import_plan: existing-match safety rule ──────────


def test_existing_organic_user_match_is_error_never_silent_merge(monkeypatch):
    _install(monkeypatch, store={"users": [{"id": "u-organic", "phone": "+13065551234", "email": None}]})
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert any("already exists" in e.message for e in plan.errors)
    assert not plan.drivers_to_insert
    assert not plan.users_to_insert


def test_existing_organic_driver_match_is_error_never_silent_merge(monkeypatch):
    _install(
        monkeypatch,
        store={"drivers": [{"id": "drv-organic", "phone": "+13065551234", "legacy_import_metadata": {}}]},
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert any("already exists" in e.message for e in plan.errors)
    assert not plan.drivers_to_insert


def test_resume_path_skips_with_warning_not_error(monkeypatch):
    """A driver already created by a PREVIOUS run of THIS importer for the
    same old_driver_id is a resume, not a conflict."""
    _install(
        monkeypatch,
        store={
            "drivers": [
                {
                    "id": "drv-1",
                    "phone": "+13065551234",
                    "legacy_import_metadata": {
                        "source": MONGO_IMPORT_SOURCE,
                        "old_driver_id": "6923ea32d1bde481895439f4",
                    },
                }
            ]
        },
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert any(w.field == "resume" for w in plan.warnings)
    assert not plan.drivers_to_insert
    assert not plan.users_to_insert


def test_matching_driver_from_a_different_old_id_is_still_a_conflict_error(monkeypatch):
    """Same phone, but the existing driver's legacy_import_metadata points at
    a DIFFERENT old_driver_id -- must not be treated as a resume."""
    _install(
        monkeypatch,
        store={
            "drivers": [
                {
                    "id": "drv-1",
                    "phone": "+13065551234",
                    "legacy_import_metadata": {"source": MONGO_IMPORT_SOURCE, "old_driver_id": "some-other-id"},
                }
            ]
        },
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert any("already exists" in e.message for e in plan.errors)
    assert not plan.drivers_to_insert


# ── build_mongo_driver_import_plan: rating parsing ──────────────────────


def test_rating_zero_is_treated_as_no_data_not_written(monkeypatch):
    """The old app's '0' means 'never rated', not a real 0-star rating --
    must not overwrite the drivers table's own 5.0 default."""
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(ratings="0")], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert "rating" not in plan.drivers_to_insert[0]


def test_rating_out_of_range_is_not_written(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(ratings="7.2")], service_area=SERVICE_AREA, import_batch="b1")
    assert "rating" not in plan.drivers_to_insert[0]


def test_rating_unparseable_is_not_written(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(ratings="not-a-number")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert "rating" not in plan.drivers_to_insert[0]


# ── build_mongo_driver_import_plan: deleted/blocked history preserved ──


def test_deleted_and_blocked_flags_preserved_but_never_block_import(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(is_deleted="true", is_block="true")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    meta = plan.drivers_to_insert[0]["legacy_import_metadata"]
    assert meta["was_deleted_in_source"] is True
    assert meta["was_blocked_in_source"] is True
    # Still lands needs_review/offline like every other row -- deletion/block
    # state is preserved as history, not an import-time rejection.
    assert plan.drivers_to_insert[0]["status"] == "needs_review"


# ── commit_mongo_driver_import_plan ─────────────────────────────────────


def test_commit_refuses_with_errors(monkeypatch):
    _install(monkeypatch)
    plan = svc.MongoDriverImportPlan()
    plan.errors.append(svc.ImportErrorItem("OLD-1", "x", "boom"))
    try:
        svc.commit_mongo_driver_import_plan(plan)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "refusing to commit" in str(exc)


def test_commit_inserts_users_then_drivers_and_encrypts_license_number(monkeypatch):
    fake = _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors

    svc.commit_mongo_driver_import_plan(plan)

    assert len(fake.store["users"]) == 1
    inserted_driver = fake.store["drivers"][0]
    assert inserted_driver["license_number"] == "enc::41626417"
    assert "_plain_license_number" not in inserted_driver


def test_commit_with_no_license_number_encrypts_none(monkeypatch):
    fake = _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(license_number="")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors

    svc.commit_mongo_driver_import_plan(plan)

    inserted_driver = fake.store["drivers"][0]
    assert inserted_driver["license_number"] is None


# ── print_mongo_driver_import_report ────────────────────────────────────


def test_print_report_dry_run_and_commit_modes(capsys):
    plan = svc.MongoDriverImportPlan()
    plan.warnings.append(svc.ImportErrorItem("OLD-1", "email", "bad format"))
    plan.errors.append(svc.ImportErrorItem("OLD-2", "phone", "invalid"))

    svc.print_mongo_driver_import_report(plan, dry_run=True)
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "WARNING old_driver_id=OLD-1" in out
    assert "ERROR old_driver_id=OLD-2" in out

    svc.print_mongo_driver_import_report(plan, dry_run=False)
    assert "COMMIT" in capsys.readouterr().out
