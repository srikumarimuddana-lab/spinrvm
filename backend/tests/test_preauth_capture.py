"""
Unit tests for the pre-authorization capture sweeper
backend/utils/preauth_capture.py.

Pins the subtask-5 behavior: after the tip window elapses, a completed card
ride with an open hold is atomically claimed and settled via settle_card
(which captures the hold). Covered:
  - tip window still open  → skipped, no claim
  - window elapsed         → atomic claim then settle_card called
  - lost claim race        → settle_card NOT called
  - settlement success     → receipt sent
  - settlement failure     → handed to retry loop (no raise)

db, settle_card, send_ride_receipt are patched on the module's bound names.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils.preauth_capture import TIP_WINDOW_MINUTES

# Window-relative rather than hardcoded minutes: TIP_WINDOW_MINUTES has already
# changed once (20 -> 5), and a literal that happens to land exactly on the new
# boundary flips a test's meaning silently.
INSIDE_WINDOW = max(1, TIP_WINDOW_MINUTES - 1)
PAST_WINDOW = TIP_WINDOW_MINUTES * 4


def _ride(minutes_ago=None, auth_status="authorized"):
    minutes_ago = PAST_WINDOW if minutes_ago is None else minutes_ago
    completed = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": "ride_sw_1",
        "rider_id": "rider_sw_1",
        "payment_method": "card",
        "payment_method_id": "pm_sw",
        "payment_status": "pending",
        "auth_status": auth_status,
        "authorized_amount": "35.00",
        "payment_intent_id": "pi_hold",
        "total_fare": "25.00",
        "grand_total": "25.00",
        "tip_amount": "0",
        "ride_completed_at": completed,
    }


def _result(success=True, **kw):
    from backend.services.payment_service import PaymentResult

    return PaymentResult(success=success, **kw)


def _patches(*, rows, claim=None, settle=None):
    P = "backend.utils.preauth_capture."
    settle_mock = AsyncMock(return_value=settle if settle is not None else _result())
    claim_mock = AsyncMock(return_value=claim)
    return (
        [
            patch(P + "db.get_rows", AsyncMock(return_value=rows)),
            patch(P + "db.update_one", claim_mock),
            patch(P + "settle_card", settle_mock),
            patch(P + "send_ride_receipt", AsyncMock(return_value=True)),
        ],
        settle_mock,
        claim_mock,
    )


@pytest.mark.asyncio
class TestCaptureTick:
    async def test_window_open_skips_without_claim(self):
        from contextlib import ExitStack

        from backend.utils.preauth_capture import _capture_tick

        patches, settle, claim = _patches(rows=[_ride(minutes_ago=INSIDE_WINDOW)], claim={"id": "ride_sw_1"})
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _capture_tick()

        claim.assert_not_called()  # tip window still open
        settle.assert_not_called()

    async def test_window_elapsed_claims_then_settles(self):
        from contextlib import ExitStack

        from backend.utils.preauth_capture import _capture_tick

        patches, settle, claim = _patches(rows=[_ride(minutes_ago=PAST_WINDOW)], claim={"id": "ride_sw_1"})
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _capture_tick()

        claim.assert_called_once()
        # claim asserts pending + auth_status, sets processing
        claim_filter = claim.call_args.args[1]
        assert claim_filter["payment_status"] == "pending"
        assert claim_filter["auth_status"] == "authorized"
        settle.assert_called_once()
        # settle_card called with total_charge = grand_total + tip (25 + 0)
        assert settle.call_args.args[3] == Decimal("25.00")

    async def test_lost_claim_race_does_not_settle(self):
        from contextlib import ExitStack

        from backend.utils.preauth_capture import _capture_tick

        patches, settle, claim = _patches(rows=[_ride(minutes_ago=PAST_WINDOW)], claim=None)  # lost race
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _capture_tick()

        claim.assert_called_once()
        settle.assert_not_called()


@pytest.mark.asyncio
class TestCaptureOne:
    async def test_success_sends_receipt(self):
        from contextlib import ExitStack

        from backend.utils.preauth_capture import _capture_one

        patches, settle, _ = _patches(rows=[], settle=_result(success=True, charged_amount="25.00"))
        receipt = AsyncMock(return_value=True)
        with ExitStack() as st:
            for p in patches[:3]:
                st.enter_context(p)
            st.enter_context(patch("backend.utils.preauth_capture.send_ride_receipt", receipt))
            await _capture_one(_ride())

        settle.assert_called_once()
        receipt.assert_called_once()

    async def test_failure_no_raise_no_receipt(self):
        from contextlib import ExitStack

        from backend.utils.preauth_capture import _capture_one

        patches, settle, _ = _patches(rows=[], settle=_result(success=False, error_code="card_declined"))
        receipt = AsyncMock(return_value=True)
        with ExitStack() as st:
            for p in patches[:3]:
                st.enter_context(p)
            st.enter_context(patch("backend.utils.preauth_capture.send_ride_receipt", receipt))
            await _capture_one(_ride())  # must not raise

        settle.assert_called_once()
        receipt.assert_not_called()

    async def test_settle_raises_is_swallowed(self):
        from contextlib import ExitStack

        from backend.utils.preauth_capture import _capture_one

        with ExitStack() as st:
            st.enter_context(
                patch("backend.utils.preauth_capture.settle_card", AsyncMock(side_effect=RuntimeError("boom")))
            )
            st.enter_context(patch("backend.utils.preauth_capture.send_ride_receipt", AsyncMock()))
            await _capture_one(_ride())  # must not raise
