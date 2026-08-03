"""
A1c Sub-tier C coverage: backend/utils/allowance_reset.py (68% -> target 90%+).

`test_c_allowance_reset_atomic.py` covers the CAS claim win/lose branches.
`test_corporate_allowance_reset.py` covers stale-allowance processing,
rollover skip, removed-member skip, and suspended-company skip. This file
closes:

- `run_allowance_reset_tick`: no corporate wallet found for the company
  (skip, no claim attempted), and one row's processing exception not
  aborting the batch (`logger.exception`, continue to the next row).
- `_add_one_month`: the day-clamp path (Jan 31 -> Feb 28/29) and a normal
  same-day month rollover.
- `allowance_reset_loop`: the happy tick (metrics/heartbeat/sleep), a tick
  exception being caught/logged/counted via `spinr_bgloop_errors_total`
  (loop survives), and the duration gauge being emitted on both outcomes.

Patch target follows the established pattern in
`test_c_allowance_reset_atomic.py`: `backend.utils.allowance_reset.<name>`.
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

P = "backend.utils.allowance_reset."

_ROW = {"id": "a1", "member_id": "m1", "period_end": "2026-01-01", "rollover": False}


async def test_no_wallet_skips_without_claim():
    from backend.utils import allowance_reset as ar

    with (
        patch(P + "list_allowances_due_for_reset", AsyncMock(return_value=[dict(_ROW)])),
        patch(P + "get_corporate_member_by_id", AsyncMock(return_value={"company_id": "c1", "status": "active"})),
        patch(P + "get_corporate_account_by_id", AsyncMock(return_value={"id": "c1", "status": "active"})),
        patch(P + "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
        patch(P + "reset_allowance_period", AsyncMock()) as claim,
        patch(P + "apply_reset", AsyncMock()) as apply_reset,
    ):
        processed = await ar.run_allowance_reset_tick()
    assert processed == 0
    claim.assert_not_awaited()
    apply_reset.assert_not_awaited()


async def test_one_row_exception_does_not_abort_batch():
    from backend.utils import allowance_reset as ar

    rows = [
        {"id": "bad", "member_id": "m-bad", "period_end": "2026-01-01", "rollover": False},
        {"id": "good", "member_id": "m-good", "period_end": "2026-01-01", "rollover": False},
    ]

    async def fake_get_member(member_id):
        if member_id == "m-bad":
            raise RuntimeError("db blip")
        return {"company_id": "c1", "status": "active"}

    with (
        patch(P + "list_allowances_due_for_reset", AsyncMock(return_value=rows)),
        patch(P + "get_corporate_member_by_id", AsyncMock(side_effect=fake_get_member)),
        patch(P + "get_corporate_account_by_id", AsyncMock(return_value={"id": "c1", "status": "active"})),
        patch(P + "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1"})),
        patch(P + "reset_allowance_period", AsyncMock(return_value={"id": "x"})),
        patch(P + "apply_reset", AsyncMock()) as apply_reset,
    ):
        processed = await ar.run_allowance_reset_tick()
    # Only the "good" row succeeds.
    assert processed == 1
    apply_reset.assert_awaited_once()


# ---------------------------------------------------------------------------
# _add_one_month
# ---------------------------------------------------------------------------


def test_add_one_month_normal_rollover():
    from backend.utils.allowance_reset import _add_one_month

    assert _add_one_month(date(2026, 1, 15)) == date(2026, 2, 15)


def test_add_one_month_clamps_day_that_does_not_exist_next_month():
    from backend.utils.allowance_reset import _add_one_month

    # Jan 31 -> Feb has no 31st (2026 is not a leap year) -> clamps to Feb 28.
    assert _add_one_month(date(2026, 1, 31)) == date(2026, 2, 28)


def test_add_one_month_wraps_december_to_january():
    from backend.utils.allowance_reset import _add_one_month

    assert _add_one_month(date(2026, 12, 10)) == date(2027, 1, 10)


# ---------------------------------------------------------------------------
# allowance_reset_loop
# ---------------------------------------------------------------------------


async def test_loop_happy_tick_records_metrics_and_heartbeat():
    from backend.utils import allowance_reset as ar

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "run_allowance_reset_tick", AsyncMock()),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_metric_gauge") as mock_gauge,
        patch(P + "_metric_inc") as mock_inc,
        patch(P + "_record_heartbeat") as mock_hb,
    ):
        with pytest.raises(asyncio.CancelledError):
            await ar.allowance_reset_loop()
    mock_gauge.assert_called_once()
    mock_inc.assert_not_called()
    mock_hb.assert_called_once_with("allowance_reset (1h)")


async def test_loop_tick_exception_is_caught_and_counted():
    from backend.utils import allowance_reset as ar

    async def failing_tick():
        raise RuntimeError("boom")

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "run_allowance_reset_tick", failing_tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_metric_gauge") as mock_gauge,
        patch(P + "_metric_inc") as mock_inc,
        patch(P + "_record_heartbeat"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await ar.allowance_reset_loop()
    mock_gauge.assert_called_once()
    mock_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "allowance_reset"})
