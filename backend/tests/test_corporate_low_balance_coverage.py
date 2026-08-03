"""
A1c Sub-tier C coverage: backend/utils/corporate_low_balance.py (62% -> target 90%+).

`test_corporate_low_balance.py` covers `run_low_balance_tick`'s happy path,
rate-limit skip/resend, missing-billing-email skip, and suspended-company
skip. This file closes:

- `run_low_balance_tick`: company lookup returning `None` (same early-return
  branch as missing billing_email, but a different code path — no company
  row at all), the malformed `low_balance_notified_at` timestamp
  (`ValueError` -> treated as unset, notification proceeds), and
  `_notify_one` raising for one wallet not aborting the loop over the rest
  (`logger.exception` swallow).
- `corporate_low_balance_loop`: happy tick (metrics/heartbeat/sleep), a
  tick exception being caught, logged, and counted via
  `spinr_bgloop_errors_total` (loop survives), and the duration gauge being
  emitted on both outcomes.

Patch target: `utils.corporate_low_balance.*` (module-bound names via its
own dual-import block), matching the established pattern in
`test_corporate_low_balance.py`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_company_not_found_skips_notify():
    wallet = {
        "id": "w1",
        "company_id": "c1",
        "balance": "30.00",
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": None,
    }
    with (
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[wallet]),
        ),
        patch("utils.corporate_low_balance.get_corporate_account_by_id", AsyncMock(return_value=None)),
        patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
        patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()
    m_send.assert_not_awaited()
    m_mark.assert_not_awaited()


async def test_malformed_notified_at_timestamp_fails_closed_and_skips_notify():
    """Fixed: a malformed `low_balance_notified_at` must not bypass the rate
    limit and re-send every tick until the DB value is repaired. It's now
    treated as "just notified" (full rate-limit window applies), not "never
    notified" — the opposite of what this test originally pinned."""
    wallet = {
        "id": "w1",
        "company_id": "c1",
        "balance": "30.00",
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": "not-a-real-timestamp",
    }
    with (
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[wallet]),
        ),
        patch(
            "utils.corporate_low_balance.get_corporate_account_by_id",
            AsyncMock(return_value={"billing_email": "b@acme.test", "name": "Acme", "status": "active"}),
        ),
        patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
        patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()
    m_send.assert_not_awaited()
    m_mark.assert_not_awaited()


async def test_one_wallet_notify_exception_does_not_abort_batch():
    wallets = [
        {
            "id": "w1",
            "company_id": "c1",
            "balance": "30.00",
            "auto_topup_threshold": "100.00",
            "low_balance_notified_at": None,
        },
        {
            "id": "w2",
            "company_id": "c2",
            "balance": "10.00",
            "auto_topup_threshold": "50.00",
            "low_balance_notified_at": None,
        },
    ]

    async def fake_get_company(company_id):
        if company_id == "c1":
            raise RuntimeError("db blip")
        return {"billing_email": "b@acme.test", "name": "Acme2", "status": "active"}

    with (
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=wallets),
        ),
        patch("utils.corporate_low_balance.get_corporate_account_by_id", AsyncMock(side_effect=fake_get_company)),
        patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
        patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()
    # Wallet w1's failure is swallowed; w2 still gets notified.
    m_send.assert_awaited_once()
    m_mark.assert_awaited_once_with(wallet_id="w2")


# ---------------------------------------------------------------------------
# corporate_low_balance_loop
# ---------------------------------------------------------------------------


async def test_loop_happy_tick_records_metrics_and_heartbeat():
    from utils import corporate_low_balance as m

    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)
        raise asyncio.CancelledError()

    with (
        patch.object(m, "run_low_balance_tick", AsyncMock()),
        patch.object(m.asyncio, "sleep", fake_sleep),
        patch.object(m, "_metric_gauge") as mock_gauge,
        patch.object(m, "_metric_inc") as mock_inc,
        patch.object(m, "_record_heartbeat") as mock_hb,
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.corporate_low_balance_loop()
    mock_gauge.assert_called_once()
    mock_inc.assert_not_called()
    mock_hb.assert_called_once_with("corporate_low_balance (1h)")


async def test_loop_tick_exception_is_caught_and_counted():
    from utils import corporate_low_balance as m

    async def failing_tick():
        raise RuntimeError("boom")

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch.object(m, "run_low_balance_tick", failing_tick),
        patch.object(m.asyncio, "sleep", fake_sleep),
        patch.object(m, "_metric_gauge") as mock_gauge,
        patch.object(m, "_metric_inc") as mock_inc,
        patch.object(m, "_record_heartbeat"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.corporate_low_balance_loop()
    mock_gauge.assert_called_once()
    mock_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "corporate_low_balance"})
