"""Rider Stripe customers carry the rider's email.

Customers used to be created with ``metadata.user_id`` only, so an email search
in the Stripe dashboard found nothing for a rider and every support / dispute /
refund lookup needed a Supabase round-trip first. The previous Spinr app mapped
customers by email; these tests pin that mapping back in place.

What is pinned:
  * both creation paths (first-time and re-provision) send ``email``
  * ``metadata.user_id`` stays the authoritative join key alongside it
  * name / phone / address are still NOT transferred (only email widened)
  * a profile email edit is pushed to the existing customer
  * the sync never raises out and never creates a customer
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

from backend.routes.payments import (
    _customer_identity_fields,
    _reprovision_stripe_customer,
    get_or_create_stripe_customer,
    sync_stripe_customer_email,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

USER_ID = "user_email_map_1"
EMAIL = "mkkreddy52@example.com"
STALE_CUS = "cus_testmode_old"
NEW_CUS = "cus_livemode_new"
# Concatenated so the repo's pre-commit secret scanner doesn't flag the fixture.
LIVE_KEY = "sk_live_" + "abc123"

# Fields Stripe would accept but that must stay inside the Canadian region.
WITHHELD = ("name", "phone", "address")


def _customer(cid: str = NEW_CUS, livemode: bool = True, email: str | None = None) -> MagicMock:
    cus = MagicMock()
    cus.id = cid
    cus.email = email
    cus.get = lambda k, default=None: {"livemode": livemode}.get(k, default)
    cus.livemode = livemode
    return cus


class _Harness:
    """Patches the DB + settings + Stripe surface ``payments.py`` reaches."""

    def __init__(self, users: list[dict], settings: dict | None = None, create=None, modify=None):
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
        self.modify = modify if modify is not None else MagicMock(return_value=_customer())
        self._patches = [
            patch("backend.routes.payments.db_supabase.get_user_by_id", side_effect=_get_user),
            patch("backend.routes.payments.db_supabase.update_one", side_effect=_update_one),
            patch("backend.routes.payments.get_app_settings", AsyncMock(return_value=cfg)),
            patch("backend.routes.payments.stripe.Customer.create", self.create),
            patch("backend.routes.payments.stripe.Customer.modify", self.modify),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


def _user(**extra) -> dict:
    row = {"id": USER_ID, "email": EMAIL, "stripe_customer_id": None}
    row.update(extra)
    return row


class TestIdentityFields:
    def test_email_is_included(self):
        assert _customer_identity_fields({"email": EMAIL}) == {"email": EMAIL}

    def test_email_is_trimmed(self):
        assert _customer_identity_fields({"email": f"  {EMAIL} "}) == {"email": EMAIL}

    @pytest.mark.parametrize("user", [None, {}, {"email": None}, {"email": ""}, {"email": "   "}])
    def test_absent_email_is_omitted_not_sent_as_none(self, user):
        """A rider mid-signup still needs a working customer — and Stripe must
        not be handed an explicit null for a field we simply don't have."""
        assert _customer_identity_fields(user) == {}

    def test_name_and_phone_are_never_transferred(self):
        fields = _customer_identity_fields(
            {"email": EMAIL, "first_name": "Nighil", "last_name": "Kumar", "phone": "+13065550100"}
        )
        assert fields == {"email": EMAIL}


class TestFirstTimeCreation:
    async def test_create_sends_email_and_user_id(self):
        with _Harness([_user(), _user(stripe_customer_id=NEW_CUS)]) as h:
            result = await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert result == NEW_CUS
        kwargs = h.create.call_args.kwargs
        assert kwargs["email"] == EMAIL
        # metadata.user_id remains the authoritative join key: it is immutable,
        # email is not, and Stripe does not enforce email uniqueness.
        assert kwargs["metadata"] == {"user_id": USER_ID}

    async def test_create_withholds_name_phone_address(self):
        with _Harness([_user(), _user(stripe_customer_id=NEW_CUS)]) as h:
            await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        kwargs = h.create.call_args.kwargs
        for field in WITHHELD:
            assert field not in kwargs, f"{field} must not be sent to Stripe"

    async def test_rider_without_email_still_gets_a_customer(self):
        """Profile incomplete: creation must not fail for want of an email."""
        with _Harness([_user(email=None), _user(email=None, stripe_customer_id=NEW_CUS)]) as h:
            result = await get_or_create_stripe_customer(USER_ID, LIVE_KEY)
        assert result == NEW_CUS
        assert "email" not in h.create.call_args.kwargs


