"""
Tests for FareService.

These tests exercise the pure helpers directly and the class methods with
a mocked db. No real Supabase, no FastAPI - this is the value of having
a service layer.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.fare_service import (  # noqa: E402
    DEFAULT_FARE,
    FareService,
    _fd,
    build_default_fares,
    find_service_area_for_point,
    merge_fare_configs_with_vehicle_types,
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


# ── Service class ─────────────────────────────────────────────────────────────


def _make_db(vehicle_types=None, areas=None, fare_configs=None):
    """Build a mock db that supports the chain `db.X.find(...).to_list(N)`."""
    db = MagicMock()

    def make_table(rows):
        find_result = MagicMock()
        find_result.to_list = AsyncMock(return_value=rows or [])
        table = MagicMock()
        table.find = MagicMock(return_value=find_result)
        return table

    db.vehicle_types = make_table(vehicle_types)
    db.service_areas = make_table(areas)
    db.fare_configs = make_table(fare_configs)
    return db


@pytest.mark.asyncio
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

    async def test_returns_defaults_with_surge_when_no_fare_configs(self):
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
        assert len(out) == 1
        assert out[0]["surge_multiplier"] == 1.50

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
