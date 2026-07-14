"""Unit tests for backend/services/driver_import_service.py build_plan logic.

These cover the DB-touching plan builder with an in-memory fake Supabase client
(no real DB), focused on the batch-prefetch existence semantics, the resume
path, and the web-flow (files_root=None) document rejection.
"""

from backend.services import driver_import_service as svc

IMPORT_SOURCE = svc.IMPORT_SOURCE

SERVICE_AREA = {
    "id": "sa-1",
    "name": "Saskatoon",
    "province": "SK",
    "required_documents": [{"key": "drivers_license"}, {"key": "insurance"}],
}


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []

    def select(self, _cols):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def ilike(self, col, val):
        self._filters.append(("ilike", col, val))
        return self

    def limit(self, _n):
        return self

    def execute(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
            elif op == "ilike":
                needle = str(val).strip("%").lower()
                rows = [r for r in rows if needle in str(r.get(col, "")).lower()]
        return _FakeExecute(rows)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _FakeQuery(name, self.store)


def _install_fake(monkeypatch, *, users=None, drivers=None):
    store = {
        "users": users or [],
        "drivers": drivers or [],
        # Mirror the real schema: vehicle_types has no display_name column.
        "vehicle_types": [{"id": "vt-sedan", "name": "Sedan"}],
    }
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))
    return store


def _driver_row(**overrides):
    row = {
        "old_driver_id": "OLD-1",
        "full_name": "Jane Doe",
        "phone": "3065551234",
        "email": "jane@example.com",
        "vehicle_plate": "ABC123",
        "vehicle_type": "Sedan",
        "vehicle_year": "2020",
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
    }
    row.update(overrides)
    return row


def test_new_driver_is_planned(monkeypatch):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row()], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert len(plan.users_to_insert) == 1
    assert len(plan.drivers_to_insert) == 1


def test_web_import_keeps_csv_approved_driver_under_review(monkeypatch):
    _install_fake(monkeypatch)
    row = _driver_row(spinr_approved="yes", regulatory_authority_approved="yes")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert plan.drivers_to_insert[0]["status"] == "needs_review"
    assert plan.drivers_to_insert[0]["is_verified"] is False


def test_city_derives_from_service_area_name(monkeypatch):
    _install_fake(monkeypatch)
    area = {**SERVICE_AREA, "id": "sa-regina", "name": "Regina"}
    # Regina rows must scope to that area, not the Saskatoon default.
    row = _driver_row(service_area="regina")
    plan = svc.build_plan([row], [], None, area, "batch1")
    assert not plan.errors
    assert plan.drivers_to_insert[0]["city"] == "Regina"


def test_city_from_csv_column_wins(monkeypatch):
    _install_fake(monkeypatch)
    row = _driver_row(city="Warman")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert plan.drivers_to_insert[0]["city"] == "Warman"


def test_existing_user_by_phone_is_conflict(monkeypatch):
    _install_fake(monkeypatch, users=[{"id": "u1", "phone": "+13065551234", "email": "other@x.com"}])
    plan = svc.build_plan([_driver_row()], [], None, SERVICE_AREA, "batch1")
    assert not plan.users_to_insert
    assert any(e.field == "phone/email" for e in plan.errors)


def test_existing_user_by_email_only_is_conflict(monkeypatch):
    # Phone differs, but email matches an existing user -> still a conflict,
    # exactly like the original phone-then-email lookup.
    _install_fake(monkeypatch, users=[{"id": "u1", "phone": "+19990000000", "email": "jane@example.com"}])
    plan = svc.build_plan([_driver_row()], [], None, SERVICE_AREA, "batch1")
    assert not plan.users_to_insert
    assert any(e.field == "phone/email" for e in plan.errors)


def test_resume_path_skips_insert_with_warning(monkeypatch):
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
    }
    _install_fake(monkeypatch, drivers=[existing_driver])
    plan = svc.build_plan([_driver_row()], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert not plan.users_to_insert
    assert any(w.field == "resume" for w in plan.warnings)


def test_web_flow_rejects_document_rows(monkeypatch):
    _install_fake(monkeypatch)
    doc_rows = [{"old_driver_id": "OLD-1", "requirement_key": "drivers_license", "file_path": "x.pdf"}]
    plan = svc.build_plan([_driver_row()], doc_rows, None, SERVICE_AREA, "batch1")
    assert any(e.field == "documents_csv" for e in plan.errors)


def test_unknown_vehicle_type_errors(monkeypatch):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row(vehicle_type="Spaceship")], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "vehicle_type" for e in plan.errors)


def test_missing_required_column_errors(monkeypatch):
    _install_fake(monkeypatch)
    bad = _driver_row()
    del bad["vehicle_make"]
    plan = svc.build_plan([bad], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "vehicle_make" for e in plan.errors)
