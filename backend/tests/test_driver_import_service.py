"""Unit tests for backend/services/driver_import_service.py build_plan logic.

These cover the DB-touching plan builder with an in-memory fake Supabase client
(no real DB), focused on the batch-prefetch existence semantics, the resume
path, and the web-flow (files_root=None) document rejection.
"""

import pytest
from unittest.mock import MagicMock

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
    # Existing driver's vehicle fields match the CSV exactly -> nothing to
    # update, so the row is skipped with a plain "resume" warning.
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
    }
    _install_fake(monkeypatch, drivers=[existing_driver])
    plan = svc.build_plan([_driver_row()], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert not plan.users_to_insert
    assert not plan.drivers_to_update
    assert any(w.field == "resume" for w in plan.warnings)


def test_resume_updates_changed_vehicle_fields(monkeypatch):
    # Existing driver imported earlier without colour/VIN; the re-upload adds
    # them and changes the plate -> queue a vehicle-only update, not a skip.
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "OLDPLATE",
        "vehicle_year": 2020,
        "vehicle_color": "",
        "vehicle_vin": None,
    }
    _install_fake(monkeypatch, drivers=[existing_driver])
    row = _driver_row(vehicle_plate="NEW999", vehicle_color="Black", vin="2T1BURHE0JC123456")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert not plan.drivers_to_insert  # existing driver — no insert
    assert len(plan.drivers_to_update) == 1
    upd = plan.drivers_to_update[0]
    assert upd["id"] == "drv-1"
    assert upd["changes"]["license_plate"] == "NEW999"
    assert upd["changes"]["vehicle_color"] == "Black"
    assert upd["vin_plain"] == "2T1BURHE0JC123456"
    # make/model/year unchanged -> not in the diff
    assert "vehicle_make" not in upd["changes"]
    assert "vehicle_year" not in upd["changes"]
    assert any(w.field == "update" for w in plan.warnings)


def test_resume_unchanged_vin_is_not_updated(monkeypatch):
    # VIN is stored as plaintext (migration 244) — an identical VIN in the
    # re-upload compares equal and produces no update.
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
        "vehicle_vin": "2T1BURHE0JC123456",
    }
    _install_fake(monkeypatch, drivers=[existing_driver])
    row = _driver_row(vin="2T1BURHE0JC123456")  # same VIN as stored
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert not plan.drivers_to_update
    assert any(w.field == "resume" for w in plan.warnings)


def test_resume_blank_csv_cell_never_wipes_existing(monkeypatch):
    # A blank colour cell in the re-upload must not overwrite an existing colour.
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
        "vehicle_color": "Red",
    }
    _install_fake(monkeypatch, drivers=[existing_driver])
    plan = svc.build_plan([_driver_row(vehicle_color="")], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert not plan.drivers_to_update


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


# ---------------------------------------------------------------------------
# A28 P2 (ACTION_ITEMS.md): driver-import validity gaps — format validation
# ---------------------------------------------------------------------------


def test_malformed_phone_errors(monkeypatch):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row(phone="12345")], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "phone" for e in plan.errors)
    assert not plan.drivers_to_insert


def test_malformed_email_errors(monkeypatch):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row(email="not-an-email")], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "email" for e in plan.errors)
    assert not plan.drivers_to_insert


def test_blank_email_is_not_a_format_error(monkeypatch):
    # Email is optional — an absent value must not be treated as malformed.
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row(email="")], [], None, SERVICE_AREA, "batch1")
    assert not any(e.field == "email" for e in plan.errors)


def test_malformed_vin_errors_on_new_driver(monkeypatch):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row(vin="TOO-SHORT")], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "vin" for e in plan.errors)
    assert not plan.drivers_to_insert


def test_valid_vin_is_normalized_on_new_driver(monkeypatch):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row(vin="2t1burhe0jc123456")], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert plan.drivers_to_insert[0]["_plain_vehicle_vin"] == "2T1BURHE0JC123456"


def test_malformed_vin_errors_on_resume_update(monkeypatch):
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
        "vehicle_vin": None,
    }
    _install_fake(monkeypatch, drivers=[existing_driver])
    plan = svc.build_plan([_driver_row(vin="INVALID-VIN")], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "vin" for e in plan.errors)
    assert not plan.drivers_to_update


# ---------------------------------------------------------------------------
# storage_signed_url — response-shape handling
# ---------------------------------------------------------------------------


def test_storage_signed_url_handles_the_current_dict_response_shape(monkeypatch):
    """supabase-py's create_signed_url returns a plain dict. The old local
    implementation only read res.data (the legacy object shape), so it raised
    on every call and took the whole bulk import down at commit time — the
    same break documents.py had already been fixed for."""
    fake = MagicMock()
    fake.storage.from_.return_value.create_signed_url.return_value = {
        "signedURL": "https://x.supabase.co/storage/v1/object/sign/driver-documents/k.pdf?token=t",
        "signedUrl": "https://x.supabase.co/storage/v1/object/sign/driver-documents/k.pdf?token=t",
    }
    monkeypatch.setattr(svc, "supabase", fake)

    assert svc.storage_signed_url("k.pdf").endswith("k.pdf?token=t")


def test_storage_signed_url_still_handles_the_legacy_object_shape(monkeypatch):
    legacy = MagicMock()
    legacy.data = {"signedURL": "https://x/storage/v1/object/sign/driver-documents/k.pdf"}
    fake = MagicMock()
    fake.storage.from_.return_value.create_signed_url.return_value = legacy
    monkeypatch.setattr(svc, "supabase", fake)

    assert svc.storage_signed_url("k.pdf").endswith("k.pdf")


def test_storage_signed_url_names_the_key_when_no_url_comes_back(monkeypatch):
    fake = MagicMock()
    fake.storage.from_.return_value.create_signed_url.return_value = {}
    monkeypatch.setattr(svc, "supabase", fake)

    with pytest.raises(RuntimeError, match="k.pdf"):
        svc.storage_signed_url("k.pdf")


def test_import_url_shape_round_trips_through_the_export_key_parser():
    """The import writes document_url; the export parses it back to a storage
    key. These two live in different modules and nothing else pins them
    together — a mismatch exports the document as metadata with no file."""
    from backend.documents import _extract_storage_key

    storage_key = "saskatoon-import/batch1/d7/criminal_record_check/main-uuid.pdf"

    assert _extract_storage_key(f"storage://driver-documents/{storage_key}") == storage_key
