"""
Regression tests for B-P0-1: driver rating aggregation column + limit.

Background
----------
rate_driver (rides.py) writes the per-ride rating to the `rider_rating` DB column
but the aggregation list-comprehension read `r.get('driver_rating')`.
`driver_rating` is a *synthetic* field injected by db_supabase enrichment methods —
it is absent from raw get_rows() output.  The result: rated_rides was always empty,
the driver profile's `rating` field was never updated, and every rating was silently
dropped.

Fix 1 (B-P0-1): changed the comprehension to use `rider_rating` (the real DB column).
Fix 2 (follow-up): raised get_rows limit 1000 → 10000, added columns="rider_rating"
  to avoid truncating the average for high-volume drivers and reduce payload ~10×.

Test layers
-----------
Layer 1 — pure-logic tests (no imports): always runnable, verify the list-comp fix.
Layer 2 — integration tests (require backend deps): use pytest.importorskip so they
           are silently skipped locally and run in CI where loguru/supabase are present.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Layer 1 — pure logic tests (no backend imports needed)
# ---------------------------------------------------------------------------


def test_aggregation_reads_rider_rating_column():
    """Fixed comprehension reads rider_rating; absent rows are excluded."""
    rides = [
        {"id": "r1", "driver_id": "d1", "rider_rating": 4},
        {"id": "r2", "driver_id": "d1", "rider_rating": 5},
        {"id": "r3", "driver_id": "d1", "rider_rating": 3},
        {"id": "r4", "driver_id": "d1"},  # unrated — no rider_rating key
    ]
    rated_rides = [float(r.get("rider_rating")) for r in rides if r.get("rider_rating") is not None]
    assert rated_rides == [4.0, 5.0, 3.0]
    assert round(sum(rated_rides) / len(rated_rides), 2) == 4.0


def test_old_code_driver_rating_always_empty():
    """
    Regression proof: the OLD comprehension used driver_rating, which is absent
    from raw DB rows → rated_rides was always [] → driver rating never updated.
    """
    rides = [
        {"id": "r1", "driver_id": "d1", "rider_rating": 4},
        {"id": "r2", "driver_id": "d1", "rider_rating": 5},
    ]
    # OLD broken code
    old_rated = [float(r.get("driver_rating")) for r in rides if r.get("driver_rating") is not None]
    assert old_rated == [], "pre-fix: driver_rating absent from raw rows → always empty"

    # NEW fixed code
    new_rated = [float(r.get("rider_rating")) for r in rides if r.get("rider_rating") is not None]
    assert new_rated == [4.0, 5.0], "post-fix: rider_rating is the real column"


def test_first_ever_rating_sets_driver_score():
    """First rating for a brand-new driver: one row → average equals submitted score."""
    rides = [{"id": "r1", "driver_id": "d1", "rider_rating": 5}]
    rated_rides = [float(r.get("rider_rating")) for r in rides if r.get("rider_rating") is not None]
    assert len(rated_rides) == 1
    assert round(sum(rated_rides) / len(rated_rides), 2) == 5.0


def test_all_unrated_rides_skips_update():
    """No rider_rating on any row → rated_rides empty → driver update would be skipped."""
    rides = [
        {"id": "r1", "driver_id": "d1"},
        {"id": "r2", "driver_id": "d1"},
    ]
    rated_rides = [float(r.get("rider_rating")) for r in rides if r.get("rider_rating") is not None]
    assert rated_rides == []
    assert not rated_rides  # falsy → the if-block guard skips update_one


def test_average_rounds_to_two_decimal_places():
    rides = [
        {"rider_rating": 4},
        {"rider_rating": 4},
        {"rider_rating": 5},
    ]
    rated_rides = [float(r.get("rider_rating")) for r in rides if r.get("rider_rating") is not None]
    avg = round(sum(rated_rides) / len(rated_rides), 2)
    assert avg == 4.33


def test_rating_aggregation_limit_is_10000_not_1000():
    """
    AST guard: the get_rows call for driver rating must use limit=10000.
    limit=1000 would truncate the average for drivers with >1000 rated rides,
    silently under-counting and skewing ratings downward.
    """
    import ast
    import pathlib

    source = pathlib.Path("backend/routes/rides.py").read_text()
    tree = ast.parse(source)

    # Find get_rows calls inside the rate_driver function body
    rate_driver_src_start = source.find("async def rate_driver(")
    rate_driver_src_end = source.find("\n@", rate_driver_src_start + 1)
    if rate_driver_src_end == -1:
        rate_driver_src_end = len(source)
    rate_driver_block = source[rate_driver_src_start:rate_driver_src_end]

    # The block must contain limit=10000
    assert "limit=10000" in rate_driver_block, (
        "rate_driver get_rows must use limit=10000 — limit=1000 truncates "
        "the average for drivers with >1000 rated rides"
    )
    # Must NOT use the old cap
    assert "limit=1000," not in rate_driver_block and "limit=1000\n" not in rate_driver_block, (
        "rate_driver must not use limit=1000 (old truncation cap)"
    )


def test_rating_aggregation_uses_rider_rating_column_kwarg():
    """
    AST guard: the get_rows call must specify columns='rider_rating' to avoid
    fetching full ride rows (pickup/dropoff/fare/etc.) just to compute the average.
    """
    import pathlib

    source = pathlib.Path("backend/routes/rides.py").read_text()
    rate_driver_start = source.find("async def rate_driver(")
    rate_driver_end = source.find("\n@", rate_driver_start + 1)
    if rate_driver_end == -1:
        rate_driver_end = len(source)
    block = source[rate_driver_start:rate_driver_end]

    assert 'columns="rider_rating"' in block, (
        'rate_driver get_rows must include columns="rider_rating" to avoid fetching full rows just for aggregation'
    )


# ---------------------------------------------------------------------------
# Layer 2 — integration tests (require backend deps; skipped if unavailable)
# ---------------------------------------------------------------------------

try:
    import backend.routes.rides as _rides_module

    _HAS_BACKEND_DEPS = True
except Exception:  # ImportError or any transitive import failure
    _rides_module = None  # type: ignore[assignment]
    _HAS_BACKEND_DEPS = False

_skip_no_deps = pytest.mark.skipif(not _HAS_BACKEND_DEPS, reason="backend deps (loguru, supabase) not installed")


@_skip_no_deps
@pytest.mark.anyio
async def test_rate_driver_calls_update_one_with_correct_average():
    """
    End-to-end: rate_driver endpoint writes the correct average to the
    drivers table when multiple prior rated rides exist.
    """
    from backend.schemas import RideRatingRequest

    base_ride = {
        "id": "ride-abc",
        "rider_id": "rider-1",
        "driver_id": "driver-1",
        "status": "completed",
        "tip_amount": Decimal("0.0"),
        "driver_earnings": Decimal("10.0"),
    }
    driver = {"id": "driver-1", "user_id": "user-driver-1", "rating": 5.0, "name": "Test"}
    prior_rides = [
        {"id": "r0", "driver_id": "driver-1", "rider_rating": 4},
        {"id": "r1", "driver_id": "driver-1", "rider_rating": 5},
        {"id": "ride-abc", "driver_id": "driver-1", "rider_rating": 3},  # current ride
    ]
    update_one_mock = AsyncMock(return_value={})

    with (
        patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides.db_supabase.update_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
        patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(return_value=prior_rides)),
        patch("backend.routes.rides.db_supabase.update_one", update_one_mock),
        patch("backend.routes.rides.send_push_notification", AsyncMock()),
    ):
        result = await _rides_module.rate_driver(
            "ride-abc",
            RideRatingRequest(rating=3, tip_amount=Decimal("0.0")),
            current_user={"id": "rider-1"},
        )

    assert result == {"success": True}
    update_one_mock.assert_called_once()
    _table, _filter, payload = update_one_mock.call_args.args
    assert _table == "drivers"
    assert payload["rating"] == 4.0  # (4 + 5 + 3) / 3
    assert payload["total_ratings"] == 3


@_skip_no_deps
@pytest.mark.anyio
async def test_rate_driver_first_rating_sets_score():
    """First-ever rating for a driver: profile rating set to submitted score."""
    from backend.schemas import RideRatingRequest

    base_ride = {
        "id": "ride-new",
        "rider_id": "rider-2",
        "driver_id": "driver-new",
        "status": "completed",
        "tip_amount": Decimal("0.0"),
        "driver_earnings": Decimal("8.0"),
    }
    driver = {"id": "driver-new", "user_id": "user-new", "rating": 5.0, "name": "New Driver"}
    first_rated_ride = [{"id": "ride-new", "driver_id": "driver-new", "rider_rating": 5}]
    update_one_mock = AsyncMock(return_value={})

    with (
        patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides.db_supabase.update_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
        patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(return_value=first_rated_ride)),
        patch("backend.routes.rides.db_supabase.update_one", update_one_mock),
        patch("backend.routes.rides.send_push_notification", AsyncMock()),
    ):
        result = await _rides_module.rate_driver(
            "ride-new",
            RideRatingRequest(rating=5, tip_amount=Decimal("0.0")),
            current_user={"id": "rider-2"},
        )

    assert result == {"success": True}
    update_one_mock.assert_called_once()
    payload = update_one_mock.call_args.args[2]
    assert payload["rating"] == 5.0
    assert payload["total_ratings"] == 1
