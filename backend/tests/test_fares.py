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
async def test_build_fares_for_area_includes_unpriced_vehicle_types_with_defaults():
    """Regression: partial area pricing must not hide vehicle types from booking.

    The rider app renders vehicle cards from /rides/estimate. If a service area
    has pricing for only one vehicle type, unpriced active types still need a
    fare row so the app can show them greyed out when no drivers are available.
    """
    from backend.routes.fares import build_fares_for_area
    from backend.services.fare_service import DEFAULT_FARE

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
            }
        ],
    }
    vehicle_types = [
        {"id": "vt_sedan", "name": "Sedan", "is_active": True},
        {"id": "vt_xl", "name": "XL", "is_active": True},
    ]

    with patch("backend.routes.fares.db_supabase.get_rows", new_callable=AsyncMock) as mock_get_rows:
        mock_get_rows.return_value = []
        fares = await build_fares_for_area(matched_area, vehicle_types)

    assert [fare["vehicle_type"]["id"] for fare in fares] == ["vt_sedan", "vt_xl"]
    fares_by_id = {fare["vehicle_type"]["id"]: fare for fare in fares}
    assert fares_by_id["vt_sedan"]["base_fare"] == "4.25"
    assert fares_by_id["vt_xl"]["base_fare"] == f'{DEFAULT_FARE["base_fare"]:.2f}'
    assert fares_by_id["vt_xl"]["per_km_rate"] == f'{DEFAULT_FARE["per_km_rate"]:.2f}'
