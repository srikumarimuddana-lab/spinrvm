"""Booking-flow tools: find_place, get_rider_location, get_fare_quote,
propose_ride_booking.

Pins the trust boundary: the proposal tool performs NO writes and returns a
_client_action payload (rendered as a native card; Confirm goes through the
normal POST /rides path). Also pins: out-of-area refusals, that the quote
runs through the real estimate engine (compute_ride_estimates) with the
best eligible promo auto-applied, rider-location biasing of place search,
maps-budget gating, and that booking tools are hidden from the MCP surface.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.ai import tools_booking
from backend.ai.tools import TOOL_REGISTRY, ensure_registry_loaded, execute_tool

RIDER = {"id": "rider-1"}

AREA = {"id": "area-1", "name": "Saskatoon"}

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


LAST_RIDE = {"pickup_lat": 50.4501, "pickup_lng": -104.6178, "pickup_address": "4325 Wakeling St, Regina"}


def _patch_last_ride(rows=None):
    return patch.object(tools_booking.db_supabase, "get_rows", AsyncMock(return_value=rows or []))


class TestFindPlace:
    @pytest.mark.anyio
    async def test_geocodes_and_flags_service_area(self):
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(GEOCODE_OK),
            _patch_area(),
            _patch_last_ride(),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "saskatoon airport"}, user=RIDER)
        assert ok
        cand = result["candidates"][0]
        assert cand["in_service_area"] is True
        assert cand["lat"] == 52.1708

    @pytest.mark.anyio
    async def test_biases_search_to_last_ride_pickup_when_no_near_args(self):
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(PLACES_OK),
            _patch_area(),
            _patch_last_ride([LAST_RIDE]),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "superstore"}, user=RIDER)
        assert ok
        assert result["search_biased_by"] == "last_ride"
        assert len(result["candidates"]) == 3

    @pytest.mark.anyio
    async def test_biases_search_to_device_location_first(self):
        rides_lookup = AsyncMock(return_value=[LAST_RIDE])
        rider = {**RIDER, "_client_location": {"lat": 50.45, "lng": -104.62}}
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(PLACES_OK),
            _patch_area(),
            patch.object(tools_booking.db_supabase, "get_rows", rides_lookup),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "superstore"}, user=rider)
        assert ok
        assert result["search_biased_by"] == "device"
        rides_lookup.assert_not_awaited()

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


def _patch_http_capture(payload):
    """Like _patch_http but also hands back the client mock so tests can
    assert on the request params."""
    resp = MagicMock()
    resp.json.return_value = payload
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(tools_booking.httpx, "AsyncClient", MagicMock(return_value=ctx)), client


class TestGeocodeBias:
    """The geocode branch must bias street-address lookups near the rider and
    return candidates nearest-first (incident: '4325 wakeling st' geocoded
    Canada-wide resolved ~12 km away and was silently quoted)."""

    NEAR = {"near_lat": 50.41, "near_lng": -104.65}

    @pytest.mark.anyio
    async def test_street_address_geocode_sends_bounds(self):
        http_patch, client = _patch_http_capture(GEOCODE_OK)
        with (
            _patch_settings(),
            _patch_budget(),
            http_patch,
            _patch_area(),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st", **self.NEAR}, user=RIDER)
        assert ok and result["candidates"]
        params = client.get.call_args.kwargs.get("params") or client.get.call_args.args[1]
        assert "bounds" in params
        south, north = params["bounds"].split("|")
        assert float(south.split(",")[0]) < 50.41 < float(north.split(",")[0])

    @pytest.mark.anyio
    async def test_unbiased_geocode_sends_no_bounds(self):
        http_patch, client = _patch_http_capture(GEOCODE_OK)
        with (
            _patch_settings(),
            _patch_budget(),
            http_patch,
            _patch_area(),
            _patch_last_ride([]),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st"}, user=RIDER)
        assert ok and result["candidates"]
        params = client.get.call_args.kwargs.get("params") or client.get.call_args.args[1]
        assert "bounds" not in params
        assert "distance_from_search_km" not in result["candidates"][0]

    @pytest.mark.anyio
    async def test_candidates_sorted_nearest_first_with_distance(self):
        far_first = {
            "status": "OK",
            "results": [
                {  # ~12 km away but ranked first by Google relevance
                    "formatted_address": "4325 Wakeling St (far), Regina, SK",
                    "geometry": {"location": {"lat": 50.5177, "lng": -104.6501}},
                },
                {  # ~1.5 km away
                    "formatted_address": "4325 Wakeling St, Regina, SK",
                    "geometry": {"location": {"lat": 50.4214, "lng": -104.6641}},
                },
            ],
        }
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(far_first),
            _patch_area(),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st", **self.NEAR}, user=RIDER)
        assert ok
        cands = result["candidates"]
        assert cands[0]["address"] == "4325 Wakeling St, Regina, SK"
        assert cands[0]["distance_from_search_km"] <= cands[1]["distance_from_search_km"]
        assert cands[0]["distance_from_search_km"] < 3

    @pytest.mark.anyio
    async def test_far_only_match_warns_the_model(self):
        far_only = {
            "status": "OK",
            "results": [
                {  # ~54 km from the bias point — outside the 25 km radius
                    "formatted_address": "4325 Wakeling St, Somewhere Else, SK",
                    "geometry": {"location": {"lat": 50.90, "lng": -104.65}},
                }
            ],
        }
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(far_only),
            _patch_area(),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st", **self.NEAR}, user=RIDER)
        assert ok
        assert "confirm the exact address" in result["note"]
        assert "km from the rider's search area" in result["note"]


class TestRiderLocation:
    @pytest.mark.anyio
    async def test_device_location_preferred(self):
        rider = {**RIDER, "_client_location": {"lat": 50.45, "lng": -104.62}}
        no_maps = AsyncMock(return_value=(None, {"error": "unavailable"}))
        with patch.object(tools_booking, "_places_available", no_maps):
            result, ok = await execute_tool("get_rider_location", {}, user=rider)
        assert ok
        assert result["source"] == "device"
        assert result["lat"] == 50.45 and result["lng"] == -104.62

    @pytest.mark.anyio
    async def test_falls_back_to_last_ride_pickup_with_address(self):
        with _patch_last_ride([LAST_RIDE]):
            result, ok = await execute_tool("get_rider_location", {}, user=RIDER)
        assert ok
        assert result["source"] == "last_ride"
        assert result["address"] == "4325 Wakeling St, Regina"
        assert "most recent ride" in result["note"]

    @pytest.mark.anyio
    async def test_no_known_location_is_a_clean_error(self):
        with _patch_last_ride([]):
            result, ok = await execute_tool("get_rider_location", {}, user=RIDER)
        assert ok and "error" in result

    @pytest.mark.anyio
    async def test_fresh_last_ride_carries_as_of_and_must_confirm_note(self):
        from datetime import datetime, timedelta, timezone

        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with _patch_last_ride([{**LAST_RIDE, "created_at": recent}]):
            result, ok = await execute_tool("get_rider_location", {}, user=RIDER)
        assert ok
        assert result["source"] == "last_ride"
        assert result["as_of"] == recent
        assert "MUST confirm" in result["note"]

    @pytest.mark.anyio
    async def test_stale_last_ride_is_not_a_location(self):
        # A pickup from a ride weeks ago is not "where the rider is" — the
        # assistant must ask for a pickup instead of silently pointing there.
        from datetime import datetime, timedelta, timezone

        stale = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        with _patch_last_ride([{**LAST_RIDE, "created_at": stale}]):
            result, ok = await execute_tool("get_rider_location", {}, user=RIDER)
        assert ok and "error" in result


ESTIMATES = {
    "estimates": [
        {
            "vehicle_type": {"id": "vt-1", "name": "Economy", "capacity": 4, "image_url": None},
            "base_fare": "3.00",
            "distance_fare": "9.60",
            "time_fare": "3.40",
            "grand_total": 18.48,
            "surge_multiplier": 1.0,
            "available": True,
            "eta_minutes": 4,
            "closest_driver_km": 1.8,
            "driver_count": 2,
            "distance_km": 6.4,
            "duration_minutes": 17,
            "fare_breakdown": [
                {"label": "Ride fare (6.4 km)", "amount": 16.0, "type": "ride"},
                {"label": "GST (5%)", "amount": 0.88, "type": "tax"},
            ],
        },
        {
            "vehicle_type": {"id": "vt-2", "name": "XL", "capacity": 6},
            "base_fare": "5.00",
            "distance_fare": "12.00",
            "time_fare": "4.00",
            "grand_total": 25.00,
            "surge_multiplier": 1.0,
            "available": False,
            "eta_minutes": None,
            "driver_count": 0,
            "distance_km": 6.4,
            "duration_minutes": 17,
        },
    ],
    "route_polyline": None,
}

PROMOS = [
    # flat $10 — best for the Economy ride portion (3.00+9.60+3.40 = 16.00)
    {"code": "SAVE10", "free_ride": False, "discount_type": "flat", "discount_value": 10.0, "min_ride_fare": 0},
    # bigger on paper but requires a $20 ride portion — must be skipped
    {"code": "BIG15", "free_ride": False, "discount_type": "flat", "discount_value": 15.0, "min_ride_fare": 20},
]


def _patch_estimates(payload=None, error: Exception | None = None):
    mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=payload)
    return patch("backend.routes.rides.estimates.compute_ride_estimates", mock)


def _patch_promos(promos=None, error: Exception | None = None):
    mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=promos or [])
    return patch("backend.routes.promotions.list_available_promos", mock)


class TestFareQuote:
    ARGS = {
        "pickup_lat": 52.1318,
        "pickup_lng": -106.6608,
        "dropoff_lat": 52.1708,
        "dropoff_lng": -106.6997,
    }

    @pytest.mark.anyio
    async def test_quote_uses_estimate_engine_and_applies_best_promo(self):
        args = dict(self.ARGS, pickup_address="123 Main St, Saskatoon", dropoff_address="Saskatoon Airport")
        # Maps unavailable → pickup reconciliation keeps the supplied coords;
        # the geocoding path has its own tests.
        with _patch_estimates(ESTIMATES), _patch_promos(PROMOS), _patch_settings(key=""):
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        # Only the available vehicle type is quoted; the other is named.
        assert len(result["quotes"]) == 1
        assert result["unavailable_vehicle_types"] == ["XL"]
        q = result["quotes"][0]
        assert q["vehicle_type"] == "Economy"
        assert q["eta_minutes"] == 4
        assert q["closest_driver_km"] == 1.8
        assert q["breakdown"][0]["label"].startswith("Ride fare")
        assert q["total"] == "18.48"
        # SAVE10 wins (BIG15 misses the $20 min ride portion of $16.00).
        assert q["promo_code"] == "SAVE10"
        assert q["promo_savings"] == "10.00"
        assert q["final_total"] == "8.48"
        assert result["recommended_vehicle_type_id"] == "vt-1"
        # Rich card for both surfaces, with the same quotes. Addresses ride
        # along so a tapped option can send a self-contained booking message
        # (history keeps only message text, never tool results).
        action = result["_client_action"]
        assert action["type"] == "fare_quote"
        assert action["quotes"] == result["quotes"]
        assert action["distance_km"] == 6.4
        assert action["pickup_address"] == "123 Main St, Saskatoon"
        assert action["dropoff_address"] == "Saskatoon Airport"
        # Priced coordinates travel with the card so a tapped option can pass
        # them back verbatim instead of forcing a re-geocode.
        assert action["pickup_lat"] == self.ARGS["pickup_lat"]
        assert action["pickup_lng"] == self.ARGS["pickup_lng"]
        assert action["dropoff_lat"] == self.ARGS["dropoff_lat"]
        assert action["dropoff_lng"] == self.ARGS["dropoff_lng"]

    @pytest.mark.anyio
    async def test_out_of_area_pickup_refused(self):
        exc = HTTPException(
            status_code=400,
            detail={"code": "OUTSIDE_SERVICE_AREA", "message": "Sorry, your pickup location is outside"},
        )
        with _patch_estimates(error=exc):
            result, ok = await execute_tool("get_fare_quote", self.ARGS, user=RIDER)
        assert ok and "outside" in result["error"]

    @pytest.mark.anyio
    async def test_no_drivers_returns_note_without_card(self):
        all_unavailable = {
            "estimates": [dict(ESTIMATES["estimates"][0], available=False, eta_minutes=None, driver_count=0)],
            "route_polyline": None,
        }
        with _patch_estimates(all_unavailable), _patch_promos([]):
            result, ok = await execute_tool("get_fare_quote", self.ARGS, user=RIDER)
        assert ok
        assert result["no_drivers"] is True
        assert result["quotes"] == []
        assert "_client_action" not in result

    @pytest.mark.anyio
    async def test_promo_failure_does_not_kill_quote(self):
        with _patch_estimates(ESTIMATES), _patch_promos(error=RuntimeError("promo db down")):
            result, ok = await execute_tool("get_fare_quote", self.ARGS, user=RIDER)
        assert ok
        q = result["quotes"][0]
        assert q["final_total"] == q["total"] == "18.48"
        assert "promo_code" not in q
        assert "promo lookup failed" in result["promo_note"]

    @pytest.mark.anyio
    async def test_quote_reconciles_stale_pickup_coord_against_address(self):
        # Same contract as propose_ride_booking: a stale coordinate riding
        # alongside a correct address is re-anchored BEFORE pricing, so the
        # quote card and the confirm card always price the same pickup.
        correct = {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "123 Main St, Saskatoon, SK, Canada",
                    "geometry": {"location": {"lat": 52.1170, "lng": -106.6345}},
                }
            ],
        }
        args = dict(
            self.ARGS,
            pickup_lat=52.2680,  # ~17 km from the real address, still "in area"
            pickup_lng=-106.6345,
            pickup_address="123 Main St, Saskatoon",
            dropoff_address="Saskatoon Airport",
        )
        estimates_mock = AsyncMock(return_value=ESTIMATES)
        with (
            patch("backend.routes.rides.estimates.compute_ride_estimates", estimates_mock),
            _patch_promos([]),
            _patch_area(),
            _patch_settings(),
            _patch_budget(),
            _patch_http(correct),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        req = estimates_mock.call_args.args[0]
        assert req.pickup_lat == 52.1170
        assert req.pickup_lng == -106.6345
        assert result["pickup_note"].startswith("the pickup pin was moved")
        assert "pickup pin was moved" in result["note"]
        assert result["_client_action"]["pickup_address"] == "123 Main St, Saskatoon, SK, Canada"

    @pytest.mark.anyio
    async def test_quote_without_address_skips_reconcile(self):
        maps = AsyncMock()
        with (
            _patch_estimates(ESTIMATES),
            _patch_promos([]),
            patch.object(tools_booking, "_places_available", maps),
        ):
            result, ok = await execute_tool("get_fare_quote", self.ARGS, user=RIDER)
        assert ok and result["quotes"]
        maps.assert_not_awaited()

    @pytest.mark.anyio
    async def test_free_ride_promo_drops_total_to_zero(self):
        free = [
            {"code": "FREERIDE", "free_ride": True, "discount_type": "flat", "discount_value": 0, "min_ride_fare": 0}
        ]
        with _patch_estimates(ESTIMATES), _patch_promos(free):
            result, ok = await execute_tool("get_fare_quote", self.ARGS, user=RIDER)
        assert ok
        q = result["quotes"][0]
        assert q["promo_code"] == "FREERIDE"
        assert q["promo_savings"] == "18.48"
        assert q["final_total"] == "0.00"


class TestSamePlaceGuard:
    """Pickup≈dropoff (< 250 m) must never be quoted or proposed silently —
    the tools return needs_confirmation until the rider explicitly says yes
    (incident: same Walmart quoted at 0.08 km with no warning)."""

    # ~80 m apart — the incident geometry.
    NEAR = {
        "pickup_lat": 50.40790,
        "pickup_lng": -104.65010,
        "dropoff_lat": 50.40862,
        "dropoff_lng": -104.65010,
    }
    PROPOSE_NEAR = {
        **NEAR,
        "pickup_address": "4500 Gordon Rd, Regina",
        "dropoff_address": "4500 Gordon Rd, Regina, SK S4S 6H7",
    }

    def _maps_unavailable(self):
        # _reconcile_pickup keeps the supplied coords when Maps is unavailable,
        # isolating these tests from the geocoding path.
        return patch.object(
            tools_booking, "_places_available", AsyncMock(return_value=(None, {"error": "unavailable"}))
        )

    @pytest.mark.anyio
    async def test_quote_at_80m_requires_confirmation(self):
        estimates = AsyncMock()
        with patch("backend.routes.rides.estimates.compute_ride_estimates", estimates):
            result, ok = await execute_tool("get_fare_quote", self.NEAR, user=RIDER)
        assert ok
        assert result["needs_confirmation"] == "same_location"
        assert 60 <= result["distance_meters"] <= 100
        assert "quotes" not in result
        assert "_client_action" not in result
        estimates.assert_not_awaited()

    @pytest.mark.anyio
    async def test_quote_proceeds_when_rider_confirmed(self):
        args = dict(self.NEAR, confirm_same_location=True)
        with _patch_estimates(ESTIMATES), _patch_promos([]):
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        assert "needs_confirmation" not in result
        assert len(result["quotes"]) == 1

    @pytest.mark.anyio
    async def test_quote_at_300m_is_untouched(self):
        args = dict(self.NEAR, dropoff_lat=50.41060)  # ~300 m
        with _patch_estimates(ESTIMATES), _patch_promos([]):
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        assert "needs_confirmation" not in result
        assert len(result["quotes"]) == 1

    @pytest.mark.anyio
    async def test_proposal_at_80m_requires_confirmation(self):
        with _patch_area(), self._maps_unavailable():
            result, ok = await execute_tool("propose_ride_booking", self.PROPOSE_NEAR, user=RIDER)
        assert ok
        assert result["needs_confirmation"] == "same_location"
        assert "_client_action" not in result

    @pytest.mark.anyio
    async def test_confirmed_proposal_carries_same_location_flag(self):
        args = dict(self.PROPOSE_NEAR, confirm_same_location=True)
        with _patch_area(), self._maps_unavailable():
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        proposal = result["_client_action"]["proposal"]
        assert proposal["same_location_confirmed"] is True

    @pytest.mark.anyio
    async def test_normal_proposal_has_no_same_location_flag(self):
        # confirm_same_location on a normal-distance trip must not stamp the
        # flag — the client guard should stay active for it.
        args = dict(self.PROPOSE_NEAR, dropoff_lat=50.44970, confirm_same_location=True)
        with _patch_area(), self._maps_unavailable():
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert "same_location_confirmed" not in result["_client_action"]["proposal"]


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
        # Re-geocoded pickup must NOT coincide with the dropoff — that would
        # (correctly) trip the same-place guard, which is not this scenario.
        downtown = {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "123 Main St, Saskatoon, SK, Canada",
                    "geometry": {"location": {"lat": 52.1170, "lng": -106.6345}},
                }
            ],
        }
        with (
            patch.object(tools_booking, "_resolve_area", stale_then_fixed),
            _patch_settings(),
            _patch_budget(),
            _patch_http(downtown),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("propose_ride_booking", self.ARGS, user=RIDER)
        assert ok
        proposal = result["_client_action"]["proposal"]
        assert proposal["pickup_lat"] == 52.1170
        assert proposal["pickup_lng"] == -106.6345
        assert proposal["pickup_address"] == "123 Main St, Saskatoon, SK, Canada"
        # A moved pin is never silent — the model must relay it.
        assert "pickup pin was moved" in result["message"]

    @pytest.mark.anyio
    async def test_proposal_corrects_pickup_coords_far_from_address_even_when_in_area(self):
        # The reported bug: the model passes a correct address but a stale /
        # hallucinated coordinate that still lands inside a service area, so
        # the old out-of-area-only guard never fired and the driver was sent
        # ~17 km away. Re-geocoding the address must win.
        correct = {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "123 Main St, Saskatoon, SK, Canada",
                    "geometry": {"location": {"lat": 52.1170, "lng": -106.6345}},
                }
            ],
        }
        args = dict(
            self.ARGS,
            pickup_lat=52.2680,  # ~17 km north of the real address, still "in area"
            pickup_lng=-106.6345,
            pickup_address="123 Main St, Saskatoon",
        )
        with (
            _patch_area(),  # every point resolves to a service area
            _patch_settings(),
            _patch_budget(),
            _patch_http(correct),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        proposal = result["_client_action"]["proposal"]
        assert proposal["pickup_lat"] == 52.1170
        assert proposal["pickup_lng"] == -106.6345
        assert proposal["pickup_address"] == "123 Main St, Saskatoon, SK, Canada"
        assert "pickup pin was moved" in result["message"]

    @pytest.mark.anyio
    async def test_proposal_keeps_pickup_coords_that_match_address(self):
        # A precise coordinate near its address must be left untouched — never
        # snapped to the geocoded street centroid.
        near = {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "123 Main St, Saskatoon, SK, Canada",
                    "geometry": {"location": {"lat": 52.1325, "lng": -106.6610}},
                }
            ],
        }
        args = dict(self.ARGS, pickup_lat=52.1318, pickup_lng=-106.6608, pickup_address="123 Main St")
        with (
            _patch_area(),
            _patch_settings(),
            _patch_budget(),
            _patch_http(near),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        proposal = result["_client_action"]["proposal"]
        assert proposal["pickup_lat"] == 52.1318
        assert proposal["pickup_lng"] == -106.6608
        assert proposal["pickup_address"] == "123 Main St"
        assert "pickup pin was moved" not in result["message"]

    @pytest.mark.anyio
    async def test_quoted_total_passes_through_normalized(self):
        args = dict(self.ARGS, quoted_total="20.9")
        with (
            _patch_area(),
            patch.object(tools_booking, "_places_available", AsyncMock(return_value=(None, {"error": "unavailable"}))),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert result["_client_action"]["proposal"]["quoted_total"] == "20.90"

    @pytest.mark.anyio
    async def test_junk_quoted_total_is_dropped_not_fatal(self):
        args = dict(self.ARGS, quoted_total="twenty bucks")
        with (
            _patch_area(),
            patch.object(tools_booking, "_places_available", AsyncMock(return_value=(None, {"error": "unavailable"}))),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert "quoted_total" not in result["_client_action"]["proposal"]

    @pytest.mark.anyio
    async def test_device_anchor_keeps_supplied_pickup_without_maps_call(self):
        # The rider's device fix sits ~25 m from the supplied pickup — the
        # rider is physically there, so no re-geocode runs (no Maps budget)
        # and the pin must not snap to an address centroid.
        rider = {**RIDER, "_client_location": {"lat": 52.1320, "lng": -106.6609}}
        maps = AsyncMock()
        with _patch_area(), patch.object(tools_booking, "_places_available", maps):
            result, ok = await execute_tool("propose_ride_booking", self.ARGS, user=rider)
        assert ok
        proposal = result["_client_action"]["proposal"]
        assert proposal["pickup_lat"] == self.ARGS["pickup_lat"]
        assert proposal["pickup_lng"] == self.ARGS["pickup_lng"]
        assert "pickup pin was moved" not in result["message"]
        maps.assert_not_awaited()

    @pytest.mark.anyio
    async def test_reconcile_picks_nearest_in_area_candidate(self):
        # Google relevance ranks a far same-named street first; reconciliation
        # must pick the candidate nearest the supplied pickup instead.
        two = {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "123 Main St (far), SK, Canada",
                    "geometry": {"location": {"lat": 52.2680, "lng": -106.6345}},
                },
                {
                    "formatted_address": "123 Main St, Saskatoon, SK, Canada",
                    "geometry": {"location": {"lat": 52.1170, "lng": -106.6345}},
                },
            ],
        }
        # ~1.9 km from the near candidate → past _PICKUP_RECONCILE_KM, so the
        # address wins — but it must be the NEAR one.
        args = dict(self.ARGS, pickup_lat=52.1000, pickup_lng=-106.6345)
        with (
            _patch_area(),
            _patch_settings(),
            _patch_budget(),
            _patch_http(two),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        proposal = result["_client_action"]["proposal"]
        assert proposal["pickup_lat"] == 52.1170
        assert proposal["pickup_address"] == "123 Main St, Saskatoon, SK, Canada"
        assert "pickup pin was moved" in result["message"]

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
    for name in ("find_place", "get_rider_location", "get_fare_quote", "propose_ride_booking"):
        assert TOOL_REGISTRY[name].mcp_exposed is False
        assert "rider" in TOOL_REGISTRY[name].audiences
        assert "driver" not in TOOL_REGISTRY[name].audiences
