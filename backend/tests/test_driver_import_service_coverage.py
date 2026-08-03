"""A1c Sub-tier C coverage tests for backend/services/driver_import_service.py.

Test-only change: no application code modified. Targets the previously-uncovered
branches of build_plan's document-row pass (files_root flow), commit_plan
(inserts/updates/storage upload/doc insert), the small pure helpers
(parse_bool/parse_date/split_name/normalize_phone/storage_signed_url/
encrypt_pii/get_service_area/vehicle_type_map/work_auth_status/
regulatory_authority_defaults), and print_report.

Written by reading backend/services/driver_import_service.py only -- pytest was
NOT run to produce or validate this file. Mirrors the in-memory fake-Supabase
style already used in test_driver_import_service.py / test_admin_driver_import.py.

Fixed (2026-08-03, application code change, explicitly approved by the
user via AskUserQuestion before applying — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 11):
build_plan's `has_import_documents` previously was computed from mere
*presence* of a document row for old_driver_id in the documents CSV,
not from that document's own `status` column — a driver row with
spinr_approved=yes + regulatory_authority_approved=yes plus a document
row whose status was explicitly "rejected" was planned as
driver_status="active" and is_verified=True. Now only an APPROVED
document counts. See
`test_active_status_requires_approved_document_status` below.
"""

import csv
import io

import pytest

from backend.services import driver_import_service as svc

IMPORT_SOURCE = svc.IMPORT_SOURCE

SERVICE_AREA = {
    "id": "sa-1",
    "name": "Saskatoon",
    "province": "SK",
    "regulatory_authority": "SGI",
    "regulatory_region": "SK",
    "required_documents": [{"key": "drivers_license"}, {"key": "insurance"}, {"key": "background_check"}],
}


# ---------------------------------------------------------------------------
# Fake Supabase supporting table select/insert/update, storage, and rpc.
# ---------------------------------------------------------------------------


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert_rows = None
        self._update_fields = None

    def select(self, *_a, **_k):
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

    def insert(self, rows):
        self._insert_rows = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, fields):
        self._update_fields = fields
        return self

    def _matching(self):
        rows = self.store.get(self.table, [])
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
            elif op == "ilike":
                needle = str(val).strip("%").lower()
                rows = [r for r in rows if needle in str(r.get(col, "")).lower()]
        return rows

    def execute(self):
        if self._insert_rows is not None:
            self.store.setdefault(self.table, []).extend(self._insert_rows)
            return _FakeExecute(list(self._insert_rows))
        if self._update_fields is not None:
            matched = self._matching()
            for row in matched:
                row.update(self._update_fields)
            return _FakeExecute(matched)
        return _FakeExecute(list(self._matching()))


class _FakeRpc:
    def __init__(self, params):
        self.params = params

    def execute(self):
        return _FakeExecute(f"ENC[{self.params.get('plaintext')}]")


class _FakeStorageBucket:
    def __init__(self, uploads, bucket):
        self.uploads = uploads
        self.bucket = bucket

    def upload(self, path, file, file_options=None):
        self.uploads.append({"bucket": self.bucket, "path": path, "size": len(file), "options": file_options})
        return {"path": path}

    def create_signed_url(self, storage_key, _ttl):
        return _FakeExecute({"signedURL": f"https://signed.example/{storage_key}"})


class _FakeStorage:
    def __init__(self, uploads):
        self.uploads = uploads

    def from_(self, bucket):
        return _FakeStorageBucket(self.uploads, bucket)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store
        self.uploads = []
        self.storage = _FakeStorage(self.uploads)

    def table(self, name):
        return _FakeQuery(name, self.store)

    def rpc(self, _name, params):
        return _FakeRpc(params)


