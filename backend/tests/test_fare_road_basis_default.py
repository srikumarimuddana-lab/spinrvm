"""Contract: road distance is the default billing basis, and the quoted fare
is collected (fare-lock) — no straight-line billing, no post-ride GPS re-price.

Owner decision: the rider is quoted the actual road (driving) distance before
the ride and charged exactly that. These tests lock the two settings that make
that the default: the estimate's ``fare_distance_basis`` default (`road`) and
migration 248 flipping ``fare_lock_enabled`` + seeding ``fare_distance_basis``.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = _BACKEND / "migrations" / "248_fare_road_basis_and_quote_lock.sql"
ESTIMATES = _BACKEND / "routes" / "rides" / "estimates.py"


def test_estimate_defaults_fare_basis_to_road_not_shadow() -> None:
    src = ESTIMATES.read_text()
    # The estimate resolves the mode with a default; that default must be "road"
    # so a fresh deploy bills the road route with no flag flip.
    assert '"fare_distance_basis", "road"' in src
    assert '"fare_distance_basis", "shadow"' not in src


def test_migration_248_enables_road_billing_and_quote_lock() -> None:
    sql = MIGRATION.read_text()
    # Collect the quoted fare: never re-price on post-ride GPS.
    assert "fare_lock_enabled = TRUE" in sql
    assert "ALTER COLUMN fare_lock_enabled SET DEFAULT TRUE" in sql
    # Road distance is the (admin-overridable) billing basis.
    assert "ADD COLUMN IF NOT EXISTS fare_distance_basis TEXT" in sql
    assert "fare_distance_basis = 'road'" in sql
    assert "WHERE id = 'app_settings'" in sql
    # Reversible on paper (CI-enforced marker).
    assert "-- Rollback:" in sql
