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

    # ── Destination filter (P2) ──────────────────────────────────────
    # Drivers in destination_mode only see offers whose dropoff brings them
    # closer to their preferred destination. Non-destination-mode drivers
    # are unaffected.

    def _ride_with_dropoff(self, dropoff_lat, dropoff_lng):
        return {
            "pickup_lat": 52.0,
            "pickup_lng": -106.0,
            "dropoff_lat": dropoff_lat,
            "dropoff_lng": dropoff_lng,
        }

    def test_destination_mode_off_ignores_dropoff_direction(self):
        # Driver going AWAY from preferred destination is still eligible
        # when destination_mode is off (which is the default).
        drivers = [self._driver("d1")]
        # ride drops far from any preferred destination
        ride = self._ride_with_dropoff(53.0, -106.0)
        out = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10000.0)
        assert len(out) == 1

    def test_destination_mode_accepts_ride_that_brings_driver_closer(self):
        # Driver at (52.0, -106.0), destination at (52.5, -106.0). Ride
        # drops at (52.3, -106.0) — clearly closer to destination than
        # driver's current position. Should be included.
        drivers = [
            {
                "id": "d1",
                "user_id": "u1",
                "lat": 52.0,
                "lng": -106.0,
                "rating": 5.0,
                "destination_mode": True,
                "destination_lat": 52.5,
                "destination_lng": -106.0,
            }
        ]
        ride = self._ride_with_dropoff(52.3, -106.0)
        out = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10000.0)
        assert len(out) == 1

    def test_destination_mode_rejects_ride_in_wrong_direction(self):
        # Driver at (52.0, -106.0), destination NORTH at (52.5, -106.0).
        # Ride drops SOUTH at (51.5, -106.0) — takes the driver further
        # from their destination. Should be filtered out.
        drivers = [
            {
                "id": "d1",
                "user_id": "u1",
                "lat": 52.0,
                "lng": -106.0,
                "rating": 5.0,
                "destination_mode": True,
                "destination_lat": 52.5,
                "destination_lng": -106.0,
            }
        ]
        ride = self._ride_with_dropoff(51.5, -106.0)
        out = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10000.0)
        assert out == []

    def test_destination_mode_fails_open_when_coords_missing(self):
        # destination_mode flag on but no stored coords — don't make the
        # driver invisible; let them see all offers (defensive default).
        drivers = [
            {
                "id": "d1",
                "user_id": "u1",
                "lat": 52.0,
                "lng": -106.0,
                "rating": 5.0,
                "destination_mode": True,
                "destination_lat": None,
                "destination_lng": None,
            }
        ]
        ride = self._ride_with_dropoff(53.0, -106.0)
        out = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10000.0)
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


class TestOrderDriversFifo:
    TERMINAL = "term-yxe"

    def _d(self, did, zone, entered, dist):
        return ({"id": did, "zone_venue_id": zone, "zone_entered_at": entered}, dist)

    def test_orders_by_wait_time_longest_first(self):
        pool = [
            self._d("newest", self.TERMINAL, "2026-06-18T10:05:00+00:00", 0.2),
            self._d("oldest", self.TERMINAL, "2026-06-18T10:00:00+00:00", 1.5),
            self._d("middle", self.TERMINAL, "2026-06-18T10:02:00+00:00", 0.9),
        ]
        ranked = order_drivers_fifo(pool, self.TERMINAL)
        assert [d["id"] for d, _, _ in ranked] == ["oldest", "middle", "newest"]

    def test_distance_is_ignored_for_ordering(self):
        # The closest driver ("newest", 0.1km) must still wait behind the
        # longest-queued one — that's the whole point of FIFO.
        pool = [
            self._d("newest", self.TERMINAL, "2026-06-18T10:05:00+00:00", 0.1),
            self._d("oldest", self.TERMINAL, "2026-06-18T10:00:00+00:00", 9.0),
        ]
        ranked = order_drivers_fifo(pool, self.TERMINAL)
        assert ranked[0][0]["id"] == "oldest"

    def test_excludes_drivers_not_queued_for_this_terminal(self):
        pool = [
            self._d("queued", self.TERMINAL, "2026-06-18T10:00:00+00:00", 1.0),
            self._d("other_terminal", "term-other", "2026-06-18T09:00:00+00:00", 0.5),
            self._d("not_queued", None, None, 0.3),
        ]
        ranked = order_drivers_fifo(pool, self.TERMINAL)
        assert [d["id"] for d, _, _ in ranked] == ["queued"]

    def test_empty_when_nobody_queued_signals_fallback(self):
        pool = [self._d("not_queued", None, None, 0.3)]
        assert order_drivers_fifo(pool, self.TERMINAL) == []

    def test_eta_seconds_derived_from_distance(self):
        pool = [self._d("d1", self.TERMINAL, "2026-06-18T10:00:00+00:00", 2.0)]
        (_driver, eta_sec, dist_km) = order_drivers_fifo(pool, self.TERMINAL)[0]
        assert dist_km == 2.0 and eta_sec == int(2.0 * 120)


