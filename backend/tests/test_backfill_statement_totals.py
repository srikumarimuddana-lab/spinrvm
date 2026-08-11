"""Safety properties of the driver_statements totals backfill.

This script rewrites stored money figures on a driver-facing audit surface,
so the properties worth pinning are the ones that make a bad run
recognisable: never destroy the original figures, never report a write that
did not happen, and never silently skip a row.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import date

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "backfill_statement_totals.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("backfill_statement_totals", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


class TestParseDate:
    def test_accepts_plain_date_string(self):
        assert mod._parse_date("2026-07-27") == date(2026, 7, 27)

    def test_accepts_full_iso_timestamp(self):
        assert mod._parse_date("2026-07-27T00:00:00+00:00") == date(2026, 7, 27)

    def test_returns_none_for_junk_rather_than_guessing(self):
        # A row we cannot date must be reported as skipped, not rebuilt over
        # an invented period.
        assert mod._parse_date("not-a-date") is None
        assert mod._parse_date("") is None
        assert mod._parse_date(None) is None


class TestCorrectedTotals:
    def _statement(self):
        return {"earnings": "47.75", "payouts_total": "0.00", "trips": 3}

    def test_preserves_original_figures_for_rollback(self):
        old = {"earnings": "47.75", "payouts_total": "115.70", "trips": 3}
        new = mod._corrected(old, self._statement())

        assert new["payouts_total"] == "0.00"
        # The pre-backfill figures survive so the rewrite is reversible and an
        # auditor can see what the driver was originally shown.
        assert new["superseded"]["payouts_total"] == "115.70"
        assert new["superseded"]["earnings"] == "47.75"
        assert new["superseded"]["reason"] == "dropped_upper_bound_filter_bug"

    def test_rerun_never_overwrites_the_original_snapshot(self):
        """An auditor needs the ORIGINAL job-time figures, not the previous
        run's — so a second pass must not clobber `superseded`."""
        already = {
            "earnings": "47.75",
            "payouts_total": "0.00",
            "trips": 3,
            "superseded": {"earnings": "47.75", "payouts_total": "115.70", "trips": 3, "reason": "x"},
        }
        new = mod._corrected(already, self._statement())
        assert new["superseded"]["payouts_total"] == "115.70"
        assert new["superseded"]["reason"] == "x"

    def test_money_keys_ignores_superseded_so_rerun_is_a_noop(self):
        """Second pass must compare equal and skip, or the script would
        rewrite every row forever."""
        corrected = mod._corrected({"earnings": "1.00", "payouts_total": "2.00", "trips": 1}, self._statement())
        assert mod._money_keys(corrected) == mod._money_keys(
            {"earnings": "47.75", "payouts_total": "0.00", "trips": 3}
        )


class TestLoadStatements:
    @pytest.mark.anyio
    async def test_pages_past_the_postgrest_row_cap(self):
        """An unbounded select silently caps at db-max-rows, which would leave
        older statements uncorrected while the run reported success."""
        pages = [
            [{"id": f"s{i}"} for i in range(mod._PAGE_SIZE)],
            [{"id": "tail"}],
        ]
        seen_offsets = []

        class _DB:
            async def get_rows(self, table, filters=None, **kw):
                seen_offsets.append(kw.get("offset"))
                return pages.pop(0) if pages else []

        rows = await mod._load_statements(_DB(), None, None, None)
        assert len(rows) == mod._PAGE_SIZE + 1
        assert seen_offsets == [0, mod._PAGE_SIZE]

    @pytest.mark.anyio
    async def test_since_filter_uses_a_two_sided_safe_lower_bound(self):
        captured = {}

        class _DB:
            async def get_rows(self, table, filters=None, **kw):
                captured["filters"] = filters
                return []

        await mod._load_statements(_DB(), "drv-1", "2026-01-01", None)
        assert captured["filters"]["driver_id"] == "drv-1"
        assert captured["filters"]["period_start"] == {"$gte": "2026-01-01"}
