"""Ride read tools: user scoping, field whitelisting, registry wiring.

The load-bearing guarantees:
- a pasted foreign ride_id reads as "ride not found" (ownership re-check)
- results never contain GPS coordinates, OTPs or other non-whitelisted fields
- the tools are registered for the rider audience and reachable through
  execute_tool (the same path the chat loop and /mcp use)
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.ai import tools_rides
from backend.ai.tools import execute_tool, tool_defs_for

RIDER = {"id": "rider-1", "is_driver": False}

RIDE_ROW = {
    "id": "ride-1",
    "rider_id": "rider-1",
    "driver_id": "driver-9",
    "status": "in_progress",
    "pickup_address": "123 Main St, Saskatoon",
    "pickup_lat": 52.1318,
    "pickup_lng": -106.6608,
    "dropoff_address": "Saskatoon Airport",
    "dropoff_lat": 52.1708,
    "dropoff_lng": -106.6997,
    "pickup_otp": "4321",
    "distance_km": 9.2,
    "duration_minutes": 14,
    "surge_multiplier": 1.0,
    "total_fare": "18.50",
    "grand_total": "21.45",
    "payment_status": "pending",
    "payment_method": "card",
    "fare_breakdown_snapshot": {
        "lines": [
            {"label": "Base fare", "amount": "3.00", "type": "base"},
            {"label": "Distance", "amount": "11.50", "type": "distance"},
        ],
        "grand_total": "21.45",
    },
    "driver_earnings": "18.50",
}

DRIVER_ROW = {
    "id": "driver-9",
    "user_id": "duser-9",
    "rating": 4.9,
    "total_rides": 812,
    "vehicle_make": "Toyota",
    "vehicle_model": "Camry",
    "vehicle_color": "White",
    "license_plate": "ABC 123",
    "lat": 52.13,
    "lng": -106.66,
    "license_number": "SECRET-DL",
}

DRIVER_USER_ROW = {"id": "duser-9", "first_name": "Alex", "last_name": "K"}


def _db(**overrides):
    """Patch the db_supabase module the handlers call."""
    mocks = {
        "get_rows": AsyncMock(return_value=[RIDE_ROW]),
        "get_ride": AsyncMock(return_value=RIDE_ROW),
        "get_driver_by_id": AsyncMock(return_value=DRIVER_ROW),
        "get_user_by_id": AsyncMock(return_value=DRIVER_USER_ROW),
    }
    mocks.update(overrides)
    return patch.multiple(tools_rides.db_supabase, **mocks)


class TestActiveRide:
    @pytest.mark.anyio
    async def test_active_ride_with_driver(self):
        with _db():
            result, ok = await execute_tool("get_active_ride", {}, user=RIDER)
        assert ok and result["active"] is True
        ride = result["ride"]
        assert ride["status"] == "in_progress"
        # First-name-only (2026-08-18 fleet audit): a driver's full legal
        # name must never reach the AI provider — see
        # tools_rides.py::_driver_public.
        assert ride["driver"]["name"] == "Alex"
        assert ride["driver"]["license_plate"] == "ABC 123"

    @pytest.mark.anyio
    async def test_no_active_ride(self):
        with _db(get_rows=AsyncMock(return_value=[])):
            result, ok = await execute_tool("get_active_ride", {}, user=RIDER)
        assert ok and result["active"] is False

    @pytest.mark.anyio
    async def test_no_gps_or_otp_in_result(self):
        with _db():
            result, _ = await execute_tool("get_active_ride", {}, user=RIDER)
        flat = str(result)
        assert "pickup_lat" not in flat
        assert "52.13" not in flat  # neither ride nor driver coordinates
        assert "4321" not in flat  # pickup OTP
        assert "SECRET-DL" not in flat  # driver licence number
        assert "driver_earnings" not in flat

    @pytest.mark.anyio
    async def test_queries_scoped_to_caller(self):
        get_rows = AsyncMock(return_value=[])
        with _db(get_rows=get_rows):
            await execute_tool("get_active_ride", {}, user=RIDER)
        filters = get_rows.await_args.args[1]
        assert filters["rider_id"] == "rider-1"


class TestRideHistory:
    @pytest.mark.anyio
    async def test_history_whitelists_fields(self):
        with _db():
            result, ok = await execute_tool("get_ride_history", {"limit": 5}, user=RIDER)
        assert ok and result["count"] == 1
        assert "pickup_lat" not in result["rides"][0]
        assert result["rides"][0]["pickup_address"] == "123 Main St, Saskatoon"

    @pytest.mark.anyio
    async def test_limit_schema_enforced(self):
        result, ok = await execute_tool("get_ride_history", {"limit": 50}, user=RIDER)
        assert ok is False
        assert "<= 10" in result["error"]


class TestOwnership:
    @pytest.mark.anyio
    async def test_foreign_ride_id_reads_as_not_found(self):
        foreign = dict(RIDE_ROW, rider_id="someone-else")
        with _db(get_ride=AsyncMock(return_value=foreign)):
            details, _ = await execute_tool("get_ride_details", {"ride_id": "ride-1"}, user=RIDER)
            receipt, _ = await execute_tool("get_ride_receipt", {"ride_id": "ride-1"}, user=RIDER)
        assert details == {"error": "ride not found"}
        assert receipt == {"error": "ride not found"}

    @pytest.mark.anyio
    async def test_missing_ride_id_reads_as_not_found(self):
        with _db(get_ride=AsyncMock(return_value=None)):
            details, _ = await execute_tool("get_ride_details", {"ride_id": "ghost"}, user=RIDER)
        assert details == {"error": "ride not found"}


class TestReceipt:
    @pytest.mark.anyio
    async def test_receipt_uses_frozen_snapshot(self):
        with _db():
            result, ok = await execute_tool("get_ride_receipt", {"ride_id": "ride-1"}, user=RIDER)
        assert ok
        receipt = result["receipt"]
        assert receipt["lines"][0]["label"] == "Base fare"
        assert receipt["lines_total"] == "21.45"

    @pytest.mark.anyio
    async def test_receipt_falls_back_to_components(self):
        no_snapshot = dict(RIDE_ROW, fare_breakdown_snapshot=None, base_fare="3.00")
        with _db(get_ride=AsyncMock(return_value=no_snapshot)):
            result, ok = await execute_tool("get_ride_receipt", {"ride_id": "ride-1"}, user=RIDER)
        assert ok
        assert result["receipt"]["components"]["base_fare"] == "3.00"


class TestRegistryWiring:
    def test_ride_tools_exposed_to_riders(self):
        names = {d["name"] for d in tool_defs_for("rider")}
        assert {"get_active_ride", "get_ride_history", "get_ride_details", "get_ride_receipt"} <= names
