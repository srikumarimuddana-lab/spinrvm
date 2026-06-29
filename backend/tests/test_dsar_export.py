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


def _capture_zip():
    """Return (recorder, getter): recorder is a fake send_email that captures
    the ZIP attachment; getter unzips it into a {name: text} dict."""
    box: dict = {}

    async def fake_send_email(**kwargs):
        attachments = kwargs.get("attachments") or []
        assert attachments, "export must be sent as an attachment"
        box["zip_bytes"] = attachments[0]["content"]
        box["filename"] = attachments[0]["filename"]

    def get_files() -> dict:
        with zipfile.ZipFile(io.BytesIO(box["zip_bytes"])) as zf:
            return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}

    return fake_send_email, get_files


async def _run_export(table_map: dict):
    """Run the export with mocked DB + email, return the unzipped files dict."""
    from backend.routes import drivers as drivers_mod

    fake_send_email, get_files = _capture_zip()

    async def _get_rows(table, filters=None, **kwargs):
        return table_map.get(table, [])

    with (
        patch("backend.routes.drivers.db_supabase.get_rows", side_effect=_get_rows),
        patch("backend.routes.drivers.send_email", AsyncMock(side_effect=fake_send_email)),
    ):
        await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

    return get_files()


class TestDsarExportBundle:
    """_build_and_email_data_export must emit a complete CSV+JSON ZIP bundle."""

    async def test_zip_contains_all_expected_files(self):
        files = await _run_export(
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
        files = await _run_export(
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
        files = await _run_export(
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
        files = await _run_export(
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
        files = await _run_export(
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
