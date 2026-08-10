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


# The canonical set of top-level keys the export must contain. Includes
# rides_as_rider + saved_addresses (N1, ACTION_ITEMS.md) — every account can
# have ridden as a passenger, so these are queried unconditionally, not just
# for accounts with a `drivers` row.
DSAR_FIELDS = frozenset(
    {
        "account",
        "driver_profile",
        "rides",
        "rides_as_rider",
        "payouts",
        "documents",
        "saved_addresses",
        "notification_preferences",
    }
)

# Files the Lyft-style bundle must always include.
EXPECTED_FILES = frozenset(
    {
        "README.txt",
        "account.csv",
        "driver_profile.csv",
        "rides.csv",
        "rides_as_rider.csv",
        "payouts.csv",
        "documents.csv",
        "saved_addresses.csv",
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
        patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows),
        patch("backend.routes.drivers.tax_exports._upload_export_zip", side_effect=fake_upload),
        patch("backend.routes.drivers._deps.send_email", AsyncMock(side_effect=fake_send_email)),
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

    async def test_rides_strip_rider_third_party_pii_but_keep_addresses(self):
        files, box = await _run_export(
            {
                "drivers": [{"id": "d1", "user_id": "u1"}],
                "users": [{"id": "u1"}],
                "rides": [
                    {
                        "id": "r1",
                        "driver_id": "d1",
                        "status": "completed",
                        "rider_id": "RIDER_X",
                        "pickup_lat": 52.13,
                        "pickup_lng": -106.66,
                        "dropoff_lat": 52.15,
                        "dropoff_lng": -106.65,
                        "route_polyline": "ENCODED_ROUTE",
                        "pickup_address": "123 Main St",
                        "dropoff_address": "456 Elm Ave",
                        "total_fare": 15.0,
                    }
                ],
                "driver_payouts": [],
                "driver_documents": [],
                "notification_preferences": [],
            }
        )
        ride = json.loads(files["raw_data.json"])["rides"][0]
        for field in ("rider_id", "pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng", "route_polyline"):
            assert field not in ride, f"{field} (rider third-party PII) must be stripped from rides"
        for secret in ("RIDER_X", "ENCODED_ROUTE", "52.13", "-106.66"):
            assert secret not in files["rides.csv"], f"{secret} must not appear in rides.csv"
        # The driver's own trip record (addresses, fare, status) is retained.
        assert ride.get("pickup_address") == "123 Main St"
        assert ride.get("dropoff_address") == "456 Elm Ave"
        assert "123 Main St" in files["rides.csv"]

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

    async def test_credentials_and_internal_fields_stripped(self):
        files, box = await _run_export(
            {
                "drivers": [
                    {
                        "id": "d1",
                        "user_id": "u1",
                        "license_number": "SK-LIC-123",
                        "stripe_account_id": "acct_SECRET",
                        "bank_account": "BANK_SECRET",
                        "lat": 52.13,
                        "lng": -106.66,
                        "location_geog": "0101000020E6100000GEOG",
                        "fcm_token": "DRIVER_FCM",
                    }
                ],
                "users": [
                    {
                        "id": "u1",
                        "phone": "+13061234567",
                        "fcm_token": "DEVICE_TOKEN_A",
                        "fcm_token_driver": "DEVICE_TOKEN_B",
                        "token_version": 7,
                        "current_session_id": "sess_SECRET",
                        "sessions_invalid_before": "2026-01-01T00:00:00Z",
                        "stripe_customer_id": "cus_SECRET",
                    }
                ],
                "rides": [],
                "driver_payouts": [],
                "driver_documents": [],
                "notification_preferences": [],
            }
        )
        payload = json.loads(files["raw_data.json"])
        account = payload.get("account", {})
        for field in (
            "fcm_token",
            "fcm_token_driver",
            "token_version",
            "current_session_id",
            "sessions_invalid_before",
            "stripe_customer_id",
        ):
            assert field not in account, f"{field} must be redacted from the account export"

        profile = payload.get("driver_profile", {})
        for field in ("stripe_account_id", "bank_account", "lat", "lng", "location_geog", "fcm_token"):
            assert field not in profile, f"{field} must be redacted from the driver profile export"

        # No secret values leak into the CSVs either.
        for secret in ("DEVICE_TOKEN_A", "sess_SECRET", "cus_SECRET"):
            assert secret not in files["account.csv"]
        for secret in ("acct_SECRET", "BANK_SECRET", "DRIVER_FCM"):
            assert secret not in files["driver_profile.csv"]

        # The subject's own data is still present.
        assert "+13061234567" in files["account.csv"]
        assert "SK-LIC-123" in files["driver_profile.csv"], "driver's own license # is legitimate DSAR data"

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
            patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows),
            patch("backend.routes.drivers.tax_exports._upload_export_zip", side_effect=failing_upload),
            patch("backend.routes.drivers._deps.send_email", AsyncMock(side_effect=fake_send_email)),
        ):
            await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

        attachments = captured.get("attachments") or []
        assert attachments, "fallback must attach the export ZIP"
        with zipfile.ZipFile(io.BytesIO(attachments[0]["content"])) as zf:
            assert "raw_data.json" in zf.namelist()


