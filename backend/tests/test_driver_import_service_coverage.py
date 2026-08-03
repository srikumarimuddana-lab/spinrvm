"""Coverage-closing tests for backend/services/driver_import_service.py.

Companion to test_driver_import_service.py (build_plan's batch-prefetch/
resume/web-flow-rejection semantics) and test_admin_driver_import.py (the
admin HTTP endpoints, exercised end-to-end). This file closes the remaining
gaps:

  * the small pure helpers (parse_bool, parse_date, date_is_ambiguous,
    split_name, normalize_phone, canonical_requirement_key, work_auth_status,
    regulatory_authority_defaults) at the unit level, each branch directly
  * storage_signed_url / encrypt_pii, which need a fake supabase.storage /
    supabase.rpc the other test files don't provide
  * get_service_area's by-id and multiple-match/no-match branches (only
    exercised indirectly, by name, via the admin route today)
  * vehicle_type_map's skip-row-without-id defensive branch
  * vehicle_field_changes' vehicle_year diff branch
  * build_plan's duplicate-old_driver_id, wrong-service-area,
    unparseable-date_of_birth, and ambiguous-date-warning branches
  * build_plan's entire CLI document-row pipeline (files_root set) — the web
    flow's files_root=None rejection is already covered elsewhere, but the
    disallowed-requirement-key / bad-status / file-not-found / resumed-skip /
    ambiguous-expiry / unparseable-expiry / happy-path branches were not
  * commit_plan end to end: refuses on validation errors, inserts users/
    drivers/updates/documents, uploads files, and encrypts license_number
    while leaving VIN plaintext
  * print_report's console summary
"""

from __future__ import annotations

from pathlib import Path

from backend.services import driver_import_service as svc

IMPORT_SOURCE = svc.IMPORT_SOURCE

SERVICE_AREA = {
    "id": "sa-1",
    "name": "Saskatoon",
    "province": "SK",
    "regulatory_authority": "SGI",
    "regulatory_region": "SK",
    "required_documents": [{"key": "drivers_license"}, {"key": "insurance"}],
}


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


# ── fake supabase (table + storage + rpc) ────────────────────────────────


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None
        self._update = None

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
        self._insert = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, fields):
        self._update = fields
        return self

    def _matched(self):
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
        return rows

    def execute(self):
        if self._insert is not None:
            self.store.setdefault(self.table, []).extend(self._insert)
            return _FakeExecute(list(self._insert))
        if self._update is not None:
            matched = self._matched()
            for row in matched:
                row.update(self._update)
            return _FakeExecute(matched)
        return _FakeExecute(self._matched())


class _FakeBucket:
    def __init__(self, recorder, signed_url_data=None, raise_on_signed_url=False):
        self.recorder = recorder
        self.signed_url_data = (
            signed_url_data if signed_url_data is not None else {"signedURL": "https://signed.example/x"}
        )
        self.raise_on_signed_url = raise_on_signed_url

    def upload(self, path, file, file_options=None):
        self.recorder.setdefault("uploads", []).append({"path": path, "size": len(file), "file_options": file_options})
        return _FakeExecute(None)

    def create_signed_url(self, storage_key, ttl):
        self.recorder.setdefault("signed_url_calls", []).append((storage_key, ttl))
        return _FakeExecute(None if self.raise_on_signed_url else self.signed_url_data)


class _FakeStorage:
    def __init__(self, recorder, **bucket_kwargs):
        self.recorder = recorder
        self.bucket_kwargs = bucket_kwargs

    def from_(self, _bucket_name):
        return _FakeBucket(self.recorder, **self.bucket_kwargs)


class _FakeRpc:
    def __init__(self, name, params, recorder):
        self.name = name
        self.params = params
        self.recorder = recorder

    def execute(self):
        self.recorder.setdefault("rpc_calls", []).append((self.name, self.params))
        # encrypt_driver_pii: echo an "encrypted" marker so tests can assert
        # the plaintext never lands in the drivers insert payload.
        return _FakeExecute(f"enc::{self.params.get('plaintext')}")


