"""Unit tests for entity_import_service.py — ZIP parsing (pure) plus
build_plan/commit_plan with db_supabase/_vault_encrypt/bundle_document_uploader
monkeypatched, mirroring test_driver_import_service.py's pattern.
"""

import io
import json
import zipfile
from unittest.mock import AsyncMock

import pytest

from backend.services.data_transfer import entity_import_service as svc


def _make_zip(entities: dict) -> bytes:
    """entities: {folder_name: raw_data_dict}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.txt", "readme")
        for folder, raw in entities.items():
            zf.writestr(f"{folder}/raw_data.json", json.dumps(raw))
    return buf.getvalue()


def _raw_driver(entity_id="d1", phone="3065551234"):
    return {
        "entity_type": "driver",
        "entity_id": entity_id,
        "user": {"id": entity_id, "phone": phone, "full_name": "Jane Doe"},
        "driver_profile": {"id": "drv1", "license_number": "PLAINTEXT-123"},
        "rides": [],
        "driver_insurance_periods": [{"period": 1}],
        "documents": [{"id": "doc1", "document_type": "insurance"}],
    }


# ---------- parse_bundle_zip (pure) ----------


def test_parse_bundle_zip_extracts_entity_and_document_files():
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("driver_d1/raw_data.json", json.dumps(_raw_driver()))
        zf.writestr("driver_d1/documents/doc1.pdf", b"fake pdf bytes")
    entities = svc.parse_bundle_zip(zip_bytes.getvalue())
    assert len(entities) == 1
    assert entities[0].entity_id == "d1"
    assert entities[0].document_files == {"doc1.pdf": b"fake pdf bytes"}


def test_parse_bundle_zip_raises_on_invalid_zip():
    with pytest.raises(svc.BundleParseError):
        svc.parse_bundle_zip(b"not a zip file at all")


def test_parse_bundle_zip_skips_folder_missing_raw_data():
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("broken_folder/documents/doc1.pdf", b"orphaned file, no raw_data.json")
    entities = svc.parse_bundle_zip(zip_bytes.getvalue())
    assert entities == []


# ---------- build_plan ----------


def _install_fake_db(monkeypatch, users_by_phone=None, legacy_matches=None):
    """users_by_phone: {phone: [rows]} for the conflict-check query.
    legacy_matches: {phone: [rows-with-legacy_import_metadata]} for the
    idempotency (existing_match) check — same table shape, different query."""
    users_by_phone = users_by_phone or {}
    legacy_matches = legacy_matches or {}

    async def fake_get_rows(table, filters=None, limit=None):
        phone = (filters or {}).get("phone")
        if table == "drivers" or table == "users":
            # _find_existing_match always queries first per entity; build_plan's
            # own conflict check queries "users" a second time with the same
            # phone. Route by whichever bucket actually has data for this call.
            if phone in legacy_matches:
                return legacy_matches[phone]
            return users_by_phone.get(phone, [])
        return []

    monkeypatch.setattr(svc.db_supabase, "get_rows", fake_get_rows)


@pytest.mark.anyio
async def test_build_plan_empty_zip_is_an_error():
    plan = await svc.build_plan([])
    assert not plan.can_commit
    assert plan.errors[0].field == "bundle"


@pytest.mark.anyio
async def test_build_plan_rejects_unknown_entity_type(monkeypatch):
    _install_fake_db(monkeypatch)
    entities = svc.parse_bundle_zip(_make_zip({"x_1": {**_raw_driver(), "entity_type": "vehicle"}}))
    plan = await svc.build_plan(entities)
    assert not plan.can_commit
    assert plan.errors[0].field == "entity_type"


@pytest.mark.anyio
async def test_build_plan_rejects_missing_phone(monkeypatch):
    _install_fake_db(monkeypatch)
    raw = _raw_driver()
    raw["user"]["phone"] = ""
    entities = svc.parse_bundle_zip(_make_zip({"driver_d1": raw}))
    plan = await svc.build_plan(entities)
    assert not plan.can_commit
    assert plan.errors[0].field == "phone"


@pytest.mark.anyio
async def test_build_plan_resolves_new_when_no_match(monkeypatch):
    _install_fake_db(monkeypatch)  # nothing exists anywhere
    entities = svc.parse_bundle_zip(_make_zip({"driver_d1": _raw_driver()}))
    plan = await svc.build_plan(entities)
    assert plan.can_commit
    assert plan.resolutions["d1"] == "new"
    assert len(plan.to_create) == 1


@pytest.mark.anyio
async def test_build_plan_resolves_existing_match_via_legacy_metadata(monkeypatch):
    _install_fake_db(
        monkeypatch,
        legacy_matches={
            "3065551234": [{"legacy_import_metadata": {"old_driver_id": "d1", "source": svc.IMPORT_SOURCE}}]
        },
    )
    entities = svc.parse_bundle_zip(_make_zip({"driver_d1": _raw_driver()}))
    plan = await svc.build_plan(entities)
    assert plan.can_commit  # existing_match is a warning, not an error
    assert plan.resolutions["d1"] == "existing_match"
    assert plan.to_create == []


@pytest.mark.anyio
async def test_build_plan_resolves_conflict_when_phone_belongs_to_other_account(monkeypatch):
    _install_fake_db(
        monkeypatch,
        users_by_phone={"3065551234": [{"id": "some-other-user", "legacy_import_metadata": {}}]},
    )
    entities = svc.parse_bundle_zip(_make_zip({"driver_d1": _raw_driver()}))
    plan = await svc.build_plan(entities)
    assert not plan.can_commit
    assert plan.resolutions["d1"] == "conflict"


# ---------- commit_plan ----------


@pytest.mark.anyio
async def test_commit_plan_raises_when_plan_has_unresolved_errors():
    plan = svc.ImportPlan(errors=[svc.ImportReportItem("d1", "phone", "missing")])
    with pytest.raises(ValueError):
        await svc.commit_plan(plan)


@pytest.mark.anyio
async def test_commit_plan_creates_rows_reencrypts_license_and_replays_documents(monkeypatch):
    inserted = {"users": [], "drivers": []}

    async def fake_insert_one(table, record):
        inserted[table].append(record)
        return record

    async def fake_vault_encrypt(value, hint=""):
        return f"REENCRYPTED[{value}]"

    replay_calls = {}

    async def fake_replay_documents(driver_id, documents, document_files):
        replay_calls["documents"] = (driver_id, documents, document_files)
        return len(documents)

    async def fake_replay_insurance_periods(driver_id, periods):
        replay_calls["insurance_periods"] = (driver_id, periods)
        return len(periods)

    monkeypatch.setattr(svc.db_supabase, "insert_one", fake_insert_one)
    monkeypatch.setattr(svc, "_vault_encrypt", fake_vault_encrypt)
    monkeypatch.setattr(svc.bundle_document_uploader, "replay_documents", fake_replay_documents)
    monkeypatch.setattr(svc.bundle_document_uploader, "replay_insurance_periods", fake_replay_insurance_periods)

    entities = svc.parse_bundle_zip(_make_zip({"driver_d1": _raw_driver()}))
    plan = svc.ImportPlan(entities=entities, resolutions={"d1": "new"})

    counts = await svc.commit_plan(plan)

    assert counts == {
        "created_users": 1,
        "created_drivers": 1,
        "updated_users": 0,
        "updated_drivers": 0,
        "documents_replayed": 1,
        "insurance_periods_replayed": 1,
    }
    assert len(inserted["users"]) == 1
    assert len(inserted["drivers"]) == 1
    # license_number must be the re-encrypted value, never the bundle's plaintext
    assert inserted["drivers"][0]["license_number"] == "REENCRYPTED[PLAINTEXT-123]"
    assert inserted["drivers"][0]["legacy_import_metadata"]["source"] == svc.IMPORT_SOURCE
    # the same freshly-generated driver_id must be used for both replay calls
    assert replay_calls["documents"][0] == inserted["drivers"][0]["id"]
    assert replay_calls["insurance_periods"][0] == inserted["drivers"][0]["id"]


@pytest.mark.anyio
async def test_build_plan_stores_existing_row_for_matched_entity(monkeypatch):
    _install_fake_db(
        monkeypatch,
        legacy_matches={
            "3065551234": [
                {
                    "id": "drv-existing",
                    "user_id": "user-existing",
                    "legacy_import_metadata": {"old_driver_id": "d1", "source": svc.IMPORT_SOURCE},
                }
            ]
        },
    )
    entities = svc.parse_bundle_zip(_make_zip({"driver_d1": _raw_driver()}))
    plan = await svc.build_plan(entities)

    assert plan.existing_rows["d1"]["id"] == "drv-existing"
    assert len(plan.to_update) == 1


@pytest.mark.anyio
async def test_commit_plan_skips_existing_match_when_update_existing_false(monkeypatch):
    updated = []
    monkeypatch.setattr(svc.db_supabase, "update_one", AsyncMock(side_effect=lambda *a: updated.append(a)))

    entities = svc.parse_bundle_zip(_make_zip({"driver_d1": _raw_driver()}))
    plan = svc.ImportPlan(
        entities=entities,
        resolutions={"d1": "existing_match"},
        existing_rows={"d1": {"id": "drv-existing", "user_id": "user-existing"}},
    )

    counts = await svc.commit_plan(plan, update_existing=False)

    assert counts["updated_users"] == 0
    assert counts["updated_drivers"] == 0
    assert updated == []


@pytest.mark.anyio
async def test_commit_plan_updates_existing_driver_and_replays_only_new_items(monkeypatch):
    updates = {"users": [], "drivers": []}

    async def fake_update_one(table, filters, values):
        updates[table].append((filters, values))
        return values

    async def fake_vault_encrypt(value, hint=""):
        return f"REENCRYPTED[{value}]"

    replay_calls = {}

    async def fake_replay_new_documents(driver_id, documents, document_files):
        replay_calls["documents"] = (driver_id, documents)
        return len(documents)

    async def fake_replay_new_insurance_periods(driver_id, periods):
        replay_calls["insurance_periods"] = (driver_id, periods)
        return len(periods)

    monkeypatch.setattr(svc.db_supabase, "update_one", fake_update_one)
    monkeypatch.setattr(svc, "_vault_encrypt", fake_vault_encrypt)
    monkeypatch.setattr(svc.bundle_document_uploader, "replay_new_documents", fake_replay_new_documents)
    monkeypatch.setattr(svc.bundle_document_uploader, "replay_new_insurance_periods", fake_replay_new_insurance_periods)

    entities = svc.parse_bundle_zip(_make_zip({"driver_d1": _raw_driver()}))
    plan = svc.ImportPlan(
        entities=entities,
        resolutions={"d1": "existing_match"},
        existing_rows={"d1": {"id": "drv-existing", "user_id": "user-existing"}},
    )

    counts = await svc.commit_plan(plan, update_existing=True)

    assert counts["updated_users"] == 1
    assert counts["updated_drivers"] == 1
    assert counts["created_users"] == 0
    assert counts["created_drivers"] == 0

    user_filters, user_values = updates["users"][0]
    assert user_filters == {"id": "user-existing"}
    assert "phone" not in user_values  # the match key must never be overwritten
    assert "id" not in user_values

    driver_filters, driver_values = updates["drivers"][0]
    assert driver_filters == {"id": "drv-existing"}
    # license_number must be re-encrypted against THIS environment's vault,
    # never written as the bundle's plaintext, on the update path too
    assert driver_values["license_number"] == "REENCRYPTED[PLAINTEXT-123]"
    assert "id" not in driver_values
    assert "user_id" not in driver_values
    assert "legacy_import_metadata" not in driver_values

    assert replay_calls["documents"][0] == "drv-existing"
    assert replay_calls["insurance_periods"][0] == "drv-existing"
