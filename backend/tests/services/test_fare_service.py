"""
Tests for FareService.

These tests exercise the pure helpers directly and the class methods with
a mocked db. No real Supabase, no FastAPI - this is the value of having
a service layer.
"""

import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.fare_service import (  # noqa: E402
    DEFAULT_FARE,
    FareService,
    _fd,
    build_default_fares,
    calculate_fare,
    find_service_area_for_point,
    merge_fare_configs_with_vehicle_types,
    merge_vehicle_pricing_with_vehicle_types,
    recalculate_fare_for_distance,
)

# ── Pure helpers ──────────────────────────────────────────────────────────────


class TestFd:
    def test_rounds_to_two_places(self):
        assert _fd(1.005) == 1.01  # half-up
        assert _fd(1.234) == 1.23
        assert _fd(1.235) == 1.24

    def test_handles_int(self):
        assert _fd(3) == 3.00

    def test_handles_string_numbers(self):
        assert _fd("1.5") == 1.50


class TestBuildDefaultFares:
    def test_returns_one_entry_per_vehicle_type(self):
        vts = [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}]
        out = build_default_fares(vts)
        assert len(out) == 3

    def test_uses_default_constants(self):
        out = build_default_fares([{"id": "v1"}])
        assert out[0]["base_fare"] == _fd(DEFAULT_FARE["base_fare"])
        assert out[0]["per_km_rate"] == _fd(DEFAULT_FARE["per_km_rate"])

    def test_applies_surge(self):
        out = build_default_fares([{"id": "v1"}], surge=1.75)
        assert out[0]["surge_multiplier"] == 1.75

    def test_default_surge_is_one(self):
        out = build_default_fares([{"id": "v1"}])
        assert out[0]["surge_multiplier"] == 1.00

    def test_empty_vehicle_types_yields_empty(self):
        assert build_default_fares([]) == []


class TestFindServiceArea:
    def _square_polygon(self, area_id: str):
        # Roughly a 1-degree square around (52, -106) - includes Saskatoon
        return {
            "id": area_id,
            "polygon": [
                {"lat": 51.5, "lng": -106.5},
                {"lat": 51.5, "lng": -105.5},
                {"lat": 52.5, "lng": -105.5},
                {"lat": 52.5, "lng": -106.5},
            ],
        }

    def test_returns_matching_area(self):
        a = self._square_polygon("saskatoon")
        result = find_service_area_for_point([a], 52.0, -106.0)
        assert result is not None
        assert result["id"] == "saskatoon"

    def test_returns_none_when_no_match(self):
        a = self._square_polygon("saskatoon")
        result = find_service_area_for_point([a], 0.0, 0.0)
        assert result is None

    def test_returns_first_match(self):
        a = self._square_polygon("saskatoon-1")
        b = self._square_polygon("saskatoon-2")
        result = find_service_area_for_point([a, b], 52.0, -106.0)
        assert result["id"] == "saskatoon-1"

    def test_handles_empty_areas(self):
        assert find_service_area_for_point([], 52.0, -106.0) is None


class TestMergeFareConfigs:
    def test_joins_configs_to_vehicle_types(self):
        fare_configs = [
            {
                "vehicle_type_id": "economy",
                "base_fare": 4.0,
                "per_km_rate": 1.5,
                "per_minute_rate": 0.3,
                "minimum_fare": 10.0,
                "booking_fee": 2.5,
            }
        ]
        vehicle_types = [{"id": "economy", "name": "Economy"}]
        out = merge_fare_configs_with_vehicle_types(fare_configs, vehicle_types, surge=1.5)
        assert len(out) == 1
        assert out[0]["vehicle_type"]["name"] == "Economy"
        assert out[0]["base_fare"] == 4.00
        assert out[0]["surge_multiplier"] == 1.50

    def test_skips_configs_with_no_matching_vehicle_type(self):
        fare_configs = [
            {
                "vehicle_type_id": "luxury",
                "base_fare": 10.0,
                "per_km_rate": 3.0,
                "per_minute_rate": 0.5,
                "minimum_fare": 20.0,
                "booking_fee": 5.0,
            }
        ]
        vehicle_types = [{"id": "economy"}]  # no "luxury"
        out = merge_fare_configs_with_vehicle_types(fare_configs, vehicle_types, surge=1.0)
        assert out == []


class TestMergeVehiclePricing:
    def test_joins_vehicle_pricing_to_vehicle_types_by_name(self):
        vehicle_pricing = [
            {
                "vehicle_type": "Economy",
                "base_fare": 4.0,
                "per_km": 1.5,
                "per_min": 0.3,
                "min_fare": 10.0,
                "booking_fee": 2.5,
            }
        ]
        vehicle_types = [
            {"id": "economy", "name": "Economy"},
            {"id": "xl", "name": "XL"},
        ]
        out = merge_vehicle_pricing_with_vehicle_types(vehicle_pricing, vehicle_types, surge=1.25)
        assert [row["vehicle_type"]["id"] for row in out] == ["economy"]
        assert out[0]["base_fare"] == 4.00
        assert out[0]["surge_multiplier"] == 1.25


