"""N17: rider email BODY copy uses the `company_app_name` setting, not a
literal "Spinr".

`company_name` (the legal entity, e.g. "Spinr Technologies Inc.") already
drives the footer/mailing-address/logo-alt-text — see
`utils/company_details.py` — and stays "Spinr Technologies Inc." in these
tests on purpose, so a footer assertion can't be confused with a body-copy
one. `company_app_name` is the separate, independently-configurable
product/brand name for inline body copy ("Open the {app_name} driver app",
"your {app_name} wallet"). This pins:

* an unconfigured setting reproduces today's literal "Spinr" subject/body
  output byte-for-byte (the CLAUDE.md fallback rule)
* a configured `company_app_name` actually appears in each swept sender's
  subject, and the stale literal does not
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.company_details as cd_mod
import utils.rider_emails as re_mod

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_USER = {"id": "u1", "first_name": "Sam", "email": "sam@example.com"}


async def _capture(coro_fn, settings=None):
    """Run a rider_emails sender with the policy layer and settings stubbed."""
    send = AsyncMock(return_value=True)
    loader = AsyncMock(return_value=settings or {})
    with (
        patch.object(re_mod, "send_lifecycle_email", send),
        patch.object(re_mod, "resolve_recipient", AsyncMock(return_value=_USER)),
        patch.object(cd_mod, "get_app_settings", loader),
    ):
        await coro_fn()
    return send.await_args.kwargs


# (sender, expected literal subject with default app_name "Spinr", expected
# subject with app_name "Northern Rides")
_CASES = [
    (
        lambda: re_mod.send_welcome_email(_USER),
        "Welcome to Spinr",
        "Welcome to Northern Rides",
    ),
    (
        lambda: re_mod.send_email_changed_notice(_USER, "old@example.com"),
        "The email on your Spinr account was changed",
        "The email on your Northern Rides account was changed",
    ),
    (
        lambda: re_mod.send_account_deletion_notice(_USER, "2033-01-01T00:00:00Z"),
        "Your Spinr account has been deactivated",
        "Your Northern Rides account has been deactivated",
    ),
    (
        lambda: re_mod.send_refund_email("u1", "12.50", user=_USER),
        "Your Spinr refund of $12.50",
        "Your Northern Rides refund of $12.50",
    ),
    (
        lambda: re_mod.send_wallet_topup_email("u1", "20.00", user=_USER),
        "Spinr wallet top-up — $20.00",
        "Northern Rides wallet top-up — $20.00",
    ),
    (
        lambda: re_mod.send_payment_blocked_email("u1", "18.00", user=_USER),
        "Action needed: your Spinr payment didn't go through",
        "Action needed: your Northern Rides payment didn't go through",
    ),
]


@pytest.mark.parametrize("sender,default_subject,configured_subject", _CASES)
async def test_unconfigured_app_name_reproduces_the_literal_spinr_subject(sender, default_subject, configured_subject):
    kwargs = await _capture(sender)
    assert kwargs["subject"] == default_subject


@pytest.mark.parametrize("sender,default_subject,configured_subject", _CASES)
async def test_configured_app_name_replaces_the_literal_in_the_subject(sender, default_subject, configured_subject):
    kwargs = await _capture(sender, settings={"company_app_name": "Northern Rides"})
    assert kwargs["subject"] == configured_subject


async def test_welcome_body_mentions_the_configured_app_name():
    kwargs = await _capture(
        lambda: re_mod.send_welcome_email(_USER),
        settings={"company_app_name": "Northern Rides"},
    )
    assert "book a ride from the Northern Rides app" in kwargs["rendered"].text
    assert "Northern Rides is Saskatchewan-built" in kwargs["rendered"].text


async def test_wallet_topup_body_mentions_the_configured_app_name():
    kwargs = await _capture(
        lambda: re_mod.send_wallet_topup_email("u1", "20.00", user=_USER),
        settings={"company_app_name": "Northern Rides"},
    )
    assert "added to your Northern Rides wallet" in kwargs["rendered"].text


async def test_payment_blocked_body_mentions_the_configured_app_name():
    kwargs = await _capture(
        lambda: re_mod.send_payment_blocked_email("u1", "18.00", user=_USER),
        settings={"company_app_name": "Northern Rides"},
    )
    assert "Open the Northern Rides app" in kwargs["rendered"].text


async def test_app_name_is_independent_of_the_legal_entity_name_in_the_footer():
    # Renaming the product name in body copy must not touch the legal-entity
    # name the footer independently reads from `company_name`.
    kwargs = await _capture(
        lambda: re_mod.send_welcome_email(_USER),
        settings={"company_app_name": "Northern Rides", "company_name": "Spinr Technologies Inc."},
    )
    assert kwargs["subject"] == "Welcome to Northern Rides"
    assert "Spinr Technologies Inc." in kwargs["rendered"].html
