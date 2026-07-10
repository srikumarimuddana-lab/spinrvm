"""
Regression tests for B-P0-1: driver rating aggregation.

Background
----------
rate_driver (rides.py) originally read `r.get('driver_rating')` in a
list-comprehension over raw get_rows() output.  `driver_rating` is a
*synthetic* field absent from raw rows, so rated_rides was always [] and
the driver profile was never updated.

Fix (B-P0-1): switched to a rolling-average approach — no full-table
get_rows needed.  The driver record's (total_ratings, rating) fields are
used to compute the new running average in O(1):

    new_avg = (old_avg * old_count + new_rating) / (old_count + 1)

Test layers
-----------
Layer 1 — pure-logic tests: always runnable, no backend imports.
Layer 2 — integration tests: skipped locally, run in CI (loguru/supabase present).
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Layer 1 — pure logic tests
# ---------------------------------------------------------------------------


def test_rolling_average_first_rating():
    """Brand-new driver (total_ratings=0): first rating sets the score exactly."""
    old_count = 0
    old_avg = 0.0
    new_rating = 5.0
    new_count = old_count + 1
    new_avg = round((old_avg * old_count + new_rating) / new_count, 2)
    assert new_avg == 5.0
    assert new_count == 1


def test_rolling_average_accumulates_correctly():
    """Two prior ratings averaging 4.5, new rating 3 → running avg 4.0."""
    old_count = 2
    old_avg = 4.5
    new_rating = 3.0
    new_count = old_count + 1
    new_avg = round((old_avg * old_count + new_rating) / new_count, 2)
    assert new_avg == 4.0
    assert new_count == 3


def test_rolling_average_rounds_to_two_decimal_places():
    """Verify rounding behaviour matches round(x, 2)."""
    old_count = 2
    old_avg = 4.0
    new_rating = 5.0
    new_count = old_count + 1
    new_avg = round((old_avg * old_count + new_rating) / new_count, 2)
    assert new_avg == 4.33


def test_rolling_average_missing_total_ratings_defaults_to_zero():
    """Driver record without total_ratings → int(...  or 0) = 0 → treated as first rating."""
    driver = {"id": "d1", "rating": 4.5}  # no total_ratings key
    old_count = int(driver.get("total_ratings") or 0)
    old_avg = float(driver.get("rating") or 0)
    new_rating = 3.0
    new_count = old_count + 1
    new_avg = round((old_avg * old_count + new_rating) / new_count, 2)
    assert old_count == 0
    assert new_avg == 3.0
    assert new_count == 1


def test_old_code_driver_rating_always_empty():
    """Regression: old comprehension used driver_rating (absent from raw rows) → always []."""
    rides = [
        {"id": "r1", "driver_id": "d1", "rider_rating": 4},
        {"id": "r2", "driver_id": "d1", "rider_rating": 5},
    ]
    old_rated = [float(r.get("driver_rating")) for r in rides if r.get("driver_rating") is not None]
    assert old_rated == [], "driver_rating absent from raw rows"

    new_rated = [float(r.get("rider_rating")) for r in rides if r.get("rider_rating") is not None]
    assert new_rated == [4.0, 5.0], "rider_rating is the real column"


# ---------------------------------------------------------------------------
# Layer 2 — integration tests (skipped without backend deps)
# ---------------------------------------------------------------------------

try:
    import backend.routes.rides as _rides_module

    _HAS_BACKEND_DEPS = True
except Exception:
    _rides_module = None  # type: ignore[assignment]
    _HAS_BACKEND_DEPS = False

_skip_no_deps = pytest.mark.skipif(not _HAS_BACKEND_DEPS, reason="backend deps not installed")


@_skip_no_deps
@pytest.mark.anyio
async def test_rate_driver_calls_update_one_with_correct_rolling_average():
    """
    rate_driver uses the driver record's (total_ratings, rating) to compute a
    rolling average — no full get_rows scan.  Two prior ratings averaging 4.5
    plus a new rating of 3 → (4.5*2 + 3) / 3 = 4.0.
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
    # Driver record carries (rating, total_ratings) used by the rolling-average formula
    driver = {"id": "driver-1", "user_id": "user-driver-1", "rating": 4.5, "total_ratings": 2, "name": "Test"}
    update_one_mock = AsyncMock(return_value={})

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
        patch("backend.routes.rides._deps.db_supabase.update_one", update_one_mock),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
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
    assert payload["rating"] == 4.0  # (4.5*2 + 3) / 3
    assert payload["total_ratings"] == 3


@_skip_no_deps
@pytest.mark.anyio
async def test_rate_driver_first_rating_sets_score():
    """First-ever rating (total_ratings absent/0): new avg equals submitted score."""
    from backend.schemas import RideRatingRequest

    base_ride = {
        "id": "ride-new",
        "rider_id": "rider-2",
        "driver_id": "driver-new",
        "status": "completed",
        "tip_amount": Decimal("0.0"),
        "driver_earnings": Decimal("8.0"),
    }
    driver = {"id": "driver-new", "user_id": "user-new", "name": "New Driver"}  # no rating/total_ratings
    update_one_mock = AsyncMock(return_value={})

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
        patch("backend.routes.rides._deps.db_supabase.update_one", update_one_mock),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
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


@_skip_no_deps
@pytest.mark.anyio
async def test_rate_driver_tip_with_null_ride_fields():
    """tip_amount and driver_earnings are NULL in DB (key present, value None).
    ride.get("tip_amount", 0) returns None, not 0 — arithmetic must not crash."""
    from backend.schemas import RideRatingRequest

    base_ride = {
        "id": "ride-tip",
        "rider_id": "rider-3",
        "driver_id": "driver-tip",
        "status": "completed",
        "tip_amount": None,
        "driver_earnings": None,
    }
    driver = {"id": "driver-tip", "user_id": "user-tip", "name": "Tip Driver", "rating": 4.0, "total_ratings": 1}
    update_ride_mock = AsyncMock(return_value=base_ride)
    update_one_mock = AsyncMock(return_value={})

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=base_ride)),
        patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
        patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
        patch("backend.routes.rides._deps.db_supabase.update_one", update_one_mock),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
    ):
        result = await _rides_module.rate_driver(
            "ride-tip",
            RideRatingRequest(rating=5, tip_amount=Decimal("2.00")),
            current_user={"id": "rider-3"},
        )

    assert result == {"success": True}
    # update_ride called twice: once for the rating, once for the tip
    assert update_ride_mock.call_count == 2
    tip_payload = update_ride_mock.call_args_list[1].args[1]
    assert tip_payload["tip_amount"] == Decimal("2.00")
    assert tip_payload["driver_earnings"] == Decimal("2.00")
