"""Regression coverage for services/cancellation_service.py::pay_driver_cancellation_fee.

Nothing in the existing test suite exercises this function directly — every
other cancellation test mocks it out entirely (`patch(".../pay_driver_
cancellation_fee", AsyncMock(...))`). This file closes that gap and pins
the driver-directed push notification's `target_app="driver"` kwarg
(ACTION_ITEMS.md N10, driver batch).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

DRIVER_ID = "driver_1"
DRIVER_USER_ID = "driver_user_1"
RIDE_ID = "ride_1"
ACTOR_ID = "rider_1"

_DRIVER_ROW = {"id": DRIVER_ID, "user_id": DRIVER_USER_ID}
_WALLET_ROW = {"id": "wallet_1", "user_id": DRIVER_USER_ID, "balance": 10.0}


async def _run(fee=Decimal("5.00")):
    from backend.services.cancellation_service import pay_driver_cancellation_fee

    return await pay_driver_cancellation_fee(
        ride_id=RIDE_ID,
        driver_id=DRIVER_ID,
        fee=fee,
        actor_user_id=ACTOR_ID,
        ride_status_at_cancel="driver_assigned",
    )


async def test_pay_driver_cancellation_fee_notifies_driver_app():
    """The payout push must pass target_app="driver" — the recipient is
    always the driver whose wallet was just credited."""
    push = AsyncMock()
    with (
        patch(
            "backend.services.cancellation_service.db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER_ROW)
        ),
        patch("backend.services.cancellation_service.db_supabase.find_one", AsyncMock(return_value=_WALLET_ROW)),
        patch("backend.services.cancellation_service.db_supabase.update_one", AsyncMock()),
        patch("backend.services.cancellation_service.db_supabase.insert_one", AsyncMock()),
        patch("backend.services.cancellation_service.send_push_notification", push),
    ):
        result = await _run()

    assert result is True
    push.assert_awaited_once()
    args, kwargs = push.await_args
    assert args[0] == DRIVER_USER_ID
    assert kwargs["target_app"] == "driver"
    assert kwargs["data"]["type"] == "cancellation_fee_paid"


async def test_pay_driver_cancellation_fee_no_driver_row_skips_push():
    push = AsyncMock()
    with (
        patch("backend.services.cancellation_service.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
        patch("backend.services.cancellation_service.send_push_notification", push),
    ):
        result = await _run()

    assert result is False
    push.assert_not_awaited()


async def test_pay_driver_cancellation_fee_no_wallet_skips_push():
    push = AsyncMock()
    with (
        patch(
            "backend.services.cancellation_service.db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER_ROW)
        ),
        patch("backend.services.cancellation_service.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("backend.services.cancellation_service.send_push_notification", push),
    ):
        result = await _run()

    assert result is False
    push.assert_not_awaited()
