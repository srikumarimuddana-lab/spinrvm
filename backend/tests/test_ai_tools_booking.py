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
from backend.ai.prompts import build_system_prompt
from backend.ai.tools import TOOL_REGISTRY, ensure_registry_loaded, execute_tool

RIDER = {"id": "rider-1"}

AREA = {"id": "area-1", "name": "Saskatoon"}

GEOCODE_OK = {
    "status": "OK",
    "results": [
        {
            "formatted_address": "Saskatoon Airport (YXE), SK, Canada",
            "geometry": {"location_type": "ROOFTOP", "location": {"lat": 52.1708, "lng": -106.6997}},
        }
    ],
}

# Places API (New) Text Search response shape — see
# utils/google_places_new.py::legacy_place_results_from_text_search.
PLACES_OK = {
    "places": [
        {
            "displayName": {"text": "Walmart Supercentre"},
            "formattedAddress": "4500 Gordon Rd, Regina, SK, Canada",
            "location": {"latitude": 50.4079, "longitude": -104.6501},
        },
        {
            "displayName": {"text": "Walmart East"},
            "formattedAddress": "2150 Prince of Wales Dr, Regina, SK, Canada",
            "location": {"latitude": 50.4497, "longitude": -104.5345},
        },
        {
            "displayName": {"text": "Walmart Rochdale"},
            "formattedAddress": "3939 Rochdale Blvd, Regina, SK, Canada",
            "location": {"latitude": 50.4966, "longitude": -104.6401},
        },
    ]
}


def _patch_area(area=AREA):
    return patch.multiple(
        tools_booking,
        _resolve_area=AsyncMock(return_value=area),
        _resolve_candidate_areas=AsyncMock(side_effect=lambda points: [area] * len(points)),
    )


