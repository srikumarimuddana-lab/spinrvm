"""
Unit tests for payment_retry double-charge guard.

Verifies that:
1. A Stripe PI already succeeded → DB updated, no second charge
2. A Stripe PI still requires payment → retry is attempted
3. Stripe retrieve raises → retry still proceeds (fail-open)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RIDE_ID = "ride_retry_test_001"
PI_ID = "pi_test_abc123"
STRIPE_SECRET = "sk_test_secret"


def _make_ride(**overrides) -> dict:
    # `created_at` must be inside the 24h window the retry loop scans, so
    # use "now" rather than a hard-coded date that ages out of the window.
    base = {
        "id": RIDE_ID,
        "rider_id": "rider_1",
        "driver_id": "driver_1",
        "payment_intent_id": PI_ID,
        "payment_status": "failed",
        "payment_retry_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def _fake_intent(status: str) -> MagicMock:
    intent = MagicMock()
    intent.status = status
    intent.amount = 2550  # cents — feeds the ride-confirm idempotency key
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

    # The retry loop's atomic-claim step calls update_one and requires a
    # truthy return to continue (it only acts on rows the claim succeeded on).
    # Returning the ride dict satisfies that gate and lets every subsequent
    # update_one call (paid / processing / failed) run for assertion.
    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
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

    # update_one is awaited twice: once for the atomic 'retrying' claim,
    # then for the final 'paid' write. Locate the 'paid' call.
    paid_calls = [c for c in mock_db_update.await_args_list if c[0][2].get("$set", {}).get("payment_status") == "paid"]
    assert len(paid_calls) == 1
    paid_call = paid_calls[0]
    assert paid_call[0][0] == "rides"
    assert paid_call[0][1] == {"id": RIDE_ID}

    # confirm must never be called
    mock_confirm.assert_not_called()


@pytest.mark.anyio
async def test_retry_skips_ride_with_open_invoice():
    """Codex P1: once an admin has sent a payable Stripe invoice
    (stripe_invoice_id set), the retry loop must NOT confirm the stored PI on
    the old card — that would collect twice alongside the invoice. The ride is
    skipped entirely (no claim, no confirm)."""
    ride = _make_ride(stripe_invoice_id="in_admin_123")

    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
    mock_confirm = MagicMock()
    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=_fake_intent("requires_payment_method"))),
        patch("stripe.PaymentIntent.confirm", mock_confirm),
    ):
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    mock_confirm.assert_not_called()
    mock_db_update.assert_not_awaited()


@pytest.mark.anyio
async def test_retry_proceeds_when_stripe_failed():
    """
    When Stripe reports the PI as 'requires_payment_method', the loop must:
      - Call stripe.PaymentIntent.confirm() with an idempotency key
      - Update DB to payment_status='processing' with incremented retry_count
    """
    ride = _make_ride(payment_retry_count=1)

    # The retry loop's atomic-claim step calls update_one and requires a
    # truthy return to continue (it only acts on rows the claim succeeded on).
    # Returning the ride dict satisfies that gate and lets every subsequent
    # update_one call (paid / processing / failed) run for assertion.
    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
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
    # Scheduled retries use per-attempt keys so each retry gets a fresh Stripe
    # call rather than replaying a cached transient error. payment_retry_count=1
    # → retry_count=1 → attempt=2 → key suffix "-retry-2".
    assert confirm_kwargs["idempotency_key"] == f"ride-confirm-{RIDE_ID}-2550-retry-2"

    # DB update must set payment_status='processing' and increment count
    mock_db_update.assert_awaited()
    # Find the processing update (there may be a push notification path too)
    processing_calls = [
        c for c in mock_db_update.await_args_list if c[0][2].get("$set", {}).get("payment_status") == "processing"
    ]
    assert len(processing_calls) == 1
    assert processing_calls[0][0][2]["$set"]["payment_retry_count"] == 2


@pytest.mark.anyio
async def test_retry_marks_ride_failed_when_retrieve_raises():
    """
    When stripe.PaymentIntent.retrieve raises a StripeError, the loop must:
      - NOT call stripe.PaymentIntent.confirm() (fail-closed per CLAUDE.md
        "never warn-and-continue on payment errors")
      - Increment payment_retry_count and mark the ride 'failed'
      - NOT silently skip the ride
    """
    import stripe as stripe_module

    ride = _make_ride()

    # The retry loop's atomic-claim step calls update_one and requires a
    # truthy return to continue (it only acts on rows the claim succeeded on).
    # Returning the ride dict satisfies that gate and lets every subsequent
    # update_one call (paid / processing / failed) run for assertion.
    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
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

    # confirm must NOT be called: retrieve failure is fail-closed; the
    # ride is marked failed and the retry counter is bumped instead.
    mock_confirm.assert_not_called()
    failed_calls = [
        c for c in mock_db_update.await_args_list if c[0][2].get("$set", {}).get("payment_status") == "failed"
    ]
    assert len(failed_calls) == 1
    assert failed_calls[0][0][2]["$set"]["payment_retry_count"] == 1
