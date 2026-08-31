"""Regression test for backend/features.py :: compute_fare_estimate's
surge_override clamp (#4638 finding 2).

surge_override unconditionally overwrote the already-clamped area surge
with no cap of its own. Both current callers hardcode Decimal("1") today
(safe), but a future computed/admin-supplied override would have silently
exceeded SURGE_CAP with nothing catching it.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


async def test_surge_override_above_cap_is_clamped():
    from features import SURGE_CAP, compute_fare_estimate

    fare_config = {
        "vehicle_type_id": "vt-1",
        "base_fare": "3.00",
        "per_km_rate": "1.50",
        "per_minute_rate": "0.25",
        "booking_fee": "2.00",
        "minimum_fare": "5.00",
    }

    with (
        patch(
            "features.db_supabase.get_rows",
            AsyncMock(side_effect=[[fare_config], []]),  # fare_configs, service_areas
        ),
        patch(
            "features.calculate_all_fees",
            AsyncMock(return_value={"fees": [], "fees_total": 0, "tax_amount": 0, "tax_breakdown": {}}),
        ),
    ):
        result = await compute_fare_estimate(
            pickup_lat=52.1,
            pickup_lng=-106.6,
            dropoff_lat=52.2,
            dropoff_lng=-106.7,
            distance_km=5.0,
            duration_minutes=15,
            vehicle_type_id="vt-1",
            surge_override=Decimal("10.0"),  # a hypothetical future computed/admin value, well above the cap
        )

    assert result["surge_multiplier"] == float(SURGE_CAP)


async def test_surge_override_below_cap_is_unaffected():
    from features import compute_fare_estimate

    fare_config = {
        "vehicle_type_id": "vt-1",
        "base_fare": "3.00",
        "per_km_rate": "1.50",
        "per_minute_rate": "0.25",
        "booking_fee": "2.00",
        "minimum_fare": "5.00",
    }

    with (
        patch("features.db_supabase.get_rows", AsyncMock(side_effect=[[fare_config], []])),
        patch(
            "features.calculate_all_fees",
            AsyncMock(return_value={"fees": [], "fees_total": 0, "tax_amount": 0, "tax_breakdown": {}}),
        ),
    ):
        result = await compute_fare_estimate(
            pickup_lat=52.1,
            pickup_lng=-106.6,
            dropoff_lat=52.2,
            dropoff_lng=-106.7,
            distance_km=5.0,
            duration_minutes=15,
            vehicle_type_id="vt-1",
            surge_override=Decimal("1.0"),  # both real callers today
        )

    assert result["surge_multiplier"] == 1.0
