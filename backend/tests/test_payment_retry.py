"""
Unit tests for payment_retry double-charge guard.

Verifies that:
1. A Stripe PI already succeeded → DB updated, no second charge
2. A Stripe PI still requires payment → retry is attempted
3. Stripe retrieve raises → retry still proceeds (fail-open)
"""

from __future__ import annotations

import asyncio
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
    write a financial_events ledger row before marking paid.

    ACTION_ITEMS B19: this now routes through the same shared
    _finalize_card_settlement finalizer settle_card's two success paths use
    (see test_atomic_settle.py) — mocked at that level (record_payment_event,
    db_supabase.update_ride, the WS notify) rather than at
    utils.payment_retry.db.update_one for the paid write, since that write no
    longer happens in this module."""
    ride = _make_ride(grand_total=20.00, tip_amount=2.00)

    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
    mock_capture = MagicMock()
    mock_confirm = MagicMock()
    mock_ledger = AsyncMock()
    mock_update_ride = AsyncMock()
    mock_ws = AsyncMock()

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
        patch("services.payment_service._atomic_settle_enabled", AsyncMock(return_value=False)),
        patch("services.payment_service.record_payment_event", mock_ledger),
        patch("services.payment_service.db_supabase.update_ride", mock_update_ride),
        patch("services.payment_service.manager.send_personal_message", mock_ws),
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

    # Exactly ONE financial_events header for the recovered capture — the
    # whole point of routing through the shared finalizer (B21's acceptance:
    # "a test mirroring test_atomic_settle.py's exactly-one-header matrix").
    mock_ledger.assert_awaited_once()
    assert mock_ledger.call_args.kwargs["amount_cents"] == 2200
    assert mock_ledger.call_args.kwargs["payment_intent_id"] == PI_ID

    # The paid-write now happens inside _finalize_card_settlement via
    # db_supabase.update_ride, not utils.payment_retry.db.update_one.
    mock_update_ride.assert_awaited_once()
    written = mock_update_ride.call_args.args[1]
    assert written["payment_status"] == "paid"
    assert written["auth_status"] == "captured"
    mock_ws.assert_awaited_once()

    # Status sequence on THIS module's own writes: retrying (claim) →
    # processing (pre-capture). The paid flip moved to db_supabase.update_ride.
    statuses = [c[0][2].get("$set", {}).get("payment_status") for c in mock_db_update.await_args_list]
    assert statuses == ["retrying", "processing"]


@pytest.mark.anyio
async def test_requires_capture_paid_write_failure_leaves_processing():
    """Codex P2 (PR #2023): if the capture succeeds but the paid-write fails,
    the ride must stay in 'processing' (owned by the stuck-processing
    reconciler) — NEVER be reset to 'failed', which would look
    retryable/invoiceable after money has already moved.

    ACTION_ITEMS B19: the paid-write failure now happens inside
    _finalize_card_settlement's legacy path (db_supabase.update_ride), which
    already implements this exact contract (see
    test_atomic_settle.py::test_legacy_update_failure_still_returns_503) —
    this test proves payment_retry's own writes (never a 'failed' status)
    still hold once that failure propagates back as
    PaymentResult(success=False)."""
    ride = _make_ride(grand_total=20.00, tip_amount=0)

    intent = _fake_intent("requires_capture")
    intent.amount = 3000

    mock_db_update = AsyncMock(return_value={"id": RIDE_ID})
    mock_capture = MagicMock()

    fake_settings = {"stripe_secret_key": STRIPE_SECRET}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=fake_settings)),
        patch("utils.payment_retry.db.update_one", mock_db_update),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=intent)),
        patch("stripe.PaymentIntent.capture", mock_capture),
        patch("services.payment_service._atomic_settle_enabled", AsyncMock(return_value=False)),
        patch("services.payment_service.record_payment_event", AsyncMock()),
        patch(
            "services.payment_service.db_supabase.update_ride",
            AsyncMock(side_effect=RuntimeError("DB write failed")),
        ),
    ):
        from utils import payment_retry

        await payment_retry.retry_failed_payments()

    mock_capture.assert_called_once()
    # No write may reset the ride to 'failed' after the capture succeeded;
    # the only successful status write on this module's own db.update_one is
    # the pre-capture 'processing' claim flip.
    statuses = [c[0][2].get("$set", {}).get("payment_status") for c in mock_db_update.await_args_list]
    assert "failed" not in statuses
    assert statuses == ["retrying", "processing"]


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


# ---------------------------------------------------------------------------
# ACTION_ITEMS B21: throttle-lock TTL must expire before the loop's own next
# wake, or the pod that ran the last tick fails its own SET NX and skips a
# full interval — see utils/ledger_projection.py's _LOCK_TTL_SECONDS for the
# sibling fix this mirrors.
# ---------------------------------------------------------------------------


def test_payment_retry_lock_ttl_expires_before_the_earliest_next_wake():
    """Stated as an invariant so a future tuning change to the interval or the
    jitter fraction can't silently re-break the cadence (see
    utils/ledger_projection.py's test of the same name)."""
    from utils import payment_retry as pr

    jitter_fraction = 0.1  # matches payment_retry_loop's `delta = interval * 0.1`
    min_sleep = pr.RETRY_INTERVAL_SECONDS * (1 - jitter_fraction)
    lock_ttl = int(pr.RETRY_INTERVAL_SECONDS * 0.85)
    assert lock_ttl < min_sleep, (
        f"lock TTL {lock_ttl}s must expire before the shortest possible sleep "
        f"({min_sleep}s), or the loop skips its own next tick"
    )


def test_payment_retry_loop_reacquires_its_own_lock_on_the_next_wake():
    """REGRESSION: with the old TTL = 1.5x interval against a 1x interval
    sleep, the pod that ran the last tick woke to find its OWN key still
    alive, failed SET NX, and slept another full interval — so a loop
    documented as "5min" actually ticked every ~10 minutes.

    Simulated against a virtual clock with real SET NX EX semantics, jitter
    pinned to its most adverse value (the SHORTEST sleep) — the case the TTL
    has to survive. Mirrors ledger_projection.py's loop-cadence regression test.
    """
    from utils import payment_retry as pr

    clock = {"t": 0.0}
    expiries: dict[str, float] = {}
    wakes = {"n": 0}

    async def fake_set_nx(key, _value, ttl):
        exp = expiries.get(key)
        if exp is not None and exp > clock["t"]:
            return False
        expiries[key] = clock["t"] + ttl
        return True

    async def fake_sleep(secs):
        clock["t"] += secs
        wakes["n"] += 1
        if wakes["n"] >= 2:
            raise asyncio.CancelledError

    with (
        patch.object(pr, "redis_set_nx", side_effect=fake_set_nx),
        patch.object(pr, "retry_failed_payments", AsyncMock()) as retry_failed,
        patch.object(pr, "retry_stuck_payouts", AsyncMock()),
        patch.object(pr, "sweep_guest_corporate_settlements", AsyncMock()),
        patch.object(pr, "_record_heartbeat"),
        patch.object(pr.asyncio, "sleep", side_effect=fake_sleep),
        # uniform(-delta, +delta) -> -delta: the shortest sleep the loop can take.
        patch.object(pr.random, "uniform", side_effect=lambda lo, _hi: lo),
    ):
        try:
            asyncio.run(pr.payment_retry_loop())
        except asyncio.CancelledError:
            pass

    assert retry_failed.await_count == 2, (
        "the single replica must tick once per interval; a TTL longer than the "
        "minimum sleep makes it skip its own next wake and halves the cadence"
    )
