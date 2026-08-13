"""The Aug 31, 2026 sunset for driver-facing previous-app payout history.

The "Previous app" presentation is transition messaging: after the cutoff,
driver-facing surfaces (payout history, statement PDFs/emails, the balance
note) stop showing previous-app money. The sunset is PRESENTATION ONLY —
admin surfaces, T4A/tax exports, and stored statement totals keep the full
picture forever, so nothing here touches those paths.

Every test pins the switch through the injectable/patchable helper rather
than the real clock, so this suite does not start failing (or silently stop
covering the pre-cutoff branch) on Sept 1, 2026.
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

from backend.utils.legacy_rides import PREVIOUS_APP_VISIBLE_UNTIL, previous_app_history_visible

DRIVER_USER = {"id": "usr-1"}


class TestCutoffHelper:
    def test_visible_through_the_cutoff_day_inclusive(self):
        assert previous_app_history_visible(today=date(2026, 8, 30)) is True
        # "until august 31" inclusive — the section survives the whole day.
        assert previous_app_history_visible(today=PREVIOUS_APP_VISIBLE_UNTIL) is True

    def test_hidden_from_the_day_after(self):
        assert previous_app_history_visible(today=date(2026, 9, 1)) is False
        assert previous_app_history_visible(today=date(2027, 1, 1)) is False


class TestPayoutHistorySunset:
    """GET /drivers/payouts filters stripe_sync rows SERVER-side after the
    cutoff, so pagination stays honest and the app section retires itself
    without an app release."""

    def _run(self, visible: bool):
        from backend.routes import drivers as drv

        captured = {}

        async def _get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [{"id": "drv-1"}]
            captured["filters"] = filters
            return []

        with (
            patch("backend.routes.drivers.payouts.previous_app_history_visible", return_value=visible),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        ):
            asyncio.run(drv.get_payout_history(limit=20, offset=0, current_user=DRIVER_USER))
        return captured["filters"]

    def test_before_cutoff_serves_everything(self):
        filters = self._run(visible=True)
        assert "$or" not in filters

    def test_after_cutoff_filters_previous_app_rows(self):
        filters = self._run(visible=False)
        # NULL-safe exclusion: a bare $ne would hide pre-backfill rows whose
        # payout_type is NULL (SQL NULL != 'x' is NULL, PostgREST drops them).
        assert filters["$or"] == [{"payout_type": {"$ne": "stripe_sync"}}, {"payout_type": None}]


class TestBalanceNoteSunset:
    """previous_app_paid_total reports 0.00 after the cutoff so the app's
    balance-card note self-hides."""

    def _balance(self, visible: bool):
        from backend.routes import drivers as drv

        def rows(table, filters=None, **kw):
            if table == "drivers":
                return [{"id": "drv-1", "user_id": "usr-1"}]
            if table == "payouts":
                return [{"amount": 200.0, "status": "completed", "payout_type": "stripe_sync"}]
            return []

        with (
            patch("backend.routes.drivers.earnings.previous_app_history_visible", return_value=visible),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=rows)),
        ):
            return asyncio.run(drv.get_driver_balance(current_user=DRIVER_USER))

    def test_before_cutoff_reports_the_amount(self):
        assert self._balance(visible=True)["previous_app_paid_total"] == "200.00"

    def test_after_cutoff_reports_zero(self):
        assert self._balance(visible=False)["previous_app_paid_total"] == "0.00"


class TestStatementFlagSunset:
    """build_statement stamps previous_app_visible for the PDF renderer; the
    DATA (rows, split totals) stays complete either way — admin stored totals
    and the recompute must keep the full picture forever."""

    def _statement(self, visible: bool):
        from backend.utils import driver_statement as stmt

        payouts = [
            {"created_at": "2026-06-07T10:00:00+00:00", "amount": 20.77, "fee": 0, "status": "completed", "payout_type": "stripe_sync"},
        ]

        async def _get_rows(table, filters=None, **kw):
            return list(payouts) if table == "payouts" else []

        with (
            patch.object(stmt, "previous_app_history_visible", return_value=visible),
            patch.object(stmt, "db_supabase") as db,
        ):
            db.get_rows = AsyncMock(side_effect=_get_rows)
            return asyncio.run(stmt.build_statement({"id": "d1"}, "monthly", date(2026, 6, 1)))

    def test_flag_follows_the_cutoff_but_data_stays_complete(self):
        before = self._statement(visible=True)
        after = self._statement(visible=False)
        assert before["previous_app_visible"] is True
        assert after["previous_app_visible"] is False
        # The numbers themselves never change — only the PDF's rendering does.
        for key in ("payouts_total", "payouts_previous_app_total", "payouts"):
            assert before[key] == after[key]
        assert after["payouts_previous_app_total"] == "20.77"
