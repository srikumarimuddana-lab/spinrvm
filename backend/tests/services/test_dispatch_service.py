"""
Tests for DispatchService.

Exercises the pure helpers directly and the class methods with a mocked
db. No real Supabase, no FastAPI, no WebSocket manager — those are
deliberately out of scope for DispatchService (see the module docstring).
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from services.dispatch_service import (  # noqa: E402
    DispatchService,
    _is_dispatchable_driver,
    filter_and_rank_drivers,
    select_driver_by_algorithm,
)

# ── Pure helpers ──────────────────────────────────────────────────────────────


class TestIsDispatchableDriver:
    def test_accepts_driver_with_user_id_and_location(self):
        assert _is_dispatchable_driver({"user_id": "u1", "lat": 52.0, "lng": -106.0}) is True

    def test_rejects_driver_without_user_id(self):
        assert _is_dispatchable_driver({"lat": 52.0, "lng": -106.0}) is False
        assert _is_dispatchable_driver({"user_id": None, "lat": 52.0, "lng": -106.0}) is False
        assert _is_dispatchable_driver({"user_id": "", "lat": 52.0, "lng": -106.0}) is False

    def test_rejects_driver_without_location(self):
        assert _is_dispatchable_driver({"user_id": "u1", "lat": None, "lng": -106.0}) is False
        assert _is_dispatchable_driver({"user_id": "u1", "lat": 52.0, "lng": None}) is False
        assert _is_dispatchable_driver({"user_id": "u1"}) is False


class TestFilterAndRankDrivers:
    def _ride(self):
        return {"pickup_lat": 52.0, "pickup_lng": -106.0}

    def _driver(self, id: str, lat=52.0, lng=-106.0, rating=5.0, user_id="u1"):
        return {"id": id, "user_id": user_id, "lat": lat, "lng": lng, "rating": rating}

    def test_empty_pool_returns_empty(self):
        out = filter_and_rank_drivers(self._ride(), [], "nearest", 4.0, 10.0)
        assert out == []

    def test_attaches_distance_to_each_driver(self):
        drivers = [self._driver("d1", lat=52.0, lng=-106.0)]  # same point
        out = filter_and_rank_drivers(self._ride(), drivers, "nearest", 4.0, 10.0)
        assert len(out) == 1
        assert out[0][0]["id"] == "d1"
        assert out[0][1] == 0  # distance is 0 km for same point

    def test_drops_driver_outside_radius(self):
        # Far driver — well outside 1km radius
        drivers = [self._driver("d_far", lat=53.0, lng=-106.0)]
        out = filter_and_rank_drivers(self._ride(), drivers, "nearest", 4.0, 1.0)
        assert out == []

    def test_drops_orphan_driver_without_user_id(self):
        drivers = [self._driver("d1", user_id=None)]
        out = filter_and_rank_drivers(self._ride(), drivers, "nearest", 4.0, 10.0)
        assert out == []

    def test_drops_driver_without_lat_lng(self):
        drivers = [self._driver("d1", lat=None)]
        out = filter_and_rank_drivers(self._ride(), drivers, "nearest", 4.0, 10.0)
        assert out == []

    def test_rating_floor_applied_for_rating_based(self):
        drivers = [
            self._driver("d_low", rating=3.0),
            self._driver("d_high", rating=4.8),
        ]
        out = filter_and_rank_drivers(self._ride(), drivers, "rating_based", 4.0, 10.0)
        ids = [d[0]["id"] for d in out]
        assert "d_low" not in ids
        assert "d_high" in ids

    def test_rating_floor_applied_for_combined(self):
        drivers = [self._driver("d_low", rating=3.0)]
        out = filter_and_rank_drivers(self._ride(), drivers, "combined", 4.0, 10.0)
        assert out == []

    def test_rating_floor_not_applied_for_nearest(self):
        # nearest should accept low-rated drivers (rating isn't a factor)
        drivers = [self._driver("d_low", rating=3.0)]
        out = filter_and_rank_drivers(self._ride(), drivers, "nearest", 4.0, 10.0)
        assert len(out) == 1

    def test_rating_floor_not_applied_for_round_robin(self):
        drivers = [self._driver("d_low", rating=3.0)]
        out = filter_and_rank_drivers(self._ride(), drivers, "round_robin", 4.0, 10.0)
        assert len(out) == 1


class TestSelectDriverByAlgorithm:
    def test_empty_returns_none(self):
        assert select_driver_by_algorithm([], "nearest") is None

    def test_nearest_picks_lowest_distance(self):
        drivers = [
            ({"id": "far", "rating": 5.0}, 5.0),
            ({"id": "close", "rating": 3.0}, 1.0),
            ({"id": "mid", "rating": 4.5}, 3.0),
        ]
        out = select_driver_by_algorithm(drivers, "nearest")
        assert out["id"] == "close"

    def test_combined_also_picks_by_distance(self):
        """Combined applies rating floor in filter step, then picks nearest."""
        drivers = [
            ({"id": "far", "rating": 5.0}, 5.0),
            ({"id": "close", "rating": 4.1}, 1.0),
        ]
        out = select_driver_by_algorithm(drivers, "combined")
        assert out["id"] == "close"

    def test_rating_based_picks_highest_rating(self):
        drivers = [
            ({"id": "close", "rating": 4.0}, 1.0),
            ({"id": "far_but_great", "rating": 4.9}, 5.0),
        ]
        out = select_driver_by_algorithm(drivers, "rating_based")
        assert out["id"] == "far_but_great"

    def test_round_robin_without_last_assigned_picks_first(self):
        drivers = [
            ({"id": "a", "rating": 5.0}, 1.0),
            ({"id": "b", "rating": 5.0}, 2.0),
        ]
        out = select_driver_by_algorithm(drivers, "round_robin", last_assigned_driver_id=None)
        assert out["id"] == "a"

    def test_round_robin_picks_next_after_last_assigned(self):
        drivers = [
            ({"id": "a", "rating": 5.0}, 1.0),
            ({"id": "b", "rating": 5.0}, 2.0),
            ({"id": "c", "rating": 5.0}, 3.0),
        ]
        out = select_driver_by_algorithm(drivers, "round_robin", last_assigned_driver_id="a")
        assert out["id"] == "b"

    def test_round_robin_wraps_around(self):
        drivers = [
            ({"id": "a", "rating": 5.0}, 1.0),
            ({"id": "b", "rating": 5.0}, 2.0),
        ]
        out = select_driver_by_algorithm(drivers, "round_robin", last_assigned_driver_id="b")
        assert out["id"] == "a"

    def test_round_robin_with_unknown_last_id_starts_over(self):
        """If the last driver isn't in the current pool (e.g. went offline),
        start from index 0 instead of crashing."""
        drivers = [
            ({"id": "a", "rating": 5.0}, 1.0),
            ({"id": "b", "rating": 5.0}, 2.0),
        ]
        out = select_driver_by_algorithm(drivers, "round_robin", last_assigned_driver_id="vanished")
        # -1 + 1 = 0 → first driver
        assert out["id"] == "a"

    def test_unknown_algorithm_falls_back_to_nearest(self):
        drivers = [
            ({"id": "far", "rating": 5.0}, 5.0),
            ({"id": "close", "rating": 3.0}, 1.0),
        ]
        out = select_driver_by_algorithm(drivers, "does_not_exist")
        assert out["id"] == "close"


# ── Service class (with mocked db) ────────────────────────────────────────────


def _make_db():
    """Minimal mock db supporting the find / update_one shapes DispatchService uses."""
    db = MagicMock()
    db.service_areas = MagicMock()
    db.service_areas.find_one = AsyncMock(return_value=None)
    db.drivers = MagicMock()
    find_result = MagicMock()
    find_result.to_list = AsyncMock(return_value=[])
    db.drivers.find = MagicMock(return_value=find_result)
    db.drivers.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.rides = MagicMock()
    db.rides.find_one = AsyncMock(return_value=None)
    db.rides.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    return db


@pytest.mark.asyncio
class TestDispatchServiceClaim:
    async def test_claim_driver_returns_true_when_row_was_available(self):
        db = _make_db()
        db.drivers.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        svc = DispatchService(db)
        assert await svc.claim_driver("d1") is True

    async def test_claim_driver_returns_false_when_row_already_taken(self):
        db = _make_db()
        db.drivers.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
        svc = DispatchService(db)
        assert await svc.claim_driver("d1") is False

    async def test_claim_any_driver_returns_first_successful(self):
        """First driver is taken, second succeeds — walk the list."""
        db = _make_db()
        results = [MagicMock(modified_count=0), MagicMock(modified_count=1)]
        db.drivers.update_one = AsyncMock(side_effect=results)
        svc = DispatchService(db)

        ranked = [({"id": "d1"}, 1.0), ({"id": "d2"}, 2.0)]
        out = await svc.claim_any_driver(ranked)
        assert out["id"] == "d2"

    async def test_claim_any_driver_returns_none_when_all_taken(self):
        db = _make_db()
        db.drivers.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
        svc = DispatchService(db)

        ranked = [({"id": "d1"}, 1.0), ({"id": "d2"}, 2.0)]
        out = await svc.claim_any_driver(ranked)
        assert out is None


@pytest.mark.asyncio
class TestDispatchServiceAssign:
    async def test_assign_driver_flips_ride_to_driver_assigned(self):
        db = _make_db()
        svc = DispatchService(db)
        import datetime as dt

        now = dt.datetime(2026, 1, 1, 12, 0, 0)
        await svc.assign_driver_to_ride("r1", "d1", now)

        db.rides.update_one.assert_awaited_once()
        call_args = db.rides.update_one.await_args
        # Filter identifies the ride by id only (business rule)
        assert call_args.args[0] == {"id": "r1"}
        # Update sets driver_id, status, timestamps
        update = call_args.args[1]["$set"]
        assert update["driver_id"] == "d1"
        assert update["status"] == "driver_assigned"
        assert update["driver_notified_at"] == now
        assert update["updated_at"] == now


@pytest.mark.asyncio
class TestDispatchServiceLastAssigned:
    async def test_returns_last_assigned_driver_id(self):
        db = _make_db()
        db.rides.find_one = AsyncMock(return_value={"driver_id": "d_last"})
        svc = DispatchService(db)
        assert await svc.last_assigned_driver_id() == "d_last"

    async def test_returns_none_when_no_prior_ride(self):
        db = _make_db()
        db.rides.find_one = AsyncMock(return_value=None)
        svc = DispatchService(db)
        assert await svc.last_assigned_driver_id() is None


@pytest.mark.asyncio
class TestDispatchServiceFindCandidates:
    async def test_queries_online_available_matching_vehicle_type(self):
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]
        find_result = MagicMock()
        find_result.to_list = AsyncMock(return_value=rows)
        db.drivers.find = MagicMock(return_value=find_result)
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers({"vehicle_type_id": "economy"})
        assert out == rows
        db.drivers.find.assert_called_once_with(
            {
                "is_online": True,
                "is_available": True,
                "vehicle_type_id": "economy",
            }
        )
