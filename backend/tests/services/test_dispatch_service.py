"""
Tests for DispatchService.

Exercises the pure helpers directly and the class methods with a mocked
db. No real Supabase, no FastAPI, no WebSocket manager — those are
deliberately out of scope for DispatchService (see the module docstring).
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from services.dispatch_service import (  # noqa: E402
    DispatchService,
    _is_dispatchable_driver,
    dispatch_geo_bounds,
    filter_and_rank_drivers,
    select_driver_by_algorithm,
)

# ── Pure helpers ──────────────────────────────────────────────────────────────


class TestDispatchGeoBounds:
    """Bounding-box clauses for the dispatch candidate SQL fetch.

    Regression for the false-"no drivers" failure: an un-geo-filtered
    LIMIT 500 could return only far-away drivers while the nearest sat in
    row 501. The box must fully contain the search-radius circle so the
    exact haversine gate downstream never loses an in-radius driver.
    """

    SASKATOON = (52.1332, -106.6700)

    @staticmethod
    def _box(clauses):
        """Collapse the $and clause list into {(col, op): value}."""
        out = {}
        for clause in clauses:
            for col, pred in clause.items():
                for op, val in pred.items():
                    out[(col, op)] = val
        return out

    def test_emits_two_sided_range_on_both_columns(self):
        box = self._box(dispatch_geo_bounds(*self.SASKATOON, 10.0))
        assert set(box) == {("lat", "$gte"), ("lat", "$lte"), ("lng", "$gte"), ("lng", "$lte")}
        assert box[("lat", "$gte")] < self.SASKATOON[0] < box[("lat", "$lte")]
        assert box[("lng", "$gte")] < self.SASKATOON[1] < box[("lng", "$lte")]

    def test_box_contains_the_full_radius_circle(self):
        # A driver exactly radius_km due north/south/east/west of the pickup
        # must be inside the box, or the SQL pre-filter would drop drivers the
        # haversine gate considers in-radius.
        lat, lng = self.SASKATOON
        radius = 10.0
        box = self._box(dispatch_geo_bounds(lat, lng, radius))
        lat_edge = radius / 110.574
        lng_edge = radius / (111.320 * 0.6137)  # cos(52.1332°) ≈ 0.6137
        assert box[("lat", "$gte")] <= lat - lat_edge
        assert box[("lat", "$lte")] >= lat + lat_edge
        assert box[("lng", "$gte")] <= lng - lng_edge
        assert box[("lng", "$lte")] >= lng + lng_edge

    def test_excludes_far_side_of_province(self):
        # Regina is ~230 km from Saskatoon — far outside a 10 km box.
        box = self._box(dispatch_geo_bounds(*self.SASKATOON, 10.0))
        regina_lat, regina_lng = 50.4452, -104.6189
        assert not (box[("lat", "$gte")] <= regina_lat <= box[("lat", "$lte")])
        assert not (box[("lng", "$gte")] <= regina_lng <= box[("lng", "$lte")])

    def test_excludes_default_zero_zero_location(self):
        # Never-located drivers sit at the (0, 0) column default; the box must
        # not fetch them (they are undispatchable anyway).
        box = self._box(dispatch_geo_bounds(*self.SASKATOON, 10.0))
        assert not (box[("lat", "$gte")] <= 0.0 <= box[("lat", "$lte")])
        assert not (box[("lng", "$gte")] <= 0.0 <= box[("lng", "$lte")])

    def test_polar_latitude_does_not_divide_by_zero(self):
        clauses = dispatch_geo_bounds(90.0, 0.0, 10.0)
        assert all(abs(v) != float("inf") for c in clauses for p in c.values() for v in p.values())


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
                # Soft-deleted accounts keep status='active' (there is no
                # 'deleted' driver status), so dispatch has to exclude them here.
                "deleted_at": None,
                "vehicle_type_id": "economy",
            },
            limit=500,
        )

    async def test_required_area_drops_drivers_without_active_subscription(self):
        """A pass-required service area keeps only subscribed drivers."""
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return rows
            if table == "driver_subscriptions":
                return [{"driver_id": "d1", "started_at": "2026-01-01T00:00:00Z", "expires_at": None}]
            return []

        db.get_rows = AsyncMock(side_effect=_get_rows)
        db.find_one = AsyncMock(return_value={"id": "area1", "subscription_required": True})
        svc = DispatchService(db)

        with (
            patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value={"d1", "d2"})),
            patch("services.dispatch_service.exhausted_driver_ids", AsyncMock(return_value=set())),
        ):
            out = await svc.find_candidate_drivers(
                {"vehicle_type_id": "economy", "service_area_id": "area1"}
            )
        assert [d["id"] for d in out] == ["d1"]

    async def test_required_area_falls_back_to_parent_area_flag(self):
        """A child area without its own flag inherits subscription_required from its parent."""
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]

        async def _find_one(table, filters=None, **kwargs):
            if filters.get("id") == "child1":
                return {"id": "child1", "subscription_required": False, "parent_service_area_id": "parent1"}
            if filters.get("id") == "parent1":
                return {"id": "parent1", "subscription_required": True}
            return None

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return rows
            if table == "driver_subscriptions":
                return [{"driver_id": "d2", "started_at": "2026-01-01T00:00:00Z", "expires_at": None}]
            return []

        db.find_one = AsyncMock(side_effect=_find_one)
        db.get_rows = AsyncMock(side_effect=_get_rows)
        svc = DispatchService(db)

        with (
            patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value={"d1", "d2"})),
            patch("services.dispatch_service.exhausted_driver_ids", AsyncMock(return_value=set())),
        ):
            out = await svc.find_candidate_drivers(
                {"vehicle_type_id": "economy", "service_area_id": "child1"}
            )
        assert [d["id"] for d in out] == ["d2"]

    async def test_expired_subscription_is_not_counted_as_active(self):
        """A subscription row past its expires_at must not satisfy the required-area gate."""
        db = _make_db()
        rows = [{"id": "d1"}]

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return rows
            if table == "driver_subscriptions":
                return [{"driver_id": "d1", "started_at": "2020-01-01T00:00:00Z", "expires_at": "2020-02-01T00:00:00Z"}]
            return []

        db.get_rows = AsyncMock(side_effect=_get_rows)
        db.find_one = AsyncMock(return_value={"id": "area1", "subscription_required": True})
        svc = DispatchService(db)

        with (
            patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value={"d1"})),
            patch("services.dispatch_service.exhausted_driver_ids", AsyncMock(return_value=set())),
        ):
            out = await svc.find_candidate_drivers(
                {"vehicle_type_id": "economy", "service_area_id": "area1"}
            )
        assert out == []

    async def test_daily_quota_exhausted_drivers_dropped_in_any_area(self):
        """The daily ride-allowance filter applies even when the area doesn't require a pass."""
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return rows
            if table == "driver_subscriptions":
                return [{"driver_id": "d2", "started_at": "2026-01-01T00:00:00Z", "expires_at": None}]
            return []

        db.get_rows = AsyncMock(side_effect=_get_rows)
        db.find_one = AsyncMock(return_value={"id": "area1", "subscription_required": False})
        svc = DispatchService(db)

        with (
            patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value={"d1", "d2"})),
            patch("services.dispatch_service.exhausted_driver_ids", AsyncMock(return_value={"d2"})),
        ):
            out = await svc.find_candidate_drivers(
                {"vehicle_type_id": "economy", "service_area_id": "area1"}
            )
        assert [d["id"] for d in out] == ["d1"]

    async def test_quota_lookup_failure_fails_open(self):
        """A transient error in the quota filter must not drop every driver."""
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]
        db.get_rows = AsyncMock(return_value=rows)
        db.find_one = AsyncMock(return_value={"id": "area1", "subscription_required": False})
        svc = DispatchService(db)

        with (
            patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value={"d1", "d2"})),
            patch(
                "services.dispatch_service.exhausted_driver_ids",
                AsyncMock(side_effect=RuntimeError("redis down")),
            ),
        ):
            out = await svc.find_candidate_drivers(
                {"vehicle_type_id": "economy", "service_area_id": "area1"}
            )
        assert [d["id"] for d in out] == ["d1", "d2"]

    async def test_pass_filter_db_error_fails_open(self):
        """A DB error anywhere in the pass-filter block must not block dispatch."""
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]
        db.get_rows = AsyncMock(return_value=rows)
        db.find_one = AsyncMock(side_effect=RuntimeError("db unavailable"))
        svc = DispatchService(db)

        with patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value={"d1", "d2"})):
            out = await svc.find_candidate_drivers(
                {"vehicle_type_id": "economy", "service_area_id": "area1"}
            )
        assert out == rows

    async def test_no_service_area_skips_pass_filter_entirely(self):
        """A ride with no service_area_id never touches the subscription/quota gates."""
        db = _make_db()
        rows = [{"id": "d1"}]
        db.get_rows = AsyncMock(return_value=rows)
        svc = DispatchService(db)

        with patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value={"d1"})):
            out = await svc.find_candidate_drivers({"vehicle_type_id": "economy"})
        assert out == rows
        db.find_one.assert_not_awaited()

    async def test_empty_driver_rows_returns_early(self):
        """No online/available drivers -> empty list, no presence/pass lookups attempted."""
        db = _make_db()
        db.get_rows = AsyncMock(return_value=[])
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers({"vehicle_type_id": "economy", "service_area_id": "area1"})
        assert out == []
        db.find_one.assert_not_awaited()

    async def test_presence_filter_failure_falls_back_to_db_online_drivers(self):
        """Redis outage on the presence check must not take every driver offline."""
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]
        db.get_rows = AsyncMock(return_value=rows)
        svc = DispatchService(db)

        with patch(
            "services.dispatch_service.present_driver_ids",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            out = await svc.find_candidate_drivers({"vehicle_type_id": "economy"})
        assert out == rows

    async def test_empty_presence_set_treated_as_fail_open(self):
        """An empty (but reachable) presence set must not zero out every driver."""
        db = _make_db()
        rows = [{"id": "d1"}, {"id": "d2"}]
        db.get_rows = AsyncMock(return_value=rows)
        svc = DispatchService(db)

        with patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value=set())):
            out = await svc.find_candidate_drivers({"vehicle_type_id": "economy"})
        assert out == rows

    async def test_wav_ride_adds_wav_filter(self):
        db = _make_db()
        rows = [{"id": "d1"}]
        db.get_rows = AsyncMock(return_value=rows)
        svc = DispatchService(db)

        with patch("services.dispatch_service.present_driver_ids", AsyncMock(return_value={"d1"})):
            await svc.find_candidate_drivers({"vehicle_type_id": "economy", "requires_wav": True})
        db.get_rows.assert_awaited_once_with(
            "drivers",
            {
                "is_online": True,
                "is_available": True,
                "is_verified": True,
                "status": "active",
                "deleted_at": None,
                "vehicle_type_id": "economy",
                "is_wav": True,
            },
            limit=500,
        )
