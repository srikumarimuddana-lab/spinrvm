"""Unit tests for services/payment_service.py::charge_late_tip.

Covers the branches routes/rides/payments.py::add_tip relies on to close
Finding 1 (docs/proposals/2026-08-17-tip-capture-stripe-cost-minimization-
strategy.md): a tip added after ride.payment_status == 'paid' must be
charged via a fresh, tip-amount-only PaymentIntent — never just recorded.

  - succeeded            -> PaymentResult(success=True), ledger row written
                             with source="late_tip" (not "process_payment")
  - declined              -> PaymentResult(success=False, 402, card_declined),
                             no ledger write
  - requires_action (SCA) -> PaymentResult(success=False, 402,
                             authentication_required), no ledger write
  - unconfigured, prod    -> refuse loudly (503), no ledger write
  - unconfigured, dev     -> pass-through success, no ledger write (mirrors
                             the settle_card dev bypass so local flows don't
                             wedge without a real Stripe key)
  - failed (ops error)    -> PaymentResult(success=False, 402), no ledger write

charge_ride / record_payment_event are patched on the payment_service bound
names, mirroring test_settle_card_capture.py / test_payment_unconfigured_guard.py.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import ChargeOutcome

pytestmark = pytest.mark.anyio

RIDE_ID = "ride_late_tip_1"
RIDER_ID = "rider_late_tip_1"


def _paid_ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "completed",
        "payment_method": "card",
        "payment_status": "paid",
        "payment_method_id": "pm_ride",
        "total_fare": "18.50",
        "tip_amount": "0",
        "driver_earnings": "15.00",
    }
    row.update(extra)
    return row


def _rider_user() -> dict:
    return {
        "id": RIDER_ID,
        "stripe_customer_id": "cus_late_tip",
        "default_payment_method": "pm_default",
    }


async def test_charge_late_tip_success_writes_ledger_with_late_tip_source():
    from backend.services import payment_service

    ride = _paid_ride()
    outcome = ChargeOutcome(status="succeeded", payment_intent_id="pi_late_1", charged_amount=Decimal("5.00"))

    with (
        patch("backend.services.payment_service.app_config", MagicMock(ENV="development")),
        patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value=_rider_user()),
        ),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)) as mock_charge,
        patch("backend.services.payment_service.record_payment_event", AsyncMock(return_value=None)) as mock_ledger,
    ):
        result = await payment_service.charge_late_tip(ride, RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert result.success is True
    assert result.charged_amount == "5.00"
    mock_charge.assert_awaited_once()
    charge_kwargs = mock_charge.await_args.kwargs
    assert charge_kwargs["total_amount"] == Decimal("5.00")
    assert charge_kwargs["payment_method_id"] == "pm_ride"  # ride's own PM preferred over the account default
    mock_ledger.assert_awaited_once()
    ledger_kwargs = mock_ledger.await_args.kwargs
    assert ledger_kwargs["source"] == "late_tip"
    assert ledger_kwargs["tip_amount"] == Decimal("5.00")


async def test_charge_late_tip_falls_back_to_default_payment_method():
    """If the ride's own payment_method_id is gone, fall back to the rider's
    current default — mirrors settle_card's override-card fallback."""
    from backend.services import payment_service

    ride = _paid_ride(payment_method_id=None)
    outcome = ChargeOutcome(status="succeeded", payment_intent_id="pi_late_2", charged_amount=Decimal("3.00"))

    with (
        patch("backend.services.payment_service.app_config", MagicMock(ENV="development")),
        patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value=_rider_user()),
        ),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)) as mock_charge,
        patch("backend.services.payment_service.record_payment_event", AsyncMock(return_value=None)),
    ):
        result = await payment_service.charge_late_tip(ride, RIDE_ID, RIDER_ID, Decimal("3.00"))

    assert result.success is True
    assert mock_charge.await_args.kwargs["payment_method_id"] == "pm_default"


async def test_charge_late_tip_declined_no_ledger_write():
    from backend.services import payment_service

    ride = _paid_ride()
    outcome = ChargeOutcome(status="declined", decline_code="insufficient_funds", error_message="Card declined")

    with (
        patch("backend.services.payment_service.app_config", MagicMock(ENV="development")),
        patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value=_rider_user()),
        ),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        patch("backend.services.payment_service.record_payment_event", AsyncMock(return_value=None)) as mock_ledger,
    ):
        result = await payment_service.charge_late_tip(ride, RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert result.success is False
    assert result.error_code == "card_declined"
    assert result.status_code == 402
    assert result.extra.get("suggested_action") == "change_card"
    mock_ledger.assert_not_awaited()


async def test_charge_late_tip_requires_action_no_ledger_write():
    from backend.services import payment_service

    ride = _paid_ride()
    outcome = ChargeOutcome(status="requires_action", payment_intent_id="pi_late_3", client_secret="secret")

    with (
        patch("backend.services.payment_service.app_config", MagicMock(ENV="development")),
        patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value=_rider_user()),
        ),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        patch("backend.services.payment_service.record_payment_event", AsyncMock(return_value=None)) as mock_ledger,
    ):
        result = await payment_service.charge_late_tip(ride, RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert result.success is False
    assert result.error_code == "authentication_required"
    assert result.status_code == 402
    mock_ledger.assert_not_awaited()


async def test_charge_late_tip_generic_failure_no_ledger_write():
    from backend.services import payment_service

    ride = _paid_ride()
    outcome = ChargeOutcome(status="failed", error_message="No default payment method on file")

    with (
        patch("backend.services.payment_service.app_config", MagicMock(ENV="development")),
        patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value=_rider_user()),
        ),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        patch("backend.services.payment_service.record_payment_event", AsyncMock(return_value=None)) as mock_ledger,
    ):
        result = await payment_service.charge_late_tip(ride, RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert result.success is False
    assert result.error_code == "tip_charge_failed"
    mock_ledger.assert_not_awaited()


async def test_charge_late_tip_unconfigured_refused_in_production():
    """Mirrors _refuse_unconfigured_settlement: a blanked Stripe key in
    production must refuse (503), never silently credit the driver."""
    from backend.services import payment_service

    ride = _paid_ride()
    outcome = ChargeOutcome(status="unconfigured", error_message="Payment processing is not configured")

    with (
        patch("backend.services.payment_service.app_config", MagicMock(ENV="production")),
        patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value=_rider_user()),
        ),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        patch("backend.services.payment_service.record_payment_event", AsyncMock(return_value=None)) as mock_ledger,
    ):
        result = await payment_service.charge_late_tip(ride, RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert result.success is False
    assert result.error_code == "stripe_unconfigured"
    assert result.status_code == 503
    mock_ledger.assert_not_awaited()


async def test_charge_late_tip_unconfigured_dev_bypass_no_ledger_write():
    """Dev/test bypass: don't wedge local flows without a real Stripe key,
    but still don't write a ledger row for money that didn't move."""
    from backend.services import payment_service

    ride = _paid_ride()
    outcome = ChargeOutcome(status="unconfigured", error_message="Payment processing is not configured")

    with (
        patch("backend.services.payment_service.app_config", MagicMock(ENV="development")),
        patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value=_rider_user()),
        ),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        patch("backend.services.payment_service.record_payment_event", AsyncMock(return_value=None)) as mock_ledger,
    ):
        result = await payment_service.charge_late_tip(ride, RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert result.success is True
    mock_ledger.assert_not_awaited()