class _FakeSupabase:
    def __init__(self, store=None, storage_kwargs=None):
        self.store = store if store is not None else {"vehicle_types": [{"id": "vt-sedan", "name": "Sedan"}]}
        self.recorder: dict = {}
        self.storage = _FakeStorage(self.recorder, **(storage_kwargs or {}))

    def table(self, name):
        return _FakeQuery(name, self.store)

    def rpc(self, name, params):
        return _FakeRpc(name, params, self.recorder)


def _install(monkeypatch, **kwargs):
    fake = _FakeSupabase(**kwargs)
    monkeypatch.setattr(svc, "supabase", fake)
    return fake


# ── CSV parsing helpers ──────────────────────────────────────────────


def test_parse_csv_rows_no_header_raises():
    import csv
    import io

    reader = csv.DictReader(io.StringIO(""))
    try:
        svc.parse_csv_rows(reader)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "no header row" in str(exc)


def test_read_csv_text_strips_leading_bom():
    text = "﻿old_driver_id,full_name\nOLD-1,Jane Doe\n"
    rows = svc.read_csv_text(text)
    assert rows == [{"old_driver_id": "OLD-1", "full_name": "Jane Doe"}]


def test_read_csv_text_without_bom():
    text = "old_driver_id,full_name\nOLD-1,Jane Doe\n"
    rows = svc.read_csv_text(text)
    assert rows[0]["old_driver_id"] == "OLD-1"


def test_read_csv_reads_from_disk(tmp_path: Path):
    csv_path = tmp_path / "drivers.csv"
    csv_path.write_text("old_driver_id,full_name\nOLD-1,Jane Doe\n", encoding="utf-8-sig")
    rows = svc.read_csv(csv_path)
    assert rows[0]["old_driver_id"] == "OLD-1"


# ── parse_bool ─────────────────────────────────────────────────────────


def test_parse_bool_truthy_values():
    for v in ("y", "yes", "true", "1", "approved", "valid", "YES", "True"):
        assert svc.parse_bool(v) is True


def test_parse_bool_falsy_values():
    for v in ("n", "no", "false", "0", "not approved", "invalid", "NO"):
        assert svc.parse_bool(v) is False


def test_parse_bool_empty_is_none():
    assert svc.parse_bool("") is None
    assert svc.parse_bool(None) is None


def test_parse_bool_unrecognized_is_none():
    assert svc.parse_bool("maybe") is None


# ── parse_date / iso_date ─────────────────────────────────────────────


def test_parse_date_iso_format():
    assert svc.parse_date("2020-01-15").isoformat() == "2020-01-15"


def test_parse_date_two_digit_year_rolls_to_2000s():
    # %d-%b-%y: 05-Mar-20 -> 2020, not 1920.
    d = svc.parse_date("05-Mar-20")
    assert d is not None
    assert d.year == 2020 and d.month == 3 and d.day == 5


def test_parse_date_unparseable_returns_none():
    assert svc.parse_date("not-a-date") is None


def test_parse_date_synonyms_return_none():
    for v in ("indefinite", "valid", "n/a", "na", "none", ""):
        assert svc.parse_date(v) is None


def test_iso_date_wraps_parse_date():
    assert svc.iso_date("2020-01-15") == "2020-01-15"
    assert svc.iso_date("garbage") is None


# ── date_is_ambiguous ──────────────────────────────────────────────────


def test_date_is_ambiguous_true_for_day_month_swap():
    # 03/04/25 is Apr 3 2025 under d/m/y, Mar 4 2025 under m/d/y.
    assert svc.date_is_ambiguous("03/04/25") is True


def test_date_is_ambiguous_false_for_iso():
    assert svc.date_is_ambiguous("2020-01-15") is False


def test_date_is_ambiguous_false_for_blank_or_synonym():
    assert svc.date_is_ambiguous("") is False
    assert svc.date_is_ambiguous("indefinite") is False


# ── split_name ───────────────────────────────────────────────────────


def test_split_name_empty():
    assert svc.split_name("") == ("", "")
    assert svc.split_name("   ") == ("", "")


def test_split_name_single_word():
    assert svc.split_name("Cher") == ("Cher", "")


def test_split_name_strips_parenthetical():
    assert svc.split_name("Jane Doe (Jenny)") == ("Jane", "Doe")


def test_split_name_multi_word_last_name():
    assert svc.split_name("Jane Middle Doe") == ("Jane", "Middle Doe")


