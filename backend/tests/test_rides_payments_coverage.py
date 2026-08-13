"""Targeted branch coverage for routes/rides/payments.py (add_tip and
process_payment) — ACTION_ITEMS.md A1.

Covers: the zero-after-rounding tip guard, the fare/earnings snapshot
rebuild branches in add_tip, the driver-lookup exception branch, the
driver-notify block (WS + push), process_payment's non-wallet
'processing' short-circuit, the negative/over-max tip guards, the wallet
re-drive Redis-lock branches (both success and already-locked), the
guard_row=None re-read branches, the tip-snapshot rebuild inside
process_payment, the wallet-lock release exception, and the
fare_breakdown_snapshot write-failure branch after a successful charge.

All deps mocked via ``backend.routes.rides.payments._deps`` (the shared
_deps module instance used by the split rides package) — no real DB/Stripe.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

RIDER_ID = "rider-pay-1"
RIDE_ID = "ride-pay-1"


def _completed_ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "completed",
        "payment_method": "card",
        "payment_status": "pending",
        "total_fare": 18.5,
        "grand_total": 18.5,
        "tip_amount": 0,
        "driver_earnings": 15.0,
    }
    row.update(extra)
    return row


# ── add_tip ──────────────────────────────────────────────────────────────


async def test_add_tip_rounds_to_zero_rejected():
    from backend.routes.rides.payments import TipRequest, add_tip

    ride = _completed_ride()
    with patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
        req = TipRequest(amount=Decimal("0.001"))
        with pytest.raises(HTTPException) as exc:
            await add_tip(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert exc.value.status_code == 400
    assert "greater than zero" in exc.value.detail


async def test_add_tip_rebuilds_fare_and_earnings_snapshots_and_notifies_driver():
    from backend.routes.rides.payments import TipRequest, add_tip

    ride = _completed_ride(
        driver_id="drv-1",
        fare_breakdown_snapshot={"lines": [{"label": "Base fare", "amount": "15.00", "type": "fare"}]},
        driver_earnings_snapshot={"fare": 15.0, "tip": 0, "incentive": 0, "tax": 0, "cancel_fee": 0},
    )
    driver_row = {"id": "drv-1", "user_id": "drv-user-1"}
    rider_row = {"id": RIDER_ID, "first_name": "Alex"}

    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.rides.payments._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
        patch("backend.routes.rides.payments._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver_row)),
        patch("backend.routes.rides.payments._deps.db_supabase.get_user_by_id", AsyncMock(return_value=rider_row)),
        patch("backend.routes.rides.payments._deps.manager.send_personal_message", AsyncMock()) as mock_ws,
        patch("backend.routes.rides.payments._push_in_background") as mock_push,
    ):
        req = TipRequest(amount=Decimal("5.00"))
        result = await add_tip(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert result["success"] is True
    assert Decimal(result["tip_amount"]) == Decimal("5.00")
    mock_ws.assert_awaited_once()
    mock_push.assert_called_once()


async def test_add_tip_driver_lookup_exception_is_swallowed():
    from backend.routes.rides.payments import TipRequest, add_tip

    ride = _completed_ride(driver_id="drv-1")
    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.rides.payments._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.payments._deps.db_supabase.get_driver_by_id",
            AsyncMock(side_effect=RuntimeError("driver lookup failed")),
        ),
        patch("backend.routes.rides.payments._deps.manager.send_personal_message", AsyncMock()) as mock_ws,
    ):
        req = TipRequest(amount=Decimal("5.00"))
        result = await add_tip(RIDE_ID, req, current_user={"id": RIDER_ID})

    # Tip is still persisted even though driver notification could not be resolved.
    assert result["success"] is True
    mock_ws.assert_not_called()


async def test_add_tip_ws_notify_failure_is_swallowed():
    from backend.routes.rides.payments import TipRequest, add_tip

    ride = _completed_ride(driver_id="drv-1")
    driver_row = {"id": "drv-1", "user_id": "drv-user-1"}
    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.rides.payments._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
        patch("backend.routes.rides.payments._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver_row)),
        patch("backend.routes.rides.payments._deps.db_supabase.get_user_by_id", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.payments._deps.manager.send_personal_message",
            AsyncMock(side_effect=RuntimeError("ws down")),
        ),
        patch("backend.routes.rides.payments._push_in_background") as mock_push,
    ):
        req = TipRequest(amount=Decimal("5.00"))
        result = await add_tip(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert result["success"] is True
    mock_push.assert_called_once()


# ── process_payment ─────────────────────────────────────────────────────


async def test_process_payment_non_wallet_processing_is_already_paid():
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment

    ride = _completed_ride(payment_status="processing", payment_method="card")
    with patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
        req = ProcessPaymentRequest(tip_amount=Decimal("0"))
        result = await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert result["already_paid"] is True


async def test_process_payment_negative_tip_rejected():
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment

    ride = _completed_ride()
    with patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
        with pytest.raises(HTTPException) as exc:
            req = ProcessPaymentRequest.model_construct(tip_amount=Decimal("-1"), payment_method_id=None)
            await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert exc.value.status_code == 400
    assert "negative" in exc.value.detail


async def test_process_payment_tip_over_max_rejected():
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment

    ride = _completed_ride()
    with patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
        req = ProcessPaymentRequest.model_construct(tip_amount=Decimal("501"), payment_method_id=None)

        with pytest.raises(HTTPException) as exc:
            await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert exc.value.status_code == 400
    assert "maximum" in exc.value.detail


async def test_process_payment_wallet_redrive_lock_blocks_concurrent_retry():
    """Wallet ride stuck at 'processing': Redis NX lock already held by
    another in-flight retry -> 409 before the DB claim update."""
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment

    ride = _completed_ride(payment_method="wallet", payment_status="processing")
    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=False)),
    ):
        req = ProcessPaymentRequest(tip_amount=Decimal("0"))
        with pytest.raises(HTTPException) as exc:
            await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert exc.value.status_code == 409
    assert "retry already in progress" in exc.value.detail


async def test_process_payment_wallet_redrive_lock_error_fails_closed_503():
    """2026-08-11 P1 fix: redis_set_nx now raises on a real Redis error
    instead of silently falling back per-replica. Unlike the background-
    loop throttle locks, this one IS the exclusivity guard for a
    concurrent re-drive of a stuck 'processing' wallet settlement -- must
    fail CLOSED (503, ask the client to retry) rather than silently
    proceeding as if exclusivity didn't matter on a money path."""
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment

    ride = _completed_ride(payment_method="wallet", payment_status="processing")
    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.utils.redis_client.redis_set_nx", AsyncMock(side_effect=ConnectionError("redis down"))),
    ):
        req = ProcessPaymentRequest(tip_amount=Decimal("0"))
        with pytest.raises(HTTPException) as exc:
            await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert exc.value.status_code == 503


