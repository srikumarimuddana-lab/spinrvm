"""Unit tests for bundle_document_uploader.py's dedup-aware update-on-reimport
helpers (replay_new_documents / replay_new_insurance_periods), with
db_supabase.get_rows/insert_one monkeypatched — no live Supabase.

replay_documents/replay_insurance_periods (the create-path replay used by
commit_plan's "new" branch) already have indirect coverage via
test_entity_import_service.py's commit_plan test (as mocks); this file
targets the update-path wrappers specifically, since their whole purpose is
the dedup check that doesn't otherwise get exercised.
"""

from unittest.mock import AsyncMock

import pytest

from backend.services.data_transfer import bundle_document_uploader as svc


def _install_fake_db(monkeypatch, existing_rows_by_table: dict):
    async def fake_get_rows(table, filters=None, **kwargs):
        return existing_rows_by_table.get(table, [])

    monkeypatch.setattr(svc.db_supabase, "get_rows", AsyncMock(side_effect=fake_get_rows))


@pytest.mark.anyio
async def test_replay_new_documents_skips_already_present_type_and_side(monkeypatch):
    _install_fake_db(
        monkeypatch,
        {"driver_documents": [{"document_type": "insurance", "side": "front"}]},
    )
    replay_calls = {}

    async def fake_replay_documents(driver_id, documents, document_files):
        replay_calls["documents"] = documents
        return len(documents)

    monkeypatch.setattr(svc, "replay_documents", fake_replay_documents)

    documents = [
        {"id": "d1", "document_type": "insurance", "side": "front"},  # already present -> skipped
        {"id": "d2", "document_type": "license", "side": "front"},  # new -> kept
    ]
    count = await svc.replay_new_documents("drv1", documents, {"d1": b"x", "d2": b"y"})

    assert count == 1
    assert [d["id"] for d in replay_calls["documents"]] == ["d2"]


@pytest.mark.anyio
async def test_replay_new_documents_all_new_when_no_existing_rows(monkeypatch):
    _install_fake_db(monkeypatch, {"driver_documents": []})

    async def fake_replay_documents(driver_id, documents, document_files):
        return len(documents)

    monkeypatch.setattr(svc, "replay_documents", fake_replay_documents)

    documents = [{"id": "d1", "document_type": "insurance", "side": "front"}]
    count = await svc.replay_new_documents("drv1", documents, {"d1": b"x"})

    assert count == 1


@pytest.mark.anyio
async def test_replay_new_insurance_periods_skips_already_present_period_and_started_at(monkeypatch):
    _install_fake_db(
        monkeypatch,
        {"driver_insurance_periods": [{"period": 1, "started_at": "2026-01-01T00:00:00Z"}]},
    )
    replay_calls = {}

    async def fake_replay_insurance_periods(driver_id, periods):
        replay_calls["periods"] = periods
        return len(periods)

    monkeypatch.setattr(svc, "replay_insurance_periods", fake_replay_insurance_periods)

    periods = [
        {"period": 1, "started_at": "2026-01-01T00:00:00Z"},  # already present -> skipped
        {"period": 2, "started_at": "2026-01-02T00:00:00Z"},  # new -> kept
    ]
    count = await svc.replay_new_insurance_periods("drv1", periods)

    assert count == 1
    assert [p["period"] for p in replay_calls["periods"]] == [2]


@pytest.mark.anyio
async def test_replay_documents_survives_real_file_type_validation(monkeypatch):
    """Regression: replay_documents previously called
    _validate_file_type(content, "application/octet-stream") — a MIME type
    that isn't in documents.py's own ALLOWED_MIME_TYPES allowlist, so the
    call always raised, was swallowed by the surrounding try/except, and
    every document in every bundle replay was silently skipped. Unlike the
    other tests in this file, this one does NOT mock replay_documents
    itself (previous coverage only ever exercised it as a mock, which is
    exactly how this bug went unnoticed) — it exercises the real function
    against a real JPEG magic-byte header so _validate_file_type's
    signature check has something genuine to check."""
    inserted = {}

    async def fake_insert_one(table, doc):
        inserted["table"] = table
        inserted["doc"] = doc
        return {"id": doc["id"]}

    monkeypatch.setattr(svc.db_supabase, "insert_one", AsyncMock(side_effect=fake_insert_one))
    monkeypatch.setattr(svc, "_upload_bytes", AsyncMock(return_value="https://example.com/signed"))

    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # real JPEG magic bytes

    replayed = await svc.replay_documents(
        "drv1",
        [{"id": "d1", "document_type": "license", "side": "front", "_storage_key": "orig/d1.jpg"}],
        {"d1": jpeg_bytes},
    )

    assert replayed == 1
    assert inserted["table"] == "driver_documents"
    assert inserted["doc"]["document_url"] == "https://example.com/signed"


@pytest.mark.anyio
async def test_replay_documents_derives_real_content_type_not_octet_stream(monkeypatch):
    """_upload_bytes (and _validate_file_type) must receive the document's
    real MIME type derived from its extension, never the hardcoded
    "application/octet-stream" that always failed validation."""
    monkeypatch.setattr(svc.db_supabase, "insert_one", AsyncMock(return_value={"id": "x"}))
    captured = {}

    async def fake_upload_bytes(content, ext, content_type):
        captured["content_type"] = content_type
        return "https://example.com/signed"

    monkeypatch.setattr(svc, "_upload_bytes", fake_upload_bytes)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    await svc.replay_documents(
        "drv1",
        [{"id": "d1", "document_type": "license", "side": "front", "_storage_key": "orig/d1.png"}],
        {"d1": png_bytes},
    )

    assert captured["content_type"] == "image/png"