class TestRiderShapedExport:
    """N1 (ACTION_ITEMS.md): a rider-only account's export must actually
    include their ride history, not just account + notification_preferences.
    Uses a filter-aware fake_get_rows (unlike _run_export's table_map, which
    can't distinguish the driver_id-filtered vs rider_id-filtered `rides`
    queries) to prove the two are queried and redacted independently."""

    async def _run_rider_export(self, rides_as_rider: list, saved_addresses: list):
        from backend.routes import drivers as drivers_mod

        fake_upload, fake_send_email, get_files, box = _capture()

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return []  # rider-only account: no drivers row
            if table == "users":
                return [{"id": "u1", "email": "rider@example.com"}]
            if table == "rides":
                if filters and filters.get("rider_id"):
                    return rides_as_rider
                return []  # driver_id-filtered wave never runs (no driver_id)
            if table == "saved_addresses":
                return saved_addresses
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows),
            patch("backend.routes.drivers.tax_exports._upload_export_zip", side_effect=fake_upload),
            patch("backend.routes.drivers._deps.send_email", AsyncMock(side_effect=fake_send_email)),
        ):
            result = await drivers_mod._build_and_email_data_export("u1", "rider@example.com")

        return get_files(), box, result

    async def test_rider_only_account_receives_its_own_ride_history(self):
        files, _box, result = await self._run_rider_export(
            rides_as_rider=[
                {
                    "id": "r1",
                    "driver_id": "DRIVER_X",
                    "status": "completed",
                    "pickup_address": "123 Main St",
                    "dropoff_address": "456 Elm Ave",
                    "total_fare": 15.0,
                }
            ],
            saved_addresses=[{"id": "addr1", "user_id": "u1", "label": "Home", "address": "789 Home St"}],
        )
        assert result is True
        payload = json.loads(files["raw_data.json"])
        assert len(payload["rides_as_rider"]) == 1, "rider-only account's own trip history must not be empty"
        assert payload["rides_as_rider"][0]["pickup_address"] == "123 Main St"
        assert len(payload["saved_addresses"]) == 1
        assert payload["saved_addresses"][0]["label"] == "Home"
        # The driver-shaped `rides` list stays empty — this account has no
        # drivers row, so wave 2 never ran.
        assert payload["rides"] == []

    async def test_driver_id_stripped_as_third_party_pii_from_rides_as_rider(self):
        files, _box, _result = await self._run_rider_export(
            rides_as_rider=[{"id": "r1", "driver_id": "DRIVER_X", "status": "completed"}],
            saved_addresses=[],
        )
        ride = json.loads(files["raw_data.json"])["rides_as_rider"][0]
        assert "driver_id" not in ride, "driver_id (third-party PII) must be stripped from rides_as_rider"
        assert "DRIVER_X" not in files["rides_as_rider.csv"]

    async def test_riders_own_pickup_dropoff_coordinates_are_kept(self):
        # Unlike the driver-side `rides` export (where a rider's coordinates
        # are third-party PII), a rider's own pickup/dropoff on their OWN
        # trip is their own data and must be included, not stripped.
        files, _box, _result = await self._run_rider_export(
            rides_as_rider=[
                {
                    "id": "r1",
                    "driver_id": "DRIVER_X",
                    "pickup_lat": 52.13,
                    "pickup_lng": -106.66,
                }
            ],
            saved_addresses=[],
        )
        ride = json.loads(files["raw_data.json"])["rides_as_rider"][0]
        assert ride.get("pickup_lat") == 52.13
        assert ride.get("pickup_lng") == -106.66


class TestReturnValue:
    """_build_and_email_data_export must report its real outcome (N1): the
    rider-side caller (routes/users.py's _fulfill_rider_data_export) uses
    this to decide whether the DSAR queue row is actually 'completed'."""

    async def test_returns_true_on_success(self):
        from backend.routes import drivers as drivers_mod

        async def _get_rows(table, filters=None, **kwargs):
            return {"drivers": [{"id": "d1", "user_id": "u1"}], "users": [{"id": "u1"}]}.get(table, [])

        async def fake_upload(*args, **kwargs):
            return FAKE_SIGNED_URL

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows),
            patch("backend.routes.drivers.tax_exports._upload_export_zip", side_effect=fake_upload),
            patch("backend.routes.drivers._deps.send_email", AsyncMock()),
        ):
            result = await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

        assert result is True

    async def test_returns_false_when_everything_fails(self):
        from backend.routes import drivers as drivers_mod

        async def _get_rows(table, filters=None, **kwargs):
            raise RuntimeError("db down")

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows):
            result = await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

        assert result is False

    async def test_returns_true_when_link_fails_but_attachment_fallback_succeeds(self):
        # The PIPEDA request is still fulfilled via the attachment fallback —
        # a storage hiccup alone must not report failure to the caller.
        from backend.routes import drivers as drivers_mod

        async def _get_rows(table, filters=None, **kwargs):
            return {"drivers": [{"id": "d1", "user_id": "u1"}], "users": [{"id": "u1"}]}.get(table, [])

        async def failing_upload(*args, **kwargs):
            raise RuntimeError("storage down")

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows),
            patch("backend.routes.drivers.tax_exports._upload_export_zip", side_effect=failing_upload),
            patch("backend.routes.drivers._deps.send_email", AsyncMock()),
        ):
            result = await drivers_mod._build_and_email_data_export("u1", "driver@example.com")

        assert result is True
