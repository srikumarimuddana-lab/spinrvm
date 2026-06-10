"""Wallet / promo / pricing / saved-places tools.

Pins: read-only wallet access (a missing wallet must NOT be created),
private-promo invisibility, surge gating (enabled AND active), and the
field whitelists.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.ai import tools_account
from backend.ai.tools import execute_tool, tool_defs_for

RIDER = {"id": "rider-1"}

WALLET_ROW = {"id": "w-1", "user_id": "rider-1", "balance": "42.50", "currency": "CAD", "is_active": True}

AREA_SURGE_ON = {
    "id": "area-1",
    "name": "Saskatoon",
    "city": "Saskatoon",
    "province": "SK",
    "surge_enabled": True,
    "surge_active": True,
    "surge_multiplier": 1.75,
    "polygon": [{"lat": 1, "lng": 2}] * 50,
}
AREA_SURGE_STALE = dict(
    AREA_SURGE_ON, id="area-2", name="Regina", city="Regina", surge_enabled=False, surge_multiplier=2.5
)


def _db(**overrides):
    mocks = {
        "find_one": AsyncMock(return_value=WALLET_ROW),
        "get_rows": AsyncMock(return_value=[]),
    }
    mocks.update(overrides)
    return patch.multiple(tools_account.db_supabase, **mocks)


class TestWallet:
    @pytest.mark.anyio
    async def test_balance(self):
        with _db():
            result, ok = await execute_tool("get_wallet_balance", {}, user=RIDER)
        assert ok and result["balance"] == "42.50"

    @pytest.mark.anyio
    async def test_missing_wallet_reads_as_zero_and_is_not_created(self):
        find_one = AsyncMock(return_value=None)
        insert = AsyncMock()
        with _db(find_one=find_one), patch.object(tools_account.db_supabase, "insert_one", insert, create=True):
            result, ok = await execute_tool("get_wallet_balance", {}, user=RIDER)
        assert ok and result["balance"] == "0.00"
        insert.assert_not_awaited()

    @pytest.mark.anyio
    async def test_transactions_whitelisted(self):
        txn = {
            "id": "t-1",
            "type": "topup",
            "amount": "20.00",
            "balance_after": "42.50",
            "description": "Wallet top-up",
            "reference_id": "pi_secret",
            "metadata": {"stripe_payment_intent": "pi_secret"},
            "created_at": "2026-06-01T00:00:00Z",
        }
        with _db(get_rows=AsyncMock(return_value=[txn])):
            result, ok = await execute_tool("get_wallet_transactions", {"limit": 5}, user=RIDER)
        assert ok
        out = result["transactions"][0]
        assert out["amount"] == "20.00"
        assert "pi_secret" not in str(result)


class TestPromos:
    @pytest.mark.anyio
    async def test_private_promo_for_other_user_hidden(self):
        promos = [
            {"code": "WELCOME10", "is_active": True, "discount_type": "flat", "discount_value": "10"},
            {"code": "VIP", "is_active": True, "assigned_user_ids": ["someone-else"]},
            {"code": "AREA", "is_active": True, "service_area_id": "area-1"},
            {"code": "EXPIRED", "is_active": True, "expiry_date": "2020-01-01T00:00:00Z"},
        ]
        with _db(get_rows=AsyncMock(return_value=promos)):
            result, ok = await execute_tool("get_available_promos", {}, user=RIDER)
        codes = {p["code"] for p in result["promos"]}
        assert ok and codes == {"WELCOME10"}

    @pytest.mark.anyio
    async def test_private_promo_for_this_user_visible(self):
        promos = [{"code": "MINE", "is_active": True, "assigned_user_ids": ["rider-1"]}]
        with _db(get_rows=AsyncMock(return_value=promos)):
            result, _ = await execute_tool("get_available_promos", {}, user=RIDER)
        assert {p["code"] for p in result["promos"]} == {"MINE"}


class TestServiceInfo:
    @pytest.mark.anyio
    async def test_surge_gated_on_both_flags(self):
        async def rows(table, *a, **kw):
            if table == "service_areas":
                return [AREA_SURGE_ON, AREA_SURGE_STALE]
            return [{"id": "vt-1", "name": "Spinr X", "description": "Everyday", "capacity": 4}]

        with _db(get_rows=AsyncMock(side_effect=rows)):
            result, ok = await execute_tool("get_service_info", {}, user=RIDER)
        assert ok
        by_city = {a["city"]: a for a in result["service_areas"]}
        assert by_city["Saskatoon"]["current_surge_multiplier"] == 1.75
        # surge_enabled=False → stale 2.5 multiplier must read as 1.0
        assert by_city["Regina"]["current_surge_multiplier"] == 1.0
        # polygons never reach the model
        assert "polygon" not in str(result)

    @pytest.mark.anyio
    async def test_city_filter(self):
        async def rows(table, *a, **kw):
            if table == "service_areas":
                return [AREA_SURGE_ON, AREA_SURGE_STALE]
            return []

        with _db(get_rows=AsyncMock(side_effect=rows)):
            result, _ = await execute_tool("get_service_info", {"city": "regina"}, user=RIDER)
        assert [a["city"] for a in result["service_areas"]] == ["Regina"]


class TestFareRates:
    @pytest.mark.anyio
    async def test_rate_card(self):
        async def rows(table, *a, **kw):
            if table == "service_areas":
                return [AREA_SURGE_ON]
            if table == "vehicle_types":
                return [{"id": "vt-1", "name": "Spinr X"}]
            if table == "fare_configs":
                return [
                    {
                        "vehicle_type_id": "vt-1",
                        "base_fare": "3.00",
                        "per_km_rate": "1.45",
                        "per_minute_rate": "0.25",
                        "minimum_fare": "7.00",
                        "booking_fee": "2.00",
                    }
                ]
            return []

        with _db(get_rows=AsyncMock(side_effect=rows)):
            result, ok = await execute_tool("explain_fare_rates", {"city": "Saskatoon"}, user=RIDER)
        assert ok
        rate = result["fare_rates"][0]["rates"][0]
        assert rate == {
            "vehicle_type": "Spinr X",
            "base_fare": "3.00",
            "per_km": "1.45",
            "per_minute": "0.25",
            "minimum_fare": "7.00",
            "booking_fee": "2.00",
        }

    @pytest.mark.anyio
    async def test_unknown_city(self):
        with _db(get_rows=AsyncMock(return_value=[])):
            result, ok = await execute_tool("explain_fare_rates", {"city": "Toronto"}, user=RIDER)
        assert ok and "error" in result


class TestSavedPlaces:
    @pytest.mark.anyio
    async def test_scoped_and_includes_coords_for_booking(self):
        get_rows = AsyncMock(
            return_value=[{"name": "Home", "address": "12 Elm St", "lat": 52.1, "lng": -106.6, "user_id": "rider-1"}]
        )
        with _db(get_rows=get_rows):
            result, ok = await execute_tool("get_saved_places", {}, user=RIDER)
        assert ok and result["places"][0]["name"] == "Home"
        assert result["places"][0]["lat"] == 52.1
        assert get_rows.await_args.args[1] == {"user_id": "rider-1"}


def test_account_tools_registered_for_riders():
    names = {d["name"] for d in tool_defs_for("rider")}
    assert {
        "get_wallet_balance",
        "get_wallet_transactions",
        "get_available_promos",
        "get_service_info",
        "explain_fare_rates",
        "get_saved_places",
    } <= names
