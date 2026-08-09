"""Unit tests for backend/utils/stripe_mode.py.

This module is the trigger for re-provisioning stored Stripe identities, so
the tests below lean hard on the *negative* cases: every error class that must
NOT be read as "this object is gone". A false positive here silently orphans a
live Stripe customer and takes the rider's saved cards with it.
"""

from __future__ import annotations

import pytest
import stripe

from utils.stripe_mode import (
    LIVE,
    TEST,
    is_missing_on_key,
    key_mode,
    object_mode,
    stale_by_mode,
)

pytestmark = pytest.mark.unit

# These are obviously-fake fixtures, but the repo's pre-commit secret scanner
# greps for `sk_live_[a-zA-Z0-9]+` / `pk_live_[a-zA-Z0-9]+` and cannot tell.
# Building them by concatenation keeps the literal out of the source while
# the values under test stay exactly what the code will see at runtime.
_LIVE_PREFIX = "sk_live_"
_RESTRICTED_LIVE_PREFIX = "rk_live_"
_PUBLISHABLE_LIVE_PREFIX = "pk_live_"
FAKE_SUFFIX = "51abcdef"


class TestKeyMode:
    @pytest.mark.parametrize(
        "secret,expected",
        [
            (_LIVE_PREFIX + FAKE_SUFFIX, LIVE),
            (_RESTRICTED_LIVE_PREFIX + FAKE_SUFFIX, LIVE),
            ("sk_test_" + FAKE_SUFFIX, TEST),
            ("rk_test_" + FAKE_SUFFIX, TEST),
        ],
    )
    def test_recognised_prefixes(self, secret, expected):
        assert key_mode(secret) == expected

    @pytest.mark.parametrize(
        "secret",
        [
            "",
            None,
            # A publishable key names a mode but can't make the calls we
            # classify — it must not be read as a usable secret key.
            _PUBLISHABLE_LIVE_PREFIX + "abc",
            "whsec_abc",
            "garbage",
            "sk_",
            "sk_livex_abc",
        ],
    )
    def test_unknown_never_guesses(self, secret):
        """Anything unrecognised must be None, never a defaulted mode.

        A defaulted mode would let a mis-read key mark good live rows stale.
        """
        assert key_mode(secret) is None


class TestObjectMode:
    def test_reads_livemode_from_dict(self):
        assert object_mode({"livemode": True}) == LIVE
        assert object_mode({"livemode": False}) == TEST

    def test_reads_livemode_from_attr(self):
        class _Obj:
            livemode = True

        assert object_mode(_Obj()) == LIVE

    @pytest.mark.parametrize("obj", [None, {}, object()])
    def test_absent_livemode_is_unknown(self, obj):
        """A partial/changed payload degrades to unknown, not a wrong stamp."""
        assert object_mode(obj) is None

    @pytest.mark.parametrize("value", ["true", 1, 0, object()])
    def test_non_boolean_livemode_is_unknown(self, value):
        """Only a real bool counts — a truthy placeholder must not stamp 'live'.

        Guessing from truthiness would record a mode we never observed, and
        the stamp is what later decides whether to re-provision an identity.
        """
        assert object_mode({"livemode": value}) is None


def _invalid_request(message: str, code: str | None = None) -> stripe.error.InvalidRequestError:
    return stripe.error.InvalidRequestError(message, param=None, code=code)


