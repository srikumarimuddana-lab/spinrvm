"""#2 (D1): PostGIS spatial supply count — GeoJSON correctness + safe fallback."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_POLY = [
    {"lat": 52.00, "lng": -106.00},
    {"lat": 52.10, "lng": -106.00},
    {"lat": 52.10, "lng": -106.10},
]


def test_spatial_count_builds_lng_lat_closed_ring_and_applies_presence():
    from backend.utils import surge_engine as se

    rpc = AsyncMock(
        return_value=[
            {"driver_id": "d1", "driver_user_id": "u1"},
            {"driver_id": "d2", "driver_user_id": "u2"},
        ]
    )
    with (
        patch("backend.utils.surge_engine.db.rpc", rpc),
        patch("backend.utils.surge_engine.present_driver_ids", AsyncMock(return_value={"d1"})),
    ):
        count = asyncio.run(se._count_supply_spatial(_POLY))

    assert count == 1  # only the present driver counts

    # GeoJSON must be [lng, lat] and a closed ring.
    args, kwargs = rpc.await_args
    assert args[0] == "drivers_available_in_polygon"
    ring = json.loads(args[1]["p_polygon_geojson"])["coordinates"][0]
    assert ring[0] == [-106.00, 52.00]  # [lng, lat], not [lat, lng]
    assert ring[0] == ring[-1]  # closed


def test_count_supply_falls_back_to_python_scan_on_spatial_error():
    from backend.utils import surge_engine as se

    with (
        patch("backend.utils.surge_engine._SURGE_SPATIAL_COUNT", True),
        patch("backend.utils.surge_engine._count_supply_spatial", AsyncMock(side_effect=RuntimeError("no postgis"))),
        patch("backend.utils.surge_engine.db.get_rows", AsyncMock(return_value=[])) as get_rows,
    ):
        count = asyncio.run(se._count_supply_in_area({"polygon": _POLY}))

    assert count == 0
    get_rows.assert_awaited()  # fell back to the Python scan; surge never broke
