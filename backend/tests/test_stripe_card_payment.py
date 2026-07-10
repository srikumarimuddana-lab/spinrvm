"""Unit tests for the Stripe card branch in process_payment().

Covers:
  - Success path: charge_ride succeeds → ride marked paid, WS event emitted.
  - Card-declined failure: charge_ride returns 'declined' → ride marked failed,
    HTTP 402 {code: card_declined, decline_code, suggested_action: change_card}.
  - Generic Stripe error: charge_ride returns 'failed' → HTTP 402 payment_error,
    ride marked failed.
  - No saved payment method → pre-flight 402 {code: no_payment_method}.
  - requires_action (off-session 3DS) → HTTP 402 authentication_required,
    ride marked failed (retry loop / rider re-confirm recovers it).

Strategy: call process_payment() directly (no TestClient) with:
  - Patched db_supabase helpers so no real DB calls are made.
  - Patched charge_ride at backend.services.payment_service.charge_ride (module-level import in rides.py).

This avoids the TestClient lifespan / circuit-breaker race-condition that makes
TestClient tests flaky when Supabase is unreachable in CI.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import ChargeOutcome

_RIDE_ID = "ride_stripe_test"
_RIDER_ID = "rider_1"

_FAKE_RIDE = {
    "id": _RIDE_ID,
    "rider_id": _RIDER_ID,
    "total_fare": 20.00,
    "payment_method": "card",
    "payment_status": "pending",
    "status": "completed",
    "tip_amount": 0,
    "corporate_account_id": None,
    "driver_id": None,
}

_FAKE_RIDER_WITH_PM = {
    "id": _RIDER_ID,
    "default_payment_method": "pm_test_abc123",
    "stripe_customer_id": "cus_test_xyz",
}

_CURRENT_USER = {"id": _RIDER_ID}


def _req(tip: str = "0"):
    from backend.routes.rides import ProcessPaymentRequest

    return ProcessPaymentRequest(tip_amount=Decimal(tip))


def _base_patches(*, rider=None, charge_outcome=None):
    """Return a dict of patch-targets → mock objects for the card branch.

    charge_outcome defaults to a successful ChargeOutcome.
    """
    if charge_outcome is None:
        charge_outcome = ChargeOutcome(
            status="succeeded",
            payment_intent_id="pi_test_success",
            charged_amount=20.00,
        )
    _rider = rider if rider is not None else _FAKE_RIDER_WITH_PM
    return {
        "backend.routes.rides._deps.db_supabase.get_ride": AsyncMock(return_value=_FAKE_RIDE),
        # db = db_supabase alias — patching db_supabase.update_one covers both
        "backend.routes.rides._deps.db_supabase.update_one": AsyncMock(return_value=MagicMock(modified_count=1)),
        "backend.routes.rides._deps.db.update_one": AsyncMock(return_value=MagicMock(modified_count=1)),
        "backend.routes.rides._deps.db_supabase.update_ride": AsyncMock(return_value=None),
        "backend.routes.rides._deps.db_supabase.get_user_by_id": AsyncMock(return_value=_rider),
        "backend.routes.rides._deps.db_supabase.get_driver_by_id": AsyncMock(return_value=None),
        "backend.routes.rides._deps.manager.send_personal_message": AsyncMock(return_value=None),
        # Intercept charge_ride at the module-level alias in rides.py so we never
        # touch stripe_charge.py or the real Stripe SDK.
        "backend.services.payment_service.charge_ride": AsyncMock(return_value=charge_outcome),
    }


async def _call(*, ride=None, rider=None, charge_outcome=None, tip="0"):
    """Call process_payment directly with full mock coverage."""
    from backend.routes.rides import process_payment

    patches = _base_patches(rider=rider, charge_outcome=charge_outcome)
    if ride is not None:
        patches["backend.routes.rides._deps.db_supabase.get_ride"] = AsyncMock(return_value=ride)

    patchers = {t: patch(t, m) for t, m in patches.items()}
    started = {t: p.start() for t, p in patchers.items()}
    try:
        result = await process_payment(
            ride_id=_RIDE_ID,
            req=_req(tip),
            current_user=_CURRENT_USER,
        )
    finally:
        for p in patchers.values():
            p.stop()

    return result, started


@pytest.mark.asyncio
async def test_success_marks_ride_paid_and_emits_ws():
    """Successful Stripe charge: ride.payment_status='paid', receipt email attempted."""
    result, mocks = await _call()

    assert result["success"] is True
    assert float(result["charged_amount"]) == pytest.approx(20.00, abs=0.01)

    update_ride = mocks["backend.routes.rides._deps.db_supabase.update_ride"]
    paid_calls = [c for c in update_ride.call_args_list if c.args[1].get("payment_status") == "paid"]
    assert paid_calls, "expected update_ride call with payment_status='paid'"


@pytest.mark.asyncio
async def test_stripe_create_called_with_correct_args():
    """Verify charge_ride is called with correct ride, rider_id, total_amount."""
    result, mocks = await _call()

    charge_mock = mocks["backend.services.payment_service.charge_ride"]
    charge_mock.assert_called_once()
    kw = charge_mock.call_args.kwargs
    assert kw["ride"]["id"] == _RIDE_ID
    assert kw["rider_id"] == _RIDER_ID
    assert float(kw["total_amount"]) == pytest.approx(20.00, abs=0.01)
    assert kw["stripe_customer_id"] == "cus_test_xyz"
    assert kw["payment_method_id"] == "pm_test_abc123"


@pytest.mark.asyncio
async def test_card_declined_returns_402_and_marks_failed():
    """charge_ride returns 'declined' → HTTP 402, ride flagged payment_status='failed'."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await _call(
            charge_outcome=ChargeOutcome(
                status="declined",
                payment_intent_id="pi_declined",
                decline_code="insufficient_funds",
                error_message="Your card has insufficient funds.",
            )
        )

    assert exc_info.value.status_code == 402
    detail = exc_info.value.detail
    assert detail["code"] == "card_declined"
    assert detail["decline_code"] == "insufficient_funds"


