"""Booking-flow tools: find_place, get_fare_quote, propose_ride_booking.

Pins the trust boundary: the proposal tool performs NO writes and returns a
_client_action payload (rendered as a native card; Confirm goes through the
normal POST /rides path). Also pins: out-of-area refusals, the approximate-
quote math (Decimal, surge applied, minimum fare respected), maps-budget
gating, and that booking tools are hidden from the MCP surface.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.ai import tools_booking
from backend.ai.tools import TOOL_REGISTRY, ensure_registry_loaded, execute_tool

RIDER = {"id": "rider-1"}

AREA = {"id": "area-1", "name": "Saskatoon"}

FARE_ROW = {
    "vehicle_type": {"id": "vt-1", "name": "Spinr X", "capacity": 4},
    "base_fare": "3.00",
    "per_km_rate": "1.50",
    "per_minute_rate": "0.20",
    "minimum_fare": "7.00",
    "booking_fee": "2.00",
    "surge_multiplier": 1.5,
}

GEOCODE_OK = {
    "status": "OK",
    "results": [
        {
            "formatted_address": "Saskatoon Airport (YXE), SK, Canada",
            "geometry": {"location": {"lat": 52.1708, "lng": -106.6997}},
        }
    ],
}

PLACES_OK = {
    "status": "OK",
    "results": [
        {
            "name": "Walmart Supercentre",
            "formatted_address": "4500 Gordon Rd, Regina, SK, Canada",
            "geometry": {"location": {"lat": 50.4079, "lng": -104.6501}},
        },
        {
            "name": "Walmart East",
            "formatted_address": "2150 Prince of Wales Dr, Regina, SK, Canada",
            "geometry": {"location": {"lat": 50.4497, "lng": -104.5345}},
        },
        {
            "name": "Walmart Rochdale",
            "formatted_address": "3939 Rochdale Blvd, Regina, SK, Canada",
            "geometry": {"location": {"lat": 50.4966, "lng": -104.6401}},
        },
    ],
}


def _patch_area(area=AREA):
    return patch.object(tools_booking, "_resolve_area", AsyncMock(return_value=area))


def _patch_http(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(tools_booking.httpx, "AsyncClient", MagicMock(return_value=ctx))


def _patch_settings(key="gmaps-key"):
    return patch.object(tools_booking, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": key}))


def _patch_budget(within=True):
    return patch.object(tools_booking, "check_budget", AsyncMock(return_value=(within, 1.0, 10.0)))


class TestFindPlace:
    @pytest.mark.anyio
    async def test_geocodes_and_flags_service_area(self):
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(GEOCODE_OK),
            _patch_area(),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "saskatoon airport"}, user=RIDER)
        assert ok
        cand = result["candidates"][0]
        assert cand["in_service_area"] is True
        assert cand["lat"] == 52.1708

    @pytest.mark.anyio
    async def test_vague_place_returns_clickable_nearby_suggestions(self):
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(PLACES_OK),
            _patch_area(),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool(
                "find_place",
                {"query": "walmart", "near_lat": 50.41, "near_lng": -104.65, "location_role": "dropoff"},
                user=RIDER,
            )
        assert ok
        assert len(result["candidates"]) == 3
        assert result["_client_action"]["type"] == "location_suggestions"
        assert result["_client_action"]["location_role"] == "dropoff"
        assert result["_client_action"]["candidates"][0]["name"] == "Walmart Supercentre"

    @pytest.mark.anyio
    async def test_budget_exhausted_degrades_gracefully(self):
        with _patch_settings(), _patch_budget(within=False):
            result, ok = await execute_tool("find_place", {"query": "airport"}, user=RIDER)
        assert ok and "error" in result

    @pytest.mark.anyio
    async def test_missing_key_degrades_gracefully(self):
        with _patch_settings(key=""):
            result, ok = await execute_tool("find_place", {"query": "airport"}, user=RIDER)
        assert ok and "error" in result


class TestFareQuote:
    ARGS = {
        "pickup_lat": 52.1318,
        "pickup_lng": -106.6608,
        "dropoff_lat": 52.1708,
        "dropoff_lng": -106.6997,
    }

    @pytest.mark.anyio
    async def test_quote_math_decimal_surge_and_booking_fee(self):
        with (
            _patch_area(),
            patch("backend.routes.fares.get_fares_for_location", AsyncMock(return_value=[FARE_ROW])),
            patch.object(tools_booking, "calculate_distance", MagicMock(return_value=10.0)),
        ):
            result, ok = await execute_tool("get_fare_quote", self.ARGS, user=RIDER)
        assert ok
        q = result["quotes"][0]
        # base 3.00 + 1.50*10km + 0.20*25min = 23.00 → ×1.5 surge = 34.50 + 2.00 fee
        assert result["duration_minutes"] == 25
        assert q["approx_fare"] == "36.50"
        assert q["surge_multiplier"] == 1.5
        assert "Approximate" in result["note"]

    @pytest.mark.anyio
    async def test_minimum_fare_floor(self):
        with (
            _patch_area(),
            patch("backend.routes.fares.get_fares_for_location", AsyncMock(return_value=[FARE_ROW])),
            patch.object(tools_booking, "calculate_distance", MagicMock(return_value=0.5)),
        ):
            result, _ = await execute_tool("get_fare_quote", self.ARGS, user=RIDER)
        # ride fare would be 3.00+0.75+1.20=4.95 → floored to 7.00 ×1.5 + 2.00
        assert result["quotes"][0]["approx_fare"] == "12.50"

    @pytest.mark.anyio
    async def test_out_of_area_pickup_refused(self):
        with _patch_area(area=None):
            result, ok = await execute_tool("get_fare_quote", self.ARGS, user=RIDER)
        assert ok and "outside" in result["error"]


class TestProposal:
    ARGS = {
        "pickup_lat": 52.1318,
        "pickup_lng": -106.6608,
        "pickup_address": "123 Main St",
        "dropoff_lat": 52.1708,
        "dropoff_lng": -106.6997,
        "dropoff_address": "Saskatoon Airport",
        "vehicle_type_id": "vt-1",
    }

    @pytest.mark.anyio
    async def test_proposal_emits_client_action_and_no_writes(self):
        insert = AsyncMock()
        args = dict(
            self.ARGS,
            promo_code="SAVE75",
            scheduled_time="2026-06-12T20:00:00-06:00",
            payment_method="wallet",
        )
        with (
            _patch_area(),
            patch("backend.db_supabase.insert_one", insert, create=True),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        action = result["_client_action"]
        assert action["type"] == "booking_proposal"
        assert action["proposal"]["pickup_address"] == "123 Main St"
        assert action["proposal"]["vehicle_type_id"] == "vt-1"
        assert action["proposal"]["promo_code"] == "SAVE75"
        assert action["proposal"]["scheduled_time"] == "2026-06-12T20:00:00-06:00"
        assert action["proposal"]["payment_method"] == "wallet"
        assert "do not claim the ride is booked" in result["message"]
        insert.assert_not_awaited()

    @pytest.mark.anyio
    async def test_proposal_reresolves_pickup_address_when_coords_are_stale(self):
        stale_then_fixed = AsyncMock(side_effect=[None, AREA, AREA])
        with (
            patch.object(tools_booking, "_resolve_area", stale_then_fixed),
            _patch_settings(),
            _patch_budget(),
            _patch_http(GEOCODE_OK),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("propose_ride_booking", self.ARGS, user=RIDER)
        assert ok
        proposal = result["_client_action"]["proposal"]
        assert proposal["pickup_lat"] == 52.1708
        assert proposal["pickup_lng"] == -106.6997
        assert proposal["pickup_address"] == "Saskatoon Airport (YXE), SK, Canada"

    @pytest.mark.anyio
    async def test_out_of_area_refused(self):
        with _patch_area(area=None):
            result, ok = await execute_tool("propose_ride_booking", self.ARGS, user=RIDER)
        assert ok and "error" in result

    @pytest.mark.anyio
    async def test_coordinate_bounds_enforced(self):
        bad = dict(self.ARGS, pickup_lat=123.0)
        result, ok = await execute_tool("propose_ride_booking", bad, user=RIDER)
        assert ok is False


def test_booking_tools_hidden_from_mcp():
    ensure_registry_loaded()
    for name in ("find_place", "get_fare_quote", "propose_ride_booking"):
        assert TOOL_REGISTRY[name].mcp_exposed is False
        assert "rider" in TOOL_REGISTRY[name].audiences
        assert "driver" not in TOOL_REGISTRY[name].audiences