# ── normalize_phone ──────────────────────────────────────────────────


def test_normalize_phone_ten_digits():
    assert svc.normalize_phone("306-555-1234") == "+13065551234"


def test_normalize_phone_eleven_digits_leading_one():
    assert svc.normalize_phone("13065551234") == "+13065551234"


def test_normalize_phone_invalid_length_passthrough():
    assert svc.normalize_phone(" 12345 ") == "12345"


# ── canonical_requirement_key ────────────────────────────────────────


def test_canonical_requirement_key_alias():
    assert svc.canonical_requirement_key("Criminal Record Check") == "background_check"
    assert svc.canonical_requirement_key("car insurance") == "insurance"


def test_canonical_requirement_key_unmapped_passthrough():
    assert svc.canonical_requirement_key("custom key") == "custom_key"


# ── work_auth_status ─────────────────────────────────────────────────


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


# ── regulatory_authority_defaults ────────────────────────────────────


def test_regulatory_authority_defaults_saskatoon_area_defaults_sk_sgi():
    authority, region = svc.regulatory_authority_defaults({}, {"name": "Saskatoon"})
    assert region == "SK"
    assert authority == "SGI"


def test_regulatory_authority_defaults_row_overrides_win():
    authority, region = svc.regulatory_authority_defaults(
        {"regulatory_authority": "Custom Authority", "regulatory_region": "AB"}, {"name": "Saskatoon"}
    )
    assert authority == "Custom Authority"
    assert region == "AB"


def test_regulatory_authority_defaults_non_sk_area_generic_authority():
    authority, region = svc.regulatory_authority_defaults({}, {"name": "Somewhere Else"})
    assert region == ""
    assert authority == "Provincial / municipal authority"


# ── storage_signed_url ───────────────────────────────────────────────


def test_storage_signed_url_dict_data(monkeypatch):
    _install(monkeypatch, storage_kwargs={"signed_url_data": {"signedURL": "https://signed.example/a"}})
    assert svc.storage_signed_url("k1") == "https://signed.example/a"


def test_storage_signed_url_object_attr_fallback(monkeypatch):
    class _ObjData:
        signed_url = "https://signed.example/b"

    _install(monkeypatch, storage_kwargs={"signed_url_data": _ObjData()})
    assert svc.storage_signed_url("k2") == "https://signed.example/b"


def test_storage_signed_url_missing_url_raises(monkeypatch):
    _install(monkeypatch, storage_kwargs={"raise_on_signed_url": True})
    try:
        svc.storage_signed_url("k3")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "k3" in str(exc)


# ── encrypt_pii ──────────────────────────────────────────────────────


def test_encrypt_pii_none_passthrough(monkeypatch):
    fake = _install(monkeypatch)
    assert svc.encrypt_pii(None) is None
    assert svc.encrypt_pii("") is None
    assert "rpc_calls" not in fake.recorder


def test_encrypt_pii_calls_rpc(monkeypatch):
    fake = _install(monkeypatch)
    result = svc.encrypt_pii("D1234567")
    assert result == "enc::D1234567"
    assert fake.recorder["rpc_calls"] == [("encrypt_driver_pii", {"plaintext": "D1234567"})]


# ── vehicle_field_changes ────────────────────────────────────────────


def test_vehicle_field_changes_vehicle_year_diff():
    existing = {"vehicle_make": "Toyota", "vehicle_model": "Corolla", "vehicle_year": 2018, "license_plate": "ABC"}
    row = _driver_row(vehicle_year="2022")
    changes, vin_plain = svc.vehicle_field_changes(row, existing)
    assert changes["vehicle_year"] == 2022
    assert vin_plain is None


# ── get_service_area ─────────────────────────────────────────────────


def test_get_service_area_by_id_found(monkeypatch):
    _install(monkeypatch, store={"service_areas": [SERVICE_AREA]})
    area = svc.get_service_area("sa-1", "")
    assert area["id"] == "sa-1"


def test_get_service_area_by_id_not_found_raises(monkeypatch):
    _install(monkeypatch, store={"service_areas": [SERVICE_AREA]})
    try:
        svc.get_service_area("sa-missing", "")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "not found" in str(exc)


