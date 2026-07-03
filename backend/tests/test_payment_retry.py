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
async def test_retry_skips_any_invoice_sentinel_fresh_or_stale():
    """Codex round-3 (#2): the retry loop must NOT re-charge in-app while ANY
    invoice claim is on the row — a finalized id, a fresh 'pending:' sentinel, or
    a stale one. Re-opening by age risks collecting alongside a payable invoice;
    recovery is admin-side (crash-safe creation), not here."""
    from datetime import timedelta

    fresh_ts = datetime.now(timezone.utc).timestamp()
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
    for sid in (f"pending:{fresh_ts}:abc", f"pending:{stale_ts}:abc", "in_admin_real"):
        ride = _make_ride(stripe_invoice_id=sid)
        mock_update = AsyncMock(return_value={"id": RIDE_ID})
        mock_confirm = MagicMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch(
                "utils.payment_retry.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET}),
            ),
            patch("utils.payment_retry.db.update_one", mock_update),
            patch("utils.payment_retry.send_push_notification", AsyncMock()),
            patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=_fake_intent("requires_payment_method"))),
            patch("stripe.PaymentIntent.confirm", mock_confirm),
        ):
            from utils import payment_retry

            await payment_retry.retry_failed_payments()
        mock_confirm.assert_not_called()
        mock_update.assert_not_awaited()


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

    # Codex round-5 (81Sa): the atomic 'retrying' claim must assert
    # stripe_invoice_id IS NULL so an admin send-invoice that wins the row between
    # the read and the claim excludes this ride from in-app retry.
    claim_calls = [
        c for c in mock_db_update.await_args_list if c[0][2].get("$set", {}).get("payment_status") == "retrying"
    ]
    assert len(claim_calls) == 1
    assert claim_calls[0][0][1]["stripe_invoice_id"] is None


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


@pytest.mark.anyio
async def test_requires_capture_hold_is_captured_for_owed_amount():
    """Codex P2 (PR #2021): a stranded manual-capture hold (settlement failed
    mid-flight, e.g. blank Stripe key) must be captured by the retry loop once
    Stripe is reachable — for the OWED amount (grand_total + tip), never the
    full authorized amount, which includes the tip buffer. Codex P2
    (PR #2023): the capture must be preceded by a 'processing' claim flip and
    write a financial_events ledger row before marking paid."""
    ride = _make_ride(grand_total=20.00, tip_amount=2.00)

    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
    mock_capture = MagicMock()
    mock_confirm = MagicMock()
    mock_ledger = AsyncMock()

    intent = _fake_intent("requires_capture")
    intent.amount = 3000  # $30 hold: fare + buffer

    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=intent)),
        patch("stripe.PaymentIntent.capture", mock_capture),
        patch("stripe.PaymentIntent.confirm", mock_confirm),
        patch("services.payment_service.record_payment_event", mock_ledger),
    ):
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    # Captured owed = 2000 + 200 = 2200 cents (not the 3000 authorized).
    mock_capture.assert_called_once_with(
        PI_ID,
        amount_to_capture=2200,
        api_key=STRIPE_SECRET,
        idempotency_key=f"ride-capture-{RIDE_ID}-2200",
    )
    mock_confirm.assert_not_called()

    # Ledger row for the recovered capture (reconciliation/reporting).
    mock_ledger.assert_awaited_once()
    assert mock_ledger.await_args.args[2] == 2200
    assert mock_ledger.await_args.args[3] == PI_ID

    # Status sequence: retrying (claim) → processing (pre-capture) → paid.
    statuses = [c[0][2].get("$set", {}).get("payment_status") for c in mock_db_update.await_args_list]
    assert statuses == ["retrying", "processing", "paid"]
    paid_calls = [c for c in mock_db_update.await_args_list if c[0][2].get("$set", {}).get("payment_status") == "paid"]
    assert paid_calls[0][0][2]["$set"]["auth_status"] == "captured"


@pytest.mark.anyio
async def test_requires_capture_paid_write_failure_leaves_processing():
    """Codex P2 (PR #2023): if the capture succeeds but the paid-write fails,
    the ride must stay in 'processing' (owned by the stuck-processing
    reconciler) — NEVER be reset to 'failed', which would look
    retryable/invoiceable after money has already moved."""
    ride = _make_ride(grand_total=20.00, tip_amount=0)

    intent = _fake_intent("requires_capture")
    intent.amount = 3000

    async def _update_one(_table, _filters, patch_body):
        if patch_body.get("$set", {}).get("payment_status") == "paid":
            raise RuntimeError("DB write failed")
        return {"id": RIDE_ID}

    mock_db_update = AsyncMock(side_effect=_update_one)
    mock_capture = MagicMock()

    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=intent)),
        patch("stripe.PaymentIntent.capture", mock_capture),
        patch("services.payment_service.record_payment_event", AsyncMock()),
    ):
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    mock_capture.assert_called_once()
    # No write may reset the ride to 'failed' after the capture succeeded;
    # the last successful status write is the pre-capture 'processing'.
    statuses = [c[0][2].get("$set", {}).get("payment_status") for c in mock_db_update.await_args_list]
    assert "failed" not in statuses


@pytest.mark.anyio
async def test_requires_capture_never_exceeds_authorized_amount():
    """If the owed total somehow exceeds the hold, capture is capped at the
    authorized amount — Stripe rejects anything higher."""
    ride = _make_ride(grand_total=35.00, tip_amount=0)

    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
    mock_capture = MagicMock()

    intent = _fake_intent("requires_capture")
    intent.amount = 3000

    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=intent)),
        patch("stripe.PaymentIntent.capture", mock_capture),
        patch("services.payment_service.record_payment_event", AsyncMock()),
    ):
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    assert mock_capture.call_args.kwargs["amount_to_capture"] == 3000


@pytest.mark.anyio
async def test_unexpected_intent_state_releases_claim_to_failed():
    """Codex P2 (PR #2021): an unexpected PI state must NOT leave the row in
    'retrying' — the scan only picks up failed/requires_action/processing, so
    that wedges the ride forever. The claim is released back to 'failed' with
    the counter bumped so persistence exhausts to the admin alert."""
    ride = _make_ride()

    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
    mock_capture = MagicMock()
    mock_confirm = MagicMock()

    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=_fake_intent("requires_action"))),
        patch("stripe.PaymentIntent.capture", mock_capture),
        patch("stripe.PaymentIntent.confirm", mock_confirm),
    ):
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    mock_capture.assert_not_called()
    mock_confirm.assert_not_called()

    # Last write must release the claim: failed + count bumped, never a
    # row left in 'retrying'.
    failed_calls = [
        c for c in mock_db_update.await_args_list if c[0][2].get("$set", {}).get("payment_status") == "failed"
    ]
    assert len(failed_calls) == 1
    assert failed_calls[0][0][2]["$set"]["payment_retry_count"] == 1
    statuses = [c[0][2].get("$set", {}).get("payment_status") for c in mock_db_update.await_args_list]
    assert statuses[-1] == "failed"
