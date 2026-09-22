from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest


def _ride(method="wallet"):
    return {
        "id": "ride-fee-1", "scheduled_time": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "payment_method": method, "payment_method_id": "pm_saved",
    }


@pytest.mark.asyncio
async def test_wallet_notice_fee_records_only_actual_partial_collection():
    from backend.routes.rides.cancellation import _charge_scheduled_cancel_notice_fee

    op = {"id": "op-fee-1", "status": "requested"}
    wallet_update = AsyncMock(return_value={"applied_delta": "-0.50", "transaction_id": "txn1"})
    ride_update = AsyncMock(return_value={"id": "ride-fee-1"})
    with (
        patch("backend.routes.rides.cancellation._deps.get_app_settings", AsyncMock(return_value={
            "scheduled_ride_notice_window_fee_enabled": True,
            "scheduled_ride_notice_window_minutes": 60,
            "scheduled_ride_notice_window_fee_amount": "3.00",
        })),
        patch("backend.routes.rides.cancellation._deps.db_supabase.find_one", AsyncMock(return_value={"id": "wallet1"})),
        patch("backend.routes.rides.cancellation._deps.db_supabase.wallet_apply_delta", wallet_update),
        patch("backend.routes.rides.cancellation._deps.db_supabase.update_one", ride_update),
        patch("backend.utils.payment_operations.record_operation", AsyncMock(return_value=op)) as record,
        patch("backend.utils.payment_operations.update_operation", AsyncMock()) as update_op,
    ):
        await _charge_scheduled_cancel_notice_fee(_ride(), "rider1")

    assert record.await_args.kwargs["amount_cents"] == 300
    assert wallet_update.await_args.kwargs["clamp_to_floor"] is True
    assert update_op.await_args.kwargs["collected_cents"] == 50
    assert update_op.await_args.kwargs["payment_intent_id"] == "txn1"
    assert ride_update.await_args.args[2]["scheduled_notice_fee_amount"] == "0.50"
    assert ride_update.await_args.args[2]["scheduled_notice_fee_status"] == "paid"


@pytest.mark.asyncio
async def test_card_authentication_required_is_saved_without_ledger_success():
    from backend.routes.rides.cancellation import _charge_scheduled_cancel_notice_fee
    from backend.utils.stripe_charge import ChargeOutcome

    op = {"id": "op-fee-2", "status": "requested"}
    ride_update = AsyncMock(return_value={"id": "ride-fee-1"})
    ledger = AsyncMock()
    with (
        patch("backend.routes.rides.cancellation._deps.get_app_settings", AsyncMock(return_value={
            "scheduled_ride_notice_window_fee_enabled": True,
            "scheduled_ride_notice_window_minutes": 60,
            "scheduled_ride_notice_window_fee_amount": "3.00",
        })),
        patch("backend.routes.rides.cancellation._deps.db_supabase.get_user_by_id", AsyncMock(return_value={
            "stripe_customer_id": "cus1", "default_payment_method": "pm_saved",
        })),
        patch("backend.routes.rides.cancellation._deps.db_supabase.update_one", ride_update),
        patch("backend.routes.rides.cancellation._deps.charge_ancillary_fee", AsyncMock(return_value=ChargeOutcome(
            status="requires_action", payment_intent_id="pi_fee",
        ))),
        patch("backend.routes.rides.cancellation._deps.record_ledger_event", ledger),
        patch("backend.utils.payment_operations.record_operation", AsyncMock(return_value=op)),
        patch("backend.utils.payment_operations.update_operation", AsyncMock()) as update_op,
    ):
        await _charge_scheduled_cancel_notice_fee(_ride("card"), "rider1")

    assert update_op.await_args.kwargs["status"] == "requires_action"
    assert update_op.await_args.kwargs["payment_intent_id"] == "pi_fee"
    assert update_op.await_args.kwargs["collected_cents"] == 0
    assert ride_update.await_args.args[2]["scheduled_notice_fee_status"] == "requires_action"
    ledger.assert_not_awaited()
