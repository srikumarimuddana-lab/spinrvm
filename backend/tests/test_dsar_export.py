"""
PIPEDA DSAR completeness test (B-P1-9).

Asserts that the driver data-export background task produces a Lyft-style ZIP
bundle: per-category CSV files, a README, and a complete raw_data.json that
contains every required top-level key (account, driver_profile, rides, payouts,
documents, notification_preferences) for a seeded driver, with sensitive fields
(password_hash, document_url) stripped from every representation.

This is a unit test: all DB helpers are mocked so no live Supabase is needed.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

# Stub heavy deps before any backend import.
_STUBS = [
    "supabase",
    "stripe",
    "gotrue",
    "postgrest",
    "realtime",
    "firebase_admin",
    "firebase_admin.auth",
    "firebase_admin.credentials",
    "firebase_admin.messaging",
    "twilio",
    "twilio.rest",
    "slowapi",
    "slowapi.errors",
    "slowapi.util",
    "redis",
    "redis.asyncio",
    "jwt",
]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()


# The canonical set of top-level keys the export must contain.
DSAR_FIELDS = frozenset({"account", "driver_profile", "rides", "payouts", "documents", "notification_preferences"})

# Files the Lyft-style bundle must always include.
EXPECTED_FILES = frozenset(
    {
        "README.txt",
        "account.csv",
        "driver_profile.csv",
        "rides.csv",
        "payouts.csv",
        "documents.csv",
        "notification_preferences.csv",
        "raw_data.json",
    }
)


FAKE_SIGNED_URL = "https://signed.example.test/storage/v1/object/sign/data-exports/abc.zip?token=xyz"


def _capture():
    """Capture the export ZIP bytes (handed to the upload helper) and the
    send_email kwargs. Returns (fake_upload, fake_send_email, get_files, box)."""
    box: dict = {"email": {}}

    async def fake_upload(user_id, zip_bytes, expires_in_seconds):
        box["zip_bytes"] = zip_bytes
        box["expires_in"] = expires_in_seconds
        box["upload_user_id"] = user_id
        return FAKE_SIGNED_URL

    async def fake_send_email(**kwargs):
        box["email"] = kwargs

    def get_files() -> dict:
        with zipfile.ZipFile(io.BytesIO(box["zip_bytes"])) as zf:
            return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}

    return fake_upload, fake_send_email, get_files, box


async def _run_export(table_map: dict):
    """Run the export with mocked DB + storage + email. Returns (files, box)."""
    from backend.routes import drivers as drivers_mod

    fake_upload, fake_send_email, get_files, box = _capture()

    async def _get_rows(table, filters=None, **kwargs):
        return table_map.get(table, [])

    with (
        patch("backend.routes.drivers.db_supabase.get_rows", side_effect=_get_rows),
        patch("backend.routes.drivers._upload_export_zip", side_effect=fake_upload),
        patch("backend.routes.drivers.send_email", AsyncMock(side_effect=fake_send_email)),
    ):
        await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

    return get_files(), box


class TestDsarExportBundle:
    """_build_and_email_data_export must emit a complete CSV+JSON ZIP bundle."""

    async def test_zip_contains_all_expected_files(self):
        files, box = await _run_export(
            {
                "drivers": [{"id": "d1", "user_id": "u1", "name": "Test Driver"}],
                "users": [{"id": "u1", "phone": "+13061234567", "role": "driver"}],
                "rides": [{"id": "r1", "driver_id": "d1", "status": "completed"}],
                "driver_payouts": [{"id": "p1", "driver_id": "d1", "amount": 50}],
                "driver_documents": [{"id": "doc1", "driver_id": "d1", "document_url": "s3://x"}],
                "notification_preferences": [{"user_id": "u1", "push_enabled": True}],
            }
        )
        missing = EXPECTED_FILES - files.keys()
        assert not missing, f"export ZIP is missing files: {missing}"

    async def test_raw_json_has_all_required_keys(self):
        files, box = await _run_export(
            {
                "drivers": [{"id": "d1", "user_id": "u1", "name": "Test Driver"}],
                "users": [{"id": "u1", "phone": "+13061234567", "role": "driver"}],
                "rides": [{"id": "r1", "driver_id": "d1", "status": "completed"}],
                "driver_payouts": [{"id": "p1", "driver_id": "d1", "amount": 50}],
                "driver_documents": [{"id": "doc1", "driver_id": "d1", "document_url": "s3://x"}],
                "notification_preferences": [{"user_id": "u1", "push_enabled": True}],
            }
        )
        payload = json.loads(files["raw_data.json"])
        missing = DSAR_FIELDS - payload.keys()
        assert not missing, f"raw_data.json is missing required keys: {missing}"

    async def test_rides_csv_has_header_and_row(self):
        files, box = await _run_export(
            {
                "drivers": [{"id": "d1", "user_id": "u1"}],
                "users": [{"id": "u1"}],
                "rides": [{"id": "r1", "driver_id": "d1", "status": "completed"}],
                "driver_payouts": [],
                "driver_documents": [],
                "notification_preferences": [],
            }
        )
        rides_csv = files["rides.csv"]
        assert "id" in rides_csv and "status" in rides_csv, "rides.csv must have a header row"
        assert "r1" in rides_csv and "completed" in rides_csv, "rides.csv must contain the ride row"

    async def test_password_hash_stripped_everywhere(self):
        files, box = await _run_export(
            {
                "drivers": [{"id": "d1", "user_id": "u1"}],
                "users": [{"id": "u1", "phone": "+13061234567", "password_hash": "SECRET"}],
                "rides": [],
                "driver_payouts": [],
                "driver_documents": [],
                "notification_preferences": [],
            }
        )
        assert "SECRET" not in files["account.csv"], "password_hash must be stripped from account.csv"
        payload = json.loads(files["raw_data.json"])
        assert "password_hash" not in payload.get("account", {}), "password_hash must be stripped from raw_data.json"

    async def test_document_url_stripped_everywhere(self):
        files, box = await _run_export(
            {
                "drivers": [{"id": "d1", "user_id": "u1"}],
                "users": [{"id": "u1"}],
                "rides": [],
                "driver_payouts": [],
                "driver_documents": [{"id": "doc1", "driver_id": "d1", "document_url": "s3://private"}],
                "notification_preferences": [],
            }
        )
        assert "s3://private" not in files["documents.csv"], "document_url must be stripped from documents.csv"
        payload = json.loads(files["raw_data.json"])
        for exported_doc in payload.get("documents", []):
            assert "document_url" not in exported_doc, "document_url must be stripped from raw_data.json"

    async def test_email_delivers_signed_link_not_attachment(self):
        _files, box = await _run_export(
            {
                "drivers": [{"id": "d1", "user_id": "u1"}],
                "users": [{"id": "u1"}],
                "rides": [],
                "driver_payouts": [],
                "driver_documents": [],
                "notification_preferences": [],
            }
        )
        email = box["email"]
        # Link is the primary delivery — no ZIP attached to the email itself.
        assert not email.get("attachments"), "primary delivery must be a link, not an attachment"
        assert FAKE_SIGNED_URL in email["body"], "plain-text email must contain the download link"
        assert FAKE_SIGNED_URL in email["html"], "HTML email must contain the download link"
        # DSAR audit metadata flows into email_send_log.
        assert email.get("email_type") == "dsar"
        assert email.get("recipient_user_id") == "u1"

    async def test_signed_link_has_seven_day_expiry(self):
        _files, box = await _run_export(
            {
                "drivers": [{"id": "d1", "user_id": "u1"}],
                "users": [{"id": "u1"}],
                "rides": [],
                "driver_payouts": [],
                "driver_documents": [],
                "notification_preferences": [],
            }
        )
        assert box["expires_in"] == 7 * 24 * 3600, "download link must expire after 7 days"
        # The email tells the user when it expires.
        assert "expires on" in box["email"]["body"].lower() or "expires" in box["email"]["body"].lower()

    async def test_falls_back_to_attachment_when_storage_fails(self):
        """If the signed-link upload fails, the ZIP is attached instead so the
        PIPEDA access request is still fulfilled."""
        from backend.routes import drivers as drivers_mod

        captured: dict = {}

        async def _get_rows(table, filters=None, **kwargs):
            return {"drivers": [{"id": "d1", "user_id": "u1"}], "users": [{"id": "u1"}]}.get(table, [])

        async def failing_upload(*args, **kwargs):
            raise RuntimeError("storage down")

        async def fake_send_email(**kwargs):
            captured.update(kwargs)

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", side_effect=_get_rows),
            patch("backend.routes.drivers._upload_export_zip", side_effect=failing_upload),
            patch("backend.routes.drivers.send_email", AsyncMock(side_effect=fake_send_email)),
        ):
            await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

        attachments = captured.get("attachments") or []
        assert attachments, "fallback must attach the export ZIP"
        with zipfile.ZipFile(io.BytesIO(attachments[0]["content"])) as zf:
            assert "raw_data.json" in zf.namelist()