def test_get_service_area_by_name_single_match(monkeypatch):
    _install(monkeypatch, store={"service_areas": [SERVICE_AREA]})
    area = svc.get_service_area(None, "Saskatoon")
    assert area["id"] == "sa-1"


def test_get_service_area_by_name_no_match_raises(monkeypatch):
    _install(monkeypatch, store={"service_areas": [SERVICE_AREA]})
    try:
        svc.get_service_area(None, "Nowhere")
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass


def test_get_service_area_by_name_multiple_matches_raises(monkeypatch):
    dup = {**SERVICE_AREA, "id": "sa-2", "name": "Saskatoon East"}
    _install(monkeypatch, store={"service_areas": [SERVICE_AREA, dup]})
    try:
        svc.get_service_area(None, "Saskatoon")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "Multiple service areas matched" in str(exc)


# ── vehicle_type_map ─────────────────────────────────────────────────


def test_vehicle_type_map_skips_row_without_id(monkeypatch):
    _install(monkeypatch, store={"vehicle_types": [{"id": "vt-sedan", "name": "Sedan"}, {"name": "Ghost"}]})
    vt_map = svc.vehicle_type_map()
    assert vt_map.get("sedan") == "vt-sedan"
    assert "ghost" not in vt_map


def test_validate_required_columns_empty_rows_errors():
    plan = svc.ImportPlan()
    svc.validate_required_columns([], plan)
    assert any(e.field == "drivers_csv" and "empty" in e.message for e in plan.errors)