async def test_process_payment_wallet_redrive_success_logs_and_settles():
    """Wallet ride 'processing' -> lock acquired -> claim succeeds (marker
    no-op) -> settle_wallet succeeds -> lock released."""
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment
    from backend.services.payment_service import PaymentResult

    ride = _completed_ride(payment_method="wallet", payment_status="processing")
    result_obj = PaymentResult(success=True, charged_amount="18.50")

    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
        patch("backend.utils.redis_client.redis_delete", AsyncMock()) as mock_del,
        patch("backend.routes.rides.payments._deps.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
        patch("backend.routes.rides.payments._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
        patch("backend.routes.rides.payments.settle_wallet", AsyncMock(return_value=result_obj)),
        patch("backend.routes.rides.payments._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        req = ProcessPaymentRequest(tip_amount=Decimal("0"))
        result = await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert result["success"] is True
    mock_del.assert_awaited_once()


async def test_process_payment_guard_lost_race_paid_returns_already_paid():
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment

    ride = _completed_ride()
    fresh = _completed_ride(payment_status="paid")
    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, fresh])),
        patch("backend.routes.rides.payments._deps.db_supabase.update_one", AsyncMock(return_value=None)),
    ):
        req = ProcessPaymentRequest(tip_amount=Decimal("0"))
        result = await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert result["already_paid"] is True


