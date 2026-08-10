"""Corporate auto-topup behaviour when the company's Stripe customer is gone.

This loop runs every 10 minutes on every replica with nobody present, so its
rules differ from the admin-present billing paths:

  * it must NEVER mint a replacement payment identity — no one is there to
    consent, and a fresh customer carries no card, so the charge it wanted to
    make could not succeed either way;
  * it must retire the dead customer rather than re-discovering it 143 more
    times today;
  * an ambiguous Stripe error must leave the row completely alone.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

from backend.utils import corporate_autotopup as at

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

COMPANY_ID = "corp_at_1"
STALE_CUS = "cus_corp_testmode"
LIVE_KEY = "sk_live_" + "abc123"


def _wallet(**extra) -> dict:
    row = {
        "id": "cw_1",
        "company_id": COMPANY_ID,
        "auto_topup_amount": "500.00",
        "auto_topup_daily_cap": "5000.00",
    }
    row.update(extra)
    return row


def _company(**extra) -> dict:
    row = {
        "id": COMPANY_ID,
        "status": "active",
        "name": "Northern Freight",
        "legal_name": "Northern Freight Ltd.",
        "billing_email": "billing@example.com",
        "stripe_customer_id": STALE_CUS,
        "stripe_customer_id_mode": None,
    }
    row.update(extra)
    return row


def _resource_missing() -> stripe.error.InvalidRequestError:
    return stripe.error.InvalidRequestError(
        f"No such customer: '{STALE_CUS}'", param=None, code="resource_missing"
    )


class _Harness:
    def __init__(self, company: dict, *, pm_side_effect=None, pi_side_effect=None):
        self.retired: list = []
        self.customers_created = MagicMock(
            side_effect=AssertionError("a background loop must never mint a payment identity")
        )

        async def _retire(comp, stale, reason):
            self.retired.append((comp["id"], stale, reason))

        self._patches = [
            patch.object(at, "get_corporate_account_by_id", AsyncMock(return_value=company)),
            patch.object(at, "sum_autotopups_today", AsyncMock(return_value=Decimal("0"))),
            patch.object(
                at,
                "get_default_payment_method",
                AsyncMock(side_effect=pm_side_effect) if pm_side_effect else AsyncMock(return_value="pm_1"),
            ),
            patch.object(at, "retire_corporate_customer", side_effect=_retire),
            patch("stripe.Customer.create", self.customers_created),
            patch(
                "stripe.PaymentIntent.create",
                MagicMock(side_effect=pi_side_effect) if pi_side_effect else MagicMock(return_value=MagicMock()),
            ),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TestStrandedCustomer:
    async def test_stamped_mismatch_retires_without_touching_stripe(self):
        pm = AsyncMock(side_effect=AssertionError("stamp alone is enough — no Stripe call"))
        with _Harness(_company(stripe_customer_id_mode="test"), pm_side_effect=pm.side_effect) as h:
            await at._process_one(_wallet(), LIVE_KEY)
        assert h.retired == [(COMPANY_ID, STALE_CUS, "mode_mismatch")]

    async def test_resource_missing_on_payment_method_lookup_retires(self):
        """The unstamped case — everything predating migration 286."""
        with _Harness(_company(), pm_side_effect=_resource_missing()) as h:
            await at._process_one(_wallet(), LIVE_KEY)
        assert h.retired == [(COMPANY_ID, STALE_CUS, "resource_missing")]

    async def test_resource_missing_on_payment_intent_retires(self):
        with _Harness(_company(), pi_side_effect=_resource_missing()) as h:
            await at._process_one(_wallet(), LIVE_KEY)
        assert h.retired == [(COMPANY_ID, STALE_CUS, "resource_missing")]

    async def test_never_creates_a_replacement_customer(self):
        """The whole reason this loop retires instead of re-provisioning."""
        with _Harness(_company(), pm_side_effect=_resource_missing()) as h:
            await at._process_one(_wallet(), LIVE_KEY)
        h.customers_created.assert_not_called()

    async def test_already_retired_company_is_skipped_for_free(self):
        """After the first retire, later ticks short-circuit on the NULL guard
        without spending a Stripe call."""
        pm = AsyncMock(side_effect=AssertionError("must not reach Stripe"))
        with _Harness(_company(stripe_customer_id=None), pm_side_effect=pm.side_effect) as h:
            await at._process_one(_wallet(), LIVE_KEY)
        assert h.retired == []


class TestAmbiguousErrorsLeaveTheRowAlone:
    @pytest.mark.parametrize(
        "exc",
        [
            stripe.error.AuthenticationError("Invalid API Key provided"),
            stripe.error.RateLimitError("slow down"),
            stripe.error.APIConnectionError("connection dropped"),
        ],
    )
    async def test_payment_intent_ambiguous_error_does_not_retire(self, exc):
        """A revoked key would otherwise retire every company's customer in one
        tick, wiping corporate billing platform-wide."""
        with _Harness(_company(), pi_side_effect=exc) as h:
            await at._process_one(_wallet(), LIVE_KEY)
        assert h.retired == []

    async def test_payment_method_lookup_ambiguous_error_propagates(self):
        """run_autotopup_tick's per-wallet handler logs it; the row is untouched."""
        with _Harness(_company(), pm_side_effect=stripe.error.AuthenticationError("bad key")) as h:
            with pytest.raises(stripe.error.AuthenticationError):
                await at._process_one(_wallet(), LIVE_KEY)
        assert h.retired == []

    async def test_one_bad_wallet_does_not_stop_the_tick(self):
        """A raise out of _process_one must stay contained to that wallet."""
        with (
            patch.object(at, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": LIVE_KEY})),
            patch.object(at, "list_wallets_needing_autotopup", AsyncMock(return_value=[_wallet(), _wallet(id="cw_2")])),
            patch.object(at, "_process_one", AsyncMock(side_effect=[RuntimeError("boom"), None])) as m_process,
        ):
            await at.run_autotopup_tick()
        assert m_process.await_count == 2
