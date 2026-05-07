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
