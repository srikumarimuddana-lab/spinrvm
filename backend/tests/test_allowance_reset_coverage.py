"""Coverage for utils/allowance_reset.py (A1c, Sub-tier C — corporate-wallet-
adjacent: `run_allowance_reset_tick` gates whether a member's monthly budget
replenishes at all, and `apply_reset` is the only thing standing between a
removed/suspended member and an indefinite auto-refill; see corporate module
lifecycle audit Finding 2 and gap #3, already regression-tested in
`test_corporate_allowance_reset.py`).

`test_corporate_allowance_reset.py` and `test_c_allowance_reset_atomic.py`
already cover: the happy-path reset, the rollover skip, the removed-member
skip, the suspended-company skip, and the CAS claim win/lose branches. This
file fills the remaining gaps:

  * member-not-found short-circuit (distinct from member-found-but-inactive)
  * wallet-not-found short-circuit
  * the per-row `except Exception` isolation in the tick loop
  * `_add_one_month`'s day-of-month clamping (Jan 31 -> Feb 28/29, leap year)
  * `allowance_reset_loop`: happy path (metrics + heartbeat + jittered sleep),
    the tick-raises path (loop must survive and still record heartbeat/metric),
    and that the `_had_error` metric is NOT emitted on a clean tick
  * the two `try/except ImportError` module-load fallback branches (the
    absolute `utils.loop_monitor` import and the relative `.metrics` import)
    by loading fresh copies of the module under synthetic import conditions,
    since the file's own top-level try/except only runs once at first import
    and both branches succeed under the normal test harness.

Test-only change — no application code modified.

Found-not-fixed (see comments at point of use, not fixed per task scope):
  * `_add_one_month`'s final fallback `return date(year, month, 28)` (source
    line ~67) is unreachable dead code: the preceding `for day in
    range(d.day, 0, -1)` loop always succeeds at `day == 1` (day 1 is valid
    in every month of every year), so the loop's `return` always fires first
    and the trailing `return date(year, month, 28)` can never execute.
  * The module's primary (relative) import branches
    (`from ..db_supabase import ...` / `from ..services... import apply_reset`,
    source lines ~28-35) can only succeed if this module is loaded as a true
    submodule of package `backend.utils` reached via genuine relative-import
    package traversal. In both production (`core/lifespan.py` imports it as
    bare `utils.allowance_reset`) and this test harness (conftest.py's
    bare-module mirroring finder), it is always loaded as top-level `utils.
    allowance_reset`, where `..db_supabase` is "attempted relative import
    beyond top-level package" and *always* raises ImportError, unconditionally
    falling through to the absolute-import except branch. Line ~35 is
    therefore dead in every reachable execution path, not just untested.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import allowance_reset as ar

pytestmark = pytest.mark.unit

_MODULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "allowance_reset.py")


# ---------------------------------------------------------------------------
# _add_one_month
# ---------------------------------------------------------------------------


class TestAddOneMonth:
    def test_mid_month_simple_increment(self):
        assert ar._add_one_month(date(2026, 3, 15)) == date(2026, 4, 15)

    def test_december_rolls_into_next_year(self):
        assert ar._add_one_month(date(2026, 12, 10)) == date(2027, 1, 10)

    def test_jan_31_clamps_to_feb_28_in_non_leap_year(self):
        # 2026 is not a leap year.
        assert ar._add_one_month(date(2026, 1, 31)) == date(2026, 2, 28)

    def test_jan_31_clamps_to_feb_29_in_leap_year(self):
        assert ar._add_one_month(date(2028, 1, 31)) == date(2028, 2, 29)

    def test_31_day_month_clamps_to_30_day_month(self):
        assert ar._add_one_month(date(2026, 4, 30)) is not None  # sanity: no explosion
        assert ar._add_one_month(date(2026, 5, 31)) == date(2026, 6, 30)


# ---------------------------------------------------------------------------
# run_allowance_reset_tick — remaining branch gaps
# ---------------------------------------------------------------------------


def _base_row(**overrides) -> dict:
    row = {
        "id": "a_missing_gap",
        "member_id": "m_x",
        "period_end": "2026-03-31",
        "rollover": False,
    }
    row.update(overrides)
    return row


class TestRunAllowanceResetTickGaps:
    @pytest.mark.anyio
    async def test_member_not_found_is_skipped(self):
        """Distinct from the 'member found but inactive' gap #3 regression
        test: here get_corporate_member_by_id returns None outright (e.g.
        the member row was hard-deleted), which must short-circuit before
        ever touching company/wallet lookups."""
        row = _base_row()
        with (
            patch("utils.allowance_reset.list_allowances_due_for_reset", AsyncMock(return_value=[row])),
            patch("utils.allowance_reset.get_corporate_member_by_id", AsyncMock(return_value=None)),
            patch("utils.allowance_reset.get_corporate_account_by_id", AsyncMock()) as m_company,
            patch("utils.allowance_reset.get_corporate_wallet_by_company", AsyncMock()) as m_wallet,
            patch("utils.allowance_reset.reset_allowance_period", AsyncMock()) as m_period,
            patch("utils.allowance_reset.apply_reset", AsyncMock()) as m_apply,
        ):
            processed = await ar.run_allowance_reset_tick(now=date(2026, 4, 1))

        assert processed == 0
        m_company.assert_not_awaited()
        m_wallet.assert_not_awaited()
        m_period.assert_not_awaited()
        m_apply.assert_not_awaited()

    @pytest.mark.anyio
    async def test_wallet_not_found_is_skipped(self):
        """Company is active but the corporate wallet lookup comes back
        empty (e.g. wallet not yet provisioned) — must not attempt the CAS
        claim or the reset with no wallet to apply it against."""
        row = _base_row()
        with (
            patch("utils.allowance_reset.list_allowances_due_for_reset", AsyncMock(return_value=[row])),
            patch(
                "utils.allowance_reset.get_corporate_member_by_id",
                AsyncMock(return_value={"id": "m_x", "company_id": "c_x", "status": "active"}),
            ),
            patch(
                "utils.allowance_reset.get_corporate_account_by_id",
                AsyncMock(return_value={"id": "c_x", "status": "active"}),
            ),
            patch("utils.allowance_reset.get_corporate_wallet_by_company", AsyncMock(return_value=None)),
            patch("utils.allowance_reset.reset_allowance_period", AsyncMock()) as m_period,
            patch("utils.allowance_reset.apply_reset", AsyncMock()) as m_apply,
        ):
            processed = await ar.run_allowance_reset_tick(now=date(2026, 4, 1))

        assert processed == 0
        m_period.assert_not_awaited()
        m_apply.assert_not_awaited()

    @pytest.mark.anyio
    async def test_row_raising_is_isolated_and_logged_not_raised(self):
        """One bad row (e.g. malformed period_end, or any lookup raising)
        must not abort the whole tick — the per-row try/except logs and
        continues to the next row."""
        bad_row = _base_row(id="a_bad", period_end="not-a-date")
        good_row = _base_row(id="a_good", member_id="m_good")
        with (
            patch(
                "utils.allowance_reset.list_allowances_due_for_reset",
                AsyncMock(return_value=[bad_row, good_row]),
            ),
            patch(
                "utils.allowance_reset.get_corporate_member_by_id",
                AsyncMock(return_value={"id": "m_good", "company_id": "c_good", "status": "active"}),
            ),
            patch(
                "utils.allowance_reset.get_corporate_account_by_id",
                AsyncMock(return_value={"id": "c_good", "status": "active"}),
            ),
            patch(
                "utils.allowance_reset.get_corporate_wallet_by_company",
                AsyncMock(return_value={"id": "w_good"}),
            ),
            patch(
                "utils.allowance_reset.reset_allowance_period",
                AsyncMock(return_value={"id": "a_good"}),
            ) as m_period,
            patch("utils.allowance_reset.apply_reset", AsyncMock()) as m_apply,
        ):
            processed = await ar.run_allowance_reset_tick(now=date(2026, 4, 1))

        # Only the good row was processed; the bad row's ValueError
        # (date.fromisoformat) was caught, logged, and skipped.
        assert processed == 1
        m_period.assert_awaited_once()
        m_apply.assert_awaited_once()

    @pytest.mark.anyio
    async def test_uses_date_today_when_now_not_provided(self):
        """Covers the `today = now or date.today()` default-arg branch."""
        with (
            patch("utils.allowance_reset.list_allowances_due_for_reset", AsyncMock(return_value=[])) as m_list,
        ):
            processed = await ar.run_allowance_reset_tick()

        assert processed == 0
        m_list.assert_awaited_once()
        # as_of must be an ISO date string derived from date.today(), not None.
        as_of = m_list.await_args.kwargs.get("as_of") or m_list.await_args.args[0]
        assert as_of == date.today().isoformat()


# ---------------------------------------------------------------------------
# allowance_reset_loop
# ---------------------------------------------------------------------------


class TestAllowanceResetLoop:
    @pytest.mark.anyio
    async def test_happy_path_records_duration_heartbeat_no_error_metric(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        with (
            patch("utils.allowance_reset.run_allowance_reset_tick", AsyncMock(return_value=3)),
            patch("utils.allowance_reset.asyncio.sleep", _fake_sleep),
            patch("utils.allowance_reset._record_heartbeat", MagicMock()) as m_hb,
            patch("utils.allowance_reset._metric_gauge", MagicMock()) as m_gauge,
            patch("utils.allowance_reset._metric_inc", MagicMock()) as m_inc,
        ):
            with pytest.raises(asyncio.CancelledError):
                await ar.allowance_reset_loop(interval_seconds=3600)

        m_hb.assert_called_once_with("allowance_reset (1h)")
        m_gauge.assert_called_once()
        gauge_args = m_gauge.call_args
        assert gauge_args[0][0] == "spinr_bgloop_duration_ms"
        assert gauge_args[0][2] == {"loop": "allowance_reset"}
        m_inc.assert_not_called()  # no error on the clean tick -> no error metric
        assert len(sleep_calls) == 1
        # Jitter is interval_seconds * (0.9 .. 1.1).
        lo, hi = 3600 * 0.9, 3600 * 1.1
        assert lo <= sleep_calls[0] <= hi

    @pytest.mark.anyio
    async def test_tick_exception_is_isolated_still_sleeps_and_still_emits_error_metric(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        with (
            patch(
                "utils.allowance_reset.run_allowance_reset_tick",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch("utils.allowance_reset.asyncio.sleep", _fake_sleep),
            patch("utils.allowance_reset._record_heartbeat", MagicMock()) as m_hb,
            patch("utils.allowance_reset._metric_gauge", MagicMock()) as m_gauge,
            patch("utils.allowance_reset._metric_inc", MagicMock()) as m_inc,
        ):
            # The loop itself must not propagate the tick's exception —
            # only the injected CancelledError from asyncio.sleep escapes.
            with pytest.raises(asyncio.CancelledError):
                await ar.allowance_reset_loop(interval_seconds=60)

        m_hb.assert_called_once_with("allowance_reset (1h)")
        m_gauge.assert_called_once()
        m_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "allowance_reset"})
        assert len(sleep_calls) == 1

    @pytest.mark.anyio
    async def test_loop_runs_multiple_iterations_before_cancellation(self):
        """Confirms the `while True` body actually re-enters (heartbeat/tick
        fire each pass), not just once."""
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        mock_tick = AsyncMock(return_value=0)
        with (
            patch("utils.allowance_reset.run_allowance_reset_tick", mock_tick),
            patch("utils.allowance_reset.asyncio.sleep", _fake_sleep),
            patch("utils.allowance_reset._record_heartbeat", MagicMock()) as m_hb,
        ):
            with pytest.raises(asyncio.CancelledError):
                await ar.allowance_reset_loop(interval_seconds=10)

        assert mock_tick.await_count == 2
        assert m_hb.call_count == 2
        assert len(sleep_calls) == 2


# ---------------------------------------------------------------------------
# Module-load import fallback branches (lines ~21-23, ~49-51).
#
# The file's own top-level try/except blocks run once, at first import, and
# under this test harness both succeed (utils.loop_monitor and utils.metrics
# are both real, importable modules) — so the except branches are dead for
# the module object under test above. To exercise them we load fresh,
# independent copies of the source file under synthetic import conditions
# that force each failure individually, without touching the real
# `utils.allowance_reset` module object (so the rest of this file/suite is
# unaffected).
# ---------------------------------------------------------------------------


def _load_fresh_module(name: str, package: str | None, poison: list[str] | None = None):
    """Exec a fresh copy of utils/allowance_reset.py under a synthetic
    __package__ / sys.modules state, then restore sys.modules afterwards."""
    poison = poison or []
    saved = {mod_name: sys.modules.get(mod_name) for mod_name in poison}
    had_key = {mod_name: (mod_name in sys.modules) for mod_name in poison}
    for mod_name in poison:
        sys.modules[mod_name] = None  # forces ImportError on `import <mod_name>`

    try:
        spec = importlib.util.spec_from_file_location(name, _MODULE_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = package
        spec.loader.exec_module(module)
        return module
    finally:
        for mod_name in poison:
            if had_key[mod_name]:
                sys.modules[mod_name] = saved[mod_name]
            else:
                sys.modules.pop(mod_name, None)


class TestModuleLoadFallbacks:
    def test_loop_monitor_import_failure_falls_back_to_noop_heartbeat(self):
        """Poison utils.loop_monitor so `from utils.loop_monitor import
        record_heartbeat` (absolute) raises ImportError, forcing the
        except branch that defines a local no-op `_record_heartbeat`
        stub (source lines ~21-23)."""
        module = _load_fresh_module(
            "utils._allowance_reset_variant_loopmonitor_missing",
            package="utils",
            poison=["utils.loop_monitor"],
        )
        # Must be the local stub, not utils.loop_monitor.record_heartbeat,
        # and calling it must be a true no-op (no exception, no return value).
        assert module._record_heartbeat("anything") is None

    def test_relative_metrics_import_failure_falls_back_to_absolute(self):
        """Load the module with no parent package (__package__ = "") so
        the relative `from .metrics import inc` raises "attempted relative
        import with no known parent package", forcing the except branch's
        absolute `from utils.metrics import inc/set_gauge` (source lines
        ~49-51). `utils` itself must remain genuinely importable (it is —
        only the *relative* resolution is broken by the missing parent),
        so the absolute fallback succeeds and binds real callables."""
        module = _load_fresh_module(
            "_allowance_reset_variant_no_parent_package",
            package="",
        )
        from utils import metrics as real_metrics

        assert module._metric_inc is real_metrics.inc
        assert module._metric_gauge is real_metrics.set_gauge
        # Sanity: the module is otherwise usable (proves the whole file
        # executed to completion, not just up to the metrics import).
        assert module._add_one_month(date(2026, 1, 31)) == date(2026, 2, 28)
