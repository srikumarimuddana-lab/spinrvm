"""Coverage for utils/corporate_low_balance.py (A1c, Sub-tier C —
corporate-wallet-adjacent: this loop is the only signal a company admin gets
that their wallet is running low with auto-topup off, so a silent bug here
means rides start failing/falling back to master-wallet with no warning).

`tests/test_corporate_low_balance.py` already covers the happy-path send,
the 12h rate limit (blocked + elapsed), the missing-billing-email skip, and
the suspended-company skip (Corporate module lifecycle audit Finding 3).
This file fills the remaining gaps: the malformed-timestamp tolerance in
`run_low_balance_tick`, the per-wallet exception isolation in the scan loop,
and the `corporate_low_balance_loop` background loop itself (happy path
metrics/heartbeat/jitter-sleep, and the error path that logs + increments
the error counter but keeps looping).

Test-only change — no application code modified.

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 6):
`run_low_balance_tick` previously caught `ValueError` on a corrupt
`low_balance_notified_at` value and set `last_dt = None`, which — because
the rate-limit check is gated on `if last_dt and ...` — silently treated
the wallet as "never notified" and defeated the 12h rate limiter every
tick until the column was repaired, with nothing logged. Now logs the
malformed value via `logger.error` and fails closed — treats it as
"just notified" (full rate-limit window applies) rather than "never
notified". See
`TestRunLowBalanceTick.test_malformed_timestamp_logs_and_fails_closed`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import corporate_low_balance


def _wallet(**overrides) -> dict:
    base = {
        "id": "w1",
        "company_id": "c1",
        "balance": "30.00",
        "auto_topup_enabled": False,
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# run_low_balance_tick — malformed timestamp + per-wallet exception isolation
# ---------------------------------------------------------------------------


class TestRunLowBalanceTick:
    @pytest.mark.anyio
    async def test_malformed_timestamp_logs_and_fails_closed(self):
        """Fixed (2026-08-03): a malformed low_balance_notified_at value
        now logs an error and is treated as "just notified" (fails closed —
        the rate limit still applies) instead of "never notified"."""
        wallet = _wallet(low_balance_notified_at="not-a-valid-timestamp")
        mock_error = MagicMock()
        with (
            patch(
                "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
                AsyncMock(return_value=[wallet]),
            ),
            patch("utils.corporate_low_balance.get_corporate_account_by_id", AsyncMock()) as m_lookup,
            patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
            patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
            patch.object(corporate_low_balance.logger, "error", mock_error),
        ):
            await corporate_low_balance.run_low_balance_tick()

        # Fails closed: no email sent, rate limit treated as still active.
        m_send.assert_not_awaited()
        m_mark.assert_not_awaited()
        m_lookup.assert_not_awaited()
        # The malformed value is logged loudly, not silently swallowed.
        assert any("malformed" in str(c.args[0]).lower() for c in mock_error.call_args_list)

    @pytest.mark.anyio
    async def test_notify_one_exception_is_logged_and_does_not_stop_the_scan(self):
        w1 = _wallet(id="w1", company_id="c1")
        w2 = _wallet(id="w2", company_id="c2")
        with (
            patch(
                "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
                AsyncMock(return_value=[w1, w2]),
            ),
            patch(
                "utils.corporate_low_balance._notify_one",
                AsyncMock(side_effect=[RuntimeError("boom"), None]),
            ) as m_notify,
        ):
            # Must not raise — a single bad wallet can't take down the scan.
            await corporate_low_balance.run_low_balance_tick()
        assert m_notify.await_count == 2

    @pytest.mark.anyio
    async def test_empty_wallet_list_is_a_noop(self):
        with (
            patch(
                "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
                AsyncMock(return_value=[]),
            ),
            patch("utils.corporate_low_balance._notify_one", AsyncMock()) as m_notify,
        ):
            await corporate_low_balance.run_low_balance_tick()
        m_notify.assert_not_awaited()


# ---------------------------------------------------------------------------
# corporate_low_balance_loop
# ---------------------------------------------------------------------------


class TestCorporateLowBalanceLoop:
    @pytest.mark.anyio
    async def test_happy_path_records_duration_heartbeat_and_jitter_sleep_no_error_metric(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        mock_gauge = MagicMock()
        mock_inc = MagicMock()
        mock_hb = MagicMock()
        with (
            patch("utils.corporate_low_balance.run_low_balance_tick", AsyncMock()),
            patch("utils.corporate_low_balance.asyncio.sleep", _fake_sleep),
            patch("utils.corporate_low_balance._metric_gauge", mock_gauge),
            patch("utils.corporate_low_balance._metric_inc", mock_inc),
            patch("utils.corporate_low_balance._record_heartbeat", mock_hb),
        ):
            with pytest.raises(asyncio.CancelledError):
                await corporate_low_balance.corporate_low_balance_loop()

        # Duration gauge always recorded, tagged to this loop.
        mock_gauge.assert_called_once()
        gauge_args = mock_gauge.call_args[0]
        assert gauge_args[0] == "spinr_bgloop_duration_ms"
        assert gauge_args[2] == {"loop": "corporate_low_balance"}
        # No error on the happy path.
        mock_inc.assert_not_called()
        mock_hb.assert_called_once_with("corporate_low_balance (1h)")
        # Sleep is ~1h with +/-10% jitter.
        assert len(sleep_calls) == 1
        lo, hi = 3600 * 0.9, 3600 * 1.1
        assert lo <= sleep_calls[0] <= hi

    @pytest.mark.anyio
    async def test_tick_exception_is_logged_increments_error_metric_but_loop_continues(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        mock_gauge = MagicMock()
        mock_inc = MagicMock()
        mock_hb = MagicMock()
        with (
            patch(
                "utils.corporate_low_balance.run_low_balance_tick",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch("utils.corporate_low_balance.asyncio.sleep", _fake_sleep),
            patch("utils.corporate_low_balance._metric_gauge", mock_gauge),
            patch("utils.corporate_low_balance._metric_inc", mock_inc),
            patch("utils.corporate_low_balance._record_heartbeat", mock_hb),
        ):
            # Must not raise (aside from our injected CancelledError from the
            # sleep, which proves control reached the bottom of the loop
            # body despite the tick blowing up).
            with pytest.raises(asyncio.CancelledError):
                await corporate_low_balance.corporate_low_balance_loop()

        mock_gauge.assert_called_once()
        mock_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "corporate_low_balance"})
        mock_hb.assert_called_once_with("corporate_low_balance (1h)")
        assert len(sleep_calls) == 1

    @pytest.mark.anyio
    async def test_loop_body_repeats_across_multiple_ticks(self):
        """Second iteration proves the `while True` actually loops rather
        than the CancelledError-from-sleep trick masking a single pass."""
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        mock_tick = AsyncMock()
        with (
            patch("utils.corporate_low_balance.run_low_balance_tick", mock_tick),
            patch("utils.corporate_low_balance.asyncio.sleep", _fake_sleep),
            patch("utils.corporate_low_balance._metric_gauge", MagicMock()),
            patch("utils.corporate_low_balance._metric_inc", MagicMock()),
            patch("utils.corporate_low_balance._record_heartbeat", MagicMock()) as mock_hb,
        ):
            with pytest.raises(asyncio.CancelledError):
                await corporate_low_balance.corporate_low_balance_loop()

        assert mock_tick.await_count == 2
        assert mock_hb.call_count == 2
        assert len(sleep_calls) == 2


# ---------------------------------------------------------------------------
# _notify_one — email body / subject shape (not covered by the existing
# happy-path test's loose "low" or "balance" substring assertion)
# ---------------------------------------------------------------------------


class TestNotifyOneEmailShape:
    @pytest.mark.anyio
    async def test_email_body_includes_balance_and_threshold(self):
        wallet = _wallet(balance="42.50", auto_topup_threshold="100.00")
        with (
            patch(
                "utils.corporate_low_balance.get_corporate_account_by_id",
                AsyncMock(return_value={"billing_email": "ap@acme.test", "name": "Acme Co", "status": "active"}),
            ),
            patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()),
            patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
        ):
            await corporate_low_balance._notify_one(wallet)

        kwargs = m_send.call_args.kwargs
        assert kwargs["to"] == "ap@acme.test"
        assert "Acme Co" in kwargs["subject"]
        assert "42.50" in kwargs["body"]
        assert "100.00" in kwargs["body"]

    @pytest.mark.anyio
    async def test_company_not_found_skips_send(self):
        wallet = _wallet()
        with (
            patch("utils.corporate_low_balance.get_corporate_account_by_id", AsyncMock(return_value=None)),
            patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
            patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
        ):
            await corporate_low_balance._notify_one(wallet)
        m_send.assert_not_awaited()
        m_mark.assert_not_awaited()
