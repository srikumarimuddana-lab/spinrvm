"""Corporate guest rides must produce a receipt like every other ride.

A guest has no app and never calls /process-payment, which is where every
other ride's receipt is sent from. auto_settle_guest_corporate settled the
ride and stopped, so this was the one class of completed, charged ride that
produced no receipt and no GST/PST line-item disclosure at all.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

import services.payment_service as ps

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_RIDE = {
    "id": "ride-1",
    "rider_id": "guest-1",
    "guest_booking": True,
    "payment_method": "company_allowance",
    "status": "completed",
    "grand_total": "31.50",
}


def _result(success=True, already_paid=False):
    return ps.PaymentResult(
        success=success,
        already_paid=already_paid,
        charged_amount=Decimal("31.50"),
    )


async def _settle(ride=None, result=None, claimed=True):
    receipt = AsyncMock(return_value=True)
    with (
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=ride if ride is not None else _RIDE)),
        patch.object(ps.db_supabase, "update_one", AsyncMock(return_value={"id": "ride-1"} if claimed else None)),
        patch.object(ps, "settle_corporate", AsyncMock(return_value=result or _result())),
        patch.object(ps, "send_ride_receipt", receipt),
        patch.object(ps, "_fire_guest_purchase_conversion", AsyncMock()),
    ):
        out = await ps.auto_settle_guest_corporate("ride-1")
    return out, receipt


async def test_successful_settlement_sends_the_receipt():
    _, receipt = await _settle()
    receipt.assert_awaited_once()
    assert receipt.await_args.args[1] == "guest-1"


async def test_guests_are_receipted_with_a_zero_tip():
    # Guests cannot tip; the receipt total must not invent one.
    _, receipt = await _settle()
    assert receipt.await_args.args[2] == Decimal("0")


async def test_replayed_settlement_does_not_resend():
    # already_paid means another replica settled this first — a second receipt
    # would tell the guest they were charged twice.
    _, receipt = await _settle(result=_result(already_paid=True))
    receipt.assert_not_awaited()


async def test_failed_settlement_sends_no_receipt():
    _, receipt = await _settle(result=_result(success=False))
    receipt.assert_not_awaited()


async def test_lost_claim_sends_no_receipt():
    _, receipt = await _settle(claimed=False)
    receipt.assert_not_awaited()


async def test_non_guest_ride_is_untouched():
    _, receipt = await _settle(ride={**_RIDE, "guest_booking": False})
    receipt.assert_not_awaited()


async def test_receipt_failure_does_not_break_settlement():
    """The money already moved; a receipt problem must not surface as a
    settlement failure or strand the ride."""
    with (
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=_RIDE)),
        patch.object(ps.db_supabase, "update_one", AsyncMock(return_value={"id": "ride-1"})),
        patch.object(ps, "settle_corporate", AsyncMock(return_value=_result())),
        patch.object(ps, "send_ride_receipt", AsyncMock(side_effect=RuntimeError("SES down"))),
        patch.object(ps, "_fire_guest_purchase_conversion", AsyncMock()),
    ):
        out = await ps.auto_settle_guest_corporate("ride-1")
    assert out is not None
    assert out.success is True
