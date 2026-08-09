"""Rider money emails: refund, wallet top-up, no-show fee, payment blocked.

Every one of these is money moving on the rider's card or wallet, and every one
of them was push-only or (for payment-blocked) not sent to the rider at all.
A push scrolls out of the tray; a charge needs a record the rider can find
again and dispute against.

The amount assertions matter as much as the routing ones — a receipt that
misstates the figure is worse than no receipt.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

import utils.rider_emails as re_mod

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_USER = {"id": "u1", "first_name": "Sam", "email": "sam@example.com"}
_RIDE = {"id": "abcdef1234", "ride_code": "SPN-4417"}


async def _capture(coro_fn):
    send = AsyncMock(return_value=True)
    with (
        patch.object(re_mod, "send_lifecycle_email", send),
        patch.object(re_mod, "resolve_recipient", AsyncMock(return_value=_USER)),
    ):
        await coro_fn()
    return send.await_args.kwargs


# --- Amounts ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("12.5"), "12.50"),
        (12.5, "12.50"),
        ("12.005", "12.01"),  # ROUND_HALF_UP, not banker's rounding
        (0, "0.00"),
        (None, "0.00"),
        ("", "0.00"),
        (Decimal("1234.567"), "1234.57"),
    ],
)
def test_money_formatting(value, expected):
    assert re_mod._money(value) == expected


def test_money_never_goes_through_float_arithmetic():
    # 0.1 + 0.2 as floats is 0.30000000000000004. Whatever reaches us, the
    # rider must see the decimal value, not a float artifact.
    assert re_mod._money(Decimal("0.1") + Decimal("0.2")) == "0.30"


async def test_refund_amount_appears_in_subject_and_body():
    kwargs = await _capture(lambda: re_mod.send_refund_email("u1", Decimal("23.45"), ride=_RIDE))
    assert "23.45" in kwargs["subject"]
    assert "23.45" in kwargs["rendered"].text


async def test_no_show_fee_amount_is_formatted_to_two_places():
    kwargs = await _capture(lambda: re_mod.send_no_show_fee_email("u1", Decimal("7.5"), ride=_RIDE))
    assert "$7.50" in kwargs["rendered"].text
    assert "7.5 " not in kwargs["rendered"].text


# --- Ride reference --------------------------------------------------------


async def test_ride_code_is_used_when_present():
    kwargs = await _capture(lambda: re_mod.send_refund_email("u1", 10, ride=_RIDE))
    assert "SPN-4417" in kwargs["rendered"].text


async def test_falls_back_to_a_short_id_when_there_is_no_ride_code():
    kwargs = await _capture(lambda: re_mod.send_refund_email("u1", 10, ride={"id": "abcdef1234"}))
    assert "ABCDEF12" in kwargs["rendered"].text


async def test_reads_cleanly_with_no_ride_at_all():
    # The refund webhook can fire for a charge with no ride attached.
    kwargs = await _capture(lambda: re_mod.send_refund_email("u1", 10))
    body = kwargs["rendered"].text
    assert "for ride" not in body
    assert "$10.00" in body


# --- Content ---------------------------------------------------------------


async def test_refund_sets_expectations_about_bank_timing():
    kwargs = await _capture(lambda: re_mod.send_refund_email("u1", 10, ride=_RIDE))
    assert "business days" in kwargs["rendered"].text


async def test_wallet_topup_reports_the_new_balance_when_known():
    kwargs = await _capture(lambda: re_mod.send_wallet_topup_email("u1", 25, 88.5))
    assert "$88.50" in kwargs["rendered"].text


async def test_wallet_topup_omits_the_balance_line_when_unknown():
    kwargs = await _capture(lambda: re_mod.send_wallet_topup_email("u1", 25, None))
    assert "balance is now" not in kwargs["rendered"].text


async def test_payment_blocked_says_booking_is_blocked_and_how_to_fix_it():
    kwargs = await _capture(lambda: re_mod.send_payment_blocked_email("u1", 31.2, ride=_RIDE))
    body = kwargs["rendered"].text
    assert "won't be able to book" in body
    assert "payment method" in body


async def test_no_show_explains_why_the_fee_exists():
    kwargs = await _capture(lambda: re_mod.send_no_show_fee_email("u1", 8, ride=_RIDE))
    assert "goes to the driver" in kwargs["rendered"].text


async def test_disputable_charges_carry_a_support_route():
    for fn in (
        lambda: re_mod.send_no_show_fee_email("u1", 8, ride=_RIDE),
        lambda: re_mod.send_refund_email("u1", 8, ride=_RIDE),
        lambda: re_mod.send_wallet_topup_email("u1", 8),
    ):
        assert "support@spinr.ca" in (await _capture(fn))["rendered"].text


# --- Contract --------------------------------------------------------------


async def test_all_money_emails_are_transactional():
    from utils.email_notifications import EmailClass

    for fn in (
        lambda: re_mod.send_refund_email("u1", 1),
        lambda: re_mod.send_wallet_topup_email("u1", 1),
        lambda: re_mod.send_no_show_fee_email("u1", 1),
        lambda: re_mod.send_payment_blocked_email("u1", 1),
    ):
        assert (await _capture(fn))["email_class"] is EmailClass.TRANSACTIONAL


async def test_each_carries_its_own_email_type_for_the_send_log():
    types = set()
    for fn in (
        lambda: re_mod.send_refund_email("u1", 1),
        lambda: re_mod.send_wallet_topup_email("u1", 1),
        lambda: re_mod.send_no_show_fee_email("u1", 1),
        lambda: re_mod.send_payment_blocked_email("u1", 1),
    ):
        types.add((await _capture(fn))["email_type"])
    assert types == {
        "rider_refund",
        "rider_wallet_topup",
        "rider_no_show_fee",
        "rider_payment_blocked",
    }


async def test_money_emails_are_branded():
    html = (await _capture(lambda: re_mod.send_refund_email("u1", 1)))["rendered"].html
    assert "/api/v1/branding/spinr-logo.png" in html
    assert "#FF3B30" in html


async def test_failure_never_propagates_into_a_settlement_path():
    with patch.object(re_mod, "send_lifecycle_email", AsyncMock(side_effect=RuntimeError("SES down"))):
        assert await re_mod.send_refund_email("u1", 1) is False
        assert await re_mod.send_wallet_topup_email("u1", 1) is False
        assert await re_mod.send_no_show_fee_email("u1", 1) is False
        assert await re_mod.send_payment_blocked_email("u1", 1) is False
