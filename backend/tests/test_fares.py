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

    # Stale legacy fare_configs rows that would re-expose Luxury if the
    # fallback were (incorrectly) consulted despite vehicle_pricing existing.
    stale_legacy_fares = [{"vehicle_type_id": "vt_lux", "base_fare": 5.00, "is_active": True}]

    with patch("backend.routes.fares.db_supabase.get_rows", new_callable=AsyncMock) as mock_get_rows:
        mock_get_rows.return_value = stale_legacy_fares
        fares = await build_fares_for_area(matched_area, vehicle_types)

    mock_get_rows.assert_not_awaited()
    assert [fare["vehicle_type"]["id"] for fare in fares] == ["vt_sedan", "vt_xl"]
    fares_by_id = {fare["vehicle_type"]["id"]: fare for fare in fares}
    assert fares_by_id["vt_sedan"]["base_fare"] == "4.25"
    assert fares_by_id["vt_xl"]["base_fare"] == "6.00"


def _vehicle_types_db(area_row, fare_config_rows):
    """get_rows side-effect for the /vehicle-types endpoint tests."""
    all_types = [
        {"id": "vt_economy", "name": "Economy", "is_active": True},
        {"id": "vt_xl", "name": "XL", "is_active": True},
        {"id": "vt_van", "name": "Van", "is_active": True},
        {"id": "vt_premium", "name": "Premium", "is_active": True},
    ]

    async def _get_rows(table, filters=None, **kw):
        if table == "vehicle_types":
            return all_types
        if table == "service_areas":
            return [area_row] if area_row else []
        if table == "fare_configs":
            return fare_config_rows
        raise AssertionError(f"unexpected table {table}")

    return _get_rows


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_vehicle_types_honours_vehicle_pricing_jsonb():
    """Regression: areas configured via the admin Vehicle Pricing editor write
    service_areas.vehicle_pricing JSONB, not fare_configs rows. The area filter
    must honour that (it previously returned [] for every such area, leaving
    the driver app's vehicle type picker empty)."""
    from backend.routes.fares import get_vehicle_types

    area = {
        "id": "area_regina",
        "vehicle_pricing": [
            {"vehicle_type": "Economy", "base_fare": 2.0},
            {"vehicle_type": "XL", "base_fare": 2.0},
            {"vehicle_type": "Van", "base_fare": 2.0},
        ],
    }
    with patch(
        "backend.routes.fares.db_supabase.get_rows",
        side_effect=_vehicle_types_db(area, fare_config_rows=[]),
    ):
        types = await get_vehicle_types(service_area_id="area_regina")

    assert [vt["name"] for vt in types] == ["Economy", "XL", "Van"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_get_vehicle_types_falls_back_to_fare_configs_when_no_jsonb():
    """Areas without vehicle_pricing JSONB still filter via legacy fare_configs."""
    from backend.routes.fares import get_vehicle_types

    area = {"id": "area_legacy", "vehicle_pricing": []}
    legacy = [{"vehicle_type_id": "vt_premium"}]
    with patch(
        "backend.routes.fares.db_supabase.get_rows",
        side_effect=_vehicle_types_db(area, fare_config_rows=legacy),
    ):
        types = await get_vehicle_types(service_area_id="area_legacy")

    assert [vt["name"] for vt in types] == ["Premium"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_resolve_area_scope_walks_parent_chain():
    """area_id plus its ancestors so parent-tagged FAQs cascade to sub-regions."""
    from backend.routes.fares import resolve_area_scope

    rows = [
        {"id": "regina", "parent_service_area_id": "sk"},
        {"id": "sk", "parent_service_area_id": None},
    ]
    with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=rows)):
        scope = await resolve_area_scope("regina")
    assert scope == {"regina", "sk"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_resolve_area_scope_degrades_loudly_on_lookup_failure(caplog):
    """Codex (PR #2049): a failed lookup must be logged loudly, not silently
    swallowed — it degrades to the exact area only, never a masked partial."""
    import logging

    from backend.routes.fares import resolve_area_scope

    with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        with caplog.at_level(logging.ERROR):
            scope = await resolve_area_scope("regina")
    assert scope == {"regina"}
    assert any("resolve_area_scope" in r.message for r in caplog.records)