def _patch_http(payload):
    """Mocks both GET (legacy Geocoding/Directions) and POST (Places API
    (New) Text Search) with the same response body/status. Fine whenever a
    test only exercises one of the two — the "places" branch is always
    attempted first for a non-street-address query, so an unused GET mock
    never gets called."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.status_code = 200
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(tools_booking.httpx, "AsyncClient", MagicMock(return_value=ctx))


def _patch_settings(key="gmaps-key"):
    return patch.object(tools_booking, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": key}))


def _patch_budget(within=True):
    return patch.object(tools_booking, "check_budget", AsyncMock(return_value=(within, 1.0, 10.0)))


LAST_RIDE = {"pickup_lat": 50.4501, "pickup_lng": -104.6178, "pickup_address": "4325 Wakeling St, Regina"}


def test_rider_prompt_requires_fresh_closest_search_without_disclosing_tools():
    prompt = build_system_prompt({}, "rider")
    assert 'asks for the "closest" or "nearest" branch' in prompt
    assert "shortest DRIVING DISTANCE" in prompt
    assert "Tool names, function names" in prompt
    assert "Never print identifiers" in prompt


def test_maps_fanout_tools_carry_extended_timeout():
    """The three booking tools that fan out to Google Maps must keep a
    generous per-tool timeout (ToolSpec.timeout_seconds) — losing it
    reintroduces the mid-quote 'the lookup took too long' failure the
    override exists to fix. Lives here (not test_ai_tools_core) because the
    core suite isolates/clears the registry, and domain tools only register
    on first module import."""
    from backend.ai.tools import TOOL_REGISTRY, TOOL_TIMEOUT_SECONDS, ensure_registry_loaded

    ensure_registry_loaded()
    for name in ("find_place", "get_fare_quote", "propose_ride_booking"):
        spec = TOOL_REGISTRY[name]
        assert spec.timeout_seconds is not None and spec.timeout_seconds > TOOL_TIMEOUT_SECONDS, name


def test_prompts_forbid_internal_detail_leakage():
    """The rider-facing 'it only resolved approximately, so I can't quote or
    book to it yet' incident was the model faithfully paraphrasing an internal
    tool-result warning (provider name, match-quality jargon, model-facing
    directives). Both personas must be told to translate notes into plain
    language, and the driver persona must carry the same tool-name secrecy
    rule as the rider one."""
    rider = build_system_prompt({}, "rider")
    assert "notes and warnings are guidance for YOU" in rider
    assert "resolved approximately" in rider  # named as a forbidden phrase
    assert "provider or service names" in rider
    driver = build_system_prompt({}, "driver")
    assert "Tool names, function names" in driver
    assert "Never print identifiers" in driver
    assert "guidance for YOU" in driver
    assert "Never ask for or repeat payment card numbers" in driver


def test_rider_prompt_trusts_tapped_suggestion_coordinates():
    """A tapped location-suggestion card sends "Use <address> [lat,lng] as my
    pickup/dropoff". Without an explicit trust rule, rule 6's "never
    coordinates you saw in an older bracketed message" wording makes the model
    re-run find_place on the address text, which re-trips the
    imprecise_address gate — the infinite "check the exact street address"
    loop. The prompt must (a) trust the tapped candidate verbatim and (b)
    never re-ask the same address question twice."""
    prompt = build_system_prompt({}, "rider")
    assert "taps one of your location suggestions" in prompt
    assert "never re-run find_place on that address" in prompt
    assert "rider-chosen candidate" in prompt
    assert "Never ask the rider to fix the same address twice" in prompt
    # The recency constraint must still cover the new case (it follows it).
    assert prompt.index("taps one of your location suggestions") < prompt.index("Bracketed coordinates count")


def _patch_last_ride(rows=None):
    return patch.object(tools_booking.db_supabase, "get_rows", AsyncMock(return_value=rows or []))


class TestFindPlaceHardRestriction:
    """B5: named-place lookups now go through Places API (New) Text Search
    with a HARD locationRestriction rectangle, not the legacy Text Search
    API's soft `radius`/`location` bias — Google cannot return a candidate
    outside the box at all."""

    @pytest.mark.anyio
    async def test_search_request_carries_a_hard_location_restriction(self):
        captured = {}

        async def maps_post(url, headers, json_body):
            captured["url"] = url
            captured["json"] = json_body
            return 200, PLACES_OK

        with (
            _patch_settings(),
            _patch_budget(),
            _patch_area(),
            patch.object(tools_booking, "_maps_post", AsyncMock(side_effect=maps_post)),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool(
                "find_place", {"query": "walmart", "near_lat": 50.41, "near_lng": -104.65}, user=RIDER
            )

        assert ok
        assert captured["url"] == tools_booking.PLACES_NEW_TEXT_SEARCH_URL
        restriction = captured["json"]["locationRestriction"]["rectangle"]
        # The rider's point must sit strictly inside the restriction box —
        # otherwise the "hard filter" claim is meaningless.
        assert restriction["low"]["latitude"] < 50.41 < restriction["high"]["latitude"]
        assert restriction["low"]["longitude"] < -104.65 < restriction["high"]["longitude"]

    @pytest.mark.anyio
    async def test_store_departments_collapse_to_one_choice(self):
        """Google lists a store's departments separately, each with its own
        pin metres away and an address that can carry a unit token — so the
        exact-address dedupe misses them and the rider was offered "Walmart
        Wireless" and "Walmart Vision & Glasses" as two destinations. They
        are one drop-off, and the parent name wins."""
        departments = {
            "places": [
                {
                    "displayName": {"text": "Walmart Wireless"},
                    "formattedAddress": "3939 Rochdale Blvd Unit 2, Regina, SK, Canada",
                    "location": {"latitude": 50.49661, "longitude": -104.64012},
                },
                {
                    "displayName": {"text": "Walmart"},
                    "formattedAddress": "3939 Rochdale Blvd, Regina, SK, Canada",
                    "location": {"latitude": 50.4966, "longitude": -104.6401},
                },
                {
                    "displayName": {"text": "Walmart Vision & Glasses"},
                    "formattedAddress": "3939 Rochdale Blvd Suite 1, Regina, SK, Canada",
                    "location": {"latitude": 50.49658, "longitude": -104.64008},
                },
            ]
        }

        with (
            _patch_settings(),
            _patch_budget(),
            _patch_area(),
            patch.object(tools_booking, "_maps_post", AsyncMock(return_value=(200, departments))),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool(
                "find_place", {"query": "walmart", "near_lat": 50.41, "near_lng": -104.65}, user=RIDER
            )

        assert ok
        candidates = result["candidates"]
        assert len(candidates) == 1, [c["name"] for c in candidates]
        assert candidates[0]["name"] == "Walmart"

    @pytest.mark.anyio
    async def test_distinct_nearby_brands_are_not_collapsed(self):
        """Proximity alone must not merge two real destinations — a shared
        leading brand token is also required."""
        plaza = {
            "places": [
                {
                    "displayName": {"text": "Walmart"},
                    "formattedAddress": "3939 Rochdale Blvd, Regina, SK, Canada",
                    "location": {"latitude": 50.4966, "longitude": -104.6401},
                },
                {
                    "displayName": {"text": "Tim Hortons"},
                    "formattedAddress": "3941 Rochdale Blvd, Regina, SK, Canada",
                    "location": {"latitude": 50.49662, "longitude": -104.64013},
                },
            ]
        }

        with (
            _patch_settings(),
            _patch_budget(),
            _patch_area(),
            patch.object(tools_booking, "_maps_post", AsyncMock(return_value=(200, plaza))),
            patch.object(tools_booking, "record_call", AsyncMock()),
            patch.object(
                tools_booking, "_rank_named_place_candidates_by_route", AsyncMock(side_effect=lambda c, *a: (c, False))
            ),
        ):
            result, ok = await execute_tool(
                "find_place", {"query": "rochdale", "near_lat": 50.41, "near_lng": -104.65}, user=RIDER
            )

        assert ok
        assert {c["name"] for c in result["candidates"]} == {"Walmart", "Tim Hortons"}

    @pytest.mark.anyio
    async def test_street_address_geocodes_never_collapse(self):
        """Neighbouring houses sit well inside the co-location radius. The
        collapse is named-place only — geocoded street addresses must all
        survive or the rider loses the house they asked for."""
        neighbours = {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "4325 Wakeling St, Regina, SK, Canada",
                    "geometry": {"location": {"lat": 50.4501, "lng": -104.6178}, "location_type": "ROOFTOP"},
                },
                {
                    "formatted_address": "4327 Wakeling St, Regina, SK, Canada",
                    "geometry": {"location": {"lat": 50.45012, "lng": -104.61782}, "location_type": "ROOFTOP"},
                },
            ],
        }

        with (
            _patch_settings(),
            _patch_budget(),
            _patch_area(),
            patch.object(tools_booking, "_maps_get", AsyncMock(return_value=neighbours)),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool(
                "find_place", {"query": "4325 wakeling st", "near_lat": 50.41, "near_lng": -104.65}, user=RIDER
            )

        assert ok
        assert len(result["candidates"]) == 2

    @pytest.mark.anyio
    async def test_restriction_and_bias_are_never_both_sent(self):
        """searchText rejects a payload carrying both locationRestriction and
        locationBias with 400 INVALID_ARGUMENT ("Location_restriction and
        location_bias cannot be set at the same time"), which killed every
        named-place lookup for riders with a known location. The mocks return
        200 regardless of payload, so only an explicit assertion catches it."""
        captured = {}

        async def maps_post(url, headers, json_body):
            captured["json"] = json_body
            return 200, PLACES_OK

        with (
            _patch_settings(),
            _patch_budget(),
            _patch_area(),
            patch.object(tools_booking, "_maps_post", AsyncMock(side_effect=maps_post)),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            _, ok = await execute_tool(
                "find_place", {"query": "canadian tire", "near_lat": 50.41, "near_lng": -104.65}, user=RIDER
            )

        assert ok
        assert "locationRestriction" in captured["json"]
        assert "locationBias" not in captured["json"]

    @pytest.mark.anyio
    async def test_no_bias_point_sends_no_restriction(self):
        """Without a near_lat/near_lng, there is nothing to build a hard box
        around — matches the legacy branch's behaviour of searching
        unrestricted in that case."""
        captured = {}

        async def maps_post(url, headers, json_body):
            captured["json"] = json_body
            return 200, {"places": []}

        with (
            _patch_settings(),
            _patch_budget(),
            patch.object(tools_booking, "_maps_post", AsyncMock(side_effect=maps_post)),
            patch.object(tools_booking, "_maps_get", AsyncMock(return_value={"status": "ZERO_RESULTS"})),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "walmart"}, user=RIDER)

        assert ok
        assert "locationRestriction" not in captured["json"]

    @pytest.mark.anyio
    async def test_places_api_error_status_is_reported_and_not_silently_swallowed(self):
        with (
            _patch_settings(),
            _patch_budget(),
            patch.object(
                tools_booking,
                "_maps_post",
                AsyncMock(return_value=(403, {"error": {"message": "API key not valid"}})),
            ),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool(
                "find_place", {"query": "walmart", "near_lat": 50.41, "near_lng": -104.65}, user=RIDER
            )

        assert ok
        assert "error" in result


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
    async def test_named_search_keeps_ten_unique_in_area_addresses(self):
        places_list = []
        for index in range(12):
            address_index = 0 if index == 1 else index
            places_list.append(
                {
                    "displayName": {"text": "Walmart Pharmacy" if index == 1 else f"Walmart {index}"},
                    "formattedAddress": f"{100 + address_index} Test Rd, Regina, SK",
                    "location": {"latitude": 50.41 + index * 0.001, "longitude": -104.65},
                }
            )
        places = {"places": places_list}

        async def areas(points):
            # The last unique candidate is outside service and must not be shown.
            return [AREA] * (len(points) - 1) + [None]

        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(places),
            patch.object(tools_booking, "_resolve_candidate_areas", AsyncMock(side_effect=areas)),
            patch.object(
                tools_booking,
                "_rank_named_place_candidates_by_route",
                AsyncMock(side_effect=lambda candidates, *_args: (candidates, False)),
            ),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool(
                "find_place",
                {"query": "walmart", "near_lat": 50.41, "near_lng": -104.65, "location_role": "dropoff"},
                user=RIDER,
            )

        assert ok
        addresses = [candidate["address"] for candidate in result["candidates"]]
        assert len(addresses) == 10
        assert len(set(addresses)) == len(addresses)
        assert "100 Test Rd, Regina, SK" in addresses
        assert result["_client_action"]["candidates"] == result["candidates"]

    @pytest.mark.anyio
    async def test_named_place_suggestions_rank_by_google_driving_distance(self):
        async def maps_get(url, params):
            destination = params["destination"]
            route_by_destination = {
                # Closest as the crow flies, but 8 km by road.
                "50.4079,-104.6501": (8000, 9),
                # Shortest road route (5 km), despite taking longer in traffic.
                "50.4497,-104.5345": (5000, 14),
                # Fastest route, but not the closest by the rider's wording.
                "50.4966,-104.6401": (6500, 8),
            }
            distance_m, minutes = route_by_destination[destination]
            return {
                "status": "OK",
                "routes": [
                    {
                        "legs": [
                            {
                                "distance": {"value": distance_m},
                                "duration": {"value": minutes * 60},
                            }
                        ]
                    }
                ],
            }

        with (
            _patch_settings(),
            _patch_budget(),
            _patch_area(),
            patch.object(tools_booking, "_maps_post", AsyncMock(return_value=(200, PLACES_OK))),
            patch.object(tools_booking, "_maps_get", AsyncMock(side_effect=maps_get)),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool(
                "find_place",
                {"query": "walmart", "near_lat": 50.41, "near_lng": -104.65, "location_role": "dropoff"},
                user=RIDER,
            )

        assert ok
        assert result["ranking_basis"] == "driving_distance"
        assert result["candidates"][0]["name"] == "Walmart East"
        assert result["candidates"][0]["driving_distance_km"] == 5.0
        assert result["_client_action"]["candidates"][0]["name"] == "Walmart East"

    @pytest.mark.anyio
    async def test_route_ranking_failure_keeps_proximity_order(self):
        async def maps_get(url, params):
            raise TimeoutError("directions timeout")

        with (
            _patch_settings(),
            _patch_budget(),
            _patch_area(),
            patch.object(tools_booking, "_maps_post", AsyncMock(return_value=(200, PLACES_OK))),
            patch.object(tools_booking, "_maps_get", AsyncMock(side_effect=maps_get)),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool(
                "find_place", {"query": "walmart", "near_lat": 50.41, "near_lng": -104.65}, user=RIDER
            )

        assert ok
        assert result["ranking_basis"] == "straight_line_distance"
        assert result["candidates"][0]["name"] == "Walmart Supercentre"

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
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 50.5177, "lng": -104.6501}},
                },
                {  # ~1.5 km away
                    "formatted_address": "4325 Wakeling St, Regina, SK",
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 50.4214, "lng": -104.6641}},
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
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 50.90, "lng": -104.65}},
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


class TestGeocodeLocalityFilter:
    """B7: `components=locality:<city>` is a HARD Geocoding API filter, unlike
    `bounds` — the strongest available defence against cross-city
    mis-resolution. Only applied when the rider's location resolves to a
    service area with a populated `city`; degrades to unfiltered on
    ZERO_RESULTS or when no city is known."""

    NEAR = {"near_lat": 50.41, "near_lng": -104.65}

    @pytest.mark.anyio
    async def test_known_city_adds_locality_filter(self):
        http_patch, client = _patch_http_capture(GEOCODE_OK)
        with (
            _patch_settings(),
            _patch_budget(),
            http_patch,
            _patch_area({"id": "area-1", "name": "Regina", "city": "Regina"}),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st", **self.NEAR}, user=RIDER)
        assert ok and result["candidates"]
        params = client.get.call_args.kwargs.get("params") or client.get.call_args.args[1]
        assert params["components"] == "locality:Regina|country:CA"
        client.get.assert_awaited_once()

    @pytest.mark.anyio
    async def test_no_city_on_area_sends_no_locality_filter(self):
        http_patch, client = _patch_http_capture(GEOCODE_OK)
        with (
            _patch_settings(),
            _patch_budget(),
            http_patch,
            _patch_area({"id": "area-1", "name": "Saskatoon"}),  # no "city" key
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st", **self.NEAR}, user=RIDER)
        assert ok and result["candidates"]
        params = client.get.call_args.kwargs.get("params") or client.get.call_args.args[1]
        assert params["components"] == "country:CA"
        client.get.assert_awaited_once()

    @pytest.mark.anyio
    async def test_no_area_resolved_sends_no_locality_filter(self):
        http_patch, client = _patch_http_capture(GEOCODE_OK)
        with (
            _patch_settings(),
            _patch_budget(),
            http_patch,
            _patch_area(None),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st", **self.NEAR}, user=RIDER)
        assert ok and result["candidates"]
        params = client.get.call_args.kwargs.get("params") or client.get.call_args.args[1]
        assert params["components"] == "country:CA"

    @pytest.mark.anyio
    async def test_zero_results_with_locality_retries_unfiltered(self):
        zero_resp = MagicMock()
        zero_resp.json.return_value = {"status": "ZERO_RESULTS", "results": []}
        ok_resp = MagicMock()
        ok_resp.json.return_value = GEOCODE_OK
        client = MagicMock()
        client.get = AsyncMock(side_effect=[zero_resp, ok_resp])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with (
            _patch_settings(),
            _patch_budget(),
            patch.object(tools_booking.httpx, "AsyncClient", MagicMock(return_value=ctx)),
            _patch_area({"id": "area-1", "name": "Regina", "city": "Regina"}),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st", **self.NEAR}, user=RIDER)
        assert ok and result["candidates"]
        assert client.get.await_count == 2
        first_params = client.get.await_args_list[0].kwargs.get("params") or client.get.await_args_list[0].args[1]
        second_params = client.get.await_args_list[1].kwargs.get("params") or client.get.await_args_list[1].args[1]
        assert first_params["components"] == "locality:Regina|country:CA"
        assert second_params["components"] == "country:CA"
        assert "km from the rider's search area" in result["note"]

    @pytest.mark.anyio
    async def test_far_warning_keeps_the_disambiguation_note(self):
        """Ambiguous AND far is the incident's own shape. The far-match warning
        used to overwrite result["note"], deleting 'ask which one they mean'
        exactly when the model most needed both instructions."""
        far_and_ambiguous = {
            "status": "OK",
            "results": [
                {  # ~54 km from the bias point — both candidates are far
                    "formatted_address": "4325 Wakeling St, Somewhere Else, SK",
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 50.90, "lng": -104.65}},
                },
                {  # ~65 km — a second plausible match keeps this ambiguous
                    "formatted_address": "4325 Wakeling Ave, Elsewhere, SK",
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 51.00, "lng": -104.65}},
                },
            ],
        }
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(far_and_ambiguous),
            _patch_area(),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("find_place", {"query": "4325 wakeling st", **self.NEAR}, user=RIDER)
        assert ok
        assert len(result["candidates"]) > 1
        # Both instructions survive, and the disambiguation one still leads.
        assert "ask the rider which one they mean" in result["note"].lower()
        assert "km from the rider's search area" in result["note"]


class TestAddressPrecision:
    """Google flags its own guesses; we used to discard the flag and quote on a
    neighbourhood centroid wearing a confident formatted_address (incident:
    '4321 Wakeling St' — a house number Google lacks — priced 8.78 km from
    '4325 Wakeling St')."""

    NEAR = {"near_lat": 50.4079, "near_lng": -104.6501}

    def _geocode(self, **geometry_extra):
        return {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "4321 Wakeling St, Regina, SK, Canada",
                    "geometry": {"location": {"lat": 50.4214, "lng": -104.6641}, **geometry_extra},
                }
            ],
        }

    async def _find(self, payload, query="4321 wakeling st"):
        with (
            _patch_settings(),
            _patch_budget(),
            _patch_http(payload),
            _patch_area(),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            return await execute_tool("find_place", {"query": query, **self.NEAR}, user=RIDER)

    @pytest.mark.anyio
    async def test_rooftop_match_is_precise_and_unflagged(self):
        result, ok = await self._find(self._geocode(location_type="ROOFTOP"))
        assert ok
        assert result["candidates"][0]["precise"] is True
        assert result["candidates"][0]["match_quality"] == "ROOFTOP"
        assert "imprecise_address" not in result

    @pytest.mark.anyio
    async def test_interpolated_match_is_still_precise_enough(self):
        # Interpolated between two known house numbers on the block — good
        # enough to send a driver to; must not trigger a false confirmation.
        result, ok = await self._find(self._geocode(location_type="RANGE_INTERPOLATED"))
        assert ok
        assert result["candidates"][0]["precise"] is True
        assert "imprecise_address" not in result

    @pytest.mark.anyio
    async def test_approximate_match_on_a_numbered_address_is_flagged(self):
        result, ok = await self._find(self._geocode(location_type="APPROXIMATE"))
        assert ok
        assert result["candidates"][0]["precise"] is False
        assert result["imprecise_address"] is True
        assert "Do NOT quote on it" in result["note"]

    @pytest.mark.anyio
    async def test_partial_match_outranks_a_precise_looking_location_type(self):
        """Google says ROOFTOP but admits partial_match: the house number was
        ignored, so the rooftop it pinned is someone else's."""
        payload = self._geocode(location_type="ROOFTOP")
        payload["results"][0]["partial_match"] = True
        result, ok = await self._find(payload)
        assert ok
        assert result["candidates"][0]["match_quality"] == "PARTIAL_MATCH"
        assert result["candidates"][0]["precise"] is False
        assert result["imprecise_address"] is True

    @pytest.mark.anyio
    async def test_poi_search_is_never_flagged_for_imprecision(self):
        """Places Text Search carries no location_type at all. A POI query has
        no house number to miss, so it must not inherit the warning — that
        would fire on every 'walmart' lookup."""
        result, ok = await self._find(PLACES_OK, query="walmart")
        assert ok
        assert "imprecise_address" not in result


class TestSameStreetGuard:
    """4325 Wakeling St → 4321 Wakeling St, adjacent houses, quoted 8.78 km /
    22 min. Every prior guard passed: the same-place check saw 8.78 km, and the
    road/haversine band only validates the route BETWEEN two points, never
    whether the points themselves are right."""

    # ~8.8 km apart — the reported geometry.
    WAKELING = {
        "pickup_lat": 50.4214,
        "pickup_lng": -104.6641,
        "pickup_address": "4325 Wakeling St, Regina, SK",
        "dropoff_lat": 50.4966,
        "dropoff_lng": -104.6401,
        "dropoff_address": "4321 Wakeling St, Regina, SK",
    }

    def _maps_unavailable(self):
        # Isolates these tests from the geocoding path entirely — including
        # the dropoff stale-coordinate guard, which also fails closed when
        # Maps is unavailable and would otherwise intercept before the
        # same-street guard under test ever runs.
        return patch.multiple(
            tools_booking,
            _places_available=AsyncMock(return_value=(None, {"error": "unavailable"})),
            _dropoff_pair_refusal=AsyncMock(return_value=None),
        )

    @pytest.mark.anyio
    async def test_same_street_kilometres_apart_is_refused(self):
        estimates = AsyncMock()
        with (
            patch("backend.routes.rides.estimates.compute_ride_estimates", estimates),
            _patch_area(),
            self._maps_unavailable(),
        ):
            result, ok = await execute_tool("get_fare_quote", self.WAKELING, user=RIDER)
        assert ok
        assert result["needs_confirmation"] == "address_mismatch"
        assert result["distance_km"] > 2
        assert "same street" in result["note"]
        assert "quotes" not in result
        estimates.assert_not_awaited()

    @pytest.mark.anyio
    async def test_proposal_refuses_the_same_trip(self):
        with _patch_area(), self._maps_unavailable():
            result, ok = await execute_tool("propose_ride_booking", self.WAKELING, user=RIDER)
        assert ok
        assert result["needs_confirmation"] == "address_mismatch"
        assert "_client_action" not in result

    @pytest.mark.anyio
    async def test_explicit_confirmation_lets_it_through(self):
        args = dict(self.WAKELING, confirm_same_location=True)
        with _patch_estimates(ESTIMATES), _patch_promos([]), _patch_area(), self._maps_unavailable():
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        assert "needs_confirmation" not in result
        assert len(result["quotes"]) == 1

    @pytest.mark.anyio
    async def test_different_streets_far_apart_quote_normally(self):
        """The guard keys on street identity, not distance — a genuine
        cross-town trip must be unaffected."""
        args = dict(self.WAKELING, dropoff_address="3939 Rochdale Blvd, Regina, SK")
        with _patch_estimates(ESTIMATES), _patch_promos([]), _patch_area(), self._maps_unavailable():
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        assert "needs_confirmation" not in result
        assert len(result["quotes"]) == 1

    @pytest.mark.anyio
    async def test_same_street_short_trip_is_untouched(self):
        """Two ends of one long street, legitimately 1 km apart — under the
        2 km band, so no confirmation is demanded."""
        args = dict(self.WAKELING, dropoff_lat=50.4304, dropoff_lng=-104.6641)  # ~1 km
        with _patch_estimates(ESTIMATES), _patch_promos([]), _patch_area(), self._maps_unavailable():
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        assert "needs_confirmation" not in result

    def test_street_key_normalizes_suffixes_and_house_numbers(self):
        key = tools_booking._street_key
        assert key("4325 Wakeling St, Regina, SK S4T 1B2") == ("wakeling st", "regina")
        assert key("4321 wakeling street, Regina") == ("wakeling st", "regina")
        assert key("4325 Wakeling St, Regina") == key("4321 Wakeling Street, regina")
        # Same street name, different city → not the same street.
        assert key("100 Main St, Regina") != key("100 Main St, Saskatoon")
        # No house number, or nothing street-shaped → no opinion.
        assert key("Wakeling St, Regina") is None
        assert key("Saskatoon Airport") is None
        assert key(None) is None


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
        # the geocoding path has its own tests. _patch_area is required now
        # that the quote enforces reconciliation's service-area verdict. The
        # dropoff stale-coordinate guard also fails closed when Maps is
        # unavailable — bypass it here since it isn't what this test covers.
        with (
            _patch_estimates(ESTIMATES),
            _patch_promos(PROMOS),
            _patch_settings(key=""),
            _patch_area(),
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
        ):
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
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 52.1170, "lng": -106.6345}},
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
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
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
    async def test_quote_refuses_an_out_of_area_pickup(self):
        """propose_ride_booking already refuses these. Quoting one first shows
        the rider a price and then takes it away — refuse at the quote too, and
        never spend the estimate call."""
        estimates_mock = AsyncMock(return_value=ESTIMATES)
        with (
            patch("backend.routes.rides.estimates.compute_ride_estimates", estimates_mock),
            _patch_promos([]),
            _patch_area(None),  # nothing resolves to a service area
            patch.object(tools_booking, "_places_available", AsyncMock(return_value=(None, {"error": "unavailable"}))),
        ):
            result, ok = await execute_tool(
                "get_fare_quote", dict(self.ARGS, pickup_address="123 Main St, Nowhere"), user=RIDER
            )
        assert ok
        assert result["error"] == tools_booking._OUT_OF_AREA_ERROR
        assert "quotes" not in result
        estimates_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_same_place_refusal_still_discloses_a_moved_pin(self):
        """A refusal the rider is asked to confirm must name the pickup we
        actually resolved — otherwise they answer "yes, same place" about a pin
        they were never told had moved."""
        walmart = {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "4500 Gordon Rd, Regina, SK S4S 6H7",
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 50.40790, "lng": -104.65010}},
                }
            ],
        }
        args = dict(
            pickup_lat=50.5177,  # ~12 km north of the real address
            pickup_lng=-104.65010,
            pickup_address="4500 Gordon Rd, Regina",
            dropoff_lat=50.40862,  # ~80 m from the RECONCILED pickup
            dropoff_lng=-104.65010,
            dropoff_address="4500 Gordon Rd, Regina",
        )
        estimates_mock = AsyncMock(return_value=ESTIMATES)
        with (
            patch("backend.routes.rides.estimates.compute_ride_estimates", estimates_mock),
            _patch_promos([]),
            _patch_area(),
            _patch_settings(),
            _patch_budget(),
            _patch_http(walmart),
            patch.object(tools_booking, "record_call", AsyncMock()),
        ):
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        assert result["needs_confirmation"] == "same_location"
        # The guard measured the reconciled pickup, not the stale supplied one.
        assert 60 <= result["distance_meters"] <= 100
        assert result["pickup_note"].startswith("the pickup pin was moved")
        estimates_mock.assert_not_awaited()

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
        # isolating these tests from the geocoding path — including the
        # dropoff stale-coordinate guard, which also fails closed when Maps
        # is unavailable and would otherwise intercept before the
        # same-place guard under test ever runs.
        return patch.multiple(
            tools_booking,
            _places_available=AsyncMock(return_value=(None, {"error": "unavailable"})),
            _dropoff_pair_refusal=AsyncMock(return_value=None),
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
            # No Maps key configured in the test env — the dropoff
            # stale-coordinate guard fails closed on that; bypass it since
            # it isn't what this test covers.
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
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
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 52.1170, "lng": -106.6345}},
                }
            ],
        }
        with (
            patch.object(tools_booking, "_resolve_area", stale_then_fixed),
            # _resolve_candidate_areas is a separate batched service-area
            # lookup used when tagging geocode candidates (not routed through
            # _resolve_area) — must also resolve the re-geocoded downtown
            # candidate into a service area, or reconciliation falls back to
            # the (stale, out-of-area) supplied point.
            patch.object(tools_booking, "_resolve_candidate_areas", AsyncMock(return_value=[AREA])),
            _patch_settings(),
            _patch_budget(),
            _patch_http(downtown),
            patch.object(tools_booking, "record_call", AsyncMock()),
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
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
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 52.1170, "lng": -106.6345}},
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
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
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
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 52.1325, "lng": -106.6610}},
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
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
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
            # The dropoff stale-coordinate guard also fails closed when Maps
            # is unavailable — bypass it since it isn't what this test covers.
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
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
            # The dropoff stale-coordinate guard also fails closed when Maps
            # is unavailable — bypass it since it isn't what this test covers.
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
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
        with (
            _patch_area(),
            patch.object(tools_booking, "_places_available", maps),
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
        ):
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
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 52.2680, "lng": -106.6345}},
                },
                {
                    "formatted_address": "123 Main St, Saskatoon, SK, Canada",
                    "geometry": {"location_type": "ROOFTOP", "location": {"lat": 52.1170, "lng": -106.6345}},
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
            patch.object(tools_booking, "_dropoff_pair_refusal", AsyncMock(return_value=None)),
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
    for name in ("find_place", "get_rider_location", "get_fare_quote", "propose_ride_booking", "request_map_pin"):
        assert TOOL_REGISTRY[name].mcp_exposed is False
        assert "rider" in TOOL_REGISTRY[name].audiences
        assert "driver" not in TOOL_REGISTRY[name].audiences


class TestRequestMapPin:
    """The chat has no map — this tool is the only bridge to one. Its client
    action must carry everything the picker screen needs, and every refusal
    note that says "drop a pin" must point the model at this tool (a bare
    "drop a pin on the map" instruction is a dead end the rider cannot act
    on — the incident this flow exists to fix)."""

    CAPABLE_RIDER = {**RIDER, "_client_capabilities": frozenset({"map_pin"})}

    @pytest.mark.anyio
    async def test_action_carries_role_approx_and_label(self):
        result, ok = await execute_tool(
            "request_map_pin",
            {
                "location_role": "dropoff",
                "approx_lat": 50.4079,
                "approx_lng": -104.6501,
                "label": "2965 Gordon Rd, Regina",
            },
            user=self.CAPABLE_RIDER,
        )
        assert ok
        assert result["shown"] is True
        action = result["_client_action"]
        assert action["type"] == "open_map_picker"
        assert action["location_role"] == "dropoff"
        assert action["approx_lat"] == 50.4079
        assert action["approx_lng"] == -104.6501
        assert action["label"] == "2965 Gordon Rd, Regina"
        # The model must relay the pin verbatim, not re-geocode it.
        assert "verbatim" in result["note"]

    @pytest.mark.anyio
    async def test_approx_optional(self):
        result, ok = await execute_tool("request_map_pin", {"location_role": "pickup"}, user=self.CAPABLE_RIDER)
        assert ok
        assert result["_client_action"]["location_role"] == "pickup"
        assert "approx_lat" not in result["_client_action"]

    @pytest.mark.anyio
    async def test_no_button_for_clients_without_the_capability(self):
        # The backend deploys ahead of mobile builds. An app installed before
        # the Drop-a-pin card shipped renders nothing for the action — the
        # assistant must not promise a button that isn't there. Old clients
        # send no capabilities at all.
        result, ok = await execute_tool("request_map_pin", {"location_role": "dropoff"}, user=RIDER)
        assert ok
        assert result["shown"] is False
        assert "_client_action" not in result
        assert "do NOT" in result["note"]

    def test_refusal_notes_point_at_this_tool(self):
        import inspect

        source = inspect.getsource(tools_booking)
        # The imprecise-address warning and the same-street mismatch refusal
        # both tell the model to offer a pin — they must name request_map_pin
        # so the model can actually show a button instead of describing a map
        # that does not exist in the chat.
        for fragment in (
            "call request_map_pin with this candidate's",
            "or call request_map_pin so they",
        ):
            assert fragment in source


class TestDropoffLabelGuard:
    """The label and the pin of a dropoff must describe the same place.

    Incident: the quote priced the rider's 1.4 km Walmart trip at $7.28; the
    booking card kept the "Walmart Supercentre" label but carried Southland
    Mall coordinates the model remembered from an earlier message — $11.76 to
    a place the rider didn't ask to go, under the right name. History keeps
    only message text, so recalled coordinates are always from an older trip.
    """

    # 4325 Wakeling St, Regina.
    PICKUP = {"pickup_lat": 50.4214, "pickup_lng": -104.6641, "pickup_address": "4325 Wakeling St, Regina"}
    # Where the Walmart label actually geocodes.
    WALMART = {"lat": 50.4079, "lng": -104.6501}
    # The stale pin recalled from the earlier Southland Mall request — ~4 km
    # from where the label resolves.
    STALE = {"lat": 50.4350, "lng": -104.6100}
    LABEL = "Walmart Supercentre, 4500 Gordon Rd, Regina"

    def _patches(self):
        async def fake_lookup(*, api_key, query, near_lat=None, near_lng=None, **kwargs):
            if "Wakeling" in query:
                # Pickup reconciles onto itself — unadjusted, not this test's subject.
                return {
                    "candidates": [
                        {
                            "lat": self.PICKUP["pickup_lat"],
                            "lng": self.PICKUP["pickup_lng"],
                            "address": self.PICKUP["pickup_address"],
                            "in_service_area": True,
                            "precise": True,
                        }
                    ]
                }
            return {
                "candidates": [
                    {
                        "lat": self.WALMART["lat"],
                        "lng": self.WALMART["lng"],
                        "address": self.LABEL,
                        "in_service_area": True,
                        "precise": True,
                    }
                ]
            }

        lookup = AsyncMock(side_effect=fake_lookup)
        return (
            _patch_area(),
            patch.object(tools_booking, "_places_available", AsyncMock(return_value=("key", None))),
            patch.object(tools_booking, "_lookup_place_candidates", lookup),
        ), lookup

    @pytest.mark.anyio
    async def test_proposal_refuses_label_over_foreign_coordinates(self):
        patches, _lookup = self._patches()
        args = {
            **self.PICKUP,
            "dropoff_lat": self.STALE["lat"],
            "dropoff_lng": self.STALE["lng"],
            "dropoff_address": self.LABEL,
        }
        with patches[0], patches[1], patches[2]:
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert result["needs_correction"] == "dropoff_label_mismatch"
        assert "_client_action" not in result
        assert "re-resolve" in result["note"]
        assert result["distance_km"] > 1.5

    @pytest.mark.anyio
    async def test_quote_refuses_the_same_pair(self):
        patches, _lookup = self._patches()
        args = {
            **self.PICKUP,
            "dropoff_lat": self.STALE["lat"],
            "dropoff_lng": self.STALE["lng"],
            "dropoff_address": self.LABEL,
        }
        with patches[0], patches[1], patches[2]:
            result, ok = await execute_tool("get_fare_quote", args, user=RIDER)
        assert ok
        assert result["needs_correction"] == "dropoff_label_mismatch"

    @pytest.mark.anyio
    async def test_matching_pair_passes(self):
        patches, _lookup = self._patches()
        args = {
            **self.PICKUP,
            "dropoff_lat": self.WALMART["lat"],
            "dropoff_lng": self.WALMART["lng"],
            "dropoff_address": self.LABEL,
        }
        with patches[0], patches[1], patches[2]:
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert result["_client_action"]["type"] == "booking_proposal"

    @pytest.mark.anyio
    async def test_coordinate_string_label_is_its_own_pin(self):
        # The map-pin fallback label ("50.43500, -104.61000") IS the pin —
        # nothing to cross-check, and no Maps call spent on it.
        patches, lookup = self._patches()
        args = {
            **self.PICKUP,
            "dropoff_lat": self.STALE["lat"],
            "dropoff_lng": self.STALE["lng"],
            "dropoff_address": "50.43500, -104.61000",
        }
        with patches[0], patches[1], patches[2]:
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert result["_client_action"]["type"] == "booking_proposal"
        dropoff_lookups = [c for c in lookup.await_args_list if "Wakeling" not in c.kwargs["query"]]
        assert dropoff_lookups == []

    @pytest.mark.anyio
    async def test_lookup_error_fails_closed(self):
        # A transient Maps failure must not wave the stale pair through —
        # that would disable the guard exactly when Google blips. Distinct
        # retryable result, not a mismatch verdict.
        args = {
            **self.PICKUP,
            "dropoff_lat": self.STALE["lat"],
            "dropoff_lng": self.STALE["lng"],
            "dropoff_address": self.LABEL,
        }
        with (
            _patch_area(),
            patch.object(tools_booking, "_places_available", AsyncMock(return_value=("key", None))),
            patch.object(
                tools_booking,
                "_lookup_place_candidates",
                AsyncMock(return_value={"error": "place lookup failed"}),
            ),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert result["needs_correction"] == "dropoff_unverified"
        assert "_client_action" not in result

    @pytest.mark.anyio
    async def test_unavailable_maps_fails_closed(self):
        args = {
            **self.PICKUP,
            "dropoff_lat": self.STALE["lat"],
            "dropoff_lng": self.STALE["lng"],
            "dropoff_address": self.LABEL,
        }
        with (
            _patch_area(),
            patch.object(
                tools_booking,
                "_places_available",
                AsyncMock(return_value=(None, {"error": "place lookup is not available right now"})),
            ),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert result["needs_correction"] == "dropoff_unverified"
        assert "_client_action" not in result

    @pytest.mark.anyio
    async def test_coordinate_label_must_match_the_pin(self):
        # A coordinate-shaped label carries its own numbers — gluing a current
        # pin label onto stale coordinates is the same incident, and it must
        # be caught numerically, without spending a Maps call.
        maps = AsyncMock(side_effect=AssertionError("coordinate label must not geocode"))
        with patch.object(tools_booking, "_places_available", maps):
            refusal = await tools_booking._dropoff_pair_refusal(
                self.STALE["lat"],
                self.STALE["lng"],
                f"{self.WALMART['lat']:.5f}, {self.WALMART['lng']:.5f}",
            )
        assert refusal is not None
        assert refusal["needs_correction"] == "dropoff_label_mismatch"

    @pytest.mark.anyio
    async def test_coordinate_label_matching_the_pin_passes(self):
        maps = AsyncMock(side_effect=AssertionError("coordinate label must not geocode"))
        with patch.object(tools_booking, "_places_available", maps):
            refusal = await tools_booking._dropoff_pair_refusal(
                self.STALE["lat"], self.STALE["lng"], f"{self.STALE['lat']:.5f}, {self.STALE['lng']:.5f}"
            )
        assert refusal is None

    @pytest.mark.anyio
    async def test_coordinate_label_uses_decimal_precision_not_poi_radius(self):
        # Roughly 100 m is well inside the 1.5 km POI-centroid allowance, but
        # far outside the rounding envelope of a five-decimal coordinate.
        maps = AsyncMock(side_effect=AssertionError("coordinate label must not geocode"))
        with patch.object(tools_booking, "_places_available", maps):
            refusal = await tools_booking._dropoff_pair_refusal(
                self.STALE["lat"] + 0.0009,
                self.STALE["lng"],
                f"{self.STALE['lat']:.5f}, {self.STALE['lng']:.5f}",
            )
        assert refusal is not None
        assert refusal["needs_correction"] == "dropoff_label_mismatch"

    @pytest.mark.anyio
    async def test_street_address_label_needs_precise_agreement(self):
        # An APPROXIMATE centroid drifting near the stale pin must not vouch
        # for a numbered street address when Google DID pin the real building
        # precisely — elsewhere.
        async def lookup(*, api_key, query, near_lat=None, near_lng=None, **kwargs):
            if "Wakeling" in query:
                return {
                    "candidates": [
                        {
                            "lat": self.PICKUP["pickup_lat"],
                            "lng": self.PICKUP["pickup_lng"],
                            "address": self.PICKUP["pickup_address"],
                            "in_service_area": True,
                            "precise": True,
                        }
                    ]
                }
            return {
                "candidates": [
                    # Neighbourhood centroid beside the stale pin — imprecise.
                    {
                        "lat": self.STALE["lat"] + 0.001,
                        "lng": self.STALE["lng"],
                        "address": "Gordon Rd area, Regina",
                        "in_service_area": True,
                        "precise": False,
                    },
                    # Google's actual rooftop for the address — far from the pin.
                    {
                        "lat": self.WALMART["lat"],
                        "lng": self.WALMART["lng"],
                        "address": self.LABEL,
                        "in_service_area": True,
                        "precise": True,
                    },
                ]
            }

        args = {
            **self.PICKUP,
            "dropoff_lat": self.STALE["lat"],
            "dropoff_lng": self.STALE["lng"],
            "dropoff_address": "4500 Gordon Rd, Regina",
        }
        with (
            _patch_area(),
            patch.object(tools_booking, "_places_available", AsyncMock(return_value=("key", None))),
            patch.object(tools_booking, "_lookup_place_candidates", AsyncMock(side_effect=lookup)),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert result["needs_correction"] == "dropoff_label_mismatch"

    @pytest.mark.anyio
    async def test_street_address_label_with_only_imprecise_candidates_stays_lenient(self):
        # With NO precise candidate anywhere, refusing on centroid distance
        # would block odd-but-real addresses — keep the lenient fallback.
        async def lookup(*, api_key, query, near_lat=None, near_lng=None, **kwargs):
            if "Wakeling" in query:
                return {
                    "candidates": [
                        {
                            "lat": self.PICKUP["pickup_lat"],
                            "lng": self.PICKUP["pickup_lng"],
                            "address": self.PICKUP["pickup_address"],
                            "in_service_area": True,
                            "precise": True,
                        }
                    ]
                }
            return {
                "candidates": [
                    {
                        "lat": self.STALE["lat"] + 0.001,
                        "lng": self.STALE["lng"],
                        "address": "Gordon Rd area, Regina",
                        "in_service_area": True,
                        "precise": False,
                    }
                ]
            }

        args = {
            **self.PICKUP,
            "dropoff_lat": self.STALE["lat"],
            "dropoff_lng": self.STALE["lng"],
            "dropoff_address": "4500 Gordon Rd, Regina",
        }
        with (
            _patch_area(),
            patch.object(tools_booking, "_places_available", AsyncMock(return_value=("key", None))),
            patch.object(tools_booking, "_lookup_place_candidates", AsyncMock(side_effect=lookup)),
        ):
            result, ok = await execute_tool("propose_ride_booking", args, user=RIDER)
        assert ok
        assert result["_client_action"]["type"] == "booking_proposal"