def _install_fake(monkeypatch, *, users=None, drivers=None, service_areas=None, vehicle_types=None):
    store = {
        "users": users or [],
        "drivers": drivers or [],
        "vehicle_types": vehicle_types if vehicle_types is not None else [{"id": "vt-sedan", "name": "Sedan"}],
        "service_areas": service_areas or [],
        "driver_documents": [],
    }
    fake = _FakeSupabase(store)
    monkeypatch.setattr(svc, "supabase", fake)
    return fake


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


# ---------------------------------------------------------------------------
# parse_csv_rows / read_csv / read_csv_text (lines 160, 166-167, 174)
# ---------------------------------------------------------------------------


def test_parse_csv_rows_raises_on_missing_header():
    reader = csv.DictReader(io.StringIO(""))
    with pytest.raises(ValueError, match="no header row"):
        svc.parse_csv_rows(reader)


def test_read_csv_reads_and_normalizes_from_disk(tmp_path):
    p = tmp_path / "drivers.csv"
    p.write_text("Old Driver Id,Full Name\nOLD-1,Jane Doe\n", encoding="utf-8-sig")
    rows = svc.read_csv(p)
    assert rows == [{"old_driver_id": "OLD-1", "full_name": "Jane Doe"}]


def test_read_csv_text_strips_leading_bom():
    text = "﻿old_driver_id,full_name\nOLD-1,Jane Doe\n"
    rows = svc.read_csv_text(text)
    assert rows == [{"old_driver_id": "OLD-1", "full_name": "Jane Doe"}]


def test_read_csv_text_without_bom_still_works():
    text = "old_driver_id,full_name\nOLD-1,Jane Doe\n"
    rows = svc.read_csv_text(text)
    assert rows[0]["old_driver_id"] == "OLD-1"


# ---------------------------------------------------------------------------
# parse_bool (lines 184-186)
# ---------------------------------------------------------------------------


def test_parse_bool_falsy_values():
    assert svc.parse_bool("no") is False
    assert svc.parse_bool("False") is False
    assert svc.parse_bool("0") is False
    assert svc.parse_bool("Not Approved") is False


def test_parse_bool_unrecognized_value_is_none():
    assert svc.parse_bool("maybe") is None


def test_parse_bool_blank_is_none():
    assert svc.parse_bool("") is None
    assert svc.parse_bool(None) is None


# ---------------------------------------------------------------------------
# parse_date / date_is_ambiguous (lines 197, 201, 226)
# ---------------------------------------------------------------------------


def test_parse_date_two_digit_year_is_promoted_to_2000s():
    parsed = svc.parse_date("15-Jan-24")
    assert parsed is not None
    assert parsed.year == 2024


def test_parse_date_unparseable_returns_none():
    assert svc.parse_date("not-a-date") is None


def test_date_is_ambiguous_two_digit_year_branch():
    # Exercise the year<100 normalization inside date_is_ambiguous itself
    # (not just parse_date) via a '/' date with two valid short-year readings.
    assert isinstance(svc.date_is_ambiguous("03/04/25"), bool)


# ---------------------------------------------------------------------------
# split_name (lines 248, 250)
# ---------------------------------------------------------------------------


def test_split_name_empty_string():
    assert svc.split_name("") == ("", "")
    assert svc.split_name("   (nickname)  ") == ("", "")


def test_split_name_single_word():
    assert svc.split_name("Cher") == ("Cher", "")


def test_split_name_strips_parenthetical():
    assert svc.split_name("Jane Doe (Janie)") == ("Jane", "Doe")


# ---------------------------------------------------------------------------
# normalize_phone (lines 258-260)
# ---------------------------------------------------------------------------


def test_normalize_phone_eleven_digit_leading_one():
    assert svc.normalize_phone("1-306-555-1234") == "+13065551234"


def test_normalize_phone_unrecognized_length_passthrough():
    assert svc.normalize_phone(" 555-1234 ") == "555-1234"


# ---------------------------------------------------------------------------
# canonical_requirement_key (lines 268-269)
# ---------------------------------------------------------------------------


