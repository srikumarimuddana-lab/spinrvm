"""SQL contract for the per-insurance-period distance audit table (migration 249).

This is the driver-audit distance store (GPS-measured, NOT billing). It must be
append-only, RLS-restricted to the driver + admins, and ride-scoped for the two
commercial-insurance periods (2 en-route, 3 passenger).
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "249_driver_period_distances.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_table_shape_period_and_distance() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS driver_period_distances" in sql
    assert "driver_id    text          NOT NULL REFERENCES drivers(id)" in sql
    assert "period       smallint      NOT NULL CHECK (period IN (0, 1, 2, 3))" in sql
    assert "distance_km  numeric(8, 3) NOT NULL CHECK (distance_km >= 0)" in sql
    # Periods 2/3 are ride-scoped and must carry a ride_id.
    assert "CHECK (period NOT IN (2, 3) OR ride_id IS NOT NULL)" in sql


def test_replay_safe_unique_ride_period() -> None:
    sql = _sql()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_driver_period_distances_ride_period" in sql
    assert "(ride_id, period)" in sql


def test_append_only_and_rls() -> None:
    sql = _sql()
    assert "ENABLE ROW LEVEL SECURITY" in sql
    # Driver-or-admin SELECT only; no INSERT/UPDATE/DELETE policy for clients.
    assert "FOR SELECT USING" in sql
    assert "'admin', 'super_admin'" in sql
    # Strictly append-only: any UPDATE/DELETE raises.
    assert "BEFORE UPDATE OR DELETE ON driver_period_distances" in sql
    assert "append-only and cannot be modified or deleted" in sql


def test_reversible_on_paper() -> None:
    assert "-- Rollback:" in _sql()
