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


def test_tokenless_road_booking_prices_road_or_flags_fallback() -> None:
    """Blocker-2 contract: a booking with no valid estimate token must still, in
    road mode, price on the road distance — or, if Directions is unavailable,
    fall back to a FLAGGED haversine (never silent plain 'haversine' that the
    suspect check + reconciliation miss). This exercises the exact function pair
    the booking safety-net calls.
    """
    from routes.rides._shared import booked_distance_suspect_reason, select_fare_distance

    hav, road = 0.7, 1.8
    # Road available → charge the road distance, not suspect.
    km, basis = select_fare_distance(hav, road, mode="road")
    assert (km, basis) == (1.8, "road_route")
    assert booked_distance_suspect_reason(km, basis) is None

    # Road unavailable → flagged fallback that the suspect check catches.
    km, basis = select_fare_distance(hav, None, mode="road")
    assert basis == "haversine_fallback"
    assert booked_distance_suspect_reason(km, basis) == "road_route_unavailable"


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
