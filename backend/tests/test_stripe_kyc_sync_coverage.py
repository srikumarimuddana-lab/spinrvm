"""Coverage for services/stripe_kyc_sync.py (A1c, Sub-tier B).

Stripe Connect KYC mirror — maps Stripe Express account.updated payloads
into cache columns on `drivers` (migration 92). SIN never persisted (only
reveal_sin_from_stripe returns it, once, for an audited admin action). Had
no dedicated test file; only 30.70% coverage.

`import stripe` is a genuine top-level third-party import (no dual-import
ambiguity, unlike this repo's own backend.* modules) — patched directly at
`stripe.Account.retrieve`. `get_app_settings` is patched on the module
under test (the name as bound via `from ..settings_loader import
get_app_settings`), not the source module.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── _kyc_mirror_fields (pure function) ─────────────────────────────────


class TestKycMirrorFields:
    def test_full_account_maps_every_field(self):
        from backend.services.stripe_kyc_sync import _kyc_mirror_fields

        account = {
            "details_submitted": True,
            "charges_enabled": True,
            "payouts_enabled": True,
            "business_type": "individual",
            "individual": {
                "id_number_provided": True,
                "id_number_last_4": "1234",
                "verification": {"status": "verified"},
            },
            "business_profile": {"tax_id": "123456789RT0001"},
            "requirements": {
                "currently_due": ["individual.verification.document"],
                "past_due": [],
                "disabled_reason": None,
            },
            "tos_acceptance": {"date": 1735689600},
        }
        result = _kyc_mirror_fields(account)
        assert result["stripe_details_submitted"] is True
        assert result["stripe_id_number_provided"] is True
        assert result["stripe_id_number_last4"] == "1234"
        assert result["gst_bn"] == "123456789RT0001"
        assert result["gst_registered"] is True
        assert result["stripe_verification_status"] == "verified"
        assert result["stripe_tos_accepted_at"] is not None
        assert result["stripe_account_onboarded"] is True

    def test_incomplete_account_defaults_gracefully(self):
        """An account mid-onboarding has no individual/business_profile/
        requirements/tos_acceptance — every nested .get() must degrade
        without raising."""
        from backend.services.stripe_kyc_sync import _kyc_mirror_fields

        result = _kyc_mirror_fields({})
        assert result["stripe_details_submitted"] is False
        assert result["stripe_id_number_provided"] is False
        assert result["stripe_id_number_last4"] is None
        assert result["gst_bn"] is None
        assert "gst_registered" not in result  # only set when gst_bn is truthy
        assert result["stripe_tos_accepted_at"] is None

    def test_invalid_last4_format_is_rejected(self):
        from backend.services.stripe_kyc_sync import _kyc_mirror_fields

        result = _kyc_mirror_fields({"individual": {"id_number_last_4": "12a4"}})
        assert result["stripe_id_number_last4"] is None

    def test_wrong_length_last4_is_rejected(self):
        from backend.services.stripe_kyc_sync import _kyc_mirror_fields

        result = _kyc_mirror_fields({"individual": {"id_number_last_4": "123"}})
        assert result["stripe_id_number_last4"] is None

    def test_zero_tos_timestamp_is_treated_as_unset(self):
        from backend.services.stripe_kyc_sync import _kyc_mirror_fields

        result = _kyc_mirror_fields({"tos_acceptance": {"date": 0}})
        assert result["stripe_tos_accepted_at"] is None

    def test_no_gst_bn_omits_gst_registered_key(self):
        from backend.services.stripe_kyc_sync import _kyc_mirror_fields

        result = _kyc_mirror_fields({"business_profile": {"tax_id": None}})
        assert result["gst_bn"] is None
        assert "gst_registered" not in result


# ── apply_account_update ────────────────────────────────────────────────


class TestApplyAccountUpdate:
    @pytest.mark.anyio
    async def test_missing_account_id_returns_none(self):
        from backend.services.stripe_kyc_sync import apply_account_update

        assert await apply_account_update({}) is None

    @pytest.mark.anyio
    async def test_no_matching_driver_returns_none(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[]))
        result = await stripe_kyc_sync.apply_account_update({"id": "acct_123"})
        assert result is None

    @pytest.mark.anyio
    async def test_success_merges_driver_and_updates(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        driver = {"id": "driver-1", "stripe_account_id": "acct_123"}
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        result = await stripe_kyc_sync.apply_account_update({"id": "acct_123", "details_submitted": True})
        assert result["id"] == "driver-1"
        assert result["stripe_details_submitted"] is True

    @pytest.mark.anyio
    async def test_db_update_failure_propagates(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        driver = {"id": "driver-1", "stripe_account_id": "acct_123"}
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(
            stripe_kyc_sync.db_supabase, "update_one", AsyncMock(side_effect=ConnectionError("db down"))
        )

        with pytest.raises(ConnectionError):
            await stripe_kyc_sync.apply_account_update({"id": "acct_123"})


# ── refresh_driver_kyc ───────────────────────────────────────────────────


class TestRefreshDriverKyc:
    @pytest.mark.anyio
    async def test_no_stripe_account_returns_status(self):
        from backend.services.stripe_kyc_sync import refresh_driver_kyc

        result = await refresh_driver_kyc({"id": "driver-1"})
        assert result == {"status": "no_stripe_account"}

    @pytest.mark.anyio
    async def test_not_configured_returns_status(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={}))
        result = await stripe_kyc_sync.refresh_driver_kyc({"id": "driver-1", "stripe_account_id": "acct_123"})
        assert result == {"status": "stripe_not_configured"}

    @pytest.mark.anyio
    async def test_stripe_retrieve_error_returns_status(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_x"}))
        with patch("stripe.Account.retrieve", side_effect=RuntimeError("stripe down")):
            result = await stripe_kyc_sync.refresh_driver_kyc({"id": "driver-1", "stripe_account_id": "acct_123"})
        assert result == {"status": "stripe_error"}

    @pytest.mark.anyio
    async def test_success_via_to_dict_recursive(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_x"}))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        fake_account = MagicMock()
        fake_account._to_dict_recursive = MagicMock(return_value={"details_submitted": True})
        with patch("stripe.Account.retrieve", return_value=fake_account):
            result = await stripe_kyc_sync.refresh_driver_kyc({"id": "driver-1", "stripe_account_id": "acct_123"})
        assert result["status"] == "ok"
        assert result["updates"]["stripe_details_submitted"] is True

    @pytest.mark.anyio
    async def test_success_falls_back_to_dict_when_no_recursive_method(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_x"}))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        # A plain dict has no _to_dict_recursive/to_dict_recursive attrs, so
        # getattr(..., None) is None for both and dict(account) is used.
        with patch("stripe.Account.retrieve", return_value={"details_submitted": False}):
            result = await stripe_kyc_sync.refresh_driver_kyc({"id": "driver-1", "stripe_account_id": "acct_123"})
        assert result["status"] == "ok"
        assert result["updates"]["stripe_details_submitted"] is False


# ── reveal_sin_from_stripe ───────────────────────────────────────────────


class TestRevealSinFromStripe:
    @pytest.mark.anyio
    async def test_no_stripe_account_returns_none(self):
        from backend.services.stripe_kyc_sync import reveal_sin_from_stripe

        assert await reveal_sin_from_stripe({"id": "driver-1"}) is None

    @pytest.mark.anyio
    async def test_not_configured_returns_none(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={}))
        assert await stripe_kyc_sync.reveal_sin_from_stripe({"id": "d1", "stripe_account_id": "acct_1"}) is None

    @pytest.mark.anyio
    async def test_retrieve_error_returns_none(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"}))
        with patch("stripe.Account.retrieve", side_effect=RuntimeError("boom")):
            result = await stripe_kyc_sync.reveal_sin_from_stripe({"id": "d1", "stripe_account_id": "acct_1"})
        assert result is None

    @pytest.mark.anyio
    async def test_missing_id_number_returns_none(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"}))
        with patch("stripe.Account.retrieve", return_value={"individual": {}}):
            result = await stripe_kyc_sync.reveal_sin_from_stripe({"id": "d1", "stripe_account_id": "acct_1"})
        assert result is None

    @pytest.mark.anyio
    async def test_non_canonical_sin_format_returns_none(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"}))
        with patch("stripe.Account.retrieve", return_value={"individual": {"id_number": "not-9-digits"}}):
            result = await stripe_kyc_sync.reveal_sin_from_stripe({"id": "d1", "stripe_account_id": "acct_1"})
        assert result is None

    @pytest.mark.anyio
    async def test_valid_sin_is_returned(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"}))
        with patch("stripe.Account.retrieve", return_value={"individual": {"id_number": "123456789"}}):
            result = await stripe_kyc_sync.reveal_sin_from_stripe({"id": "d1", "stripe_account_id": "acct_1"})
        assert result == "123456789"


# ── get_legal_name_and_address_from_stripe ──────────────────────────────


class TestGetLegalNameAndAddress:
    @pytest.mark.anyio
    async def test_no_stripe_account_returns_none(self):
        from backend.services.stripe_kyc_sync import get_legal_name_and_address_from_stripe

        assert await get_legal_name_and_address_from_stripe({"id": "d1"}) is None

    @pytest.mark.anyio
    async def test_not_configured_returns_none(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={}))
        result = await stripe_kyc_sync.get_legal_name_and_address_from_stripe(
            {"id": "d1", "stripe_account_id": "acct_1"}
        )
        assert result is None

    @pytest.mark.anyio
    async def test_retrieve_error_returns_none(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"}))
        with patch("stripe.Account.retrieve", side_effect=RuntimeError("boom")):
            result = await stripe_kyc_sync.get_legal_name_and_address_from_stripe(
                {"id": "d1", "stripe_account_id": "acct_1"}
            )
        assert result is None

    @pytest.mark.anyio
    async def test_no_name_and_no_address_returns_none(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"}))
        with patch("stripe.Account.retrieve", return_value={"individual": {}}):
            result = await stripe_kyc_sync.get_legal_name_and_address_from_stripe(
                {"id": "d1", "stripe_account_id": "acct_1"}
            )
        assert result is None

    @pytest.mark.anyio
    async def test_success_returns_name_and_address(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"}))
        account = {
            "individual": {
                "first_name": "Jordan",
                "last_name": "Driver",
                "address": {
                    "line1": "123 Main St",
                    "line2": "Unit 4",
                    "city": "Saskatoon",
                    "state": "SK",
                    "postal_code": "S7K 0A1",
                    "country": "CA",
                },
            }
        }
        with patch("stripe.Account.retrieve", return_value=account):
            result = await stripe_kyc_sync.get_legal_name_and_address_from_stripe(
                {"id": "d1", "stripe_account_id": "acct_1"}
            )
        assert result["legal_name"] == "Jordan Driver"
        assert result["city"] == "Saskatoon"
        assert result["country"] == "CA"

    @pytest.mark.anyio
    async def test_returns_result_when_only_address_present_no_name(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        monkeypatch.setattr(stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"}))
        account = {"individual": {"address": {"line1": "123 Main St"}}}
        with patch("stripe.Account.retrieve", return_value=account):
            result = await stripe_kyc_sync.get_legal_name_and_address_from_stripe(
                {"id": "d1", "stripe_account_id": "acct_1"}
            )
        assert result is not None
        assert result["legal_name"] is None
        assert result["address_line1"] == "123 Main St"
