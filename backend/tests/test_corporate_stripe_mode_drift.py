"""Corporate Stripe customer repair across a test → live key rotation.

The corporate surface's defining constraint is that some of its billing paths
run with nobody present (the auto-topup loop). So this module has two
behaviours and the tests pin the split:

  * admin-present  → re-provision (a fresh customer has no card, so nothing
    can be charged until someone deliberately adds one);
  * background     → retire only, never create. A replacement customer could
    not have been charged either, and retiring converts an endless 10-minutely
    Stripe failure into one clean "no Stripe customer" state.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

from backend.services.corporate_stripe_identity import (
    CorporateCustomerUnavailable,
    corporate_customer_is_stale,
    get_or_create_corporate_customer,
    retire_corporate_customer,
    with_corporate_customer_repair,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

COMPANY_ID = "corp_drift_1"
STALE_CUS = "cus_corp_testmode"
NEW_CUS = "cus_corp_livemode"
LIVE_KEY = "sk_live_" + "abc123"

# Sentinel: "no override — the re-read sees whatever we wrote".
_UNSET = object()


def _company(**extra) -> dict:
    row = {
        "id": COMPANY_ID,
        "name": "Northern Freight",
        "legal_name": "Northern Freight Ltd.",
        "billing_email": "billing@example.com",
        "status": "active",
        "stripe_customer_id": STALE_CUS,
        "stripe_customer_id_mode": None,
    }
    row.update(extra)
    return row


def _customer(cid: str = NEW_CUS, livemode: bool = True) -> MagicMock:
    cus = MagicMock()
    cus.id = cid
    cus.get = lambda k, default=None: {"livemode": livemode}.get(k, default)
    cus.livemode = livemode
    return cus


def _resource_missing(oid: str = STALE_CUS) -> stripe.error.InvalidRequestError:
    return stripe.error.InvalidRequestError(f"No such customer: '{oid}'", param=None, code="resource_missing")


class _Harness:
    """Fakes the DB + settings + Stripe surface the identity service reaches.

    `persisted` is what the post-write re-read sees. A replacement create
    re-reads the row and defers to whatever actually landed, so tests can
    simulate a concurrent retire/repair by overriding it.
    """

    def __init__(self, *, flag: bool = True, create=None, persisted: Any = _UNSET):
        self.updates: list = []
        self._persisted = persisted

        async def _update_one(table, filters, update):
            self.updates.append((table, filters, update))

        async def _find_one(table, filters):
            if self._persisted is not _UNSET:
                return {"id": COMPANY_ID, "stripe_customer_id": self._persisted}
            # Default: our write won — the row carries whatever we just wrote.
            for _t, _f, upd in reversed(self.updates):
                if "stripe_customer_id" in upd:
                    return {"id": COMPANY_ID, "stripe_customer_id": upd["stripe_customer_id"]}
            return {"id": COMPANY_ID, "stripe_customer_id": None}

        self.create = create if create is not None else MagicMock(return_value=_customer())
        self._patches = [
            patch("backend.services.corporate_stripe_identity.db_supabase.update_one", side_effect=_update_one),
            patch("backend.services.corporate_stripe_identity.db_supabase.find_one", side_effect=_find_one),
            patch(
                "backend.services.corporate_stripe_identity.get_app_settings",
                AsyncMock(return_value={"stripe_reprovision_stale_ids": flag}),
            ),
            patch("backend.services.corporate_stripe_identity.stripe.Customer.create", self.create),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TestStaleDetection:
    def test_test_stamp_under_live_key(self):
        assert corporate_customer_is_stale(_company(stripe_customer_id_mode="test"), LIVE_KEY) is True

    def test_matching_stamp(self):
        assert corporate_customer_is_stale(_company(stripe_customer_id_mode="live"), LIVE_KEY) is False

    def test_unstamped_is_not_assumed_stale(self):
        assert corporate_customer_is_stale(_company(), LIVE_KEY) is False


class TestRetire:
    """Background-safe: archive, never create."""

    async def test_archives_and_nulls_without_creating(self):
        create = MagicMock(side_effect=AssertionError("background must never mint a payment identity"))
        with _Harness(create=create) as h:
            await retire_corporate_customer(_company(), STALE_CUS, reason="resource_missing")
        table, filters, update = h.updates[0]
        assert table == "corporate_accounts"
        assert filters == {"id": COMPANY_ID, "stripe_customer_id": STALE_CUS}
        assert update["stripe_customer_id"] is None
        assert update["stripe_customer_id_superseded"] == STALE_CUS
        assert update["stripe_customer_id_mode"] is None

    async def test_kill_switch_blocks_retire_and_writes_nothing(self):
        with _Harness(flag=False) as h:
            with pytest.raises(CorporateCustomerUnavailable):
                await retire_corporate_customer(_company(), STALE_CUS, reason="mode_mismatch")
        assert h.updates == []


class TestGetOrCreate:
    async def test_creates_when_absent_and_stamps_mode(self):
        with _Harness() as h:
            result = await get_or_create_corporate_customer(_company(stripe_customer_id=None), LIVE_KEY)
        assert result == NEW_CUS
        assert h.updates[0][2] == {"stripe_customer_id": NEW_CUS, "stripe_customer_id_mode": "live"}
        # First-time create uses the plain key.
        assert h.create.call_args.kwargs["idempotency_key"] == f"cus-create-corp-{COMPANY_ID}"

    async def test_business_identifiers_are_sent(self):
        """A company's legal name and billing email identify a business, not a
        natural person — Stripe needs them for corporate invoices."""
        with _Harness() as h:
            await get_or_create_corporate_customer(_company(stripe_customer_id=None), LIVE_KEY)
        kwargs = h.create.call_args.kwargs
        assert kwargs["email"] == "billing@example.com"
        assert kwargs["name"] == "Northern Freight Ltd."

    async def test_mode_mismatch_reprovisions_without_a_stripe_lookup(self):
        with _Harness() as h:
            result = await get_or_create_corporate_customer(_company(stripe_customer_id_mode="test"), LIVE_KEY)
        assert result == NEW_CUS
        # Replacement key must differ from the first-time key, or a replay
        # inside Stripe's 24h window returns the customer we just retired.
        assert h.create.call_args.kwargs["idempotency_key"] == f"cus-reprov-corp-{COMPANY_ID}-{STALE_CUS}"
        assert h.create.call_args.kwargs["metadata"]["superseded_customer"] == STALE_CUS

    async def test_healthy_customer_is_untouched(self):
        create = MagicMock(side_effect=AssertionError("must not create"))
        with _Harness(create=create) as h:
            result = await get_or_create_corporate_customer(_company(stripe_customer_id_mode="live"), LIVE_KEY)
        assert result == STALE_CUS
        assert h.updates == []

    async def test_kill_switch_blocks_reprovision(self):
        with _Harness(flag=False) as h:
            with pytest.raises(CorporateCustomerUnavailable):
                await get_or_create_corporate_customer(_company(stripe_customer_id_mode="test"), LIVE_KEY)
        assert h.updates == []


class TestWithCorporateCustomerRepair:
    async def test_healthy_runs_op_once(self):
        calls: list[str] = []

        async def _op(cid: str):
            calls.append(cid)
            return "pi_ok"

        create = MagicMock(side_effect=AssertionError("must not create"))
        with _Harness(create=create) as h:
            cid, result = await with_corporate_customer_repair(
                _company(stripe_customer_id_mode="live"), LIVE_KEY, _op
            )
        assert (cid, result, calls) == (STALE_CUS, "pi_ok", [STALE_CUS])
        assert h.updates == []

    async def test_resource_missing_reprovisions_then_retries(self):
        calls: list[str] = []

        async def _op(cid: str):
            calls.append(cid)
            if cid == STALE_CUS:
                raise _resource_missing()
            return "pi_ok"

        with _Harness() as h:
            cid, result = await with_corporate_customer_repair(_company(), LIVE_KEY, _op)
        assert (cid, result) == (NEW_CUS, "pi_ok")
        assert calls == [STALE_CUS, NEW_CUS]
        assert h.updates[0][2]["stripe_customer_id"] == NEW_CUS

    async def test_retry_surfaces_no_payment_method_honestly(self):
        """The company's card lived on the customer that is gone, so the retry
        must report that plainly rather than an opaque Stripe error."""

        async def _op(cid: str):
            if cid == STALE_CUS:
                raise _resource_missing()
            raise ValueError("no_payment_method_on_file")

        with _Harness():
            with pytest.raises(ValueError, match="no_payment_method_on_file"):
                await with_corporate_customer_repair(_company(), LIVE_KEY, _op)

    @pytest.mark.parametrize(
        "exc",
        [
            stripe.error.AuthenticationError("Invalid API Key provided"),
            stripe.error.APIConnectionError("connection dropped"),
            stripe.error.RateLimitError("slow down"),
            stripe.error.CardError("declined", param=None, code="card_declined"),
        ],
    )
    async def test_ambiguous_error_propagates_without_repair(self, exc):
        async def _op(_cid: str):
            raise exc

        create = MagicMock(side_effect=AssertionError("must not create on an ambiguous error"))
        with _Harness(create=create) as h:
            with pytest.raises(type(exc)):
                await with_corporate_customer_repair(_company(), LIVE_KEY, _op)
        assert h.updates == []

    async def test_repair_is_attempted_only_once(self):
        calls: list[str] = []

        async def _op(cid: str):
            calls.append(cid)
            raise _resource_missing(cid)

        with _Harness():
            with pytest.raises(stripe.error.InvalidRequestError):
                await with_corporate_customer_repair(_company(), LIVE_KEY, _op)
        assert calls == [STALE_CUS, NEW_CUS]


class TestConcurrentRepairRace:
    """The conditional write can match zero rows: the auto-topup loop retired
    the customer, or another admin repaired it, between our read and our write.
    Returning our own id then would charge a customer the row does not point
    at and leak an orphan into Stripe."""

    async def test_defers_to_the_persisted_winner(self):
        other = "cus_won_the_race"
        with _Harness(persisted=other):
            result = await get_or_create_corporate_customer(_company(stripe_customer_id_mode="test"), LIVE_KEY)
        assert result == other

    async def test_raises_when_the_row_was_retired_out_from_under_us(self):
        with _Harness(persisted=None):
            with pytest.raises(CorporateCustomerUnavailable):
                await get_or_create_corporate_customer(_company(stripe_customer_id_mode="test"), LIVE_KEY)

    async def test_first_time_create_does_not_re_read(self):
        """Nothing to race with, so no extra round-trip."""
        with _Harness(persisted=None) as h:
            result = await get_or_create_corporate_customer(_company(stripe_customer_id=None), LIVE_KEY)
        assert result == NEW_CUS
        assert h.updates[0][1] == {"id": COMPANY_ID}


class TestSurfacesAsA503:
    def test_unavailable_carries_a_deliberate_status(self):
        """As a plain RuntimeError this reached the corporate routes uncaught
        and became an opaque 500, making the documented kill-switch rollback
        look like a crash."""
        exc = CorporateCustomerUnavailable("nope")
        assert exc.status_code == 503
