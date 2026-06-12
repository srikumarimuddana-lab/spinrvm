"""Booking-flow tools: place lookup, approximate quote, booking proposal.

Trust boundary (see plan §3.3): the model can only *propose* a booking.
propose_ride_booking returns a ``_client_action`` payload that the app
renders as a native confirmation card; the card fetches the authoritative
estimate through the existing POST /rides/estimate (surge-locked
estimate_token) and Confirm goes through the unmodified POST /rides path.
Nothing in this module writes to the database.

These tools are chat-only (mcp_exposed=False) and rider-only. Coordinates
flow through tool results because the flow needs them; they are ephemeral —
never logged, never persisted (backend/ai/tools.py contract + migration 140).

The conversational quote is an APPROXIMATION (same fare-config data the
/fares endpoint serves, Decimal math, surge included) and is labelled as
such; the binding number is on the card.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

import httpx

try:
    from .tools import ToolSpec, register
except ImportError:
    from ai.tools import ToolSpec, register

try:
    from ..geo_utils import calculate_distance
    from ..settings_loader import get_app_settings
    from ..utils.maps_budget import check_budget, record_call
except ImportError:
    from geo_utils import calculate_distance
    from settings_loader import get_app_settings
    from utils.maps_budget import check_budget, record_call

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_TEXT_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_HTTP_TIMEOUT = 4.0
_CENT = Decimal("0.01")
_PLACE_RADIUS_METERS = 25000

_COORD_PROPS = {
    "pickup_lat": {"type": "number", "minimum": -90, "maximum": 90},
    "pickup_lng": {"type": "number", "minimum": -180, "maximum": 180},
    "dropoff_lat": {"type": "number", "minimum": -90, "maximum": 90},
    "dropoff_lng": {"type": "number", "minimum": -180, "maximum": 180},
}


def _money(v) -> Decimal:
    return Decimal(str(v)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _looks_like_street_address(query: str) -> bool:
    q = query.lower()
    return any(ch.isdigit() for ch in q) and any(
        token in q
        for token in (
            " st",
            " street",
            " ave",
            " avenue",
            " rd",
            " road",
            " dr",
            " drive",
            " blvd",
            " boulevard",
            " cres",
            " crescent",
            " way",
            " lane",
            " ln",
        )
    )


async def _resolve_area(lat: float, lng: float):
    try:
        from ..routes.fares import resolve_service_area_for_point
    except ImportError:
        from routes.fares import resolve_service_area_for_point
    return await resolve_service_area_for_point(lat, lng)


async def _maps_get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        return resp.json()


async def _candidates_from_results(results) -> list:
    candidates = []
    for result in (results or [])[:3]:
        loc = (result.get("geometry") or {}).get("location") or {}
        lat, lng = loc.get("lat"), loc.get("lng")
        if lat is None or lng is None:
            continue
        area = await _resolve_area(lat, lng)
        candidates.append(
            {
                "name": result.get("name"),
                "address": result.get("formatted_address"),
                "lat": lat,
                "lng": lng,
                "in_service_area": area is not None,
                "service_area": (area or {}).get("name"),
            }
        )
    return candidates


def _suggestions_action(query: str, candidates: list, location_role: Optional[str]) -> Dict[str, Any]:
    return {
        "type": "location_suggestions",
        "query": query,
        "location_role": location_role,
        "candidates": candidates[:3],
    }


async def _lookup_place_candidates(
    *,
    api_key: str,
    query: str,
    near_lat: Optional[float] = None,
    near_lng: Optional[float] = None,
) -> Dict[str, Any]:
    use_geocode_first = _looks_like_street_address(query)
    attempts = ("geocode", "places") if use_geocode_first else ("places", "geocode")

    for kind in attempts:
        try:
            if kind == "places":
                params: Dict[str, Any] = {
                    "query": query,
                    "region": "ca",
                    "key": api_key,
                    "language": "en",
                }
                if near_lat is not None and near_lng is not None:
                    params["location"] = f"{near_lat},{near_lng}"
                    params["radius"] = _PLACE_RADIUS_METERS
                data = await _maps_get(_PLACES_TEXT_URL, params)
                allowed = ("OK", "ZERO_RESULTS")
            else:
                data = await _maps_get(
                    _GEOCODE_URL,
                    {
                        "address": query,
                        "components": "country:CA",
                        "region": "ca",
                        "key": api_key,
                        "language": "en",
                    },
                )
                allowed = ("OK", "ZERO_RESULTS")
        except Exception:
            logger.error("ai find_place maps request failed", exc_info=True)
            return {"error": "place lookup failed — try again or pick the location in the app"}

        if data.get("status") not in allowed:
            logger.error("ai find_place maps API error: %s", data.get("status"))
            return {"error": "place lookup failed — try again or pick the location in the app"}

        await record_call("places_text_search" if kind == "places" else "geocode")
        candidates = await _candidates_from_results(data.get("results") or [])
        if candidates:
            return {"candidates": candidates, "source": kind}

    return {"candidates": []}


async def _places_available() -> tuple:
    settings = await get_app_settings()
    api_key = settings.get("google_maps_api_key") or ""
    if not api_key:
        return None, {
            "error": "place lookup is not available right now — ask the rider to pick the location in the app"
        }

    within, spent, budget = await check_budget()
    if not within:
        logger.error("ai find_place blocked: maps budget exhausted (%.2f/%.2f USD)", spent, budget)
        return None, {
            "error": "place lookup is not available right now — ask the rider to pick the location in the app"
        }
    return api_key, None


async def find_place(
    user: Dict[str, Any],
    query: str,
    near_lat: Optional[float] = None,
    near_lng: Optional[float] = None,
    location_role: Optional[str] = None,
) -> Dict[str, Any]:
    api_key, error = await _places_available()
    if error:
        return error

    lookup = await _lookup_place_candidates(api_key=api_key, query=query, near_lat=near_lat, near_lng=near_lng)
    if lookup.get("error"):
        return {"error": lookup["error"]}
    candidates = lookup.get("candidates") or []
    if not candidates:
        return {"candidates": [], "note": "No matching place found — ask the rider to rephrase or pick on the map."}
    if len(candidates) > 1:
        return {
            "candidates": candidates,
            "_client_action": _suggestions_action(query, candidates, location_role),
            "note": "Multiple matches — ask the rider which one they mean.",
        }
    return {"candidates": candidates}


async def get_fare_quote(
    user: Dict[str, Any],
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
) -> Dict[str, Any]:
    area = await _resolve_area(pickup_lat, pickup_lng)
    if area is None:
        return {
            "error": "pickup is outside Spinr's service areas",
            "hint": "use get_service_info to list operating cities",
        }

    try:
        from ..routes.fares import get_fares_for_location
    except ImportError:
        from routes.fares import get_fares_for_location

    fares = await get_fares_for_location(pickup_lat, pickup_lng)
    distance_km = calculate_distance(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
    # Same heuristic the /rides/estimate endpoint uses for duration.
    duration_minutes = int(distance_km / 30 * 60) + 5

    quotes = []
    for f in fares or []:
        vt = f.get("vehicle_type") or {}
        surge = Decimal(str(f.get("surge_multiplier", 1.0)))
        ride_fare = (
            _money(f.get("base_fare", 0))
            + _money(f.get("per_km_rate", 0)) * _money(distance_km)
            + _money(f.get("per_minute_rate", 0)) * Decimal(duration_minutes)
        )
        ride_fare = max(ride_fare, _money(f.get("minimum_fare", 0)))
        approx = _money(ride_fare * surge + _money(f.get("booking_fee", 0)))
        quotes.append(
            {
                "vehicle_type_id": vt.get("id"),
                "vehicle_type": vt.get("name"),
                "capacity": vt.get("capacity"),
                "approx_fare": str(approx),
                "surge_multiplier": float(surge),
            }
        )

    return {
        "service_area": area.get("name"),
        "distance_km": round(distance_km, 1),
        "duration_minutes": duration_minutes,
        "quotes": quotes,
        "note": (
            "Approximate, before taxes and area fees. Tell the rider the exact total "
            "appears on the booking card before they confirm. Surge shown is live."
        ),
    }


async def propose_ride_booking(
    user: Dict[str, Any],
    pickup_lat: float,
    pickup_lng: float,
    pickup_address: str,
    dropoff_lat: float,
    dropoff_lng: float,
    dropoff_address: str,
    vehicle_type_id: Optional[str] = None,
    promo_code: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> Dict[str, Any]:
    area = await _resolve_area(pickup_lat, pickup_lng)
    if area is None:
        api_key, error = await _places_available()
        if not error and api_key:
            retry = await _lookup_place_candidates(
                api_key=api_key,
                query=pickup_address,
                near_lat=dropoff_lat,
                near_lng=dropoff_lng,
            )
            for candidate in retry.get("candidates") or []:
                if candidate.get("in_service_area"):
                    pickup_lat = candidate["lat"]
                    pickup_lng = candidate["lng"]
                    pickup_address = candidate.get("address") or pickup_address
                    area = await _resolve_area(pickup_lat, pickup_lng)
                    break
        if area is None:
            return {"error": "pickup is outside Spinr's service areas — booking is not possible there"}

    proposal = {
        "pickup_lat": pickup_lat,
        "pickup_lng": pickup_lng,
        "pickup_address": pickup_address,
        "dropoff_lat": dropoff_lat,
        "dropoff_lng": dropoff_lng,
        "dropoff_address": dropoff_address,
    }
    if vehicle_type_id:
        proposal["vehicle_type_id"] = vehicle_type_id
    if promo_code:
        proposal["promo_code"] = promo_code
    if scheduled_time:
        proposal["scheduled_time"] = scheduled_time
    if payment_method:
        proposal["payment_method"] = payment_method.lower()

    return {
        # Lifted out by the orchestrator into an SSE `action` frame; the
        # client renders the native confirmation card from it.
        "_client_action": {"type": "booking_proposal", "proposal": proposal},
        "message": (
            "A booking card with the exact fare is now shown to the rider. Ask them to "
            "review it and tap Confirm — do not claim the ride is booked."
        ),
    }


register(
    ToolSpec(
        name="find_place",
        description=(
            "Call this to turn a place the rider names ('downtown Saskatoon', 'the "
            "airport', a street address) into coordinates before quoting or proposing a "
            "ride. For saved places like 'home' or 'work', call get_saved_places instead. "
            "If multiple candidates return, ask the rider to choose."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Place name or address to look up (Canada).",
                },
                "near_lat": {
                    "type": "number",
                    "minimum": -90,
                    "maximum": 90,
                    "description": "Optional latitude to bias vague place searches near a known pickup or dropoff.",
                },
                "near_lng": {
                    "type": "number",
                    "minimum": -180,
                    "maximum": 180,
                    "description": "Optional longitude to bias vague place searches near a known pickup or dropoff.",
                },
                "location_role": {
                    "type": "string",
                    "enum": ["pickup", "dropoff"],
                    "description": "Which endpoint this place will fill, if known.",
                },
            },
            "required": ["query"],
        },
        handler=find_place,
        mcp_exposed=False,
    )
)

register(
    ToolSpec(
        name="get_fare_quote",
        description=(
            "Call this once pickup and dropoff coordinates are known, to tell the rider "
            "the approximate price per vehicle option (surge included). Always present it "
            "as approximate — the exact total appears on the booking card."
        ),
        input_schema={
            "type": "object",
            "properties": dict(_COORD_PROPS),
            "required": ["pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng"],
        },
        handler=get_fare_quote,
        mcp_exposed=False,
    )
)

register(
    ToolSpec(
        name="propose_ride_booking",
        description=(
            "Call this ONLY after the rider has seen a quote and clearly said they want "
            "to book. Shows them a native confirmation card with the exact fare. You "
            "cannot book rides yourself — the rider must tap Confirm on the card. Never "
            "call this twice for the same request unless the rider asks again."
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_COORD_PROPS,
                "pickup_address": {"type": "string", "maxLength": 300},
                "dropoff_address": {"type": "string", "maxLength": 300},
                "vehicle_type_id": {"type": "string", "maxLength": 64},
                "promo_code": {"type": "string", "maxLength": 40},
                "scheduled_time": {
                    "type": "string",
                    "maxLength": 80,
                    "description": "ISO-8601 pickup time for scheduled rides. Omit for now.",
                },
                "payment_method": {
                    "type": "string",
                    "enum": ["card", "wallet"],
                    "description": "Rider's stated payment preference. Omit if unknown.",
                },
            },
            "required": [
                "pickup_lat",
                "pickup_lng",
                "pickup_address",
                "dropoff_lat",
                "dropoff_lng",
                "dropoff_address",
            ],
        },
        handler=propose_ride_booking,
        mcp_exposed=False,
    )
)
