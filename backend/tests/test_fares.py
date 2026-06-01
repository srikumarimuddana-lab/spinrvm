"""Unit tests for backend/routes/fares.py — surge cap regression."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.unit
@pytest.mark.anyio
async def test_fare_estimate_surge_capped_at_2_5x():
    """Regression: build_fares_for_area must never return surge_multiplier > 2.5.

    PR #510 added min(..., SURGE_CAP) to prevent a DB value of 5.0 (or any
    admin-override above 2.5) from propagating to the rider fare estimate.
    """
    from backend.routes.fares import build_fares_for_area

    matched_area = {
        "id": "area_test_1",
        "name": "Test Area",
        "surge_enabled": True,
        "surge_active": True,
        "surge_multiplier": 5.0,
        "vehicle_pricing": [
            {
                "vehicle_type": "sedan",
                "base_fare": 3.50,
                "per_km": 1.50,
                "per_min": 0.25,
                "min_fare": 8.00,
                "booking_fee": 2.00,
            }
        ],
    }

    vehicle_types = [{"id": "vt_sedan", "name": "sedan", "is_active": True}]

    with patch("backend.routes.fares.db_supabase.get_rows", new_callable=AsyncMock) as mock_get_rows:
        mock_get_rows.return_value = []
        fares = await build_fares_for_area(matched_area, vehicle_types)

    assert fares, "Expected at least one fare entry"
    for fare in fares:
        assert fare["surge_multiplier"] <= 2.5, (
            f"surge_multiplier {fare['surge_multiplier']} exceeds 2.5× cap (vehicle_type={fare.get('vehicle_type')})"
        )


@pytest.mark.unit
@pytest.mark.anyio
async def test_build_fares_for_area_returns_only_configured_vehicle_types():
    """Ride options should be scoped to vehicle types assigned to the area."""
    from backend.routes.fares import build_fares_for_area

    matched_area = {
        "id": "area_partial_pricing",
        "name": "Partial Pricing Area",
        "surge_enabled": False,
        "surge_active": False,
        "surge_multiplier": 1.0,
        "vehicle_pricing": [
            {
                "vehicle_type": "Sedan",
                "base_fare": 4.25,
                "per_km": 1.75,
                "per_min": 0.35,
                "min_fare": 9.00,
                "booking_fee": 2.50,
            },
            {
                "vehicle_type": "XL",
                "base_fare": 6.00,
                "per_km": 2.25,
                "per_min": 0.45,
                "min_fare": 12.00,
                "booking_fee": 3.00,
            },
        ],
    }
    vehicle_types = [
        {"id": "vt_sedan", "name": "Sedan", "is_active": True},
        {"id": "vt_xl", "name": "XL", "is_active": True},
        {"id": "vt_lux", "name": "Luxury", "is_active": True},
    ]

    stale_legacy_fares = [
        {
            "vehicle_type_id": "vt_lux",
            "base_fare": 20.00,
            "per_km_rate": 4.00,
            "per_minute_rate": 0.80,
            "minimum_fare": 30.00,
            "booking_fee": 5.00,
        }
    ]

    with patch("backend.routes.fares.db_supabase.get_rows", new_callable=AsyncMock) as mock_get_rows:
        mock_get_rows.return_value = stale_legacy_fares
        fares = await build_fares_for_area(matched_area, vehicle_types)

    mock_get_rows.assert_not_awaited()
    assert [fare["vehicle_type"]["id"] for fare in fares] == ["vt_sedan", "vt_xl"]
    fares_by_id = {fare["vehicle_type"]["id"]: fare for fare in fares}
    assert fares_by_id["vt_sedan"]["base_fare"] == "4.25"
    assert fares_by_id["vt_xl"]["base_fare"] == "6.00"


@pytest.mark.unit
@pytest.mark.anyio
async def test_build_fares_for_area_uses_legacy_fare_configs_when_no_vehicle_pricing():
    """Legacy fare_configs remain supported only when JSONB pricing is absent."""
    from backend.routes.fares import build_fares_for_area

    matched_area = {
        "id": "area_legacy_pricing",
        "name": "Legacy Pricing Area",
        "surge_enabled": False,
        "surge_active": False,
        "surge_multiplier": 1.0,
        "vehicle_pricing": [],
    }
    vehicle_types = [
        {"id": "vt_sedan", "name": "Sedan", "is_active": True},
        {"id": "vt_xl", "name": "XL", "is_active": True},
    ]
    legacy_fares = [
        {
            "vehicle_type_id": "vt_xl",
            "base_fare": 6.00,
            "per_km_rate": 2.25,
            "per_minute_rate": 0.45,
            "minimum_fare": 12.00,
            "booking_fee": 3.00,
        }
    ]

    with patch("backend.routes.fares.db_supabase.get_rows", new_callable=AsyncMock) as mock_get_rows:
        mock_get_rows.return_value = legacy_fares
        fares = await build_fares_for_area(matched_area, vehicle_types)

    mock_get_rows.assert_awaited_once()
    assert [fare["vehicle_type"]["id"] for fare in fares] == ["vt_xl"]
    assert fares[0]["base_fare"] == "6.00"