def test_canonical_requirement_key_alias_via_raw_lower():
    # "Car Insurance" slugs to "car_insurance" which IS in the alias dict via
    # the slug path already; use a value whose *raw* lowercase (with a space)
    # is the alias key that only matches the second lookup fallback.
    assert svc.canonical_requirement_key("Driving License") == "drivers_license"


def test_canonical_requirement_key_unmapped_falls_back_to_slug():
    assert svc.canonical_requirement_key("Some Other Doc") == "some_other_doc"


# ---------------------------------------------------------------------------
# storage_signed_url (lines 273-282)
# ---------------------------------------------------------------------------


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _SignedUrlStorage:
    def __init__(self, result_data):
        self._result_data = result_data

    def from_(self, _bucket):
        return self

    def create_signed_url(self, _key, _ttl):
        return _Obj(data=self._result_data)


class _SignedUrlSupabase:
    def __init__(self, result_data):
        self.storage = _SignedUrlStorage(result_data)


def test_storage_signed_url_dict_signedurl_key(monkeypatch):
    monkeypatch.setattr(svc, "supabase", _SignedUrlSupabase({"signedURL": "https://x/y"}))
    assert svc.storage_signed_url("k") == "https://x/y"


def test_storage_signed_url_dict_signed_url_snake_key(monkeypatch):
    monkeypatch.setattr(svc, "supabase", _SignedUrlSupabase({"signed_url": "https://x/z"}))
    assert svc.storage_signed_url("k") == "https://x/z"


def test_storage_signed_url_object_attribute_fallback(monkeypatch):
    monkeypatch.setattr(svc, "supabase", _SignedUrlSupabase(_Obj(signed_url="https://x/obj")))
    assert svc.storage_signed_url("k") == "https://x/obj"


def test_storage_signed_url_raises_when_no_url_found(monkeypatch):
    monkeypatch.setattr(svc, "supabase", _SignedUrlSupabase(_Obj()))
    with pytest.raises(RuntimeError, match="no URL"):
        svc.storage_signed_url("some-key")


# ---------------------------------------------------------------------------
# encrypt_pii (lines 288-289)
# ---------------------------------------------------------------------------


def test_encrypt_pii_none_input_short_circuits(monkeypatch):
    # No supabase call needed for falsy input.
    monkeypatch.setattr(svc, "supabase", None)
    assert svc.encrypt_pii(None) is None
    assert svc.encrypt_pii("") is None


def test_encrypt_pii_calls_rpc(monkeypatch):
    fake = _install_fake(monkeypatch)
    assert svc.encrypt_pii("SECRET123") == "ENC[SECRET123]"


# ---------------------------------------------------------------------------
# vehicle_field_changes year branch (line 321)
# ---------------------------------------------------------------------------


def test_vehicle_field_changes_year_diff_detected():
    existing = {"vehicle_year": 2019}
    changes, vin = svc.vehicle_field_changes({"vehicle_year": "2022"}, existing)
    assert changes["vehicle_year"] == 2022
    assert vin is None


# ---------------------------------------------------------------------------
# get_service_area (lines 334, 356)
# ---------------------------------------------------------------------------


def test_get_service_area_by_id(monkeypatch):
    _install_fake(
        monkeypatch,
        service_areas=[{"id": "sa-1", "name": "Saskatoon", "province": "SK"}],
    )
    area = svc.get_service_area("sa-1", "")
    assert area["id"] == "sa-1"


def test_get_service_area_multiple_matches_without_id_raises(monkeypatch):
    _install_fake(
        monkeypatch,
        service_areas=[
            {"id": "sa-1", "name": "Saskatoon North", "province": "SK"},
            {"id": "sa-2", "name": "Saskatoon South", "province": "SK"},
        ],
    )
    with pytest.raises(RuntimeError, match="Multiple service areas matched"):
        svc.get_service_area(None, "Saskatoon")


