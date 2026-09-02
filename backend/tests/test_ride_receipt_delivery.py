"""Structured ride-receipt delivery for the transactional outbox.

Covers hydration from ride_id, accepted/terminal/retryable propagation, and
no-recipient handling. Receipt arithmetic and GST/PST lines are unchanged —
see test_receipt_route_snapshot.py and receipt line-item tests.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _status_types():
    try:
        from utils.email_provider import EmailDeliveryResult, EmailDeliveryStatus
    except ImportError:
        from backend.utils.email_provider import EmailDeliveryResult, EmailDeliveryStatus
    return EmailDeliveryResult, EmailDeliveryStatus


def _accepted(message_id="msg-1"):
    EmailDeliveryResult, EmailDeliveryStatus = _status_types()
    return EmailDeliveryResult(
        status=EmailDeliveryStatus.accepted,
        provider="ses",
        message_id=message_id,
    )


def _terminal(code="no_recipient"):
    EmailDeliveryResult, EmailDeliveryStatus = _status_types()
    return EmailDeliveryResult(status=EmailDeliveryStatus.terminal_skip, error_code=code)


def _retryable(code="provider_unavailable"):
    EmailDeliveryResult, EmailDeliveryStatus = _status_types()
    return EmailDeliveryResult(status=EmailDeliveryStatus.retryable_failure, error_code=code)


_RIDE = {
    "id": "ride-1",
    "rider_id": "rider-1",
    "driver_id": "drv-1",
    "status": "completed",
    "payment_status": "paid",
    "grand_total": "12.00",
    "tip_amount": "0",
}


async def test_send_ride_receipt_result_hydrates_from_ride_id_and_accepts():
    EmailDeliveryResult, EmailDeliveryStatus = _status_types()
    from services import payment_service as ps

    rider = {"id": "rider-1", "email": "rider@example.com", "first_name": "Rae"}
    driver = {"id": "drv-1", "user_id": "du-1", "driver_code": "D1", "vehicle_make": "Toyota", "vehicle_model": "Camry"}
    driver_user = {"id": "du-1", "first_name": "Dee", "last_name": "Driver"}

    with (
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=dict(_RIDE))),
        patch.object(ps.db_supabase, "get_user_by_id", AsyncMock(side_effect=[rider, driver_user])),
        patch.object(ps.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch(
            "utils.email_receipt.send_receipt_email_result",
            AsyncMock(return_value=_accepted()),
        ) as send,
    ):
        result = await ps.send_ride_receipt_result("ride-1")

    assert result.status == EmailDeliveryStatus.accepted
    assert result.message_id == "msg-1"
    send.assert_awaited_once()
    ride_arg, rider_arg, driver_arg, tip = send.await_args.args[:4]
    assert ride_arg["id"] == "ride-1"
    assert rider_arg["id"] == "rider-1"
    assert driver_arg["driver_code"] == "D1"
    assert tip == 0.0


async def test_send_ride_receipt_result_no_recipient_is_terminal():
    EmailDeliveryResult, EmailDeliveryStatus = _status_types()
    from services import payment_service as ps

    rider = {"id": "rider-1", "email": ""}
    with (
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=dict(_RIDE))),
        patch.object(ps.db_supabase, "get_user_by_id", AsyncMock(return_value=rider)),
        patch.object(ps.db_supabase, "get_driver_by_id", AsyncMock(return_value=None)),
        patch(
            "utils.email_receipt.send_receipt_email_result",
            AsyncMock(return_value=_terminal("no_recipient")),
        ),
    ):
        result = await ps.send_ride_receipt_result("ride-1")
    assert result.status == EmailDeliveryStatus.terminal_skip
    assert result.error_code == "no_recipient"


async def test_send_ride_receipt_result_propagates_retryable_failure():
    EmailDeliveryResult, EmailDeliveryStatus = _status_types()
    from services import payment_service as ps

    rider = {"id": "rider-1", "email": "rider@example.com"}
    with (
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=dict(_RIDE))),
        patch.object(ps.db_supabase, "get_user_by_id", AsyncMock(return_value=rider)),
        patch.object(ps.db_supabase, "get_driver_by_id", AsyncMock(return_value=None)),
        patch(
            "utils.email_receipt.send_receipt_email_result",
            AsyncMock(return_value=_retryable()),
        ),
    ):
        result = await ps.send_ride_receipt_result("ride-1")
    assert result.status == EmailDeliveryStatus.retryable_failure
    assert result.error_code == "provider_unavailable"


async def test_send_ride_receipt_result_missing_ride_is_retryable():
    EmailDeliveryResult, EmailDeliveryStatus = _status_types()
    from services import payment_service as ps

    with patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=None)):
        result = await ps.send_ride_receipt_result("missing")
    assert result.status == EmailDeliveryStatus.retryable_failure
    assert result.error_code == "ride_not_found"


async def test_send_receipt_email_result_no_email_is_terminal_without_sending():
    EmailDeliveryResult, EmailDeliveryStatus = _status_types()
    from utils.email_receipt import send_receipt_email_result

    with patch("utils.email_receipt.send_transactional_email_result", AsyncMock()) as send:
        result = await send_receipt_email_result(_RIDE, {"id": "rider-1"}, None, 0)
    assert result.status == EmailDeliveryStatus.terminal_skip
    assert result.error_code == "no_recipient"
    send.assert_not_awaited()


async def test_bool_send_ride_receipt_still_true_on_accepted():
    from services import payment_service as ps

    with (
        patch.object(ps.db_supabase, "get_user_by_id", AsyncMock(return_value={"id": "rider-1", "email": "a@b.c"})),
        patch.object(ps.db_supabase, "get_driver_by_id", AsyncMock(return_value=None)),
        patch("utils.email_receipt.send_receipt_email", AsyncMock(return_value=True)),
    ):
        assert await ps.send_ride_receipt(dict(_RIDE), "rider-1", Decimal("0")) is True
