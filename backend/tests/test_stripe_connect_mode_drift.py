"""Driver Stripe Connect account repair across a test → live key rotation.

A Connect account is a payout destination carrying bank details and verified
identity, so the driver-side rules differ from the rider-side ones:

  * a stranded account is RETIRED, not silently replaced in place — the KYC
    mirror columns that describe it are reset in the same write, because they
    are what the admin slideout and the driver app's "bank linked" state read;
  * nothing auto-onboards. The driver re-onboards through the existing
    "Set up payouts" flow, which is where the replacement account is minted;
  * a test-mode account holds no real bank and no verified identity, so there
    is genuinely nothing to carry over.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

from backend.services.stripe_kyc_sync import (
    account_is_stale_by_mode,
    refresh_driver_kyc,
    retire_stripe_account,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

DRIVER_ID = "drv_drift_1"
STALE_ACCT = "acct_testmode_old"
LIVE_KEY = "sk_live_" + "abc123"
TEST_KEY = "sk_test_abc123"


def _driver(**extra) -> dict:
    row = {
        "id": DRIVER_ID,
        "stripe_account_id": STALE_ACCT,
        "stripe_account_id_mode": None,
        "stripe_account_onboarded": True,
        "stripe_details_submitted": True,
        "stripe_payouts_enabled": True,
        "stripe_id_number_provided": True,
        "stripe_id_number_last4": "1234",
    }
    row.update(extra)
    return row


def _resource_missing(obj_id: str = STALE_ACCT) -> stripe.error.InvalidRequestError:
    return stripe.error.InvalidRequestError(f"No such account: '{obj_id}'", param=None, code="resource_missing")


class TestAccountIsStaleByMode:
    def test_test_stamp_under_live_key(self):
        assert account_is_stale_by_mode(_driver(stripe_account_id_mode="test"), LIVE_KEY) is True

    def test_matching_stamp(self):
        assert account_is_stale_by_mode(_driver(stripe_account_id_mode="live"), LIVE_KEY) is False

    def test_unstamped_is_not_assumed_stale(self):
        """Pre-migration-286 rows are resolved by resource_missing instead."""
        assert account_is_stale_by_mode(_driver(), LIVE_KEY) is False


class TestRetireStripeAccount:
    async def test_archives_id_and_resets_the_kyc_mirror(self):
        updates: list = []

        async def _update_one(table, filters, update):
            updates.append((table, filters, update))

        with patch("backend.services.stripe_kyc_sync.db_supabase.update_one", side_effect=_update_one):
            await retire_stripe_account(_driver(), STALE_ACCT, reason="mode_mismatch")

        table, filters, update = updates[0]
        assert table == "drivers"
        # Filtered on the stale id so a concurrent retire or a completed
        # re-onboarding is never clobbered.
        assert filters == {"id": DRIVER_ID, "stripe_account_id": STALE_ACCT}
        assert update["stripe_account_id"] is None
        assert update["stripe_account_id_superseded"] == STALE_ACCT
        assert update["stripe_account_id_mode"] is None

    async def test_mirror_no_longer_advertises_payouts(self):
        """The dangerous case: admin showing 'payouts enabled' for a driver
        whose payout destination does not exist on the live platform."""
        updates: list = []

        async def _update_one(table, filters, update):
            updates.append(update)

        with patch("backend.services.stripe_kyc_sync.db_supabase.update_one", side_effect=_update_one):
            await retire_stripe_account(_driver(), STALE_ACCT, reason="resource_missing")

        u = updates[0]
        assert u["stripe_account_onboarded"] is False
        assert u["stripe_details_submitted"] is False
        assert u["stripe_payouts_enabled"] is False
        assert u["stripe_charges_enabled"] is False
        # SIN state belonged to the retired account — payouts must re-gate on it.
        assert u["stripe_id_number_provided"] is False
        assert u["stripe_id_number_last4"] is None
        # ToS was accepted on the retired account; the replacement needs its own.
        assert u["stripe_tos_accepted_at"] is None
        assert u["stripe_requirements_due"] == []


class _RefreshHarness:
    def __init__(self, secret: str = LIVE_KEY, retrieve=None):
        self.updates: list = []

        async def _update_one(table, filters, update):
            self.updates.append((table, filters, update))

        self._patches = [
            patch("backend.services.stripe_kyc_sync.db_supabase.update_one", side_effect=_update_one),
            patch(
                "backend.services.stripe_kyc_sync.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": secret}),
            ),
            patch("stripe.Account.retrieve", retrieve or MagicMock()),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TestRefreshDriverKyc:
    async def test_mode_mismatch_retires_without_calling_stripe(self):
        retrieve = MagicMock(side_effect=AssertionError("stamp alone is enough"))
        with _RefreshHarness(retrieve=retrieve) as h:
            result = await refresh_driver_kyc(_driver(stripe_account_id_mode="test"), retire_if_unreachable=True)
        assert result == {"status": "account_not_on_key", "retired": True}
        assert h.updates[0][2]["stripe_account_id"] is None

    async def test_resource_missing_retires(self):
        with _RefreshHarness(retrieve=MagicMock(side_effect=_resource_missing())) as h:
            result = await refresh_driver_kyc(_driver(), retire_if_unreachable=True)
        assert result == {"status": "account_not_on_key", "retired": True}
        assert h.updates[0][2]["stripe_payouts_enabled"] is False

    @pytest.mark.parametrize(
        "exc",
        [
            stripe.error.AuthenticationError("Invalid API Key provided"),
            stripe.error.APIConnectionError("connection dropped"),
            stripe.error.RateLimitError("slow down"),
        ],
    )
    async def test_ambiguous_error_reports_stripe_error_and_retires_nothing(self, exc):
        """A revoked key makes every account look missing. Retiring on that
        would wipe the KYC mirror for the entire driver fleet."""
        with _RefreshHarness(retrieve=MagicMock(side_effect=exc)) as h:
            result = await refresh_driver_kyc(_driver())
        assert result == {"status": "stripe_error"}
        assert h.updates == []

    async def test_no_account_is_not_an_error(self):
        with _RefreshHarness() as h:
            result = await refresh_driver_kyc(_driver(stripe_account_id=None))
        assert result == {"status": "no_stripe_account"}
        assert h.updates == []

    async def test_retire_requires_opt_in(self):
        """Default is non-destructive.

        The legacy Stripe mapping import calls refresh_driver_kyc right after
        committing a stripe_account_id. A Scenario-B account still living on
        the old Connect platform answers PermissionError, and retiring on that
        would null the mapping the import just wrote — and null it again on
        every re-import. Only callers whose job is repair opt in.
        """
        with _RefreshHarness(retrieve=MagicMock(side_effect=_resource_missing())) as h:
            result = await refresh_driver_kyc(_driver())
        assert result == {"status": "account_not_on_key", "retired": False}
        assert h.updates == []

    async def test_mode_mismatch_without_opt_in_does_not_retire(self):
        with _RefreshHarness(retrieve=MagicMock(side_effect=_resource_missing())) as h:
            result = await refresh_driver_kyc(_driver(stripe_account_id_mode="test"))
        assert result.get("retired") is False
        assert h.updates == []

    async def test_error_naming_a_different_object_is_not_treated_as_ours(self):
        """resource_missing about some *other* object must not retire this one.

        A Stripe request names several objects; without this check an error
        about an unrelated resource would detach a healthy payout destination.
        """
        other = stripe.error.InvalidRequestError(
            "No such external_account: 'ba_xyz'", param=None, code="resource_missing"
        )
        with _RefreshHarness(retrieve=MagicMock(side_effect=other)) as h:
            result = await refresh_driver_kyc(_driver(), retire_if_unreachable=True)
        assert result == {"status": "stripe_error"}
        assert h.updates == []
