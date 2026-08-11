"""
A1c Sub-tier C coverage: backend/utils/preauth_capture.py (72% -> target 90%+).

`test_preauth_capture.py` covers `_capture_tick`'s tip-window/claim-race
branches and `_capture_one`'s success/failure/settle-raises branches. This
file closes:

- `_capture_tick`: the `rides` fetch-exception branch (logged, tick returns
  without processing).
- `_capture_one`: the Meta Purchase-conversion hook firing on a genuinely
  new capture (`result.success and not result.already_paid`), that hook
  being SKIPPED on an idempotent replay (`already_paid=True`), and the
  receipt-send exception being swallowed (logged, not re-raised) rather
  than aborting the capture.
- `preauth_capture_loop`: lock-not-acquired skips the tick and re-loops,
  lock-acquired runs the tick, a tick exception is caught and logged (loop
  survives), and the heartbeat/jitter-sleep call on every iteration.
- `_pod_id`'s hostname:pid shape, `_d`/`_round`'s None/Decimal-string
  coercion.

Patch target follows the established pattern in `test_preauth_capture.py`:
`backend.utils.preauth_capture.<name>`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

P = "backend.utils.preauth_capture."


def _ride(minutes_ago=60, auth_status="authorized"):
    completed = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": "ride_sw_1",
        "rider_id": "rider_sw_1",
        "payment_method": "card",
        "payment_status": "pending",
        "auth_status": auth_status,
        "total_fare": "25.00",
        "grand_total": "25.00",
        "tip_amount": "0",
        "ride_completed_at": completed,
    }


def _result(success=True, **kw):
    from backend.services.payment_service import PaymentResult

    return PaymentResult(success=success, **kw)


async def test_capture_tick_fetch_exception_is_logged_and_returns():
    from backend.utils.preauth_capture import _capture_tick

    with patch(P + "db.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        # Must not raise.
        await _capture_tick()


async def test_capture_one_fires_meta_purchase_conversion_on_new_capture():
    from backend.utils.preauth_capture import _capture_one

    conversion = AsyncMock()
    with (
        patch(
            P + "settle_card", AsyncMock(return_value=_result(success=True, charged_amount="25.00", already_paid=False))
        ),
        patch(P + "send_ride_receipt", AsyncMock(return_value=True)),
        patch("backend.services.meta_conversions_service.send_ride_purchase_for_ride", conversion),
    ):
        await _capture_one(_ride())
    conversion.assert_awaited_once()


async def test_capture_one_skips_meta_conversion_when_already_paid():
    from backend.utils.preauth_capture import _capture_one

    conversion = AsyncMock()
    with (
        patch(
            P + "settle_card", AsyncMock(return_value=_result(success=True, charged_amount="25.00", already_paid=True))
        ),
        patch(P + "send_ride_receipt", AsyncMock(return_value=True)),
        patch("backend.services.meta_conversions_service.send_ride_purchase_for_ride", conversion),
    ):
        await _capture_one(_ride())
    conversion.assert_not_awaited()


async def test_capture_one_receipt_exception_is_swallowed():
    from backend.utils.preauth_capture import _capture_one

    with (
        patch(
            P + "settle_card", AsyncMock(return_value=_result(success=True, charged_amount="25.00", already_paid=True))
        ),
        patch(P + "send_ride_receipt", AsyncMock(side_effect=RuntimeError("email down"))),
    ):
        # Must not raise even though the receipt send blew up.
        await _capture_one(_ride())


# ---------------------------------------------------------------------------
# preauth_capture_loop
# ---------------------------------------------------------------------------


async def test_loop_lock_not_acquired_skips_tick_and_sleeps():
    tick = AsyncMock()

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(return_value=False)),
        patch(P + "_capture_tick", tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_record_heartbeat"),
    ):
        from backend.utils.preauth_capture import preauth_capture_loop

        with pytest.raises(asyncio.CancelledError):
            await preauth_capture_loop()
    tick.assert_not_awaited()


async def test_loop_lock_acquired_runs_tick():
    tick = AsyncMock()

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(return_value=True)),
        patch(P + "_capture_tick", tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_record_heartbeat") as mock_hb,
    ):
        from backend.utils.preauth_capture import preauth_capture_loop

        with pytest.raises(asyncio.CancelledError):
            await preauth_capture_loop()
    tick.assert_awaited_once()
    mock_hb.assert_called_once()


async def test_loop_survives_a_redis_lock_error_and_still_runs_the_tick():
    """2026-08-11 P1 fix: redis_set_nx now raises on a real Redis error
    instead of silently falling back per-replica. Previously this call sat
    directly in `while True:` with no surrounding try/except -- an
    unhandled exception here would have killed the loop task permanently."""
    tick = AsyncMock()

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(side_effect=ConnectionError("redis down"))),
        patch(P + "_capture_tick", tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_record_heartbeat") as mock_hb,
    ):
        from backend.utils.preauth_capture import preauth_capture_loop

        with pytest.raises(asyncio.CancelledError):
            await preauth_capture_loop()
    tick.assert_awaited_once()
    mock_hb.assert_called_once()


async def test_loop_survives_tick_exception():
    async def failing_tick():
        raise RuntimeError("boom")

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(return_value=True)),
        patch(P + "_capture_tick", failing_tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_record_heartbeat"),
    ):
        from backend.utils.preauth_capture import preauth_capture_loop

        with pytest.raises(asyncio.CancelledError):
            await preauth_capture_loop()


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def test_pod_id_shape():
    from backend.utils.preauth_capture import _pod_id

    pod_id = _pod_id()
    assert ":" in pod_id


def test_d_coerces_none_to_zero():
    from backend.utils.preauth_capture import _d

    assert _d(None) == Decimal("0")
    assert _d("12.34") == Decimal("12.34")


def test_round_half_up_two_places():
    from backend.utils.preauth_capture import _round

    assert _round(Decimal("10.005")) == Decimal("10.01")
