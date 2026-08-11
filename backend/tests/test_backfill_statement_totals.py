"""Safety properties of the driver_statements totals backfill.

This script rewrites stored money figures on a driver-facing audit surface,
so the properties worth pinning are the ones that make a bad run
recognisable: never destroy the original figures, never report a write that
did not happen, and never silently skip a row.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.services import statement_totals_backfill as mod


class TestParseDate:
    def test_accepts_plain_date_string(self):
        assert mod.parse_period_date("2026-07-27") == date(2026, 7, 27)

    def test_accepts_full_iso_timestamp(self):
        assert mod.parse_period_date("2026-07-27T00:00:00+00:00") == date(2026, 7, 27)

    def test_returns_none_for_junk_rather_than_guessing(self):
        # A row we cannot date must be reported as skipped, not rebuilt over
        # an invented period.
        assert mod.parse_period_date("not-a-date") is None
        assert mod.parse_period_date("") is None
        assert mod.parse_period_date(None) is None


class TestCorrectedTotals:
    def _statement(self):
        return {"earnings": "47.75", "payouts_total": "0.00", "trips": 3}

    def test_preserves_original_figures_for_rollback(self):
        old = {"earnings": "47.75", "payouts_total": "115.70", "trips": 3}
        new = mod.corrected_totals(old, self._statement())

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
        new = mod.corrected_totals(already, self._statement())
        assert new["superseded"]["payouts_total"] == "115.70"
        assert new["superseded"]["reason"] == "x"

    def test_money_keys_ignores_superseded_so_rerun_is_a_noop(self):
        """Second pass must compare equal and skip, or the script would
        rewrite every row forever."""
        corrected = mod.corrected_totals({"earnings": "1.00", "payouts_total": "2.00", "trips": 1}, self._statement())
        assert mod.money_keys(corrected) == mod.money_keys(
            {"earnings": "47.75", "payouts_total": "0.00", "trips": 3}
        )


class TestLoadStatements:
    @pytest.mark.anyio
    async def test_pages_past_the_postgrest_row_cap(self, monkeypatch):
        """An unbounded select silently caps at db-max-rows, which would leave
        older statements uncorrected while the run reported success."""
        pages = [
            [{"id": f"s{i}"} for i in range(mod.PAGE_SIZE)],
            [{"id": "tail"}],
        ]
        seen_offsets = []

        async def _get_rows(table, filters=None, **kw):
            seen_offsets.append(kw.get("offset"))
            return pages.pop(0) if pages else []

        monkeypatch.setattr(mod.db_supabase, "get_rows", _get_rows)
        rows, has_more = await mod.load_statements()
        assert len(rows) == mod.PAGE_SIZE + 1
        assert seen_offsets == [0, mod.PAGE_SIZE]
        assert has_more is False

    @pytest.mark.anyio
    async def test_reports_has_more_instead_of_truncating_silently(self, monkeypatch):
        """A capped run must SAY it was capped — a silent truncation reads as
        'everything was corrected' (CLAUDE.md: no silent caps)."""

        async def _get_rows(table, filters=None, **kw):
            return [{"id": f"s{kw.get('offset')}-{i}"} for i in range(mod.PAGE_SIZE)]

        monkeypatch.setattr(mod.db_supabase, "get_rows", _get_rows)
        rows, has_more = await mod.load_statements(limit=mod.PAGE_SIZE)
        assert len(rows) == mod.PAGE_SIZE
        assert has_more is True

    @pytest.mark.anyio
    async def test_scope_filters_are_passed_through(self, monkeypatch):
        captured = {}

        async def _get_rows(table, filters=None, **kw):
            captured["filters"] = filters
            return []

        monkeypatch.setattr(mod.db_supabase, "get_rows", _get_rows)
        await mod.load_statements(driver_ids=["drv-1"], since="2026-01-01")
        assert captured["filters"]["driver_id"] == {"$in": ["drv-1"]}
        assert captured["filters"]["period_start"] == {"$gte": "2026-01-01"}


class TestRecompute:
    @pytest.mark.anyio
    async def test_dry_run_writes_nothing_but_reports_the_diff(self, monkeypatch):
        """apply=False is what the dashboard previews before confirming — it
        must produce the full diff without touching a single row."""
        writes = []

        async def _get_rows(table, filters=None, **kw):
            if table == "driver_statements":
                return [
                    {
                        "id": "s1",
                        "driver_id": "drv-1",
                        "period_type": "weekly",
                        "period_start": "2026-07-27",
                        "period_end": "2026-08-02",
                        "totals": {"earnings": "0.00", "payouts_total": "115.70", "trips": 0},
                    }
                ]
            if table == "drivers":
                return [{"id": "drv-1"}]
            return []

        async def _update_one(table, filters, update):
            writes.append((table, filters, update))
            return {"id": "s1"}

        async def _build(driver, period_type, start_d, **kw):
            return {"earnings": "0.00", "payouts_total": "0.00", "trips": 0}

        monkeypatch.setattr(mod.db_supabase, "get_rows", _get_rows)
        monkeypatch.setattr(mod.db_supabase, "update_one", _update_one)
        monkeypatch.setattr(mod, "build_statement", _build)

        result = await mod.recompute_statement_totals(apply=False)
        assert writes == []
        assert result.corrected == 1
        assert result.changes[0].before["payouts_total"] == "115.70"
        assert result.changes[0].after["payouts_total"] == "0.00"
        assert result.delta_payouts == pytest.approx(-115.70)

        result = await mod.recompute_statement_totals(apply=True)
        assert len(writes) == 1
        assert writes[0][2]["totals"]["payouts_total"] == "0.00"
        assert writes[0][2]["totals"]["superseded"]["payouts_total"] == "115.70"

    @pytest.mark.anyio
    async def test_zero_row_update_counts_as_failure_not_success(self, monkeypatch):
        """update_one returns None when nothing matched — reporting that as a
        correction would claim a write that never happened."""

        async def _get_rows(table, filters=None, **kw):
            if table == "driver_statements":
                return [
                    {
                        "id": "s1",
                        "driver_id": "drv-1",
                        "period_type": "weekly",
                        "period_start": "2026-07-27",
                        "period_end": "2026-08-02",
                        "totals": {"earnings": "0.00", "payouts_total": "115.70", "trips": 0},
                    }
                ]
            if table == "drivers":
                return [{"id": "drv-1"}]
            return []

        async def _update_one(table, filters, update):
            return None  # matched nothing

        async def _build(driver, period_type, start_d, **kw):
            return {"earnings": "0.00", "payouts_total": "0.00", "trips": 0}

        monkeypatch.setattr(mod.db_supabase, "get_rows", _get_rows)
        monkeypatch.setattr(mod.db_supabase, "update_one", _update_one)
        monkeypatch.setattr(mod, "build_statement", _build)

        result = await mod.recompute_statement_totals(apply=True)
        assert result.failed == ["s1"]

    @pytest.mark.anyio
    async def test_missing_driver_is_reported_not_silently_dropped(self, monkeypatch):
        async def _get_rows(table, filters=None, **kw):
            if table == "driver_statements":
                return [
                    {
                        "id": "s1",
                        "driver_id": "gone",
                        "period_type": "weekly",
                        "period_start": "2026-07-27",
                        "period_end": "2026-08-02",
                        "totals": {},
                    }
                ]
            return []

        monkeypatch.setattr(mod.db_supabase, "get_rows", _get_rows)
        result = await mod.recompute_statement_totals(apply=False)
        assert result.corrected == 0
        assert len(result.skipped) == 1
        assert "no longer exists" in result.skipped[0]
