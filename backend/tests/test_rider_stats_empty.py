"""get_rider_stats must not crash for a rider with no completed rides.

Regression: GET /api/v1/rides/stats returned 500 with
`AttributeError: 'int' object has no attribute 'quantize'`. total_saved was
built with `sum(<generator>)`, whose empty-iterable result is int 0; _round()
then called Decimal.quantize() on that int. Seeding the sums with _d(0) keeps
them Decimal even when there are no rides.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.rides import queries

pytestmark = pytest.mark.anyio


async def test_get_rider_stats_no_rides_returns_zero_decimal():
    # Every get_rows call (recent-ride tz probe + the main stats query) returns
    # no rows — the empty-rider path that used to crash.
    with patch(
        "backend.routes.rides._deps.db_supabase.get_rows",
        AsyncMock(return_value=[]),
    ):
        result = await queries.get_rider_stats(period="all", current_user={"id": "rider-1"})

    assert result["total_rides"] == 0
    assert result["total_saved"] == "0.00"  # str(_round(Decimal(0)))
    assert result["total_distance_km"] == 0.0
    assert result["co2_saved_kg"] == 0.0


async def test_get_rider_stats_sums_discounts_when_rides_exist():
    rides = [
        {"distance_km": "5.0", "discount_amount": "1.50"},
        {"distance_km": "3.0", "discount_amount": "2.25"},
    ]

    async def _get_rows(table, *args, **kwargs):
        # First call is the recent-ride timezone probe; the stats query is the
        # one filtered by COMPLETED status. Return the rides for the main query,
        # nothing for the tz probe (keeps the default timezone).
        if kwargs.get("limit") == 10000:
            return rides
        return []

    with patch(
        "backend.routes.rides._deps.db_supabase.get_rows",
        AsyncMock(side_effect=_get_rows),
    ):
        result = await queries.get_rider_stats(period="all", current_user={"id": "rider-1"})

    assert result["total_rides"] == 2
    assert result["total_saved"] == "3.75"
    assert result["total_distance_km"] == 8.0