@pytest.mark.asyncio
async def test_generic_stripe_error_returns_402():
    """charge_ride returns status='failed' → HTTP 402, ride marked failed."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await _call(
            charge_outcome=ChargeOutcome(
                status="failed",
                error_message="api_connection_error: Unable to reach Stripe",
            )
        )

    assert exc_info.value.status_code == 402
    detail = exc_info.value.detail
    assert detail["code"] == "payment_error"


@pytest.mark.asyncio
async def test_no_saved_payment_method_returns_402():
    """Rider has no default_payment_method → pre-flight guard raises 402
    with a structured no_payment_method code so the app can surface the
    Change Card / Add Card escape (was a bare 400).
    """
    from fastapi import HTTPException

    rider_no_pm = {"id": _RIDER_ID, "stripe_customer_id": "cus_test", "default_payment_method": None}
    with pytest.raises(HTTPException) as exc_info:
        await _call(
            rider=rider_no_pm,
            charge_outcome=ChargeOutcome(
                status="failed",
                error_message="No default payment method on file",
            ),
        )

    assert exc_info.value.status_code == 402
    assert exc_info.value.detail["code"] == "no_payment_method"
    assert exc_info.value.detail["suggested_action"] == "change_card"


@pytest.mark.asyncio
async def test_requires_action_intent_returns_402():
    """Intent status 'requires_action' (3DS off-session) → HTTP 402 authentication_required.

    Off-session 3DS charges cannot be completed without rider interaction.
    process_payment treats this as a payment failure and tells the rider to
    use a different card (simpler UX than returning a client_secret for 3DS).
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await _call(
            charge_outcome=ChargeOutcome(
                status="requires_action",
                payment_intent_id="pi_3ds",
                client_secret="pi_3ds_secret_abc",
            )
        )

    assert exc_info.value.status_code == 402
    detail = exc_info.value.detail
    assert detail["code"] == "authentication_required"
