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


# ── apply_account_update: payouts-transition notification (N6) ──────────
#
# Stripe redelivers account.updated freely, so these pin the *edge*-only
# firing rule: a genuine True->False (or False->True) change on
# stripe_payouts_enabled, never a repeat delivery of an unchanged value, and
# never a first-ever sync where the pre-update value is unset (None).
#
# send_push_notification is imported lazily inside
# _send_payouts_notice (`from ..features import send_push_notification`), so
# it is patched on its defining module (backend.features) — the same
# late-binding local-import pattern documented for db_supabase elsewhere in
# this file, just one module over.


class TestApplyAccountUpdatePayoutsNotification:
    @pytest.mark.anyio
    async def test_enabled_to_blocked_fires_exactly_one_account_priority_push(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        driver = {
            "id": "driver-1",
            "user_id": "user-1",
            "stripe_account_id": "acct_123",
            "stripe_payouts_enabled": True,
        }
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        push_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("backend.features.send_push_notification", push_mock)

        # payouts_enabled absent from the payload -> bool(None) == False: the
        # blocked state.
        await stripe_kyc_sync.apply_account_update({"id": "acct_123", "details_submitted": True})

        push_mock.assert_awaited_once()
        args, kwargs = push_mock.await_args
        assert args[0] == "user-1"  # users.id, not drivers.id
        assert kwargs["priority"] == "account"
        assert kwargs["data"]["type"] == "stripe_payouts_blocked"
        assert kwargs["data"]["deeplink"] == "/driver/payout"
        assert kwargs["target_app"] == "driver"

    @pytest.mark.anyio
    async def test_redelivery_of_already_blocked_account_does_not_spam(self, monkeypatch):
        """Same webhook redelivered for an account that was already blocked
        (no transition — pre-update value is already False) must not fire a
        duplicate push."""
        from backend.services import stripe_kyc_sync

        driver = {
            "id": "driver-1",
            "user_id": "user-1",
            "stripe_account_id": "acct_123",
            "stripe_payouts_enabled": False,
        }
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        push_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("backend.features.send_push_notification", push_mock)

        await stripe_kyc_sync.apply_account_update({"id": "acct_123", "payouts_enabled": False})

        push_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_first_ever_sync_with_unset_prior_value_does_not_spuriously_fire(self, monkeypatch):
        """A drivers row that has never been synced before has no
        stripe_payouts_enabled key at all (None, not False). The very first
        account.updated for it landing on payouts_enabled=False must not read
        as a "newly blocked" transition — there is no prior enabled state to
        have transitioned from."""
        from backend.services import stripe_kyc_sync

        driver = {"id": "driver-1", "user_id": "user-1", "stripe_account_id": "acct_123"}  # key absent -> None
        assert "stripe_payouts_enabled" not in driver
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        push_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("backend.features.send_push_notification", push_mock)

        await stripe_kyc_sync.apply_account_update({"id": "acct_123", "payouts_enabled": False})

        push_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_first_ever_sync_landing_enabled_does_not_fire_recovery(self, monkeypatch):
        """Symmetric first-sync guard for the recovery direction: None -> True
        is a driver's normal onboarding completion, not a "recovery"."""
        from backend.services import stripe_kyc_sync

        driver = {"id": "driver-1", "user_id": "user-1", "stripe_account_id": "acct_123"}
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        push_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("backend.features.send_push_notification", push_mock)

        await stripe_kyc_sync.apply_account_update({"id": "acct_123", "payouts_enabled": True})

        push_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_blocked_to_enabled_fires_recovery_push_at_normal_priority(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        driver = {
            "id": "driver-1",
            "user_id": "user-1",
            "stripe_account_id": "acct_123",
            "stripe_payouts_enabled": False,
        }
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        push_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("backend.features.send_push_notification", push_mock)

        await stripe_kyc_sync.apply_account_update({"id": "acct_123", "payouts_enabled": True})

        push_mock.assert_awaited_once()
        _, kwargs = push_mock.await_args
        assert kwargs["priority"] == "normal"
        assert kwargs["data"]["type"] == "stripe_payouts_recovered"

    @pytest.mark.anyio
    async def test_no_change_enabled_to_enabled_does_not_fire(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        driver = {
            "id": "driver-1",
            "user_id": "user-1",
            "stripe_account_id": "acct_123",
            "stripe_payouts_enabled": True,
        }
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        push_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("backend.features.send_push_notification", push_mock)

        await stripe_kyc_sync.apply_account_update({"id": "acct_123", "payouts_enabled": True})

        push_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_push_failure_does_not_raise_and_persist_already_committed(self, monkeypatch):
        """The mirror write must not be undone, and apply_account_update must
        not raise, if the best-effort notification blows up."""
        from backend.services import stripe_kyc_sync

        driver = {
            "id": "driver-1",
            "user_id": "user-1",
            "stripe_account_id": "acct_123",
            "stripe_payouts_enabled": True,
        }
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        update_mock = AsyncMock()
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", update_mock)

        push_mock = AsyncMock(side_effect=RuntimeError("fcm down"))
        monkeypatch.setattr("backend.features.send_push_notification", push_mock)

        result = await stripe_kyc_sync.apply_account_update({"id": "acct_123", "payouts_enabled": False})

        update_mock.assert_awaited_once()  # persisted before the notify attempt
        push_mock.assert_awaited_once()
        assert result["id"] == "driver-1"
        assert result["stripe_payouts_enabled"] is False

    @pytest.mark.anyio
    async def test_no_user_id_skips_notification_without_raising(self, monkeypatch):
        from backend.services import stripe_kyc_sync

        driver = {"id": "driver-1", "stripe_account_id": "acct_123", "stripe_payouts_enabled": True}  # no user_id
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
        monkeypatch.setattr(stripe_kyc_sync.db_supabase, "update_one", AsyncMock())

        push_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("backend.features.send_push_notification", push_mock)

        result = await stripe_kyc_sync.apply_account_update({"id": "acct_123", "payouts_enabled": False})

        push_mock.assert_not_awaited()
        assert result["id"] == "driver-1"


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


# ── reveal_sin_from_stripe: REMOVED ─────────────────────────────────────
# The function is gone. `individual.id_number` is write-only on Connect, so
# it could never return a SIN. The reveal now decrypts Spinr's own column
# and is covered by TestRevealSin in test_admin_drivers_coverage.py.


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
