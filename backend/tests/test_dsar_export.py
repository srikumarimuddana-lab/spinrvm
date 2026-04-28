"""
PIPEDA DSAR completeness test (B-P1-9).

Asserts that the driver data-export background task returns a payload that
contains every required top-level key (account, driver_profile, rides,
payouts, documents, notification_preferences) populated for a seeded driver.

This is a unit test: all DB helpers are mocked so no live Supabase is needed.
"""

from __future__ import annotations

import sys
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


class TestDsarExportCompleteness:
    """_build_and_email_data_export must include all DSAR_FIELDS."""

    async def test_all_required_keys_present(self):
        from backend.routes import drivers as drivers_mod

        driver = {"id": "d1", "user_id": "u1", "name": "Test Driver"}
        user = {"id": "u1", "phone": "+13061234567", "role": "driver"}
        ride = {"id": "r1", "driver_id": "d1", "status": "completed"}
        payout = {"id": "p1", "driver_id": "d1", "amount": 50}
        doc = {"id": "doc1", "driver_id": "d1", "document_url": "s3://…"}
        notif_pref = {"user_id": "u1", "push_enabled": True}

        captured: dict = {}

        async def fake_send_email(to, subject, body):
            import json

            # The body is "…header…\n\n---\n\n" + JSON
            json_part = body.split("---\n\n", 1)[-1]
            captured.update(json.loads(json_part))

        def make_get_rows(table_data):
            async def _get_rows(table, filters=None, **kwargs):
                return table_data.get(table, [])

            return _get_rows

        table_map = {
            "drivers": [driver],
            "users": [user],
            "rides": [ride],
            "driver_payouts": [payout],
            "driver_documents": [doc],
            "notification_preferences": [notif_pref],
        }

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", side_effect=make_get_rows(table_map)),
            patch("backend.routes.drivers.send_email", AsyncMock(side_effect=fake_send_email)),
        ):
            await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

        missing = DSAR_FIELDS - captured.keys()
        assert not missing, f"DSAR export is missing required keys: {missing}"

    async def test_account_excludes_password_hash(self):
        from backend.routes import drivers as drivers_mod

        user = {"id": "u1", "phone": "+13061234567", "password_hash": "SECRET"}
        captured: dict = {}

        async def fake_send_email(to, subject, body):
            import json

            json_part = body.split("---\n\n", 1)[-1]
            captured.update(json.loads(json_part))

        table_map = {
            "drivers": [{"id": "d1", "user_id": "u1"}],
            "users": [user],
            "rides": [],
            "driver_payouts": [],
            "driver_documents": [],
            "notification_preferences": [],
        }

        with (
            patch(
                "backend.routes.drivers.db_supabase.get_rows", side_effect=lambda t, f=None, **k: table_map.get(t, [])
            ),
            patch("backend.routes.drivers.send_email", AsyncMock(side_effect=fake_send_email)),
        ):
            await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

        assert "password_hash" not in captured.get("account", {}), "password_hash must be stripped from DSAR export"

    async def test_documents_exclude_document_url(self):
        from backend.routes import drivers as drivers_mod

        doc = {"id": "doc1", "driver_id": "d1", "document_url": "s3://private"}
        captured: dict = {}

        async def fake_send_email(to, subject, body):
            import json

            json_part = body.split("---\n\n", 1)[-1]
            captured.update(json.loads(json_part))

        table_map = {
            "drivers": [{"id": "d1", "user_id": "u1"}],
            "users": [{"id": "u1"}],
            "rides": [],
            "driver_payouts": [],
            "driver_documents": [doc],
            "notification_preferences": [],
        }

        with (
            patch(
                "backend.routes.drivers.db_supabase.get_rows", side_effect=lambda t, f=None, **k: table_map.get(t, [])
            ),
            patch("backend.routes.drivers.send_email", AsyncMock(side_effect=fake_send_email)),
        ):
            await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

        for exported_doc in captured.get("documents", []):
            assert "document_url" not in exported_doc, "document_url must be stripped from DSAR export"