class TestReprovisionCreation:
    """A replacement customer must carry the same identity as a first-time one,
    or the test→live cutover silently drops email findability for every rider
    it repairs."""

    async def test_reprovision_sends_email(self):
        with _Harness([_user(stripe_customer_id=STALE_CUS), _user(stripe_customer_id=NEW_CUS)]) as h:
            result = await _reprovision_stripe_customer(USER_ID, STALE_CUS, LIVE_KEY, reason="mode_mismatch")
        assert result == NEW_CUS
        kwargs = h.create.call_args.kwargs
        assert kwargs["email"] == EMAIL
        assert kwargs["metadata"]["user_id"] == USER_ID
        assert kwargs["metadata"]["superseded_customer"] == STALE_CUS

    async def test_reprovision_withholds_name_phone_address(self):
        with _Harness([_user(stripe_customer_id=STALE_CUS), _user(stripe_customer_id=NEW_CUS)]) as h:
            await _reprovision_stripe_customer(USER_ID, STALE_CUS, LIVE_KEY, reason="mode_mismatch")
        for field in WITHHELD:
            assert field not in h.create.call_args.kwargs


class TestEmailChangeSync:
    async def test_existing_customer_is_updated(self):
        with _Harness([_user(stripe_customer_id=STALE_CUS)]) as h:
            assert await sync_stripe_customer_email(USER_ID) is True
        h.modify.assert_called_once()
        assert h.modify.call_args.args[0] == STALE_CUS
        assert h.modify.call_args.kwargs["email"] == EMAIL

    async def test_no_customer_yet_is_a_noop(self):
        """Creation carries the email itself — the sync must not mint one."""
        with _Harness([_user(stripe_customer_id=None)]) as h:
            assert await sync_stripe_customer_email(USER_ID) is False
        h.modify.assert_not_called()
        h.create.assert_not_called()

    async def test_rider_without_email_is_a_noop(self):
        """Never blank out an address already on the Stripe customer."""
        with _Harness([_user(email="", stripe_customer_id=STALE_CUS)]) as h:
            assert await sync_stripe_customer_email(USER_ID) is False
        h.modify.assert_not_called()

    async def test_unconfigured_stripe_is_a_noop(self):
        with _Harness([_user(stripe_customer_id=STALE_CUS)], settings={"stripe_secret_key": ""}) as h:
            assert await sync_stripe_customer_email(USER_ID) is False
        h.modify.assert_not_called()

    async def test_stripe_failure_never_raises_out(self):
        """The profile write is already committed; a Stripe outage must not
        fail a profile edit. The backfill script is the reconciliation net."""
        boom = MagicMock(side_effect=stripe.error.APIConnectionError("stripe down"))
        with _Harness([_user(stripe_customer_id=STALE_CUS)], modify=boom):
            assert await sync_stripe_customer_email(USER_ID) is False

    async def test_stranded_customer_is_not_reprovisioned_by_the_sync(self):
        """Re-provisioning rewrites users.stripe_customer_id and clears the
        saved default card — not a decision for a background directory sync."""
        missing = MagicMock(
            side_effect=stripe.error.InvalidRequestError(
                f"No such customer: '{STALE_CUS}'", param=None, code="resource_missing"
            )
        )
        with _Harness([_user(stripe_customer_id=STALE_CUS)], modify=missing) as h:
            assert await sync_stripe_customer_email(USER_ID) is False
        h.create.assert_not_called()
        assert h.updates == []

    async def test_email_is_never_logged(self, caplog):
        """CLAUDE.md: email addresses must never appear in logs."""
        with _Harness([_user(stripe_customer_id=STALE_CUS)]):
            with caplog.at_level("INFO"):
                await sync_stripe_customer_email(USER_ID)
        assert EMAIL not in caplog.text
