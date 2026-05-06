"""
Unit tests for payment_retry double-charge guard.

Verifies that:
1. A Stripe PI already succeeded → DB updated, no second charge
2. A Stripe PI still requires payment → retry is attempted
3. Stripe retrieve raises → retry still proceeds (fail-open)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RIDE_ID = "ride_retry_test_001"
PI_ID = "pi_test_abc123"
STRIPE_SECRET = "sk_test_secret"


def _make_ride(**overrides) -> dict:
    base = {
        "id": RIDE_ID,
        "rider_id": "rider_1",
        "driver_id": "driver_1",
        "payment_intent_id": PI_ID,
        "payment_status": "failed",
        "payment_retry_count": 0,
        "created_at": "2026-05-06T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _fake_intent(status: str) -> MagicMock:
    intent = MagicMock()
    intent.status = status
    return intent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_retry_skips_when_stripe_already_succeeded():
    """
    When Stripe reports the PI as 'succeeded', the loop must:
      - Update the DB row to payment_status='paid'
      - NOT call stripe.PaymentIntent.confirm()
    """
    ride = _make_ride()

    mock_db_update = AsyncMock(return_value=None)
    mock_confirm = MagicMock()

    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch(
            "stripe.PaymentIntent.retrieve",
            MagicMock(return_value=_fake_intent("succeeded")),
        ),
        patch("stripe.PaymentIntent.confirm", mock_confirm),
    ):
        # Import after patching to pick up mocks
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    # DB should be updated to 'paid'
    mock_db_update.assert_awaited_once()
    call_args = mock_db_update.await_args
    assert call_args[0][0] == "rides"
    assert call_args[0][1] == {"id": RIDE_ID}
    update_set = call_args[0][2]["$set"]
    assert update_set["payment_status"] == "paid"

    # confirm must never be called
    mock_confirm.assert_not_called()


@pytest.mark.anyio
async def test_retry_proceeds_when_stripe_failed():
    """
    When Stripe reports the PI as 'requires_payment_method', the loop must:
      - Call stripe.PaymentIntent.confirm() with an idempotency key
      - Update DB to payment_status='processing' with incremented retry_count
    """
    ride = _make_ride(payment_retry_count=1)

    mock_db_update = AsyncMock(return_value=None)
    mock_confirm = MagicMock(return_value=_fake_intent("processing"))

    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch(
            "stripe.PaymentIntent.retrieve",
            MagicMock(return_value=_fake_intent("requires_payment_method")),
        ),
        patch("stripe.PaymentIntent.confirm", mock_confirm),
    ):
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    # confirm must have been called with the idempotency key
    mock_confirm.assert_called_once()
    _, confirm_kwargs = mock_confirm.call_args
    assert "idempotency_key" in confirm_kwargs
    # key must include ride_id and attempt number (attempt = retry_count + 1 = 2)
    assert RIDE_ID in confirm_kwargs["idempotency_key"]
    assert "2" in confirm_kwargs["idempotency_key"]

    # DB update must set payment_status='processing' and increment count
    mock_db_update.assert_awaited()
    # Find the processing update (there may be a push notification path too)
    processing_calls = [
        c for c in mock_db_update.await_args_list if c[0][2].get("$set", {}).get("payment_status") == "processing"
    ]
    assert len(processing_calls) == 1
    assert processing_calls[0][0][2]["$set"]["payment_retry_count"] == 2


@pytest.mark.anyio
async def test_retry_falls_through_when_retrieve_raises():
    """
    When stripe.PaymentIntent.retrieve raises a StripeError, the loop must:
      - Still call stripe.PaymentIntent.confirm() (fail-open)
      - NOT silently skip the ride
    """
    import stripe as stripe_module

    ride = _make_ride()

    mock_db_update = AsyncMock(return_value=None)
    mock_confirm = MagicMock(return_value=_fake_intent("processing"))

    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch(
            "stripe.PaymentIntent.retrieve",
            MagicMock(side_effect=stripe_module.error.StripeError("network error")),
        ),
        patch("stripe.PaymentIntent.confirm", mock_confirm),
    ):
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    # confirm must still be called because retrieve failure is fail-open
    mock_confirm.assert_called_once()
    _, confirm_kwargs = mock_confirm.call_args
    assert "idempotency_key" in confirm_kwargs