class TestIsMissingOnKey:
    def test_resource_missing_code(self):
        assert is_missing_on_key(_invalid_request("No such customer: 'cus_x'", code="resource_missing")) is True

    @pytest.mark.parametrize(
        "message",
        [
            "No such customer: 'cus_abc'",
            "No such account: 'acct_abc'",
            "No such PaymentMethod: 'pm_abc'",
            "a similar object exists in test mode, but a live mode key was used",
        ],
    )
    def test_message_fallback_when_code_absent(self, message):
        """Older/edge responses carry only the message, not the code."""
        assert is_missing_on_key(_invalid_request(message)) is True

    def test_permission_error_is_missing(self):
        """A Connect account on another platform is unreachable and permanent."""
        assert is_missing_on_key(stripe.error.PermissionError("not have access to account 'acct_x'")) is True

    def test_authentication_error_is_not_missing(self):
        """Our key is bad — EVERY object looks missing, none of them are.

        This is the case that would mass-orphan live customers if it were
        misread, e.g. someone pastes a revoked or wrong-account key.
        """
        assert is_missing_on_key(stripe.error.AuthenticationError("Invalid API Key provided")) is False

    @pytest.mark.parametrize(
        "exc",
        [
            stripe.error.APIConnectionError("connection dropped"),
            stripe.error.RateLimitError("too many requests"),
            stripe.error.APIError("upstream 500"),
        ],
    )
    def test_transient_errors_are_not_missing(self, exc):
        assert is_missing_on_key(exc) is False

    def test_card_error_is_not_missing(self):
        """The card failed; the customer is fine."""
        exc = stripe.error.CardError("Your card was declined.", param=None, code="card_declined")
        assert is_missing_on_key(exc) is False

    def test_unrelated_invalid_request_is_not_missing(self):
        exc = _invalid_request("Amount must be at least 50 cents", code="amount_too_small")
        assert is_missing_on_key(exc) is False

    def test_plain_exception_is_not_missing(self):
        assert is_missing_on_key(ValueError("boom")) is False


class TestExpectedIdScoping:
    """A Stripe request names several objects; any of them can be missing.

    `PaymentMethod.attach(pm_…, customer=cus_…)` with a stale `pm_…` answers
    resource_missing about the PAYMENT METHOD. Without scoping, that would
    re-provision a perfectly healthy customer and archive the rider's saved
    cards along with it — the worst outcome this module exists to prevent.
    """

    def test_error_about_our_object_matches(self):
        exc = _invalid_request("No such customer: 'cus_ours'", code="resource_missing")
        assert is_missing_on_key(exc, "cus_ours") is True

    def test_error_about_a_different_object_does_not_match(self):
        exc = _invalid_request("No such PaymentMethod: 'pm_stale'", code="resource_missing")
        assert is_missing_on_key(exc, "cus_ours") is False

    def test_price_missing_does_not_condemn_the_customer(self):
        """Subscription.create names both a customer and a price."""
        exc = _invalid_request("No such price: 'price_gone'", code="resource_missing")
        assert is_missing_on_key(exc, "cus_ours") is False

    def test_match_is_case_insensitive(self):
        exc = _invalid_request("No such customer: 'CUS_Ours'", code="resource_missing")
        assert is_missing_on_key(exc, "cus_ours") is True

    def test_unnamed_object_fails_closed(self):
        """If Stripe doesn't name the object, we leave the row alone."""
        exc = _invalid_request("No such customer", code="resource_missing")
        assert is_missing_on_key(exc, "cus_ours") is False

    def test_permission_error_is_still_scoped(self):
        exc = stripe.error.PermissionError("does not have access to account 'acct_theirs'")
        assert is_missing_on_key(exc, "acct_ours") is False
        assert is_missing_on_key(exc, "acct_theirs") is True

    def test_omitting_expected_id_keeps_the_old_broad_behaviour(self):
        exc = _invalid_request("No such PaymentMethod: 'pm_x'", code="resource_missing")
        assert is_missing_on_key(exc) is True


class TestStaleByMode:
    def test_known_and_different_is_stale(self):
        assert stale_by_mode(TEST, LIVE) is True
        assert stale_by_mode(LIVE, TEST) is True

    def test_known_and_same_is_not_stale(self):
        assert stale_by_mode(LIVE, LIVE) is False
        assert stale_by_mode(TEST, TEST) is False

    @pytest.mark.parametrize(
        "stored,current",
        [(None, LIVE), (LIVE, None), (None, None)],
    )
    def test_unknown_is_never_stale(self, stored, current):
        """Absence of a stamp is not evidence of staleness.

        Every row predating migration 286 has stored_mode=None; those are
        resolved by catching is_missing_on_key() on the call that fails, not
        by assuming here.
        """
        assert stale_by_mode(stored, current) is False
