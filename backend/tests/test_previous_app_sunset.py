"""Previous-app money is now a PERMANENT part of driver-facing surfaces.

Business decision 2026-08-13 (docs/change-log/2026-08-13-blended-lifetime-
earnings.md): the Aug 31, 2026 sunset for driver-facing previous-app payout
history (payout history, statement PDFs/emails, the balance note) has been
reversed. Hiding a driver's own previous-app money on a date made their
lifetime earnings figure look like it shrank — the same trust problem A31
fixed for trip/distance/duration counts. `previous_app_history_visible()`
and `PREVIOUS_APP_VISIBLE_UNTIL` (utils/legacy_rides) are unchanged and
still correct as a pure date helper; the three driver-facing call sites
(`get_driver_balance`, `get_payout_history`, `build_statement`) simply no
longer call it.

This file used to pin the pre/post-cutoff branches for those three call
sites; it now pins that previous-app money is ALWAYS present regardless of
date, so a regression that re-adds the sunset gate fails these tests.
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

from backend.utils.legacy_rides import PREVIOUS_APP_VISIBLE_UNTIL, previous_app_history_visible

DRIVER_USER = {"id": "usr-1"}


class TestCutoffHelperStillCorrect:
    """The pure date helper itself is untouched — kept correct in case it's
    ever needed again — even though nothing calls it anymore."""

    def test_visible_through_the_cutoff_day_inclusive(self):
        assert previous_app_history_visible(today=date(2026, 8, 30)) is True
        assert previous_app_history_visible(today=PREVIOUS_APP_VISIBLE_UNTIL) is True

    def test_hidden_from_the_day_after(self):
        assert previous_app_history_visible(today=date(2026, 9, 1)) is False
        assert previous_app_history_visible(today=date(2027, 1, 1)) is False


class TestPayoutHistoryAlwaysIncludesPreviousApp:
    """GET /drivers/payouts must never filter stripe_sync rows out, on any
    date — regression guard for the reversed sunset."""

    def _run(self):
        from backend.routes import drivers as drv

        captured = {}

        async def _get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [{"id": "drv-1"}]
            captured["filters"] = filters
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)):
            asyncio.run(drv.get_payout_history(limit=20, offset=0, current_user=DRIVER_USER))
        return captured["filters"]

    def test_no_date_based_filter_is_applied(self):
        filters = self._run()
        assert "$or" not in filters
        assert filters == {"driver_id": "drv-1"}


class TestBalanceAlwaysReportsPreviousAppMoney:
    """previous_app_paid_total must reflect the real amount unconditionally
    — no sunset zeroing."""

    def _balance(self):
        from backend.routes import drivers as drv

        def rows(table, filters=None, **kw):
            if table == "drivers":
                return [{"id": "drv-1", "user_id": "usr-1"}]
            if table == "payouts":
                return [{"amount": 200.0, "status": "completed", "payout_type": "stripe_sync"}]
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=rows)):
            return asyncio.run(drv.get_driver_balance(current_user=DRIVER_USER))

    def test_reports_the_amount(self):
        assert self._balance()["previous_app_paid_total"] == "200.00"


class TestStatementFlagAlwaysTrue:
    """build_statement always stamps previous_app_visible=True; the PDF
    renderer (utils/driver_statement_pdf.py) already branches correctly on
    this flag, so always-True is enough to keep previous-app rows/notes
    rendering permanently — no PDF-renderer change needed."""

    def _statement(self):
        from backend.utils import driver_statement as stmt

        payouts = [
            {"created_at": "2026-06-07T10:00:00+00:00", "amount": 20.77, "fee": 0, "status": "completed", "payout_type": "stripe_sync"},
        ]

        async def _get_rows(table, filters=None, **kw):
            return list(payouts) if table == "payouts" else []

        with patch.object(stmt, "db_supabase") as db:
            db.get_rows = AsyncMock(side_effect=_get_rows)
            return asyncio.run(stmt.build_statement({"id": "d1"}, "monthly", date(2026, 6, 1)))

    def test_flag_is_always_true(self):
        result = self._statement()
        assert result["previous_app_visible"] is True
        assert result["payouts_previous_app_total"] == "20.77"