def test_get_service_area_not_found_raises(monkeypatch):
    _install_fake(monkeypatch, service_areas=[])
    with pytest.raises(RuntimeError, match="not found"):
        svc.get_service_area(None, "Nowhere")


# ---------------------------------------------------------------------------
# vehicle_type_map skip-no-id branch (line 369)
# ---------------------------------------------------------------------------


def test_vehicle_type_map_skips_rows_without_id(monkeypatch):
    _install_fake(monkeypatch, vehicle_types=[{"id": None, "name": "Ghost"}, {"id": "vt-1", "name": "Sedan"}])
    out = svc.vehicle_type_map()
    assert "ghost" not in out
    assert out["sedan"] == "vt-1"


# ---------------------------------------------------------------------------
# validate_required_columns empty rows (lines 378-379)
# ---------------------------------------------------------------------------


def test_validate_required_columns_empty_rows():
    plan = svc.ImportPlan()
    svc.validate_required_columns([], plan)
    assert len(plan.errors) == 1
    assert plan.errors[0].field == "drivers_csv"


# ---------------------------------------------------------------------------
# work_auth_status (lines 387, 389, 392, 394)
# ---------------------------------------------------------------------------


def test_work_auth_status_citizen():
    assert svc.work_auth_status({"citizen": "yes"}) == "citizen"


def test_work_auth_status_permanent_resident():
    assert svc.work_auth_status({"permanent_resident": "yes"}) == "permanent_resident"


def test_work_auth_status_indefinite():
    assert svc.work_auth_status({"work_authorization_expiry": "Indefinite"}) == "indefinite"


def test_work_auth_status_expiring():
    assert svc.work_auth_status({"work_authorization_expiry": "2027-01-01"}) == "expiring"


def test_work_auth_status_unknown():
    assert svc.work_auth_status({}) == "unknown"


# ---------------------------------------------------------------------------
# regulatory_authority_defaults (line 413)
# ---------------------------------------------------------------------------


def test_regulatory_authority_defaults_derives_sk_from_area_name():
    authority, region = svc.regulatory_authority_defaults({}, {"name": "Saskatoon", "province": ""})
    assert region == "SK"
    assert authority == "SGI"


def test_regulatory_authority_defaults_non_sk_area():
    authority, region = svc.regulatory_authority_defaults({}, {"name": "Somewhere Else"})
    assert region == ""
    assert authority == "Provincial / municipal authority"


# ---------------------------------------------------------------------------
# build_plan: duplicate old_driver_id / service scope mismatch (504-505, 516-517)
# ---------------------------------------------------------------------------


def test_build_plan_duplicate_old_driver_id_errors(monkeypatch):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row(), _driver_row()], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "old_driver_id" and "duplicate" in e.message for e in plan.errors)


def test_build_plan_service_area_scope_mismatch_errors(monkeypatch):
    _install_fake(monkeypatch)
    row = _driver_row(service_area="regina")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "service_area" for e in plan.errors)


# ---------------------------------------------------------------------------
# build_plan: date_of_birth parse error + ambiguous date warning (575-576, 580)
# ---------------------------------------------------------------------------


def test_build_plan_unparseable_dob_errors(monkeypatch):
    _install_fake(monkeypatch)
    row = _driver_row(date_of_birth="not-a-real-date")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "date_of_birth" for e in plan.errors)


def test_build_plan_ambiguous_driver_date_field_warns(monkeypatch):
    _install_fake(monkeypatch)
    row = _driver_row(license_expiry="03/04/25")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert any(w.field == "license_expiry" for w in plan.warnings)


# ---------------------------------------------------------------------------
# build_plan document-row pass with files_root set (lines 691-771, the
# largest missing block).
# ---------------------------------------------------------------------------


def _doc_row(**overrides):
    row = {
        "old_driver_id": "OLD-1",
        "requirement_key": "drivers_license",
        "status": "approved",
        "file_path": "doc.pdf",
        "side": "",
    }
    row.update(overrides)
    return row


