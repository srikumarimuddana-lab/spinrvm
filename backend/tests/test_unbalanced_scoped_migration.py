"""SQL contract for the date-scoped trial-balance check (migrations 292 + 293).

These pin the properties that make the fix a fix rather than a rename. The
nightly check asks "any unbalanced double-entry journals today?"; it used to
filter the financial_event_entries_unbalanced view on MIN(created_at) — an
aggregate output Postgres cannot push below the view's GROUP BY — so it
re-aggregated the entire table every night. Migration 293 moves the bound
inside the aggregate; migration 292 is the index that makes that bound cheap.
Lose either and the query silently reverts to a full scan.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
INDEX_SQL = MIGRATIONS / "292_financial_event_entries_created_at_index.sql"
FUNC_SQL = MIGRATIONS / "293_financial_event_entries_unbalanced_scoped.sql"

RPC_NAME = "financial_event_entries_unbalanced_between"


def test_index_is_on_bare_created_at() -> None:
    """(account, created_at DESC) cannot serve a bare created_at range —
    account leads it and Postgres has no index skip scan."""
    sql = INDEX_SQL.read_text()
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS financial_event_entries_created_at" in sql
    assert "ON financial_event_entries (created_at)" in sql


def test_index_is_concurrent_and_reversible() -> None:
    sql = INDEX_SQL.read_text()
    # CONCURRENTLY: the table is populated wherever the double-entry flag has
    # been on, and run_migrations.py runs such files outside a transaction.
    assert "CONCURRENTLY" in sql
    assert "-- Rollback:" in sql
    assert "DROP INDEX CONCURRENTLY IF EXISTS financial_event_entries_created_at" in sql


def test_function_bounds_the_window_inside_the_aggregate() -> None:
    """The whole point: the date predicate must sit on the base table, not on
    the view's MIN(created_at)."""
    sql = FUNC_SQL.read_text()
    assert f"CREATE OR REPLACE FUNCTION {RPC_NAME}(" in sql
    assert "w.created_at >= p_start" in sql
    assert "w.created_at <  p_end" in sql or "w.created_at < p_end" in sql


def test_function_scopes_by_event_not_by_leg() -> None:
    """Legs of one event share a timestamp today, so a bare WHERE would be
    correct — but it becomes WRONG the moment a batch straddles midnight, and
    each half would look unbalanced. Selecting event ids from the window and
    aggregating ALL of their legs is correct either way."""
    sql = FUNC_SQL.read_text()
    assert "WHERE e.event_id IN (" in sql
    assert "GROUP BY e.event_id" in sql


def test_function_returns_the_view_shape() -> None:
    """SETOF the view keeps the scoped and unscoped paths column-identical, so
    an operator querying by hand and the nightly job cannot disagree. It also
    avoids hardcoding the sum types (SUM(bigint) is numeric, not bigint)."""
    sql = FUNC_SQL.read_text()
    assert "RETURNS SETOF financial_event_entries_unbalanced" in sql
    assert "::bigint" not in sql, "casting the sums would break the SETOF type match"


def test_function_is_service_role_only() -> None:
    """SECURITY DEFINER bypasses financial_event_entries' RLS, and migration
    286 revoked the view from anon/authenticated — the function must match."""
    sql = FUNC_SQL.read_text()
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public" in sql
    assert "FROM PUBLIC, anon, authenticated" in sql
    # REVOKE ... FROM PUBLIC also strips service_role's inherited EXECUTE
    # (migration 205), so it has to be granted back explicitly.
    assert f"GRANT EXECUTE ON FUNCTION {RPC_NAME}(timestamptz, timestamptz)\n    TO service_role;" in sql


def test_function_is_reversible_and_drops_before_replacing() -> None:
    sql = FUNC_SQL.read_text()
    assert "-- Rollback:" in sql
    # CREATE OR REPLACE cannot change a signature; a differing parameter list
    # would silently coexist as an overload (migration 111 incident).
    assert f"DROP FUNCTION IF EXISTS {RPC_NAME}(timestamptz, timestamptz);" in sql


def test_reconciliation_calls_the_name_the_migration_defines() -> None:
    """Guards the one link no SQL test can see: a rename on either side would
    turn the nightly check into a permanent silent no-op, because a missing
    RPC is deliberately treated as 'not deployed yet' rather than an error."""
    recon = (Path(__file__).resolve().parents[1] / "utils" / "reconciliation.py").read_text()
    assert f'"{RPC_NAME}"' in recon
    assert "financial_event_entries_unbalanced_between" in FUNC_SQL.read_text()
