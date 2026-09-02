"""Automatic-receipt cutover: skip direct send when an outbox row exists."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_RIDE = {"id": "ride-1", "rider_id": "rider-1", "tip_amount": "0"}


async def test_auto_receipt_is_queued_delegates_to_outbox_row_lookup():
    from services.outbox_receipts import auto_receipt_is_queued

    with patch(
        "services.outbox_receipts.is_auto_receipt_queued",
        AsyncMock(return_value=True),
    ) as lookup:
        assert await auto_receipt_is_queued("ride-1") is True
    lookup.assert_awaited_once_with("ride-1")


async def test_maybe_send_skips_direct_send_when_outbox_row_exists():
    from services.outbox_receipts import maybe_send_auto_receipt

    spawn = MagicMock()
    with (
        patch("services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=True)),
        patch("services.payment_service.send_ride_receipt", AsyncMock()) as send,
    ):
        queued = await maybe_send_auto_receipt(_RIDE, "rider-1", Decimal("0"), spawn=spawn)
    assert queued is True
    spawn.assert_not_called()
    send.assert_not_called()


async def test_maybe_send_spawns_fallback_when_no_outbox_row():
    from services.outbox_receipts import maybe_send_auto_receipt

    spawn = MagicMock(side_effect=lambda coro: coro.close() if hasattr(coro, "close") else None)
    send = AsyncMock()
    with (
        patch("services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=False)),
        patch("services.payment_service.send_ride_receipt", send),
    ):
        queued = await maybe_send_auto_receipt(_RIDE, "rider-1", Decimal("1.50"), spawn=spawn)
    assert queued is True
    spawn.assert_called_once()
    send.assert_called_once()


async def test_maybe_send_awaits_fallback_when_no_spawn():
    from services.outbox_receipts import maybe_send_auto_receipt

    send = AsyncMock(return_value=True)
    with (
        patch("services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=False)),
        patch("services.payment_service.send_ride_receipt", send),
    ):
        result = await maybe_send_auto_receipt(_RIDE, "rider-1", Decimal("0"))
    assert result is True
    send.assert_awaited_once()


async def test_lookup_failure_uses_direct_fallback():
    from services.outbox_receipts import maybe_send_auto_receipt

    spawn = MagicMock(side_effect=lambda coro: coro.close() if hasattr(coro, "close") else None)
    with (
        patch(
            "services.outbox_receipts.is_auto_receipt_queued",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch("services.payment_service.send_ride_receipt", AsyncMock()),
    ):
        await maybe_send_auto_receipt(_RIDE, "rider-1", Decimal("0"), spawn=spawn)
    spawn.assert_called_once()


async def test_process_payment_skips_spawn_when_outbox_queued():
    from backend.routes.rides.payments import ProcessPaymentRequest, process_payment
    from backend.services.payment_service import PaymentResult

    ride = {
        "id": "ride-1",
        "rider_id": "rider-1",
        "status": "completed",
        "payment_status": "pending",
        "payment_method": "card",
        "total_fare": "18.50",
        "grand_total": "18.50",
        "tip_amount": "0",
        "fare_breakdown_snapshot": {"lines": []},
    }
    spawn = MagicMock(side_effect=lambda coro: coro.close())
    with (
        patch("backend.routes.rides.payments._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(
            "backend.routes.rides.payments._deps.db_supabase.update_one",
            AsyncMock(return_value={"id": "ride-1"}),
        ),
        patch("backend.routes.rides.payments._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.payments.settle_card",
            AsyncMock(return_value=PaymentResult(success=True, charged_amount="18.50")),
        ),
        patch("backend.routes.rides.payments._deps.spawn", spawn),
        patch(
            "backend.routes.rides.payments._fire_purchase_conversion",
            MagicMock(),
        ),
        patch(
            "backend.routes.rides.payments.maybe_send_auto_receipt",
            AsyncMock(return_value=True),
        ) as maybe,
    ):
        result = await process_payment(
            "ride-1",
            ProcessPaymentRequest(tip_amount=Decimal("0")),
            current_user={"id": "rider-1"},
        )
    assert result["email_sent"] is True
    maybe.assert_awaited_once()
    spawn.assert_not_called()


async def test_invoice_paid_uses_outbox_gate_and_skips_direct_send_when_queued():
    from backend.routes.webhooks import _handle_ride_invoice_paid

    ride = {
        "id": "ride-1",
        "rider_id": "rider-1",
        "payment_status": "failed",
        "tip_amount": "0",
    }
    send = AsyncMock(return_value=True)
    maybe = AsyncMock(return_value=True)
    with (
        patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock()),
        patch("backend.services.payment_service.record_payment_event", AsyncMock()),
        patch("services.payment_service.record_payment_event", AsyncMock()),
        patch("backend.services.payment_service._tip_ride_update", return_value={}),
        patch("services.payment_service._tip_ride_update", return_value={}),
        patch("backend.services.outbox_receipts.maybe_send_auto_receipt", maybe),
        patch("services.outbox_receipts.maybe_send_auto_receipt", maybe),
        patch("backend.services.payment_service.send_ride_receipt", send),
        patch("services.payment_service.send_ride_receipt", send),
        patch("backend.socket_manager.manager.send_personal_message", AsyncMock()),
        patch("socket_manager.manager.send_personal_message", AsyncMock()),
    ):
        await _handle_ride_invoice_paid(
            {"id": "in_1", "amount_paid": 500, "payment_intent": "pi_1"},
            "ride-1",
            "evt_1",
        )
    maybe.assert_awaited()
    send.assert_not_called()


async def test_invoice_paid_falls_back_to_direct_send_when_not_queued():
    from backend.routes.webhooks import _handle_ride_invoice_paid

    ride = {
        "id": "ride-1",
        "rider_id": "rider-1",
        "payment_status": "failed",
        "tip_amount": "0",
    }
    send = AsyncMock(return_value=True)
    with (
        patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock()),
        patch("backend.services.payment_service.record_payment_event", AsyncMock()),
        patch("services.payment_service.record_payment_event", AsyncMock()),
        patch("backend.services.payment_service._tip_ride_update", return_value={}),
        patch("services.payment_service._tip_ride_update", return_value={}),
        patch("services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=False)),
        patch("backend.services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=False)),
        patch("backend.services.payment_service.send_ride_receipt", send),
        patch("services.payment_service.send_ride_receipt", send),
        patch("backend.socket_manager.manager.send_personal_message", AsyncMock()),
        patch("socket_manager.manager.send_personal_message", AsyncMock()),
    ):
        await _handle_ride_invoice_paid(
            {"id": "in_1", "amount_paid": 500, "payment_intent": "pi_1"},
            "ride-1",
            "evt_1",
        )
    send.assert_awaited()


async def test_preauth_skips_direct_receipt_when_outbox_queued():
    from backend.services.payment_service import PaymentResult
    from backend.utils.preauth_capture import _capture_one

    receipt = AsyncMock(return_value=True)
    with (
        patch(
            "backend.utils.preauth_capture.settle_card",
            AsyncMock(return_value=PaymentResult(success=True)),
        ),
        patch("backend.utils.preauth_capture.send_ride_receipt", receipt),
        patch("backend.services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=True)),
        patch("services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=True)),
    ):
        await _capture_one(
            {
                "id": "ride-1",
                "rider_id": "rider-1",
                "grand_total": "25.00",
                "tip_amount": "0",
            }
        )
    receipt.assert_not_awaited()


async def test_guest_corporate_skips_direct_receipt_when_outbox_queued():
    import services.payment_service as ps

    ride = {
        "id": "ride-1",
        "rider_id": "guest-1",
        "guest_booking": True,
        "payment_method": "company_allowance",
        "status": "completed",
        "grand_total": "31.50",
    }
    receipt = AsyncMock(return_value=True)
    with (
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=ride)),
        patch.object(ps.db_supabase, "update_one", AsyncMock(return_value={"id": "ride-1"})),
        patch.object(
            ps,
            "settle_corporate",
            AsyncMock(return_value=ps.PaymentResult(success=True, charged_amount=Decimal("31.50"))),
        ),
        patch.object(ps, "send_ride_receipt", receipt),
        patch.object(ps, "_fire_guest_purchase_conversion", AsyncMock()),
        patch("services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=True)),
        patch("backend.services.outbox_receipts.auto_receipt_is_queued", AsyncMock(return_value=True)),
    ):
        await ps.auto_settle_guest_corporate("ride-1")
    receipt.assert_not_awaited()


def test_manual_resend_is_not_outbox_gated_and_webhooks_use_the_gate():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    receipts = (root / "routes" / "rides" / "receipts.py").read_text(encoding="utf-8")
    admin = (root / "routes" / "admin" / "rides.py").read_text(encoding="utf-8")
    webhooks = (root / "routes" / "webhooks.py").read_text(encoding="utf-8")
    assert "maybe_send_auto_receipt" not in receipts
    assert "maybe_send_auto_receipt" not in admin
    assert webhooks.count("maybe_send_auto_receipt") >= 2
    assert "await send_ride_receipt(" not in webhooks