async def test_process_payment_guard_lost_race_wallet_returns_409():
    """Wallet ride whose claim was lost and the re-read is still
    'processing' -> retryable 409, never a false already-paid."""
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment

    ride = _completed_ride(payment_method="wallet", payment_status="pending")
    fresh = _completed_ride(payment_method="wallet", payment_status="processing")
    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, fresh])),
        patch("backend.routes.rides.payments._deps.db_supabase.update_one", AsyncMock(return_value=None)),
    ):
        req = ProcessPaymentRequest(tip_amount=Decimal("0"))
        with pytest.raises(HTTPException) as exc:
            await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert exc.value.status_code == 409


async def test_process_payment_rebuilds_tip_snapshot_and_writes_after_success():
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment
    from backend.services.payment_service import PaymentResult

    ride = _completed_ride(
        fare_breakdown_snapshot={"lines": [{"label": "Base fare", "amount": "18.50", "type": "fare"}]},
    )
    result_obj = PaymentResult(success=True, charged_amount="20.50")

    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.rides.payments._deps.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
        patch("backend.routes.rides.payments._deps.db_supabase.update_ride", AsyncMock(return_value=None)) as mock_upd,
        patch("backend.routes.rides.payments.settle_card", AsyncMock(return_value=result_obj)),
        patch("backend.routes.rides.payments._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        req = ProcessPaymentRequest(tip_amount=Decimal("2.00"))
        result = await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert result["success"] is True
    # fare_breakdown_snapshot must have been persisted with the tip line rebuilt.
    snap_write = next(c for c in mock_upd.await_args_list if "fare_breakdown_snapshot" in c.args[1])
    lines = snap_write.args[1]["fare_breakdown_snapshot"]["lines"]
    assert any(ln["type"] == "tip" for ln in lines)


async def test_process_payment_snapshot_write_failure_after_success_is_swallowed():
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment
    from backend.services.payment_service import PaymentResult

    ride = _completed_ride(
        fare_breakdown_snapshot={"lines": [{"label": "Base fare", "amount": "18.50", "type": "fare"}]},
    )
    result_obj = PaymentResult(success=True, charged_amount="20.50")

    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.rides.payments._deps.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
        patch(
            "backend.routes.rides.payments._deps.db_supabase.update_ride",
            AsyncMock(side_effect=RuntimeError("snapshot write failed")),
        ),
        patch("backend.routes.rides.payments.settle_card", AsyncMock(return_value=result_obj)),
        patch("backend.routes.rides.payments._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        req = ProcessPaymentRequest(tip_amount=Decimal("2.00"))
        result = await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    # Payment already succeeded — a failed cosmetic snapshot write must not
    # surface as an error to the rider.
    assert result["success"] is True


async def test_process_payment_wallet_lock_release_failure_is_swallowed():
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment
    from backend.services.payment_service import PaymentResult

    ride = _completed_ride(payment_method="wallet", payment_status="processing")
    result_obj = PaymentResult(success=True, charged_amount="18.50")

    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
        patch("backend.utils.redis_client.redis_delete", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch("backend.routes.rides.payments._deps.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
        patch("backend.routes.rides.payments._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
        patch("backend.routes.rides.payments.settle_wallet", AsyncMock(return_value=result_obj)),
        patch("backend.routes.rides.payments._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        req = ProcessPaymentRequest(tip_amount=Decimal("0"))
        result = await process_payment(RIDE_ID, req, current_user={"id": RIDER_ID})

    assert result["success"] is True
