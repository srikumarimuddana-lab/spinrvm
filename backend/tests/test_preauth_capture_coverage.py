"""Coverage for utils/preauth_capture.py (A1c, Sub-tier C — money-adjacent:
this sweeper is the only thing that captures a booking-time card hold when a
rider never opens the tip/rate screen; a silent bug here means the
0%-commission driver simply never gets paid until Stripe auto-expires the
hold ~7 days later).

`tests/test_preauth_capture.py` already covers the core `_capture_tick` /
`_capture_one` happy paths (tip-window-open skip, claim-then-settle,
lost-claim-race, settlement success/failure). This file fills the remaining
gaps: the dual-import fallback branch, `_pod_id`, the `grand_total` → None →
`total_fare` fallback, the receipt-send exception guard, the DB fetch
failure in `_capture_tick`, and the `preauth_capture_loop` background loop
itself (lock contention, per-tick exception isolation, jitter sleep).

It also PINS a real bug found while reading the source (see
`TestCaptureOneMetaConversionBug` below) rather than fixing it — flagged in
the report per instructions.

Test-only change — no application code modified. Written without running
pytest; not verified against a live coverage run.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import preauth_capture

P = "backend.utils.preauth_capture."


def _ride(minutes_ago=60, auth_status="authorized", **overrides):
    completed = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    base = {
        "id": "ride_cov_1",
        "rider_id": "rider_cov_1",
        "payment_method": "card",
        "payment_method_id": "pm_cov",
        "payment_status": "pending",
        "auth_status": auth_status,
        "authorized_amount": "35.00",
        "payment_intent_id": "pi_hold_cov",
        "total_fare": "25.00",
        "grand_total": "25.00",
        "tip_amount": "0",
        "ride_completed_at": completed,
    }
    base.update(overrides)
    return base


def _result(success=True, **kw):
    from backend.services.payment_service import PaymentResult

    return PaymentResult(success=success, **kw)


# ---------------------------------------------------------------------------
# _pod_id (line 75)
# ---------------------------------------------------------------------------


class TestPodId:
    def test_pod_id_combines_hostname_and_pid(self):
        pod_id = preauth_capture._pod_id()
        assert ":" in pod_id
        host, _, pid = pod_id.partition(":")
        assert host
        assert pid.isdigit()


# ---------------------------------------------------------------------------
# Dual-import fallback branch (lines 37-46)
#
# preauth_capture.py resolves db / settle_card / send_ride_receipt /
# parse_iso_utc / redis_set_nx via `try: from ..x import y / except
# ImportError: from x import y`, per CLAUDE.md's dual-import convention (so
# the same file works loaded as `backend.utils.preauth_capture` -- relative
# imports succeed -- or as a top-level `utils.preauth_capture` -- relative
# imports raise "attempted relative import beyond top-level package" and the
# except branch's absolute imports take over). `backend/` is on sys.path
# (see tests/conftest.py), so importing the module fresh under the
# `utils.preauth_capture` name exercises the except-branch for real, the
# same technique already used by test_payment_retry_coverage.py-adjacent
# suites in this repo (`from utils import payment_retry` there relies on the
# identical fallback).
# ---------------------------------------------------------------------------


class TestDualImportFallback:
    def test_top_level_import_uses_absolute_fallback_branch(self):
        import importlib.util

        # Load the source file fresh under a throwaway, never-colliding module
        # name instead of reimporting "utils.preauth_capture" or
        # "backend.utils.preauth_capture" in place. Those two names are the
        # SAME module object here (conftest.py's _BareModuleAliasFinder
        # aliases "backend.X" to bare "X" for every module under
        # utils/services/etc.) — reimporting either key mutates the shared
        # "utils" package's `preauth_capture` attribute via Python's normal
        # parent.child submodule registration, which is what
        # `unittest.mock.patch("backend.utils.preauth_capture....")` walks in
        # every other test in this file. A throwaway name sidesteps that
        # entirely: nothing else in the process ever looks it up by name.
        spec = importlib.util.spec_from_file_location(
            "_preauth_capture_dual_import_probe", preauth_capture.__file__
        )
        mod = importlib.util.module_from_spec(spec)
        # No __package__ set (module loaded outside any package), so the
        # relative imports at the top of preauth_capture.py
        # (`from ..db import db`, etc.) raise ImportError and the except
        # branch's absolute imports (`from db import db`, etc.) take over —
        # exactly the fallback path this test targets.
        spec.loader.exec_module(mod)

        # Sanity: the module loaded and bound its dual-import names (proves
        # the except-branch absolute imports actually resolved, not just
        # that "import" didn't raise).
        assert callable(mod._pod_id)
        assert mod.db is not None
        assert callable(mod.settle_card)
        assert callable(mod.parse_iso_utc)
        assert callable(mod.redis_set_nx)


# ---------------------------------------------------------------------------
# _capture_one — grand_total None falls back to total_fare (line 84)
# ---------------------------------------------------------------------------


class TestCaptureOneGrandFallback:
    @pytest.mark.anyio
    async def test_missing_grand_total_falls_back_to_total_fare(self):
        ride = _ride(grand_total=None, total_fare="18.50", tip_amount="1.50")
        settle_mock = AsyncMock(return_value=_result(success=True, charged_amount="20.00"))
        with (
            patch(P + "settle_card", settle_mock),
            patch(P + "send_ride_receipt", AsyncMock(return_value=True)),
        ):
            await preauth_capture._capture_one(ride)

        settle_mock.assert_called_once()
        # total_charge = total_fare (18.50) + tip (1.50) = 20.00
        assert settle_mock.call_args.args[3] == Decimal("20.00")

    @pytest.mark.anyio
    async def test_missing_grand_total_and_missing_total_fare_defaults_to_zero_plus_tip(self):
        ride = _ride(grand_total=None, total_fare=None, tip_amount="5.00")
        settle_mock = AsyncMock(return_value=_result(success=True, charged_amount="5.00"))
        with (
            patch(P + "settle_card", settle_mock),
            patch(P + "send_ride_receipt", AsyncMock(return_value=True)),
        ):
            await preauth_capture._capture_one(ride)

        assert settle_mock.call_args.args[3] == Decimal("5.00")


# ---------------------------------------------------------------------------
# _capture_one — receipt-send exception is swallowed (lines 100-101)
# ---------------------------------------------------------------------------


class TestCaptureOneReceiptFailure:
    @pytest.mark.anyio
    async def test_receipt_send_raises_is_logged_not_raised(self):
        ride = _ride()
        settle_mock = AsyncMock(return_value=_result(success=True, charged_amount="25.00", already_paid=True))
        receipt_mock = AsyncMock(side_effect=RuntimeError("email provider down"))
        with (
            patch(P + "settle_card", settle_mock),
            patch(P + "send_ride_receipt", receipt_mock),
        ):
            # already_paid=True short-circuits the Meta conversion hook below
            # so this test isolates just the receipt-failure branch.
            await preauth_capture._capture_one(ride)  # must not raise

        receipt_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# _capture_one — Meta Purchase conversion hook (lines 102-114)
# ---------------------------------------------------------------------------


class TestCaptureOneMetaConversion:
    @pytest.mark.anyio
    async def test_already_paid_skips_conversion_hook(self):
        ride = _ride()
        settle_mock = AsyncMock(return_value=_result(success=True, charged_amount="25.00", already_paid=True))
        send_purchase_mock = AsyncMock()
        with (
            patch(P + "settle_card", settle_mock),
            patch(P + "send_ride_receipt", AsyncMock(return_value=True)),
            patch("backend.services.meta_conversions_service.send_ride_purchase_for_ride", send_purchase_mock),
        ):
            await preauth_capture._capture_one(ride)

        send_purchase_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_not_already_paid_fires_conversion_hook_with_charged_amount(self):
        ride = _ride()
        settle_mock = AsyncMock(return_value=_result(success=True, charged_amount="25.00", already_paid=False))
        send_purchase_mock = AsyncMock()
        with (
            patch(P + "settle_card", settle_mock),
            patch(P + "send_ride_receipt", AsyncMock(return_value=True)),
            patch("backend.services.meta_conversions_service.send_ride_purchase_for_ride", send_purchase_mock),
        ):
            await preauth_capture._capture_one(ride)

        send_purchase_mock.assert_awaited_once()
        args = send_purchase_mock.await_args.args
        assert args[0] == ride
        assert args[1] == ride["rider_id"]
        assert args[2] == "25.00"

    @pytest.mark.anyio
    async def test_meta_conversion_bug_unhandled_exception_propagates(self):
        """FOUND NOT FIXED (backend/utils/preauth_capture.py:108-113):
        unlike `settle_card` (wrapped, line 88-95) and `send_ride_receipt`
        (wrapped, line 98-101), the `await send_ride_purchase_for_ride(...)`
        call has NO try/except around it, even though `_capture_one`'s own
        docstring (line 79) promises "Never raises". If the Meta pixel side
        -hook throws (network blip, bad creds, etc.) AFTER the hold has
        already been captured and money has already moved, the exception
        propagates out of `_capture_one` uncaught.

        Blast radius: `_capture_tick`'s `for ride in rides: ... await
        _capture_one(ride)` loop (line 168) has no per-ride try/except
        either, so this one bad analytics call aborts the *rest of that
        tick* -- any other already-claimed-but-unprocessed rides in the same
        batch of up to 50 are simply skipped until the next 5-minute tick.
        The outer `preauth_capture_loop` catches it (so the loop itself
        survives), logged only as a generic "Pre-auth capture loop error",
        which reads like the whole tick failed even though the actual card
        capture for THIS ride already succeeded.

        This test pins the current (buggy) behavior -- exception escapes --
        rather than fixing it, per instructions not to modify application
        code in this pass.
        """
        ride = _ride()
        settle_mock = AsyncMock(return_value=_result(success=True, charged_amount="25.00", already_paid=False))
        send_purchase_mock = AsyncMock(side_effect=RuntimeError("meta api down"))
        with (
            patch(P + "settle_card", settle_mock),
            patch(P + "send_ride_receipt", AsyncMock(return_value=True)),
            patch("backend.services.meta_conversions_service.send_ride_purchase_for_ride", send_purchase_mock),
        ):
            with pytest.raises(RuntimeError, match="meta api down"):
                await preauth_capture._capture_one(ride)


# ---------------------------------------------------------------------------
# _capture_tick — DB fetch failure returns early (lines 145-147)
# ---------------------------------------------------------------------------


class TestCaptureTickFetchFailure:
    @pytest.mark.anyio
    async def test_get_rows_raises_returns_early_without_claiming(self):
        claim_mock = AsyncMock()
        with (
            patch(P + "db.get_rows", AsyncMock(side_effect=RuntimeError("db unreachable"))),
            patch(P + "db.update_one", claim_mock),
        ):
            await preauth_capture._capture_tick()  # must not raise

        claim_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# preauth_capture_loop (lines 173-192)
# ---------------------------------------------------------------------------


class TestPreauthCaptureLoop:
    @pytest.mark.anyio
    async def test_lock_not_acquired_skips_tick_heartbeats_and_sleeps(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        tick_mock = AsyncMock()
        with (
            patch(P + "redis_set_nx", AsyncMock(return_value=False)),
            patch(P + "_capture_tick", tick_mock),
            patch(P + "asyncio.sleep", _fake_sleep),
            patch(P + "_record_heartbeat", MagicMock()) as hb_mock,
        ):
            with pytest.raises(asyncio.CancelledError):
                await preauth_capture.preauth_capture_loop()

        tick_mock.assert_not_awaited()
        assert hb_mock.call_count == 2
        # No lock -> plain interval sleep, no jitter applied.
        assert sleep_calls == [
            preauth_capture.CAPTURE_INTERVAL_SECONDS,
            preauth_capture.CAPTURE_INTERVAL_SECONDS,
        ]

    @pytest.mark.anyio
    async def test_lock_acquired_runs_tick_then_heartbeats_and_jitters(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        tick_mock = AsyncMock()
        with (
            patch(P + "redis_set_nx", AsyncMock(return_value=True)),
            patch(P + "_capture_tick", tick_mock),
            patch(P + "asyncio.sleep", _fake_sleep),
            patch(P + "_record_heartbeat", MagicMock()) as hb_mock,
        ):
            with pytest.raises(asyncio.CancelledError):
                await preauth_capture.preauth_capture_loop()

        tick_mock.assert_awaited_once()
        assert hb_mock.call_count == 1
        assert len(sleep_calls) == 1
        delta = preauth_capture.CAPTURE_INTERVAL_SECONDS * 0.1
        lo = preauth_capture.CAPTURE_INTERVAL_SECONDS - delta
        hi = preauth_capture.CAPTURE_INTERVAL_SECONDS + delta
        assert lo <= sleep_calls[0] <= hi

    @pytest.mark.anyio
    async def test_tick_exception_is_isolated_loop_still_heartbeats_and_sleeps(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        tick_mock = AsyncMock(side_effect=RuntimeError("tick blew up"))
        with (
            patch(P + "redis_set_nx", AsyncMock(return_value=True)),
            patch(P + "_capture_tick", tick_mock),
            patch(P + "asyncio.sleep", _fake_sleep),
            patch(P + "_record_heartbeat", MagicMock()) as hb_mock,
        ):
            # The loop must survive a _capture_tick exception -- only
            # asyncio.CancelledError (from our fake sleep, simulating
            # shutdown) should propagate out.
            with pytest.raises(asyncio.CancelledError):
                await preauth_capture.preauth_capture_loop()

        tick_mock.assert_awaited_once()
        assert hb_mock.call_count == 1
        assert len(sleep_calls) == 1

    @pytest.mark.anyio
    async def test_lock_acquisition_uses_double_interval_ttl(self):
        set_nx_mock = AsyncMock(return_value=True)

        async def _fake_sleep(seconds):
            raise asyncio.CancelledError()

        with (
            patch(P + "redis_set_nx", set_nx_mock),
            patch(P + "_capture_tick", AsyncMock()),
            patch(P + "asyncio.sleep", _fake_sleep),
            patch(P + "_record_heartbeat", MagicMock()),
        ):
            with pytest.raises(asyncio.CancelledError):
                await preauth_capture.preauth_capture_loop()

        set_nx_mock.assert_awaited_once()
        args = set_nx_mock.await_args.args
        assert args[0] == "spinr:preauth:capture:lock"
        assert args[2] == preauth_capture.CAPTURE_INTERVAL_SECONDS * 2