def test_build_plan_empty_driver_rows_errors(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_plan([], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "drivers_csv" for e in plan.errors)


# ── build_plan: duplicate / scope / DOB / ambiguous-date branches ────


def test_build_plan_duplicate_old_driver_id_errors(monkeypatch):
    _install(monkeypatch)
    rows = [_driver_row(), _driver_row(phone="3065559999", email="other@example.com")]
    plan = svc.build_plan(rows, [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "old_driver_id" and "duplicate" in e.message for e in plan.errors)
    assert len(plan.users_to_insert) == 1  # only the first row was planned


def test_build_plan_wrong_service_area_errors(monkeypatch):
    _install(monkeypatch)
    row = _driver_row(service_area="regina")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "service_area" for e in plan.errors)
    assert not plan.users_to_insert


def test_build_plan_unparseable_dob_errors(monkeypatch):
    _install(monkeypatch)
    row = _driver_row(date_of_birth="not-a-date")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert any(e.field == "date_of_birth" for e in plan.errors)


def test_build_plan_ambiguous_date_field_warns(monkeypatch):
    _install(monkeypatch)
    row = _driver_row(license_expiry="03/04/25")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert any(w.field == "license_expiry" for w in plan.warnings)


def test_build_plan_resume_vehicle_year_change(monkeypatch):
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "ABC123",
        "vehicle_year": 2018,
    }
    _install(monkeypatch, store={"drivers": [existing_driver], "vehicle_types": [{"id": "vt-sedan", "name": "Sedan"}]})
    row = _driver_row(vehicle_year="2022")
    plan = svc.build_plan([row], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert plan.drivers_to_update[0]["changes"]["vehicle_year"] == 2022


# ── build_plan: CLI document-row pipeline (files_root set) ───────────


def test_build_plan_document_happy_path(tmp_path: Path, monkeypatch):
    _install(monkeypatch)
    doc_file = tmp_path / "license.pdf"
    doc_file.write_bytes(b"%PDF-1.4 fake")
    doc_rows = [
        {
            "old_driver_id": "OLD-1",
            "requirement_key": "drivers_license",
            "file_path": "license.pdf",
            "status": "approved",
        }
    ]
    plan = svc.build_plan([_driver_row()], doc_rows, tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert len(plan.docs_to_insert) == 1
    assert plan.docs_to_insert[0]["requirement_key"] == "drivers_license"
    assert plan.docs_to_insert[0]["status"] == "approved"
    assert len(plan.files_to_upload) == 1


def test_build_plan_document_disallowed_requirement_key_errors(tmp_path: Path, monkeypatch):
    _install(monkeypatch)
    doc_file = tmp_path / "x.pdf"
    doc_file.write_bytes(b"x")
    doc_rows = [{"old_driver_id": "OLD-1", "requirement_key": "not_a_real_key", "file_path": "x.pdf"}]
    plan = svc.build_plan([_driver_row()], doc_rows, tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "requirement_key" for e in plan.errors)


def test_build_plan_document_invalid_status_errors(tmp_path: Path, monkeypatch):
    _install(monkeypatch)
    doc_file = tmp_path / "x.pdf"
    doc_file.write_bytes(b"x")
    doc_rows = [
        {"old_driver_id": "OLD-1", "requirement_key": "drivers_license", "file_path": "x.pdf", "status": "bogus"}
    ]
    plan = svc.build_plan([_driver_row()], doc_rows, tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "status" for e in plan.errors)


def test_build_plan_document_file_not_found_errors(tmp_path: Path, monkeypatch):
    _install(monkeypatch)
    doc_rows = [{"old_driver_id": "OLD-1", "requirement_key": "drivers_license", "file_path": "missing.pdf"}]
    plan = svc.build_plan([_driver_row()], doc_rows, tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "file_path" for e in plan.errors)


def test_build_plan_document_unknown_old_id_errors(tmp_path: Path, monkeypatch):
    _install(monkeypatch)
    doc_file = tmp_path / "x.pdf"
    doc_file.write_bytes(b"x")
    doc_rows = [{"old_driver_id": "OLD-999", "requirement_key": "drivers_license", "file_path": "x.pdf"}]
    plan = svc.build_plan([_driver_row()], doc_rows, tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "old_driver_id" and "document row" in e.message for e in plan.errors)


def test_build_plan_document_ambiguous_expiry_warns(tmp_path: Path, monkeypatch):
    _install(monkeypatch)
    doc_file = tmp_path / "x.pdf"
    doc_file.write_bytes(b"x")
    doc_rows = [
        {
            "old_driver_id": "OLD-1",
            "requirement_key": "drivers_license",
            "file_path": "x.pdf",
            "expiry_date": "03/04/25",
        }
    ]
    plan = svc.build_plan([_driver_row()], doc_rows, tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert any(w.field == "expiry_date" for w in plan.warnings)


def test_build_plan_document_unparseable_expiry_errors(tmp_path: Path, monkeypatch):
    _install(monkeypatch)
    doc_file = tmp_path / "x.pdf"
    doc_file.write_bytes(b"x")
    doc_rows = [
        {
            "old_driver_id": "OLD-1",
            "requirement_key": "drivers_license",
            "file_path": "x.pdf",
            "expiry_date": "not-a-date",
        }
    ]
    plan = svc.build_plan([_driver_row()], doc_rows, tmp_path, SERVICE_AREA, "batch1")
    assert any(e.field == "expiry_date" for e in plan.errors)


def test_build_plan_document_resumed_driver_skips_existing_doc(tmp_path: Path, monkeypatch):
    existing_driver = {
        "id": "drv-1",
        "phone": "+13065551234",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "OLD-1"},
        "vehicle_make": "Toyota",
        "vehicle_model": "Corolla",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
    }
    fake = _install(
        monkeypatch,
        store={
            "drivers": [existing_driver],
            "driver_documents": [{"driver_id": "drv-1", "requirement_key": "drivers_license", "side": None}],
            "vehicle_types": [{"id": "vt-sedan", "name": "Sedan"}],
        },
    )
    doc_file = tmp_path / "x.pdf"
    doc_file.write_bytes(b"x")
    doc_rows = [{"old_driver_id": "OLD-1", "requirement_key": "drivers_license", "file_path": "x.pdf"}]
    plan = svc.build_plan([_driver_row()], doc_rows, tmp_path, SERVICE_AREA, "batch1")
    assert not plan.errors
    assert not plan.docs_to_insert
    assert any(w.field == "drivers_license" and "already imported" in w.message for w in plan.warnings)
    assert fake  # keep reference alive/used


# ── commit_plan ──────────────────────────────────────────────────────


def test_commit_plan_refuses_with_errors(monkeypatch):
    _install(monkeypatch)
    plan = svc.ImportPlan()
    plan.errors.append(svc.ImportErrorItem("OLD-1", "x", "boom"))
    try:
        svc.commit_plan(plan)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "refusing to commit" in str(exc)


def test_commit_plan_full_happy_path(tmp_path: Path, monkeypatch):
    fake = _install(monkeypatch, storage_kwargs={"signed_url_data": {"signedURL": "https://signed.example/doc"}})
    doc_file = tmp_path / "license.pdf"
    doc_file.write_bytes(b"%PDF fake bytes")

    plan = svc.build_plan([_driver_row()], [], None, SERVICE_AREA, "batch1")
    assert not plan.errors
    # Simulate a plaintext license_number + VIN so the encrypt/plaintext split is exercised.
    plan.drivers_to_insert[0]["_plain_license_number"] = "D1234567"
    plan.drivers_to_insert[0]["_plain_vehicle_vin"] = "2T1BURHE0JC123456"
    plan.docs_to_insert.append(
        {
            "id": "doc-1",
            "driver_id": plan.drivers_to_insert[0]["id"],
            "requirement_key": "drivers_license",
            "document_type": "Drivers License",
            "document_url": "storage://placeholder",
            "side": None,
            "status": "pending",
            "expiry_date": None,
        }
    )
    plan.files_to_upload.append((doc_file, "saskatoon-import/batch1/OLD-1/drivers_license/main-doc-1.pdf", "doc-1"))

    svc.commit_plan(plan)

    assert len(fake.store["users"]) == 1
    inserted_driver = fake.store["drivers"][0]
    assert inserted_driver["vehicle_vin"] == "2T1BURHE0JC123456"  # plaintext
    assert inserted_driver["license_number"] == "enc::D1234567"  # encrypted via rpc
    assert "_plain_license_number" not in inserted_driver
    assert "_plain_vehicle_vin" not in inserted_driver
    assert fake.recorder["uploads"][0]["path"] == "saskatoon-import/batch1/OLD-1/drivers_license/main-doc-1.pdf"
    inserted_doc = fake.store["driver_documents"][0]
    assert inserted_doc["document_url"] == "https://signed.example/doc"


def test_commit_plan_applies_vehicle_updates(monkeypatch):
    fake = _install(
        monkeypatch,
        store={
            "drivers": [{"id": "drv-1", "vehicle_color": "Red"}],
            "vehicle_types": [{"id": "vt-sedan", "name": "Sedan"}],
        },
    )
    plan = svc.ImportPlan()
    plan.drivers_to_update.append(
        {"id": "drv-1", "old_driver_id": "OLD-1", "changes": {"vehicle_color": "Black"}, "vin_plain": "VIN123"}
    )
    svc.commit_plan(plan)
    updated = fake.store["drivers"][0]
    assert updated["vehicle_color"] == "Black"
    assert updated["vehicle_vin"] == "VIN123"


def test_commit_plan_skips_update_with_no_fields(monkeypatch):
    """A drivers_to_update entry with no changes and no vin_plain must not
    issue an empty PATCH (defensive branch: build_plan never constructs one
    of these today, but commit_plan must stay safe if it ever does)."""
    fake = _install(monkeypatch, store={"drivers": [{"id": "drv-1", "vehicle_color": "Red"}]})
    plan = svc.ImportPlan()
    plan.drivers_to_update.append({"id": "drv-1", "old_driver_id": "OLD-1", "changes": {}, "vin_plain": None})
    svc.commit_plan(plan)
    # Unchanged — no update call went through.
    assert fake.store["drivers"][0]["vehicle_color"] == "Red"


# ── print_report ─────────────────────────────────────────────────────


def test_print_report_dry_run_and_commit_modes(capsys):
    plan = svc.ImportPlan()
    plan.users_to_insert.append({"id": "u1"})
    plan.drivers_to_insert.append({"id": "d1"})
    plan.warnings.append(svc.ImportErrorItem("OLD-1", "resume", "already imported"))
    plan.errors.append(svc.ImportErrorItem("OLD-2", "vehicle_type", "no match"))

    svc.print_report(plan, dry_run=True)
    out = capsys.readouterr().out
    assert "DRY RUN report" in out
    assert "users planned: 1" in out
    assert "WARNING old_driver_id=OLD-1" in out
    assert "ERROR old_driver_id=OLD-2" in out

    svc.print_report(plan, dry_run=False)
    out2 = capsys.readouterr().out
    assert "COMMIT report" in out2