# ── Service class (with mocked db) ────────────────────────────────────────────


def _make_db():
    """Minimal mock db supporting the flat Supabase-style interface DispatchService uses."""
    db = MagicMock()
    db.find_one = AsyncMock(return_value=None)
    db.get_rows = AsyncMock(return_value=[])
    db.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.insert_one = AsyncMock(return_value=None)
    return db


pytestmark = pytest.mark.anyio


class TestDispatchServiceClaim:
    async def test_claim_driver_returns_true_when_row_was_available(self):
        db = _make_db()
        db.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        svc = DispatchService(db)
        assert await svc.claim_driver("d1") is True

    async def test_claim_driver_returns_false_when_row_already_taken(self):
        db = _make_db()
        db.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
        svc = DispatchService(db)
        assert await svc.claim_driver("d1") is False

    async def test_claim_any_driver_returns_first_successful(self):
        """First driver is taken, second succeeds — walk the list."""
        db = _make_db()
        results = [MagicMock(modified_count=0), MagicMock(modified_count=1)]
        db.update_one = AsyncMock(side_effect=results)
        svc = DispatchService(db)

        ranked = [({"id": "d1"}, 1.0), ({"id": "d2"}, 2.0)]
        out = await svc.claim_any_driver(ranked)
        assert out["id"] == "d2"

    async def test_claim_any_driver_returns_none_when_all_taken(self):
        db = _make_db()
        db.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
        svc = DispatchService(db)

        ranked = [({"id": "d1"}, 1.0), ({"id": "d2"}, 2.0)]
        out = await svc.claim_any_driver(ranked)
        assert out is None


class TestDispatchServiceAssign:
    async def test_assign_driver_flips_ride_to_driver_assigned(self):
        db = _make_db()
        svc = DispatchService(db)
        import datetime as dt

        now = dt.datetime(2026, 1, 1, 12, 0, 0)
        await svc.assign_driver_to_ride("r1", "d1", now)

        db.update_one.assert_awaited_once()
        call_args = db.update_one.await_args
        # First positional arg is table name, second is filter, third is update
        assert call_args.args[0] == "rides"
        assert call_args.args[1] == {"id": "r1"}
        # Update sets driver_id, status, timestamps
        update = call_args.args[2]["$set"]
        assert update["driver_id"] == "d1"
        assert update["status"] == "driver_assigned"
        assert update["driver_notified_at"] == now
        assert update["updated_at"] == now


class TestDispatchServiceLastAssigned:
    async def test_returns_last_assigned_driver_id(self):
        db = _make_db()
        db.get_rows = AsyncMock(return_value=[{"driver_id": "d_last"}])
        svc = DispatchService(db)
        assert await svc.last_assigned_driver_id() == "d_last"

    async def test_returns_none_when_no_prior_ride(self):
        db = _make_db()
        db.get_rows = AsyncMock(return_value=[])
        svc = DispatchService(db)
        assert await svc.last_assigned_driver_id() is None


class TestDispatchServiceFindCandidates:
    async def test_queries_online_available_matching_vehicle_type(self):
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]
        db.get_rows = AsyncMock(return_value=rows)
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers({"vehicle_type_id": "economy"})
        assert out == rows
        db.get_rows.assert_awaited_once_with(
            "drivers",
            {
                "is_online": True,
                "is_available": True,
                "is_verified": True,
                "status": "active",
                "vehicle_type_id": "economy",
            },
            limit=500,
        )
