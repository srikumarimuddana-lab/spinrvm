"""
Integration tests for the card branch of backend/routes/rides.py::process_payment.

These pin the glue between the handler and charge_ride():
    - Success:       persist payment_status=paid + payment_intent_id
    - requires_action: persist payment_status=failed, raise HTTPException 402
                       with code=authentication_required (off-session 3DS not
                       completable without interactive session)
    - declined:      persist payment_status=failed, raise HTTPException 402
                     with {code, decline_code, suggested_action}
    - failed:        persist payment_status=failed, raise HTTPException 402
                     with code=payment_error
    - unconfigured:  dev fallback — still marks paid
    - Idempotency:   second call with payment_status=paid returns
                     already_paid=True without re-charging

charge_ride() itself is covered by test_stripe_charge.py; here we only
validate the handler's response shapes and DB-write contracts.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

RIDER_ID = "rider_pc_1"
RIDE_ID = "ride_pc_1"


def _completed_ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "completed",
        "payment_method": "card",
        "payment_status": "pending",
        "total_fare": 18.5,
        "tip_amount": 0,
    }
    row.update(extra)
    return row


async def _noop_async(*a, **kw):
    return None


def _install_common_patches(outcome, *, ride_row=None, already_paid=False):
    """Patch the handler's DB + Stripe dependencies for a single call.

    `outcome` is a ChargeOutcome instance that charge_ride() will return.
    Pass `already_paid=True` to simulate the atomic guard losing the race
    (db_supabase.update_one returns None because payment_status was not "pending")."""
    ride = ride_row or _completed_ride()
    rider_user = {
        "id": RIDER_ID,
        "stripe_customer_id": "cus_X",
        "default_payment_method": "pm_X",
    }

    updates = []

    async def _capture_update(ride_id, patch):
        updates.append(patch)
        return {"modified_count": 1}

    # Supabase update_one returns None when 0 rows matched the filter.
    guard_row = None if already_paid else {"id": RIDE_ID}

    # Capture receipt email send if it fires
    async def _fake_receipt(*a, **kw):
        return True

    patches = [
        patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider_user)),
        patch("backend.routes.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
        patch("backend.routes.rides.db_supabase.update_ride", AsyncMock(side_effect=_capture_update)),
        patch("backend.routes.rides.db_supabase.update_one", AsyncMock(return_value=guard_row)),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
    ]
    return patches, updates


def _last_payment_status_write(updates: list[dict]) -> str | None:
    """Pick the most recent payment_status value across capture.
    update_ride may be called more than once (e.g. retry unlock)."""
    for patch_body in reversed(updates):
        if "payment_status" in patch_body:
            return patch_body["payment_status"]
    return None


@pytest.mark.asyncio
class TestCardSuccess:
    async def test_persists_paid_and_pi_id_on_success(self):
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        outcome = ChargeOutcome(
            status="succeeded",
            payment_intent_id="pi_succ_1",
            charged_amount=18.5,
        )
        patches, updates = _install_common_patches(outcome)

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            result = await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert result["success"] is True
        assert Decimal(str(result["charged_amount"])) == Decimal("18.5")

        # The last write to payment_status must be "paid"
        assert _last_payment_status_write(updates) == "paid"

        # payment_intent_id must be persisted so webhook dispatcher in
        # webhooks.py can find the ride by PI id later
        paid_write = next(u for u in updates if u.get("payment_status") == "paid")
        assert paid_write["payment_intent_id"] == "pi_succ_1"

    async def test_tip_added_to_total_charge(self):
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        outcome = ChargeOutcome(status="succeeded", payment_intent_id="pi", charged_amount=20.5)
        patches, updates = _install_common_patches(outcome)

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("2"))
            result = await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        # Fare was 18.5, tip 2 → 20.5
        assert Decimal(str(result["charged_amount"])) == Decimal("20.5")
        # tip_amount must be persisted on the ride
        paid_write = next(u for u in updates if u.get("payment_status") == "paid")
        assert Decimal(str(paid_write.get("tip_amount"))) == Decimal("2.0")


@pytest.mark.asyncio
class TestRequiresAction3DS:
    async def test_returns_client_secret_and_persists_requires_action(self):
        """Off-session 3DS path: mark ride failed and raise 402 so the rider
        is prompted to update their payment method. Off-session charges
        requiring 3DS cannot be completed without an interactive session."""
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        outcome = ChargeOutcome(
            status="requires_action",
            payment_intent_id="pi_3ds_1",
            client_secret="pi_3ds_1_secret_abc",
        )
        patches, updates = _install_common_patches(outcome)

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["code"] == "authentication_required"
        assert _last_payment_status_write(updates) == "failed"


@pytest.mark.asyncio
class TestCardDecline:
    async def test_raises_402_with_structured_body(self):
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        outcome = ChargeOutcome(
            status="declined",
            payment_intent_id="pi_dec",
            decline_code="insufficient_funds",
            error_message="Your card has insufficient funds.",
        )
        patches, updates = _install_common_patches(outcome)

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert exc_info.value.status_code == 402
        body = exc_info.value.detail
        assert body["code"] == "card_declined"
        assert body["decline_code"] == "insufficient_funds"
        assert body["suggested_action"] == "change_card"

        # Lock released for retry: last write flips status to failed
        assert _last_payment_status_write(updates) == "failed"


@pytest.mark.asyncio
class TestStripeOpsFailure:
    async def test_non_card_error_raises_402(self):
        """Ops-level failure (api_connection_error, invalid_request,
        rate_limit) — not a decline. Handler raises 402 with payment_error
        code so the rider can retry or contact support."""
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        outcome = ChargeOutcome(
            status="failed",
            error_message="rate_limit_error: Too many requests",
        )
        patches, updates = _install_common_patches(outcome)

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert exc_info.value.status_code == 402
        body = exc_info.value.detail
        assert body["code"] == "payment_error"


@pytest.mark.asyncio
class TestUnconfiguredFallback:
    async def test_dev_env_without_stripe_key_still_marks_paid(self):
        """In dev/test with no stripe_secret_key, charge_ride returns
        unconfigured. The handler falls through to mark the ride paid
        so the test environment doesn't wedge — prod has guard rails
        on settings load that prevent this from firing there."""
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        outcome = ChargeOutcome(status="unconfigured", error_message="no key")
        patches, updates = _install_common_patches(outcome)

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            result = await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert result["success"] is True
        assert _last_payment_status_write(updates) == "paid"


@pytest.mark.asyncio
class TestIdempotencyGuards:
    async def test_already_paid_ride_returns_already_paid_without_calling_stripe(self):
        """If payment_status is already 'paid' on the row, short-circuit
        before charge_ride is ever called. This is the double-tap /
        webhook-arrived-first scenario."""
        from backend.routes import rides as rides_mod

        ride = _completed_ride(payment_status="paid", tip_amount=3.0)
        rider_user = {
            "id": RIDER_ID,
            "stripe_customer_id": "cus_X",
            "default_payment_method": "pm_X",
        }

        charge_mock = AsyncMock()

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider_user)),
            patch("backend.services.payment_service.charge_ride", charge_mock),
        ):
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            result = await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert result.get("already_paid") is True
        charge_mock.assert_not_called()

    async def test_concurrent_request_loses_to_atomic_guard(self):
        """Two parallel requests: the first sets payment_status=processing
        via the atomic update_one. The second's update_one matches 0 rows;
        the handler re-reads the ride, sees the winner's card 'processing'
        state, and returns {already_paid: True} without calling Stripe.
        Pins the double-charge race protection."""
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        outcome = ChargeOutcome(status="succeeded", payment_intent_id="pi")
        # modified_count=0 → atomic guard lost the race
        patches, updates = _install_common_patches(outcome, already_paid=True)

        charge_mock = AsyncMock(return_value=outcome)
        # First read: pre-claim state. Second read (after losing the guard):
        # the winner has already flipped the row to processing.
        get_ride_mock = AsyncMock(
            side_effect=[_completed_ride(), _completed_ride(payment_status="processing")]
        )

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            # Override charge_ride patch so we can assert not-called
            st.enter_context(patch("backend.services.payment_service.charge_ride", charge_mock))
            st.enter_context(patch("backend.routes.rides.db_supabase.get_ride", get_ride_mock))

            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            result = await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert result.get("already_paid") is True
        charge_mock.assert_not_called()

    async def test_concurrent_loser_gets_409_when_wallet_processing(self):
        """A wallet ride the loser couldn't claim is NOT reported as paid —
        wallet 'processing' may still fail, so the loser gets a retryable
        409 instead of a false 'Paid'."""
        from backend.routes import rides as rides_mod
        from backend.utils.stripe_charge import ChargeOutcome

        outcome = ChargeOutcome(status="succeeded", payment_intent_id="pi")
        patches, updates = _install_common_patches(
            outcome, ride_row=_completed_ride(payment_method="wallet"), already_paid=True
        )
        get_ride_mock = AsyncMock(
            side_effect=[
                _completed_ride(payment_method="wallet"),
                _completed_ride(payment_method="wallet", payment_status="processing"),
            ]
        )

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(patch("backend.routes.rides.db_supabase.get_ride", get_ride_mock))

            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
class TestOpenInvoiceGuard:
    """Codex P1 (62i3): once an admin emails a payable invoice, in-app charging
    must be blocked so the rider can't be collected twice. The guard raises
    before any Stripe call, so only get_ride needs stubbing."""

    async def test_finalized_invoice_blocks_in_app_charge(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride(stripe_invoice_id="in_admin_123")
        with patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert exc.value.status_code == 409
        # Codex round-5 (81SZ): structured code so the rider app shows the
        # pay-by-email instruction instead of looping on Change Card.
        assert isinstance(exc.value.detail, dict)
        assert exc.value.detail["code"] == "invoice_issued"

    async def test_fresh_pending_claim_blocks(self):
        from datetime import datetime, timezone

        from backend.routes import rides as rides_mod

        fresh = _completed_ride(stripe_invoice_id=f"pending:{datetime.now(timezone.utc).timestamp()}:u1")
        with patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=fresh)):
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})
        assert exc.value.status_code == 409

    async def test_stale_pending_claim_also_blocks(self):
        """Codex round-3 (#2): in-app charging must NOT unblock by claim age — a
        stale 'pending:' sentinel blocks just like a fresh one. Recovery is
        admin-side (crash-safe), never by silently re-opening the in-app charge."""
        from datetime import datetime, timedelta, timezone

        from backend.routes import rides as rides_mod

        stale = _completed_ride(
            stripe_invoice_id=f"pending:{(datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()}:u1"
        )
        with patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=stale)):
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})
        assert exc.value.status_code == 409


@pytest.mark.asyncio
class TestAuthorization:
    async def test_other_rider_cannot_pay_for_this_ride(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": "someone_else"})

        assert exc_info.value.status_code == 403