# ── Service class ─────────────────────────────────────────────────────────────


def _make_db(vehicle_types=None, areas=None, fare_configs=None):
    """Build a mock db that supports the flat Supabase-style interface FareService uses."""
    db = MagicMock()

    # FareService calls db.get_rows(table_name, filters, limit=...) sequentially:
    # 1st call: vehicle_types, 2nd call: service_areas, optional 3rd call: fare_configs
    side_effects = []
    side_effects.append(vehicle_types if vehicle_types is not None else [])
    if areas is not None:
        side_effects.append(areas)
    if fare_configs is not None:
        side_effects.append(fare_configs)

    db.get_rows = AsyncMock(side_effect=side_effects)
    db.find_one = AsyncMock(return_value=None)
    db.insert_one = AsyncMock(return_value=None)
    db.update_one = AsyncMock(return_value=None)
    return db


pytestmark = pytest.mark.anyio


class TestFareService:
    async def test_returns_empty_when_no_vehicle_types(self):
        svc = FareService(_make_db(vehicle_types=[]))
        out = await svc.fares_for_location(52.0, -106.0)
        assert out == []

    async def test_returns_defaults_when_no_matching_area(self):
        svc = FareService(
            _make_db(
                vehicle_types=[{"id": "economy", "name": "Economy"}],
                areas=[],  # no service areas
            )
        )
        out = await svc.fares_for_location(52.0, -106.0)
        assert len(out) == 1
        assert out[0]["base_fare"] == _fd(DEFAULT_FARE["base_fare"])
        assert out[0]["surge_multiplier"] == 1.00

    async def test_returns_empty_when_matching_area_has_no_fare_configs(self):
        svc = FareService(
            _make_db(
                vehicle_types=[{"id": "economy"}],
                areas=[
                    {
                        "id": "saskatoon",
                        "surge_multiplier": 1.5,
                        "polygon": [
                            {"lat": 51.5, "lng": -106.5},
                            {"lat": 51.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -106.5},
                        ],
                    }
                ],
                fare_configs=[],
            )
        )
        out = await svc.fares_for_location(52.0, -106.0)
        assert out == []

    async def test_returns_merged_when_fare_configs_match(self):
        svc = FareService(
            _make_db(
                vehicle_types=[{"id": "economy", "name": "Economy"}],
                areas=[
                    {
                        "id": "saskatoon",
                        "surge_multiplier": 1.0,
                        "polygon": [
                            {"lat": 51.5, "lng": -106.5},
                            {"lat": 51.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -106.5},
                        ],
                    }
                ],
                fare_configs=[
                    {
                        "vehicle_type_id": "economy",
                        "base_fare": 5.0,
                        "per_km_rate": 2.0,
                        "per_minute_rate": 0.4,
                        "minimum_fare": 12.0,
                        "booking_fee": 3.0,
                    }
                ],
            )
        )
        out = await svc.fares_for_location(52.0, -106.0)
        assert len(out) == 1
        assert out[0]["base_fare"] == 5.00
        assert out[0]["per_km_rate"] == 2.00


# ── calculate_fare: earnings attribution & minimum-fare uplift ────────────────


def _fare_info(**overrides):
    """A complete fare_info dict for calculate_fare, tweakable per test."""
    info = {
        "base_fare": 3.50,
        "per_km_rate": 1.50,
        "per_minute_rate": 0.25,
        "minimum_fare": 8.00,
        "booking_fee": 2.00,
    }
    info.update(overrides)
    return info


