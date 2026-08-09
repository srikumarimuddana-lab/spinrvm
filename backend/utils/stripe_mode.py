"""Stripe mode (test vs live) awareness for stored Stripe identities.

Stripe object IDs are **mode-scoped**. A ``cus_…`` minted with an
``sk_test_…`` key does not exist under ``sk_live_…``; a ``acct_…`` from a
test-mode platform is invisible to the live platform. The ID itself carries
no evidence of its mode — the ``cus_`` / ``acct_`` prefixes are identical in
both — so the only two sources of truth are:

1. the **key** in use (``sk_live_`` vs ``sk_test_`` prefix), and
2. the ``livemode`` flag on an object Stripe actually returned.

Spinr keeps ``stripe_secret_key`` in the ``app_settings`` DB table so it can
be rotated without a redeploy (see CLAUDE.md). That flexibility means the key
can flip test→live *underneath* rows that already carry test-mode IDs, which
strands every one of them: ``users.stripe_customer_id`` (rider saved cards),
``drivers.stripe_account_id`` (payout destination), and
``corporate_accounts.stripe_customer_id`` (billing) all start failing
``resource_missing`` on the next Stripe call.

This module is the shared vocabulary for detecting that, used by the rider
card path, the driver payout path, and the admin Stripe-mode audit.

Nothing here talks to Stripe or to the database — it is pure classification,
so it is cheap enough to call on a hot path and exhaustively unit-testable.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import stripe

logger = logging.getLogger(__name__)

LIVE = "live"
TEST = "test"

# Secret ("sk_") and restricted ("rk_") keys both carry the mode in the prefix.
# Publishable keys ("pk_") never reach this module — they cannot make the calls
# whose failures we are classifying.
_LIVE_PREFIXES = ("sk_live_", "rk_live_")
_TEST_PREFIXES = ("sk_test_", "rk_test_")

# Substrings Stripe uses when the object genuinely is not on this key. Belt and
# braces behind the structured `code` check below: `code` is the contract, but
# a few older/edge responses only carry the message.
_MISSING_MESSAGE_HINTS = (
    "no such customer",
    "no such account",
    # Stripe spells these inconsistently across endpoints — snake_case in some
    # messages, CamelCase (which lowercases to one word) in others.
    "no such payment_method",
    "no such paymentmethod",
    "no such setupintent",
    "no such setup_intent",
    "no such payment_intent",
    "no such paymentintent",
    "similar object exists in test mode",
    "similar object exists in live mode",
)


def key_mode(stripe_secret: Optional[str]) -> Optional[str]:
    """Return ``"live"`` / ``"test"`` for a Stripe secret key, else ``None``.

    ``None`` means "cannot tell" — an empty key (Stripe unconfigured, the
    demo-mode path) or an unrecognised prefix. Callers must treat ``None`` as
    *no information*, never as a default mode: guessing here would let a
    misread key trigger re-provisioning of perfectly good live identities.
    """
    if not stripe_secret:
        return None
    if stripe_secret.startswith(_LIVE_PREFIXES):
        return LIVE
    if stripe_secret.startswith(_TEST_PREFIXES):
        return TEST
    return None


def object_mode(obj: Any) -> Optional[str]:
    """Return the mode of a Stripe object Stripe actually returned.

    Reads the ``livemode`` boolean that every Stripe object carries. Used to
    stamp a row's ``*_mode`` column from evidence rather than inference after
    a successful retrieve. Returns ``None`` when the attribute is absent or is
    not a real boolean, so a changed/partial payload degrades to "unknown"
    instead of a wrong stamp — callers fall back to the key's mode.

    The ``isinstance(bool)`` check is deliberate rather than a truthiness
    test: Stripe always sends a JSON boolean here, so anything else is a
    payload we do not understand, and guessing ``live`` from a truthy
    placeholder would stamp a row with a mode we never actually observed.
    """
    if obj is None:
        return None
    live = obj.get("livemode") if isinstance(obj, dict) else getattr(obj, "livemode", None)
    if not isinstance(live, bool):
        return None
    return LIVE if live else TEST


def is_missing_on_key(exc: BaseException) -> bool:
    """True when Stripe said *this object* does not exist under the key we used.

    This is the trigger for re-provisioning a stored identity, so its
    precision matters more than its recall — a false positive orphans a real
    live customer (and the rider's saved cards with it). The rule is therefore
    narrow: only errors that are evidence **about the object** count.

    Counted:
      - ``InvalidRequestError`` with ``code == "resource_missing"`` — Stripe's
        explicit "no such object" contract, including the "a similar object
        exists in test mode" variant that names this exact situation.
      - ``PermissionError`` — a Connect ``acct_…`` that belongs to a different
        platform. Unreachable for us in the same way, and equally permanent.

    Deliberately NOT counted, because they are evidence about *us* or about
    the network, not about the object — re-provisioning on any of these would
    destroy good data during a routine outage or a mis-pasted key:
      - ``AuthenticationError`` — our key is invalid/revoked. Every object
        looks missing; none of them are.
      - ``APIConnectionError`` / ``RateLimitError`` / ``APIError`` — transient.
      - ``CardError`` — the card failed, the customer exists.
    """
    if isinstance(exc, stripe.error.AuthenticationError):
        return False
    if isinstance(exc, stripe.error.PermissionError):
        return True
    if not isinstance(exc, stripe.error.InvalidRequestError):
        return False
    if getattr(exc, "code", None) == "resource_missing":
        return True
    message = str(getattr(exc, "user_message", None) or exc).lower()
    return any(hint in message for hint in _MISSING_MESSAGE_HINTS)


def stale_by_mode(stored_mode: Optional[str], current_key_mode: Optional[str]) -> bool:
    """True only when both modes are known AND they disagree.

    Lets the hot path skip a doomed Stripe round-trip once a row has been
    stamped. An unstamped row (``stored_mode is None`` — every row predating
    migration 286) returns False here by design: absence of a stamp is not
    evidence of staleness, and those rows are resolved the other way, by
    catching :func:`is_missing_on_key` on the call that actually fails.
    """
    if stored_mode is None or current_key_mode is None:
        return False
    return stored_mode != current_key_mode
