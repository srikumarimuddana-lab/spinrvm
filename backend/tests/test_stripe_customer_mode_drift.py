"""Rider Stripe-customer repair across a test → live key rotation.

`stripe_secret_key` lives in app_settings and can be rotated without a
redeploy. Rotating it ACROSS modes strands every stored
`users.stripe_customer_id`, because Stripe scopes object IDs per mode.

Two repair routes are pinned here:

  * `get_or_create_stripe_customer` — a row whose stamped mode disagrees with
    the running key is repaired from the stamp alone, with NO Stripe call.
  * `with_customer_repair` — an *unstamped* stale row (everything predating
    migration 286) is repaired when the real call answers `resource_missing`,
    then the operation is retried against the fresh customer.

The negative cases matter most: a bad key makes every customer look missing,
so repairing on an auth or transient error would orphan live customers at
scale and take their saved cards with them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi import HTTPException

from backend.routes.payments import get_or_create_stripe_customer, with_customer_repair

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

USER_ID = "user_drift_1"
STALE_CUS = "cus_testmode_old"
NEW_CUS = "cus_livemode_new"
# Concatenated so the repo's pre-commit secret scanner (which greps for
# `sk_live_[a-zA-Z0-9]+`) doesn't flag this obviously-fake fixture.
LIVE_KEY = "sk_live_" + "abc123"


def _user(**extra) -> dict:
    row = {
        "id": USER_ID,
        "stripe_customer_id": STALE_CUS,
        "stripe_customer_id_mode": None,
        "default_payment_method": "pm_testmode_old",
    }
    row.update(extra)
    return row


def _customer(cid: str = NEW_CUS, livemode: bool = True) -> MagicMock:
    cus = MagicMock()
    cus.id = cid
    cus.get = lambda k, default=None: {"livemode": livemode}.get(k, default)
    cus.livemode = livemode
    return cus


def _resource_missing(obj_id: str = STALE_CUS) -> stripe.error.InvalidRequestError:
    return stripe.error.InvalidRequestError(f"No such customer: '{obj_id}'", param=None, code="resource_missing")


class _Harness:
    """Patches the DB + settings + Stripe surface `payments.py` reaches."""

    def __init__(self, users: list[dict], settings: dict | None = None, create=None):
        self.users = list(users)
        self.updates: list[tuple] = []
        cfg = {"stripe_secret_key": LIVE_KEY}
        cfg.update(settings or {})

        async def _get_user(_uid):
            return self.users.pop(0) if len(self.users) > 1 else self.users[0]

        async def _update_one(table, filters, update):
            self.updates.append((table, filters, update))
            return None

        self.create = create if create is not None else MagicMock(return_value=_customer())
        self._patches = [
            patch("backend.routes.payments.db_supabase.get_user_by_id", side_effect=_get_user),
            patch("backend.routes.payments.db_supabase.update_one", side_effect=_update_one),
            patch("backend.routes.payments.get_app_settings", AsyncMock(return_value=cfg)),
            patch("backend.routes.payments.stripe.Customer.create", self.create),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TestStampedRowMismatch:
    """The cheap path: the stamp alone proves the id is unusable."""

    async def test_test_stamped_row_under_live_key_is_reprovisioned(self):
        with _Harness([_user(stripe_customer_id_mode="test"), _user(stripe_customer_id=NEW_CUS)]) as h:
            result = await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert result == NEW_CUS
        table, filters, update = h.updates[0]
        assert table == "users"
        # Conditional on the stale value so a concurrent healer is not clobbered.
        assert filters == {"id": USER_ID, "stripe_customer_id": STALE_CUS}
        assert update["stripe_customer_id"] == NEW_CUS
        assert update["stripe_customer_id_mode"] == "live"
        assert update["stripe_customer_id_superseded"] == STALE_CUS

    async def test_reprovision_clears_default_payment_method(self):
        """A pm_ is scoped to its customer; leaving it would break settlement."""
        with _Harness([_user(stripe_customer_id_mode="test"), _user(stripe_customer_id=NEW_CUS)]) as h:
            await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert h.updates[0][2]["default_payment_method"] is None

    async def test_matching_stamp_is_left_alone(self):
        """Already live-stamped: no Stripe call, no write, same id back."""
        create = MagicMock(side_effect=AssertionError("must not create"))
        with _Harness([_user(stripe_customer_id_mode="live")], create=create) as h:
            result = await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert result == STALE_CUS
        assert h.updates == []

    async def test_unstamped_row_makes_no_extra_call(self):
        """Unstamped rows are NOT verified eagerly — that would cost a Stripe
        round-trip on every hot path. They heal via with_customer_repair."""
        create = MagicMock(side_effect=AssertionError("must not create"))
        with _Harness([_user()], create=create) as h:
            result = await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert result == STALE_CUS
        assert h.updates == []


class TestWithCustomerRepair:
    async def test_healthy_customer_runs_op_once_and_writes_nothing(self):
        calls: list[str] = []

        async def _op(cid: str):
            calls.append(cid)
            return "ok"

        create = MagicMock(side_effect=AssertionError("must not create"))
        with _Harness([_user(stripe_customer_id_mode="live")], create=create) as h:
            cid, result = await with_customer_repair(USER_ID, LIVE_KEY, _op)
        assert (cid, result, calls) == (STALE_CUS, "ok", [STALE_CUS])
        assert h.updates == []

    async def test_resource_missing_repairs_then_retries_against_new_customer(self):
        calls: list[str] = []

        async def _op(cid: str):
            calls.append(cid)
            if cid == STALE_CUS:
                raise _resource_missing()
            return "ok"

        with _Harness([_user(), _user(stripe_customer_id=NEW_CUS)]) as h:
            cid, result = await with_customer_repair(USER_ID, LIVE_KEY, _op)
        assert (cid, result) == (NEW_CUS, "ok")
        assert calls == [STALE_CUS, NEW_CUS]
        assert h.updates[0][2]["stripe_customer_id_superseded"] == STALE_CUS

    async def test_repair_is_attempted_only_once(self):
        """If the fresh customer also 404s, the key/account is wrong — surface it."""
        calls: list[str] = []

        async def _op(cid: str):
            calls.append(cid)
            raise _resource_missing(cid)

        with _Harness([_user(), _user(stripe_customer_id=NEW_CUS)]):
            with pytest.raises(stripe.error.InvalidRequestError):
                await with_customer_repair(USER_ID, LIVE_KEY, _op)
        assert calls == [STALE_CUS, NEW_CUS]

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
        """The load-bearing safety property.

        A revoked or wrong-account key makes EVERY customer look missing.
        Repairing on that would orphan real live customers en masse.
        """

        async def _op(_cid: str):
            raise exc

        create = MagicMock(side_effect=AssertionError("must not create on an ambiguous error"))
        with _Harness([_user()], create=create) as h:
            with pytest.raises(type(exc)):
                await with_customer_repair(USER_ID, LIVE_KEY, _op)
        assert h.updates == []


class TestKillSwitch:
    async def test_flag_off_blocks_repair_and_surfaces_503(self):
        create = MagicMock(side_effect=AssertionError("kill switch must prevent the create"))
        with _Harness(
            [_user(stripe_customer_id_mode="test")],
            settings={"stripe_reprovision_stale_ids": False},
            create=create,
        ) as h:
            with pytest.raises(HTTPException) as ei:
                await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert ei.value.status_code == 503
        assert h.updates == []


class TestFirstTimeCreate:
    async def test_new_customer_is_stamped_with_its_livemode(self):
        with _Harness([_user(stripe_customer_id=None), _user(stripe_customer_id=NEW_CUS)]) as h:
            result = await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert result == NEW_CUS
        assert h.updates[0][2] == {"stripe_customer_id": NEW_CUS, "stripe_customer_id_mode": "live"}

    async def test_unpersisted_customer_raises_rather_than_returning_falsy(self):
        """Callers pass this straight to Stripe, where `customer=None` silently
        creates an unattached object instead of erroring — so a lost write must
        surface, not slip through."""
        with _Harness([_user(stripe_customer_id=None), _user(stripe_customer_id=None)]):
            with pytest.raises(HTTPException) as ei:
                await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert ei.value.status_code == 502

    async def test_stamp_follows_the_object_not_the_key(self):
        """Evidence over inference: the stamp comes from Stripe's livemode."""
        with _Harness(
            [_user(stripe_customer_id=None), _user(stripe_customer_id=NEW_CUS)],
            create=MagicMock(return_value=_customer(livemode=False)),
        ) as h:
            await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert h.updates[0][2]["stripe_customer_id_mode"] == "test"