class TestCalculateFare:
    """The invariant: total_fare == driver_earnings + admin_earnings.

    On a minimum-fare ride the charged total is clamped up to the floor; the
    minimum − subtotal uplift belongs to the driver (0% commission — booking
    and airport fees are the platform's admin_earnings). See the receipt's
    'Driver earns 100%' disclosure in build_fare_breakdown_lines.
    """

    def test_min_fare_uplift_goes_to_driver(self):
        # Tiny ride: base 3.50 + dist 0.15 + time 0.25 + booking 2.00 = 5.90
        # subtotal, clamped up to the 8.00 floor. Driver gets the uplift.
        fb = calculate_fare(_fare_info(), distance_km=0.1, duration_minutes=1)
        assert fb.total_fare == Decimal("8.00")
        assert fb.admin_earnings == Decimal("2.00")  # booking fee only
        assert fb.driver_earnings == Decimal("6.00")  # 8.00 − 2.00

    def test_earnings_sum_to_total_on_min_fare_ride(self):
        fb = calculate_fare(_fare_info(), distance_km=0.1, duration_minutes=1)
        assert fb.driver_earnings + fb.admin_earnings == fb.total_fare

    def test_non_min_ride_attribution_unchanged(self):
        # 10 km, 20 min: subtotal 25.50 > floor, driver == base+dist+time.
        fb = calculate_fare(_fare_info(), distance_km=10, duration_minutes=20)
        assert fb.total_fare == Decimal("25.50")
        assert fb.admin_earnings == Decimal("2.00")
        assert fb.driver_earnings == Decimal("23.50")
        assert fb.driver_earnings + fb.admin_earnings == fb.total_fare

    def test_surge_and_minimum_interaction_reconciles(self):
        # Surge 2.0× but the ride still floors: uplift still goes to driver.
        fb = calculate_fare(
            _fare_info(minimum_fare=20.00),
            distance_km=1,
            duration_minutes=2,
            surge=Decimal("2"),
        )
        assert fb.total_fare == Decimal("20.00")
        assert fb.admin_earnings == Decimal("2.00")
        assert fb.driver_earnings == Decimal("18.00")
        assert fb.driver_earnings + fb.admin_earnings == fb.total_fare

    def test_airport_fee_stays_with_admin_on_min_fare_ride(self):
        # Airport fee is the platform's, even under the minimum-fare clamp.
        fb = calculate_fare(
            _fare_info(minimum_fare=20.00),
            distance_km=0.1,
            duration_minutes=1,
            airport_fee=5.00,
        )
        assert fb.total_fare == Decimal("20.00")
        assert fb.admin_earnings == Decimal("7.00")  # booking 2.00 + airport 5.00
        assert fb.driver_earnings == Decimal("13.00")  # 20.00 − 7.00
        assert fb.driver_earnings + fb.admin_earnings == fb.total_fare

    def test_attribution_invariant_property(self):
        # The invariant holds across a wide spread of inputs, Decimal-exact.
        import random

        rng = random.Random(20260719)
        for _ in range(300):
            info = _fare_info(
                base_fare=round(rng.uniform(1.0, 6.0), 2),
                per_km_rate=round(rng.uniform(0.5, 3.0), 2),
                per_minute_rate=round(rng.uniform(0.1, 0.6), 2),
                minimum_fare=round(rng.uniform(5.0, 25.0), 2),
                booking_fee=round(rng.uniform(0.0, 4.0), 2),
            )
            fb = calculate_fare(
                info,
                distance_km=round(rng.uniform(0.0, 30.0), 2),
                duration_minutes=round(rng.uniform(0.0, 45.0), 1),
                surge=Decimal(str(round(rng.uniform(1.0, 2.5), 2))),
                airport_fee=round(rng.choice([0.0, 3.5, 5.0]), 2),
            )
            assert fb.driver_earnings + fb.admin_earnings == fb.total_fare


# ── recalculate_fare_for_distance: completion-time clamp & attribution ────────


def _completed_ride(**overrides):
    """A stored ride row as seen at trip completion, tweakable per test."""
    ride = {
        "id": "ride_1",
        "base_fare": 3.50,
        "distance_fare": 0.15,  # planned 0.1 km at 1.50/km
        "time_fare": 0.25,
        "booking_fee": 2.00,
        "airport_fee": 0,
        "distance_km": 0.1,
        "total_fare": 8.00,  # clamped up to the 8.00 floor at booking
    }
    ride.update(overrides)
    return ride


class TestRecalculateFareForDistance:
    """Completion recompute must keep the minimum-fare floor and stay reconciled.

    A ride clamped to the minimum at booking must not settle below it just
    because the actual distance came in short, and the recomputed earnings
    must still satisfy total_fare == driver_earnings + admin_earnings.
    """

    def test_recalc_keeps_minimum_floor_on_shorter_actual_distance(self):
        # Booked at the 8.00 floor; actual distance even shorter than planned.
        out = recalculate_fare_for_distance(_completed_ride(), actual_distance_km=0.05)
        assert out["total_fare"] == 8.00
        assert out["driver_earnings"] == 6.00  # 8.00 − booking 2.00

    def test_recalc_driver_earnings_reconcile_with_admin_share(self):
        out = recalculate_fare_for_distance(_completed_ride(), actual_distance_km=0.05)
        admin = 2.00  # booking + airport, unchanged at completion
        assert round(out["driver_earnings"] + admin, 2) == out["total_fare"]

    def test_recalc_unclamped_ride_behavior_preserved(self):
        # A ride that was never floored: driver still == base + dist + time.
        ride = _completed_ride(distance_fare=15.00, time_fare=5.00, distance_km=10, total_fare=25.50)
        out = recalculate_fare_for_distance(ride, actual_distance_km=8)
        assert out["total_fare"] == 22.50  # 3.50 + 12.00 + 5.00 + 2.00
        assert out["driver_earnings"] == 20.50  # 22.50 − booking 2.00

    def test_recalc_longer_actual_lifts_above_floor(self):
        # Floored at booking, but the actual trip ran long — no artificial floor.
        out = recalculate_fare_for_distance(_completed_ride(), actual_distance_km=20)
        assert out["total_fare"] == 35.75  # 3.50 + 30.00 + 0.25 + 2.00
        assert out["driver_earnings"] == 33.75  # 35.75 − booking 2.00
