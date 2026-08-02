"""Coverage for services/data_transfer/bundle_document_uploader.py (A1c, Sub-tier B).

Re-uploads a bundle entity's raw document bytes to the target environment's
storage and re-attaches them to a freshly-created driver_id (part of the
data-transfer/import pipeline alongside entity_export_service.py and
bundle_zip_builder.py). Had no dedicated test file; only 38.75% coverage.

Dual-import note: this module does
`try: from ... import db_supabase / except ImportError: import db_supabase`
(and similarly for documents.ALLOWED_EXTENSIONS/_extract_signed_url/
_validate_file_type and supabase_client.supabase). Per this session's
established convention, every patch target below is the name AS BOUND on the
`backend.services.data_transfer.bundle_document_uploader` module object
(`bundle_document_uploader.db_supabase`, `bundle_document_uploader.supabase`,
`bundle_document_uploader._validate_file_type`, etc.), never a separately
imported reference to the source module.

Note: an earlier draft of this file flagged a "declared MIME type is
hardcoded to application/octet-stream, which is never in
documents.ALLOWED_MIME_TYPES, so every document is silently skipped"
finding. That was fixed (by a concurrent session, before this file was
committed): `replay_documents` now derives `content_type` from the file
extension via `_EXT_TO_MIME_TYPE` before calling `_validate_file_type`,
so a real image/PDF extension passes validation correctly. The test
below (`test_type_validation_passes_with_real_extension_mapping`) now
pins the CORRECT behavior against the real (unpatched)
`_validate_file_type`; all other success-path tests still patch
`_validate_file_type` to a no-op so the rest of `replay_documents`'s
branches remain independently exercisable.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


# ── _upload_bytes ───────────────────────────────────────────────────────


class TestUploadBytes:
    @pytest.mark.anyio
    async def test_success_uploads_and_returns_signed_url(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        bucket = MagicMock()
        bucket.upload.return_value = {"path": "some/key"}
        bucket.create_signed_url.return_value = {"signedURL": "https://storage.example/signed"}

        fake_supabase = MagicMock()
        fake_supabase.storage.from_.return_value = bucket
        monkeypatch.setattr(bundle_document_uploader, "supabase", fake_supabase)

        url = await bundle_document_uploader._upload_bytes(b"hello", ".png", "image/png")

        assert url == "https://storage.example/signed"
        fake_supabase.storage.from_.assert_any_call(bundle_document_uploader.DOCUMENT_STORAGE_BUCKET)
        upload_kwargs = bucket.upload.call_args.kwargs
        assert upload_kwargs["file"] == b"hello"
        assert upload_kwargs["path"].endswith(".png")
        assert upload_kwargs["file_options"] == {"content-type": "image/png"}
        signed_args = bucket.create_signed_url.call_args.args
        assert signed_args[0] == upload_kwargs["path"]
        assert signed_args[1] == 3600

    @pytest.mark.anyio
    async def test_upload_failure_propagates(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        bucket = MagicMock()
        bucket.upload.side_effect = RuntimeError("storage down")
        fake_supabase = MagicMock()
        fake_supabase.storage.from_.return_value = bucket
        monkeypatch.setattr(bundle_document_uploader, "supabase", fake_supabase)

        with pytest.raises(RuntimeError):
            await bundle_document_uploader._upload_bytes(b"hello", ".png", "image/png")

    @pytest.mark.anyio
    async def test_extract_signed_url_failure_propagates(self, monkeypatch):
        """A malformed signed_url response (no recognizable key) makes
        _extract_signed_url raise RuntimeError; _upload_bytes has no
        try/except of its own so it propagates to the caller."""
        from backend.services.data_transfer import bundle_document_uploader

        bucket = MagicMock()
        bucket.upload.return_value = {"path": "some/key"}
        bucket.create_signed_url.return_value = {"unexpected": "shape"}
        fake_supabase = MagicMock()
        fake_supabase.storage.from_.return_value = bucket
        monkeypatch.setattr(bundle_document_uploader, "supabase", fake_supabase)

        with pytest.raises(RuntimeError):
            await bundle_document_uploader._upload_bytes(b"hello", ".png", "image/png")


# ── replay_documents ─────────────────────────────────────────────────────


class TestReplayDocuments:
    @pytest.mark.anyio
    async def test_returns_zero_when_storage_not_configured(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", None)

        result = await bundle_document_uploader.replay_documents(
            "driver-1", [{"id": "doc-1"}], {"doc-1": b"content"}
        )
        assert result == 0

    @pytest.mark.anyio
    async def test_type_validation_passes_with_real_extension_mapping(self, monkeypatch):
        """With the real (unpatched) `_validate_file_type`, a document whose
        `_storage_key` extension maps to an allowed MIME type (`.png` →
        `image/png`) is correctly accepted, not skipped — see the module
        docstring's note above."""
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        doc = {"id": "doc-1", "_storage_key": "orig/key.png"}
        result = await bundle_document_uploader.replay_documents("driver-1", [doc], {"doc-1": b"\x89PNGdata"})
        assert result == 1

    @pytest.mark.anyio
    async def test_skips_document_with_no_matching_content(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        doc = {"id": "doc-1", "_storage_key": "orig/key.png"}
        result = await bundle_document_uploader.replay_documents("driver-1", [doc], {"other-doc": b"content"})
        assert result == 0

    @pytest.mark.anyio
    async def test_finds_content_via_filename_prefix_fallback(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        monkeypatch.setattr(bundle_document_uploader, "_validate_file_type", MagicMock(return_value=None))
        monkeypatch.setattr(
            bundle_document_uploader, "_upload_bytes", AsyncMock(return_value="https://signed/url")
        )
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", AsyncMock())

        doc = {"id": "doc-1", "_storage_key": "orig/key.png"}
        # Keyed by original filename ("license_doc-1.png") rather than by id —
        # doc_id "doc-1" is a substring of the key, so the fallback matches it.
        result = await bundle_document_uploader.replay_documents(
            "driver-1", [doc], {"license_doc-1.png": b"content"}
        )
        assert result == 1

    @pytest.mark.anyio
    async def test_skips_document_with_disallowed_extension(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        monkeypatch.setattr(bundle_document_uploader, "_validate_file_type", MagicMock(return_value=None))
        doc = {"id": "doc-1", "_storage_key": "orig/key.exe"}
        result = await bundle_document_uploader.replay_documents("driver-1", [doc], {"doc-1": b"content"})
        assert result == 0

    @pytest.mark.anyio
    async def test_missing_storage_key_defaults_to_bin_and_is_rejected(self, monkeypatch):
        """No _storage_key -> ext defaults to '.bin', which is not in
        ALLOWED_EXTENSIONS, so the document is skipped by the extension
        guard before validation is even attempted."""
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        validate_mock = MagicMock(return_value=None)
        monkeypatch.setattr(bundle_document_uploader, "_validate_file_type", validate_mock)
        doc = {"id": "doc-1"}
        result = await bundle_document_uploader.replay_documents("driver-1", [doc], {"doc-1": b"content"})
        assert result == 0
        validate_mock.assert_not_called()

    @pytest.mark.anyio
    async def test_skips_document_that_fails_type_validation(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        monkeypatch.setattr(
            bundle_document_uploader, "_validate_file_type", MagicMock(side_effect=ValueError("bad bytes"))
        )
        upload_mock = AsyncMock(return_value="https://signed/url")
        monkeypatch.setattr(bundle_document_uploader, "_upload_bytes", upload_mock)

        doc = {"id": "doc-1", "_storage_key": "orig/key.png"}
        result = await bundle_document_uploader.replay_documents("driver-1", [doc], {"doc-1": b"content"})
        assert result == 0
        upload_mock.assert_not_called()

    @pytest.mark.anyio
    async def test_skips_document_when_upload_raises(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        monkeypatch.setattr(bundle_document_uploader, "_validate_file_type", MagicMock(return_value=None))
        monkeypatch.setattr(
            bundle_document_uploader, "_upload_bytes", AsyncMock(side_effect=RuntimeError("storage down"))
        )
        insert_mock = AsyncMock()
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", insert_mock)

        doc = {"id": "doc-1", "_storage_key": "orig/key.png"}
        result = await bundle_document_uploader.replay_documents("driver-1", [doc], {"doc-1": b"content"})
        assert result == 0
        insert_mock.assert_not_called()

    @pytest.mark.anyio
    async def test_skips_document_when_insert_raises_after_successful_upload(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        monkeypatch.setattr(bundle_document_uploader, "_validate_file_type", MagicMock(return_value=None))
        monkeypatch.setattr(
            bundle_document_uploader, "_upload_bytes", AsyncMock(return_value="https://signed/url")
        )
        monkeypatch.setattr(
            bundle_document_uploader.db_supabase, "insert_one", AsyncMock(side_effect=ConnectionError("db down"))
        )

        doc = {"id": "doc-1", "_storage_key": "orig/key.png"}
        result = await bundle_document_uploader.replay_documents("driver-1", [doc], {"doc-1": b"content"})
        assert result == 0

    @pytest.mark.anyio
    async def test_success_path_builds_expected_record_and_counts(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        monkeypatch.setattr(bundle_document_uploader, "_validate_file_type", MagicMock(return_value=None))
        monkeypatch.setattr(
            bundle_document_uploader, "_upload_bytes", AsyncMock(return_value="https://signed/url")
        )
        insert_mock = AsyncMock()
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", insert_mock)

        doc = {
            "id": "doc-1",
            "_storage_key": "orig/key.PNG",  # uppercase extension must be lowercased
            "requirement_id": "req-1",
            "requirement_key": "drivers_license",
            "document_type": "license",
            "side": "front",
            "status": "approved",
            "expiry_date": "2027-01-01",
        }
        result = await bundle_document_uploader.replay_documents("driver-1", [doc], {"doc-1": b"content"})

        assert result == 1
        insert_mock.assert_awaited_once()
        table_name, record = insert_mock.call_args.args
        assert table_name == "driver_documents"
        assert record["driver_id"] == "driver-1"
        assert record["requirement_id"] == "req-1"
        assert record["requirement_key"] == "drivers_license"
        assert record["document_type"] == "license"
        assert record["document_url"] == "https://signed/url"
        assert record["side"] == "front"
        assert record["status"] == "approved"
        assert record["expiry_date"] == "2027-01-01"
        assert "uploaded_at" in record and "updated_at" in record
        assert record["id"] != "doc-1"  # a fresh id is minted, not reused

    @pytest.mark.anyio
    async def test_status_defaults_to_pending_when_absent(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        monkeypatch.setattr(bundle_document_uploader, "_validate_file_type", MagicMock(return_value=None))
        monkeypatch.setattr(
            bundle_document_uploader, "_upload_bytes", AsyncMock(return_value="https://signed/url")
        )
        insert_mock = AsyncMock()
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", insert_mock)

        doc = {"id": "doc-1", "_storage_key": "orig/key.png"}
        await bundle_document_uploader.replay_documents("driver-1", [doc], {"doc-1": b"content"})
        _, record = insert_mock.call_args.args
        assert record["status"] == "pending"

    @pytest.mark.anyio
    async def test_multiple_documents_partial_success(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        monkeypatch.setattr(bundle_document_uploader, "_validate_file_type", MagicMock(return_value=None))
        monkeypatch.setattr(
            bundle_document_uploader, "_upload_bytes", AsyncMock(return_value="https://signed/url")
        )
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", AsyncMock())

        docs = [
            {"id": "doc-1", "_storage_key": "a.png"},  # succeeds
            {"id": "doc-2", "_storage_key": "b.exe"},  # bad extension
            {"id": "doc-3"},  # no matching content
        ]
        files = {"doc-1": b"content", "unrelated-file": b"unused"}
        result = await bundle_document_uploader.replay_documents("driver-1", docs, files)
        assert result == 1

    @pytest.mark.anyio
    async def test_empty_documents_list_returns_zero(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader, "supabase", MagicMock())
        result = await bundle_document_uploader.replay_documents("driver-1", [], {})
        assert result == 0


# ── replay_insurance_periods ──────────────────────────────────────────────


class TestReplayInsurancePeriods:
    @pytest.mark.anyio
    async def test_success_all_periods_replayed(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        insert_mock = AsyncMock()
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", insert_mock)

        periods = [
            {"period": 1, "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T01:00:00Z", "ride_id": "r1"},
            {"period": 2, "started_at": "2026-01-01T01:00:00Z", "ended_at": None, "ride_id": "r1"},
        ]
        result = await bundle_document_uploader.replay_insurance_periods("driver-1", periods)
        assert result == 2
        assert insert_mock.await_count == 2

    @pytest.mark.anyio
    async def test_ride_id_is_dropped_from_replayed_record(self, monkeypatch):
        """ride_id refers to a ride that does not exist in the target env,
        per the module docstring — must always be None in the inserted row."""
        from backend.services.data_transfer import bundle_document_uploader

        insert_mock = AsyncMock()
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", insert_mock)

        period = {"period": 3, "started_at": "2026-01-01T02:00:00Z", "ended_at": None, "ride_id": "source-ride-1"}
        await bundle_document_uploader.replay_insurance_periods("driver-1", [period])
        table_name, record = insert_mock.call_args.args
        assert table_name == "driver_insurance_periods"
        assert record["ride_id"] is None
        assert record["driver_id"] == "driver-1"
        assert record["period"] == 3

    @pytest.mark.anyio
    async def test_insert_failure_is_skipped_and_others_still_replayed(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        insert_mock = AsyncMock(side_effect=[ConnectionError("db down"), None])
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", insert_mock)

        periods = [
            {"period": 1, "started_at": "t1", "ended_at": None, "ride_id": None},
            {"period": 2, "started_at": "t2", "ended_at": None, "ride_id": None},
        ]
        result = await bundle_document_uploader.replay_insurance_periods("driver-1", periods)
        assert result == 1

    @pytest.mark.anyio
    async def test_empty_periods_list_returns_zero(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        insert_mock = AsyncMock()
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "insert_one", insert_mock)
        result = await bundle_document_uploader.replay_insurance_periods("driver-1", [])
        assert result == 0
        insert_mock.assert_not_awaited()


# ── replay_new_documents ──────────────────────────────────────────────────


class TestReplayNewDocuments:
    @pytest.mark.anyio
    async def test_filters_out_documents_matching_existing_type_and_side(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        existing = [{"document_type": "license", "side": "front"}]
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "get_rows", AsyncMock(return_value=existing))
        replay_mock = AsyncMock(return_value=0)
        monkeypatch.setattr(bundle_document_uploader, "replay_documents", replay_mock)

        docs = [
            {"id": "doc-1", "document_type": "license", "side": "front"},  # filtered out
            {"id": "doc-2", "document_type": "license", "side": "back"},  # kept
        ]
        await bundle_document_uploader.replay_new_documents("driver-1", docs, {})

        replay_mock.assert_awaited_once()
        called_driver_id, called_docs, called_files = replay_mock.call_args.args
        assert called_driver_id == "driver-1"
        assert len(called_docs) == 1
        assert called_docs[0]["id"] == "doc-2"

    @pytest.mark.anyio
    async def test_no_existing_documents_passes_all_through(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader.db_supabase, "get_rows", AsyncMock(return_value=[]))
        replay_mock = AsyncMock(return_value=2)
        monkeypatch.setattr(bundle_document_uploader, "replay_documents", replay_mock)

        docs = [
            {"id": "doc-1", "document_type": "license", "side": "front"},
            {"id": "doc-2", "document_type": "insurance", "side": None},
        ]
        result = await bundle_document_uploader.replay_new_documents("driver-1", docs, {})
        assert result == 2
        _, called_docs, _ = replay_mock.call_args.args
        assert len(called_docs) == 2

    @pytest.mark.anyio
    async def test_get_rows_none_treated_as_no_existing_rows(self, monkeypatch):
        """get_rows returning None (rather than []) must not raise when
        building the existing_keys set comprehension."""
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader.db_supabase, "get_rows", AsyncMock(return_value=None))
        replay_mock = AsyncMock(return_value=1)
        monkeypatch.setattr(bundle_document_uploader, "replay_documents", replay_mock)

        docs = [{"id": "doc-1", "document_type": "license", "side": "front"}]
        result = await bundle_document_uploader.replay_new_documents("driver-1", docs, {})
        assert result == 1


# ── replay_new_insurance_periods ──────────────────────────────────────────


class TestReplayNewInsurancePeriods:
    @pytest.mark.anyio
    async def test_filters_out_periods_matching_existing_period_and_started_at(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        existing = [{"period": 1, "started_at": "2026-01-01T00:00:00Z"}]
        monkeypatch.setattr(bundle_document_uploader.db_supabase, "get_rows", AsyncMock(return_value=existing))
        replay_mock = AsyncMock(return_value=0)
        monkeypatch.setattr(bundle_document_uploader, "replay_insurance_periods", replay_mock)

        periods = [
            {"period": 1, "started_at": "2026-01-01T00:00:00Z"},  # filtered out
            {"period": 2, "started_at": "2026-01-01T01:00:00Z"},  # kept
        ]
        await bundle_document_uploader.replay_new_insurance_periods("driver-1", periods)

        replay_mock.assert_awaited_once()
        called_driver_id, called_periods = replay_mock.call_args.args
        assert called_driver_id == "driver-1"
        assert len(called_periods) == 1
        assert called_periods[0]["period"] == 2

    @pytest.mark.anyio
    async def test_no_existing_periods_passes_all_through(self, monkeypatch):
        from backend.services.data_transfer import bundle_document_uploader

        monkeypatch.setattr(bundle_document_uploader.db_supabase, "get_rows", AsyncMock(return_value=[]))
        replay_mock = AsyncMock(return_value=3)
        monkeypatch.setattr(bundle_document_uploader, "replay_insurance_periods", replay_mock)

        periods = [
            {"period": 1, "started_at": "t1"},
            {"period": 2, "started_at": "t2"},
            {"period": 3, "started_at": "t3"},
        ]
        result = await bundle_document_uploader.replay_new_insurance_periods("driver-1", periods)
        assert result == 3
        _, called_periods = replay_mock.call_args.args
        assert len(called_periods) == 3
