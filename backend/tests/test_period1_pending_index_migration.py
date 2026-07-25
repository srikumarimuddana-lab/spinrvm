"""SQL contract for the Period-1 pending-accumulator partial index (migration 251)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "251_drivers_period1_accum_pending_index.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_partial_index_backs_the_finalizer_scan() -> None:
    sql = _sql()
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS drivers_period1_accum_pending" in sql
    assert "ON drivers (id)" in sql
    # Partial: only the small in-flight set with a non-zero accumulator.
    assert "WHERE period1_accum_km > 0" in sql


def test_reversible_on_paper() -> None:
    assert "-- Rollback:" in _sql()
    assert "DROP INDEX CONCURRENTLY IF EXISTS drivers_period1_accum_pending" in _sql()
