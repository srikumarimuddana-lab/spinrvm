"""Tests for backend/scripts/verify_restore.py (ACTION_ITEMS.md E7).

Covers the production-URL guard and the row-count / ride-lifecycle check
functions against a mocked connection. This script is a standalone,
opt-in, human-run tool (never invoked by a route/loop/CI job), so these
tests exercise its pure logic directly rather than through any fixture
that talks to a real database.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from backend.scripts.verify_restore import (
    CORE_TABLES,
    ProductionURLGuardError,
    VerificationReport,
    check_ride_lifecycle,
    check_row_counts,
    find_sample_completed_ride,
    resolve_database_url,
    run_verification,
)

pytestmark = pytest.mark.unit


BRANCH_URL = "postgresql://postgres:branchpw@db.abcxyz-branch.supabase.co:5432/postgres"
PROD_URL = "postgresql://postgres:prodpw@db.prod-ref.supabase.co:5432/postgres"


# ---------------------------------------------------------------------------
# resolve_database_url — the production-URL guard
# ---------------------------------------------------------------------------


class TestResolveDatabaseUrl:
    def test_explicit_cli_url_is_used(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("RESTORE_BRANCH_DATABASE_URL", raising=False)
        assert resolve_database_url(BRANCH_URL) == BRANCH_URL

    def test_env_var_fallback_is_used(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("RESTORE_BRANCH_DATABASE_URL", BRANCH_URL)
        assert resolve_database_url(None) == BRANCH_URL

    def test_no_url_anywhere_raises(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("RESTORE_BRANCH_DATABASE_URL", raising=False)
        with pytest.raises(ProductionURLGuardError):
            resolve_database_url(None)

    def test_bare_database_url_env_var_is_never_read_as_fallback(self, monkeypatch):
        """DATABASE_URL alone (no RESTORE_BRANCH_DATABASE_URL, no --database-url)
        must never be silently used as the restore-branch target."""
        monkeypatch.setenv("DATABASE_URL", PROD_URL)
        monkeypatch.delenv("RESTORE_BRANCH_DATABASE_URL", raising=False)
        with pytest.raises(ProductionURLGuardError):
            resolve_database_url(None)

    def test_refuses_when_resolved_url_matches_database_url(self, monkeypatch):
        """The core safety guard: if the resolved restore-branch URL is
        identical to DATABASE_URL (also set in this shell), refuse to run —
        this is exactly the "pointed at production by mistake" case."""
        monkeypatch.setenv("DATABASE_URL", PROD_URL)
        with pytest.raises(ProductionURLGuardError, match="REFUSING TO RUN"):
            resolve_database_url(PROD_URL)

    def test_refuses_via_env_var_path_too(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", PROD_URL)
        monkeypatch.setenv("RESTORE_BRANCH_DATABASE_URL", PROD_URL)
        with pytest.raises(ProductionURLGuardError, match="REFUSING TO RUN"):
            resolve_database_url(None)

    def test_allows_distinct_branch_url_even_when_database_url_set(self, monkeypatch):
        """A real drill: DATABASE_URL (prod) is set in the operator's shell,
        but the branch URL is genuinely different — must be allowed."""
        monkeypatch.setenv("DATABASE_URL", PROD_URL)
        assert resolve_database_url(BRANCH_URL) == BRANCH_URL

    def test_trailing_slash_does_not_evade_the_guard(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", PROD_URL)
        with pytest.raises(ProductionURLGuardError, match="REFUSING TO RUN"):
            resolve_database_url(PROD_URL + "/")


# ---------------------------------------------------------------------------
# Mock DB cursor/connection helpers
# ---------------------------------------------------------------------------


def _make_cursor(fetchone_result=None, fetchall_result=None, raise_on_execute=None):
    cursor = MagicMock()
    if raise_on_execute is not None:
        cursor.execute.side_effect = raise_on_execute
    cursor.fetchone.return_value = fetchone_result
    cursor.fetchall.return_value = fetchall_result or []
    return cursor


def _make_conn(cursor):
    """A connection whose `with conn.cursor() as cur:` always yields the same cursor."""
    conn = MagicMock()

    @contextmanager
    def _cursor_cm():
        yield cursor

    conn.cursor.side_effect = lambda: _cursor_cm()
    return conn


def _make_conn_sequence(cursors):
    """A connection whose successive `with conn.cursor() as cur:` calls yield
    each cursor in `cursors`, in order (one per statement the code under test
    issues)."""
    conn = MagicMock()
    remaining = list(cursors)

    @contextmanager
    def _cursor_cm():
        yield remaining.pop(0)

    conn.cursor.side_effect = lambda: _cursor_cm()
    return conn


# ---------------------------------------------------------------------------
# check_row_counts
# ---------------------------------------------------------------------------


class TestCheckRowCounts:
    def test_all_tables_pass_when_populated(self):
        cursor = _make_cursor(fetchone_result=(42,))
        conn = _make_conn(cursor)

        report = VerificationReport()
        counts = check_row_counts(conn, report, tables=("rides", "drivers"))

        assert counts == {"rides": 42, "drivers": 42}
        assert report.ok
        assert len(report.results) == 2
        assert all(r.passed for r in report.results)

    def test_empty_table_is_reported_as_a_failure(self):
        cursor = _make_cursor(fetchone_result=(0,))
        conn = _make_conn(cursor)

        report = VerificationReport()
        check_row_counts(conn, report, tables=("stripe_disputes",))

        assert not report.ok
        assert report.results[0].name == "row_count:stripe_disputes"
        assert not report.results[0].passed
        assert "0 rows" in report.results[0].detail

    def test_query_failure_is_reported_not_raised(self):
        cursor = _make_cursor(raise_on_execute=RuntimeError("connection reset"))
        conn = _make_conn(cursor)

        report = VerificationReport()
        counts = check_row_counts(conn, report, tables=("payouts",))

        assert counts["payouts"] == -1
        assert not report.ok
        assert "query failed" in report.results[0].detail

    def test_default_tables_cover_core_financial_and_regulatory_tables(self):
        # Guard against silently dropping a table this repo's runbook cares
        # about (payouts/stripe_disputes/driver_insurance_periods/financial_events).
        assert set(CORE_TABLES) >= {
            "users",
            "drivers",
            "rides",
            "payouts",
            "stripe_disputes",
            "driver_insurance_periods",
            "financial_events",
        }


# ---------------------------------------------------------------------------
# find_sample_completed_ride
# ---------------------------------------------------------------------------


class TestFindSampleCompletedRide:
    def test_returns_ride_id_when_found(self):
        cursor = _make_cursor(fetchone_result=("ride_123",))
        conn = _make_conn(cursor)
        assert find_sample_completed_ride(conn) == "ride_123"

    def test_returns_none_when_no_completed_ride(self):
        cursor = _make_cursor(fetchone_result=None)
        conn = _make_conn(cursor)
        assert find_sample_completed_ride(conn) is None


# ---------------------------------------------------------------------------
# check_ride_lifecycle
# ---------------------------------------------------------------------------


class TestCheckRideLifecycle:
    def test_no_ride_id_fails_sample_found_only(self):
        conn = MagicMock()
        report = VerificationReport()
        check_ride_lifecycle(conn, report, None)

        assert len(report.results) == 1
        assert report.results[0].name == "ride_lifecycle:sample_found"
        assert not report.results[0].passed
        conn.cursor.assert_not_called()

    def test_full_lifecycle_all_present_passes(self):
        # Three sequential cursor uses: ride row, insurance periods, ledger.
        ride_row = ("ride_1", "completed", "driver_1", "rider_1", "2026-08-01T00:00:00Z")
        insurance_rows = [(2, "2026-08-01T00:00:00Z", "2026-08-01T00:10:00Z")]
        ledger_rows = [("fare_settle", 2500, "2026-08-01T00:12:00Z")]

        cursors = [
            _make_cursor(fetchone_result=ride_row),
            _make_cursor(fetchall_result=insurance_rows),
            _make_cursor(fetchall_result=ledger_rows),
        ]
        conn = _make_conn_sequence(cursors)

        report = VerificationReport()
        check_ride_lifecycle(conn, report, "ride_1")

        names = {r.name: r.passed for r in report.results}
        assert names["ride_lifecycle:sample_found"] is True
        assert names["ride_lifecycle:ride_row"] is True
        assert names["ride_lifecycle:insurance_periods"] is True
        assert names["ride_lifecycle:financial_events"] is True
        assert report.ok

    def test_missing_ride_row_fails_that_check(self):
        cursors = [
            _make_cursor(fetchone_result=None),
            _make_cursor(fetchall_result=[]),
            _make_cursor(fetchall_result=[]),
        ]
        conn = _make_conn_sequence(cursors)

        report = VerificationReport()
        check_ride_lifecycle(conn, report, "ride_missing")

        names = {r.name: r.passed for r in report.results}
        assert names["ride_lifecycle:ride_row"] is False

    def test_ride_not_actually_completed_fails_ride_row_check(self):
        ride_row = ("ride_2", "in_progress", "driver_1", "rider_1", "2026-08-01T00:00:00Z")
        cursors = [
            _make_cursor(fetchone_result=ride_row),
            _make_cursor(fetchall_result=[]),
            _make_cursor(fetchall_result=[]),
        ]
        conn = _make_conn_sequence(cursors)

        report = VerificationReport()
        check_ride_lifecycle(conn, report, "ride_2")

        names = {r.name: r.passed for r in report.results}
        assert names["ride_lifecycle:ride_row"] is False

    def test_missing_insurance_and_ledger_rows_reported_but_do_not_crash(self):
        ride_row = ("ride_3", "completed", "driver_1", "rider_1", "2026-08-01T00:00:00Z")
        cursors = [
            _make_cursor(fetchone_result=ride_row),
            _make_cursor(fetchall_result=[]),
            _make_cursor(fetchall_result=[]),
        ]
        conn = _make_conn_sequence(cursors)

        report = VerificationReport()
        check_ride_lifecycle(conn, report, "ride_3")

        names = {r.name: r.passed for r in report.results}
        assert names["ride_lifecycle:insurance_periods"] is False
        assert names["ride_lifecycle:financial_events"] is False
        assert (
            "pre-migration-64" in [r.detail for r in report.results if r.name == "ride_lifecycle:insurance_periods"][0]
        )


# ---------------------------------------------------------------------------
# run_verification — timing + overall pass/fail wiring
# ---------------------------------------------------------------------------


class TestRunVerification:
    def test_elapsed_seconds_is_recorded_and_conn_is_closed(self, monkeypatch):
        cursor = _make_cursor(fetchone_result=(5,))
        conn = _make_conn(cursor)
        # find_sample_completed_ride + check_ride_lifecycle also run; give a
        # cursor that answers fetchone (ride id / row) and fetchall ([]) for
        # every subsequent call so the whole pipeline completes.
        cursor.fetchone.return_value = None  # no completed ride found path

        monkeypatch.setattr("backend.scripts.verify_restore._connect", lambda url: conn)

        report = run_verification("postgresql://irrelevant/db", tables=("rides",))

        assert report.elapsed_seconds >= 0
        conn.close.assert_called_once()

    def test_exit_code_semantics_via_report_ok(self, monkeypatch):
        """Non-zero exit is driven by report.ok — verified at the report level
        since main() parses argv/env which is covered by the guard tests above."""
        report = VerificationReport()
        report.add("check_a", True)
        assert report.ok is True

        report.add("check_b", False, "boom")
        assert report.ok is False
