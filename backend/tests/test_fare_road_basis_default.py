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


def test_pricing_wait_covers_directions_timeout() -> None:
    """The billed distance basis must depend on whether Directions succeeded,
    never on scheduler timing. When the pricing wait (1.5 s) sat below the
    Directions HTTP timeout (3.0 s), a response landing in the gap silently
    priced the SAME trip on haversine in one call and the road route in the
    next — the incident's $30.92 → $39.44 flip between quote and confirm.

    Strictly greater, not >=: at equality the two deadlines race and the gap
    reopens.
    """
    from routes.rides import estimates
    from routes.rides._shared import DIRECTIONS_TIMEOUT_S

    assert estimates._PRICING_ROUTE_WAIT_S > DIRECTIONS_TIMEOUT_S


def test_pricing_wait_stays_within_the_estimate_latency_budget() -> None:
    """The determinism invariant above is satisfied by raising the wait, which
    makes it a one-way ratchet on rider-visible latency: CLAUDE.md pins fare
    estimate P95 at 300 ms, and the wait is the worst case a rider can feel on
    "tap → price shown". Ceiling here so a bump to DIRECTIONS_TIMEOUT_S has to
    come with a deliberate decision rather than silently costing seconds.

    Ceiling raised 2.0 s -> 3.5 s (DIRECTIONS_TIMEOUT_S 1.5 -> 3.0) as an
    explicit product decision, 2026-07-29: the road route is the billing
    basis and haversine is a guardrail for a dead upstream, not a second
    pricing mode. Every timeout billed the straight line, which is always
    <= the road distance, so the loss was one-directional and — under 0%
    commission — came out of the driver's fare (reported: a 16.6 km road
    trip billed as 15.5 km). The wait costs nothing on a warm call; it only
    extends the slow tail that was mispricing. Raising it further should
    again be a deliberate call, not a silent creep.
    """
    from routes.rides import estimates

    assert estimates._PRICING_ROUTE_WAIT_S <= 3.5


@pytest.mark.anyio
async def test_slow_directions_response_still_prices_road_basis() -> None:
    """A route task that resolves just inside the HTTP timeout must be awaited
    and billed as road_route, not dropped for haversine."""
    import asyncio

    from routes.rides import estimates
    from routes.rides._shared import DIRECTIONS_TIMEOUT_S, select_fare_distance

    async def slow_route():
        # Derived from the timeout rather than hardcoded, so this keeps testing
        # "landed just before the HTTP deadline" whatever the constants become.
        await asyncio.sleep(DIRECTIONS_TIMEOUT_S * 0.9)
        return {"distance_km": 16.46, "polyline": [], "duration_s": 1500}

    route_task = asyncio.ensure_future(slow_route())
    done, _ = await asyncio.wait({route_task}, timeout=estimates._PRICING_ROUTE_WAIT_S)
    assert route_task in done, "pricing wait must outlast a slow-but-successful Directions call"
    road_km = (route_task.result() or {}).get("distance_km")
    km, basis = select_fare_distance(12.116, road_km, mode="road")
    assert (km, basis) == (16.46, "road_route")


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