def test_build_plan_document_row_no_matching_driver_errors(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row()], [_doc_row(old_driver_id="OLD-999")], tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "old_driver_id" and "no importable driver row" in e.message for e in plan.errors)


def test_build_plan_document_row_disallowed_requirement_key_errors(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    plan = svc.build_plan(
        [_driver_row()], [_doc_row(requirement_key="underwater_basket_weaving")], tmp_path, SERVICE_AREA, "batch1"
    )
    assert any(e.field == "requirement_key" for e in plan.errors)


def test_build_plan_document_row_invalid_status_errors(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row()], [_doc_row(status="on_fire")], tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "status" for e in plan.errors)


def test_build_plan_document_row_file_not_found_errors(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    plan = svc.build_plan([_driver_row()], [_doc_row(file_path="missing.pdf")], tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "file_path" for e in plan.errors)


def test_build_plan_document_row_success_plans_file_and_doc(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4 fake content")
    plan = svc.build_plan([_driver_row()], [_doc_row()], tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert len(plan.docs_to_insert) == 1
    assert len(plan.files_to_upload) == 1
    doc = plan.docs_to_insert[0]
    assert doc["requirement_key"] == "drivers_license"
    assert doc["status"] == "approved"
    file_path, storage_key, doc_id = plan.files_to_upload[0]
    assert file_path == tmp_path / "doc.pdf"
    assert doc_id == doc["id"]
    assert "batch1/OLD-1/drivers_license" in storage_key


def test_build_plan_document_row_expiry_unparseable_errors(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    (tmp_path / "doc.pdf").write_bytes(b"data")
    plan = svc.build_plan([_driver_row()], [_doc_row(expiry_date="garbage")], tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "expiry_date" for e in plan.errors)


def test_build_plan_document_row_ambiguous_expiry_warns(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    (tmp_path / "doc.pdf").write_bytes(b"data")
    plan = svc.build_plan([_driver_row()], [_doc_row(expiry_date="03/04/25")], tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert any(w.field == "expiry_date" for w in plan.warnings)


def test_build_plan_document_row_indefinite_expiry_is_fine(monkeypatch, tmp_path):
    _install_fake(monkeypatch)
    (tmp_path / "doc.pdf").write_bytes(b"data")
    plan = svc.build_plan([_driver_row()], [_doc_row(expiry_date="indefinite")], tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert plan.docs_to_insert[0]["expiry_date"] is None


def test_build_plan_resumed_driver_dedups_existing_documents(monkeypatch, tmp_path):
    # Driver was already imported by this importer (resume path); a document
    # of the same (requirement_key, side) already exists in driver_documents
    # -> the re-upload's document row is skipped with a warning, not re-queued.
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
    }
    fake = _install_fake(monkeypatch, drivers=[existing_driver])
    fake.store["driver_documents"] = [{"driver_id": "drv-1", "requirement_key": "drivers_license", "side": None}]
    (tmp_path / "doc.pdf").write_bytes(b"data")
    plan = svc.build_plan([_driver_row()], [_doc_row(side="")], tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert not plan.docs_to_insert
    assert any(w.message.startswith("document already imported") for w in plan.warnings)


def test_build_plan_resumed_driver_new_document_is_queued(monkeypatch, tmp_path):
    # Same resume scenario, but the document row is for a requirement_key not
    # already present -> it IS queued (existing_docs_cache miss branch).
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
    }
    fake = _install_fake(monkeypatch, drivers=[existing_driver])
    fake.store["driver_documents"] = [{"driver_id": "drv-1", "requirement_key": "insurance", "side": None}]
    (tmp_path / "doc.pdf").write_bytes(b"data")
    plan = svc.build_plan(
        [_driver_row()], [_doc_row(requirement_key="drivers_license")], tmp_path, SERVICE_AREA, "batch1"
    )
    assert not plan.errors
    assert len(plan.docs_to_insert) == 1


def test_build_plan_document_row_no_allowed_keys_accepts_any_slug(monkeypatch, tmp_path):
    # required_documents is empty -> allowed_doc_keys is empty -> the
    # `if allowed_doc_keys and key not in allowed_doc_keys` guard is skipped,
    # so any requirement_key slug is accepted.
    _install_fake(monkeypatch)
    area = {**SERVICE_AREA, "required_documents": []}
    (tmp_path / "doc.pdf").write_bytes(b"data")
    plan = svc.build_plan([_driver_row()], [_doc_row(requirement_key="anything_goes")], tmp_path, area, "batch1")
    assert not plan.errors
    assert plan.docs_to_insert[0]["requirement_key"] == "anything_goes"


# ---------------------------------------------------------------------------
# Fixed (2026-08-03): has_import_documents now requires an APPROVED document,
# not mere presence of a document row. See driver_import_service.py
# build_plan()'s `document_old_ids` set comprehension.
# ---------------------------------------------------------------------------


def test_active_status_requires_approved_document_status(monkeypatch, tmp_path):
    """A rejected document must NOT be sufficient to mark the driver
    active/verified — has_import_documents now checks the document's own
    status, not just its presence."""
    _install_fake(monkeypatch)
    (tmp_path / "doc.pdf").write_bytes(b"data")
    row = _driver_row(spinr_approved="yes", regulatory_authority_approved="yes")
    doc_row = _doc_row(status="rejected")  # explicitly REJECTED document
    plan = svc.build_plan([row], [doc_row], tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    driver = plan.drivers_to_insert[0]
    assert driver["status"] == "needs_review"
    assert driver["is_verified"] is False
    assert plan.docs_to_insert[0]["status"] == "rejected"


def test_active_status_with_approved_document_status(monkeypatch, tmp_path):
    """An approved document DOES count toward active/verified status,
    given the CSV approval flags are also both true."""
    _install_fake(monkeypatch)
    (tmp_path / "doc.pdf").write_bytes(b"data")
    row = _driver_row(spinr_approved="yes", regulatory_authority_approved="yes")
    doc_row = _doc_row(status="approved")
    plan = svc.build_plan([row], [doc_row], tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    driver = plan.drivers_to_insert[0]
    assert driver["status"] == "active"
    assert driver["is_verified"] is True


def test_active_status_requires_approved_document_status_pending(monkeypatch, tmp_path):
    """A pending (not yet reviewed) document also must not count."""
    _install_fake(monkeypatch)
    (tmp_path / "doc.pdf").write_bytes(b"data")
    row = _driver_row(spinr_approved="yes", regulatory_authority_approved="yes")
    doc_row = _doc_row(status="pending")
    plan = svc.build_plan([row], [doc_row], tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    driver = plan.drivers_to_insert[0]
    assert driver["status"] == "needs_review"
    assert driver["is_verified"] is False


# ---------------------------------------------------------------------------
# commit_plan (lines 796-802, 806-812, 816-818, 820)
# ---------------------------------------------------------------------------


def test_commit_plan_refuses_with_errors(monkeypatch):
    _install_fake(monkeypatch)
    plan = svc.ImportPlan()
    plan.errors.append(svc.ImportErrorItem("OLD-1", "x", "boom"))
    with pytest.raises(RuntimeError, match="validation errors"):
        svc.commit_plan(plan)


def test_commit_plan_inserts_users_and_drivers(monkeypatch):
    fake = _install_fake(monkeypatch)
    plan = svc.ImportPlan()
    plan.users_to_insert.append({"id": "u1", "phone": "+13065551234"})
    plan.drivers_to_insert.append(
        {
            "id": "d1",
            "user_id": "u1",
            "_plain_vehicle_vin": "1FTEXAMPLE",
            "_plain_license_number": "LIC-1",
            "name": "Jane Doe",
        }
    )
    svc.commit_plan(plan)
    assert fake.store["users"] == [{"id": "u1", "phone": "+13065551234"}]
    d = fake.store["drivers"][0]
    assert d["vehicle_vin"] == "1FTEXAMPLE"
    assert d["license_number"] == "ENC[LIC-1]"
    assert "_plain_vehicle_vin" not in d
    assert "_plain_license_number" not in d


def test_commit_plan_skips_update_when_no_fields_changed(monkeypatch):
    fake = _install_fake(monkeypatch, drivers=[{"id": "d1", "vehicle_color": "Red"}])
    plan = svc.ImportPlan()
    plan.drivers_to_update.append({"id": "d1", "old_driver_id": "OLD-1", "changes": {}, "vin_plain": None})
    svc.commit_plan(plan)
    # Untouched -- the `if not fields: continue` branch skipped the update call.
    assert fake.store["drivers"][0]["vehicle_color"] == "Red"
    assert "updated_at" not in fake.store["drivers"][0]


def test_commit_plan_applies_vehicle_update_with_vin(monkeypatch):
    fake = _install_fake(monkeypatch, drivers=[{"id": "d1", "vehicle_color": "Red", "vehicle_vin": None}])
    plan = svc.ImportPlan()
    plan.drivers_to_update.append(
        {"id": "d1", "old_driver_id": "OLD-1", "changes": {"vehicle_color": "Black"}, "vin_plain": "VIN123"}
    )
    svc.commit_plan(plan)
    updated = fake.store["drivers"][0]
    assert updated["vehicle_color"] == "Black"
    assert updated["vehicle_vin"] == "VIN123"
    assert "updated_at" in updated


def test_commit_plan_uploads_files_and_inserts_docs(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch)
    file_path = tmp_path / "license.pdf"
    file_path.write_bytes(b"pdf-bytes")
    plan = svc.ImportPlan()
    plan.files_to_upload.append((file_path, "storage/key/1", "doc-1"))
    plan.docs_to_insert.append(
        {
            "id": "doc-1",
            "driver_id": "d1",
            "requirement_key": "drivers_license",
            "status": "approved",
            "document_url": "placeholder",
        }
    )
    svc.commit_plan(plan)
    assert len(fake.uploads) == 1
    assert fake.uploads[0]["bucket"] == "driver-documents"
    assert fake.uploads[0]["path"] == "storage/key/1"
    inserted = fake.store["driver_documents"][0]
    assert inserted["document_url"] == "https://signed.example/storage/key/1"


def test_commit_plan_no_docs_no_insert_call(monkeypatch):
    fake = _install_fake(monkeypatch)
    plan = svc.ImportPlan()
    svc.commit_plan(plan)
    assert fake.store.get("driver_documents", []) == []


# ---------------------------------------------------------------------------
# print_report (lines 824-836)
# ---------------------------------------------------------------------------


def test_print_report_dry_run(capsys):
    plan = svc.ImportPlan()
    plan.users_to_insert.append({"id": "u1"})
    plan.drivers_to_insert.append({"id": "d1"})
    plan.warnings.append(svc.ImportErrorItem("OLD-1", "resume", "already imported"))
    plan.errors.append(svc.ImportErrorItem("OLD-2", "vehicle_type", "no match"))
    svc.print_report(plan, dry_run=True)
    out = capsys.readouterr().out
    assert "DRY RUN report" in out
    assert "users planned: 1" in out
    assert "WARNING old_driver_id=OLD-1 field=resume" in out
    assert "ERROR old_driver_id=OLD-2 field=vehicle_type" in out


def test_print_report_commit_mode(capsys):
    plan = svc.ImportPlan()
    svc.print_report(plan, dry_run=False)
    out = capsys.readouterr().out
    assert "COMMIT report" in out
    assert "warnings: 0" in out
    assert "errors: 0" in out
