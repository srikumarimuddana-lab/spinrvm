"""Booking-flow tools: place lookup, rider location, fare quote, booking proposal.

Trust boundary (see plan §3.3): the model can only *propose* a booking.
propose_ride_booking returns a ``_client_action`` payload that the app
renders as a native confirmation card; the card fetches the authoritative
estimate through the existing POST /rides/estimate (surge-locked
estimate_token) and Confirm goes through the unmodified POST /rides path.
Nothing in this module writes to the database.

These tools are chat-only (mcp_exposed=False) and rider-only. Coordinates
flow through tool results because the flow needs them; they are ephemeral —
never logged, never persisted (backend/ai/tools.py contract + migration 140).

get_fare_quote runs the SAME engine as POST /rides/estimate
(compute_ride_estimates: geofence, live driver availability/ETA, surge,
fees + taxes) and auto-applies the best eligible promo through the same
helpers as /promo/available — so the chat quote, the booking card and the
rider's receipt can never disagree. The quote also ships a ``fare_quote``
client action so both the rider app and the admin AI console render it as
a rich card instead of re-reading numbers from model prose.
"""

import asyncio
import json
import logging
import math
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

try:
    from .tools import ToolSpec, register
except ImportError:
    from ai.tools import ToolSpec, register

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from ..utils.google_places_new import (
        PLACES_NEW_TEXT_SEARCH_FIELD_MASK,
        PLACES_NEW_TEXT_SEARCH_URL,
        build_text_search_payload,
        legacy_place_results_from_text_search,
        places_new_headers,
    )
    from ..utils.maps_budget import check_budget, record_call
    from ..utils.redis_client import redis_get, redis_set
except ImportError:
    import db_supabase
    from settings_loader import get_app_settings
    from utils.google_places_new import (  # type: ignore
        PLACES_NEW_TEXT_SEARCH_FIELD_MASK,
        PLACES_NEW_TEXT_SEARCH_URL,
        build_text_search_payload,
        legacy_place_results_from_text_search,
        places_new_headers,
    )
    from utils.maps_budget import check_budget, record_call
    from utils.redis_client import redis_get, redis_set  # type: ignore

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
_HTTP_TIMEOUT = 4.0
_CENT = Decimal("0.01")
_PLACE_RADIUS_METERS = 25000
_PLACE_CANDIDATE_LIMIT = 10
# A model-supplied pickup that sits more than this far from its own
# (re-geocoded) address is treated as stale/hallucinated and replaced by the
# address — the driver is dispatched to the coordinate, never the text, so the
# two must agree before a booking card is shown.
_PICKUP_RECONCILE_KM = 1.0
# Pickup and dropoff closer than this are "the same place" for a rider: a
# POI centroid vs. a GPS fix across a big-box parking lot commonly differs
# 100-300 m, while genuinely distinct nearby destinations (the next store
# over, a hotel across a highway) sit further apart. Below this, quoting or
# proposing requires the rider's explicit confirmation — a false positive
# costs one chat question, a false negative books a pointless minimum-fare
# ride (incident: same Walmart quoted at 0.08 km with no warning).
_SAME_PLACE_CONFIRM_KM = 0.25

# Shared by get_fare_quote and propose_ride_booking so the two can never
# disagree about whether a pickup is serviceable.
_OUT_OF_AREA_ERROR = "pickup is outside Spinr's service areas — booking is not possible there"

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


async def _resolve_candidate_areas(points: list[tuple[float, float]]) -> list:
    """Resolve a candidate set with one service-area read, not an N+1 loop."""
    try:
        from ..routes.fares import resolve_service_area_for_point
    except ImportError:
        from routes.fares import resolve_service_area_for_point

    all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=500)
    return [await resolve_service_area_for_point(lat, lng, all_areas=all_areas) for lat, lng in points]


async def _maps_get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        return resp.json()


async def _maps_post(url: str, headers: Dict[str, str], json_body: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    """Places API (New) endpoints are POST-only and report errors via HTTP
    status + an ``error`` body, not a legacy ``status`` field — callers must
    check the returned status code themselves."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=json_body)
        return resp.status_code, resp.json()


# Google Geocoding `geometry.location_type` values that actually pin a
# building. ROOFTOP is the building itself; RANGE_INTERPOLATED is a position
# interpolated between two known house numbers on the block — both are precise
# enough to send a driver to. GEOMETRIC_CENTER (street/polyline centre) and
# APPROXIMATE (locality centroid) are NOT: they are what Google returns when it
# cannot find the address you asked for, and quoting on one prices a trip to
# the middle of a neighbourhood.
_PRECISE_LOCATION_TYPES = frozenset({"ROOFTOP", "RANGE_INTERPOLATED"})
# Google's own "I could not match the whole thing" flag.
_PARTIAL_MATCH_QUALITY = "PARTIAL_MATCH"


def _match_quality(result: Dict[str, Any]) -> str:
    """How confident Google is in this geocode. `partial_match` outranks
    location_type: a partial match on a numbered address means the house number
    was ignored, whatever precision the returned point claims.

    Places Text Search results carry neither field; absent both, report
    UNKNOWN and let the caller treat it as unverified rather than precise.
    """
    if result.get("partial_match"):
        return _PARTIAL_MATCH_QUALITY
    return ((result.get("geometry") or {}).get("location_type")) or "UNKNOWN"


# Two POIs this close are the same building for drop-off purposes — a store's
# departments (Walmart Vision Centre, Walmart Wireless) each get their own
# Google listing and their own pin metres apart. Only applied to named-place
# results: street-address geocodes must NOT collapse, neighbouring houses are
# well inside this radius.
_COLOCATED_MAX_KM = 0.075


def _leading_token(name: Optional[str]) -> Optional[str]:
    """First word of a place name, normalized — the brand token shared by a
    store and its in-store departments."""
    tokens = [token for token in re.split(r"[^a-z0-9]+", (name or "").casefold()) if token]
    return tokens[0] if tokens else None


def _collapse_colocated(items: list) -> list:
    """Collapse same-brand POIs sharing one building into one choice.

    Address-string dedupe alone misses these: Google hands a department its
    own listing whose formatted_address can carry a unit/suite token, so the
    keys differ and the rider is offered "Walmart Wireless" and "Walmart
    Vision & Glasses" as if they were two destinations. Requires BOTH
    proximity and a shared leading brand token, so two genuinely different
    businesses sharing a plaza are never merged.

    Keeps the nearest member as the representative, but prefers a name that
    is a prefix of the others ("Walmart" over "Walmart Wireless"). Never
    invents a name that Google did not return.
    """
    groups: list = []
    for item in items:
        _distance, result, lat, lng = item
        token = _leading_token(result.get("name"))
        for group in groups:
            head = group[0]
            if (
                token is not None
                and token == _leading_token(head[1].get("name"))
                and _trip_distance_km(head[2], head[3], lat, lng) <= _COLOCATED_MAX_KM
            ):
                group.append(item)
                break
        else:
            groups.append([item])

    collapsed = []
    for group in groups:
        representative = group[0]  # nearest — ordering is preserved from the caller
        if len(group) > 1:
            names = [str(member[1].get("name") or "") for member in group]
            parent = min(
                (
                    name
                    for name in names
                    if name and all(other.casefold().startswith(name.casefold()) for other in names)
                ),
                key=len,
                default=None,
            )
            if parent and parent != representative[1].get("name"):
                representative = (
                    representative[0],
                    {**representative[1], "name": parent},
                    representative[2],
                    representative[3],
                )
        collapsed.append(representative)
    return collapsed


# How long a quoted trip stays replayable into the next turn. Long enough to
# cover a rider reading the quote and typing "book it"; short enough that a
# stale trip cannot resurface in an unrelated later conversation.
_QUOTE_PIN_TTL_SECONDS = 900


def _quote_pin_key(conversation_id: str) -> str:
    return f"ai:quote:{conversation_id}"


async def _pin_quote(conversation_id: Optional[str], quote: Dict[str, Any]) -> None:
    """Remember the trip we just priced, keyed by conversation.

    Best-effort: a pin failure must never break a working quote, so this
    logs and returns rather than raising. Losing the pin degrades to the old
    re-resolve behaviour, not to a broken quote.
    """
    if not conversation_id:
        return
    try:
        await redis_set(_quote_pin_key(conversation_id), json.dumps(quote), ttl=_QUOTE_PIN_TTL_SECONDS)
    except Exception:
        logger.error("ai quote pin write failed", exc_info=True, extra={"conversation_id": conversation_id})


async def load_pinned_quote(conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The most recent quote priced in this conversation, if still fresh."""
    if not conversation_id:
        return None
    try:
        raw = await redis_get(_quote_pin_key(conversation_id))
    except Exception:
        logger.error("ai quote pin read failed", exc_info=True, extra={"conversation_id": conversation_id})
        return None
    if not raw:
        return None
    try:
        pinned = json.loads(raw)
    except (TypeError, ValueError):
        logger.error("ai quote pin was not valid JSON", extra={"conversation_id": conversation_id})
        return None
    return pinned if isinstance(pinned, dict) else None


async def _candidates_from_results(
    results,
    near_lat: Optional[float] = None,
    near_lng: Optional[float] = None,
    collapse_colocated: bool = False,
) -> list:
    biased = near_lat is not None and near_lng is not None
    parsed = []
    for result in results or []:
        loc = (result.get("geometry") or {}).get("location") or {}
        lat, lng = loc.get("lat"), loc.get("lng")
        if lat is None or lng is None:
            continue
        distance_km = _trip_distance_km(near_lat, near_lng, lat, lng) if biased else None
        parsed.append((distance_km, result, lat, lng))
    if biased:
        # Google orders by its own relevance, which can rank a far same-named
        # match above the one beside the rider (incident: "4325 wakeling st"
        # resolved ~12 km away). Nearest-first before the cut — the same
        # defence as maps_proxy's autocomplete re-sort.
        parsed.sort(key=lambda item: item[0])
    # Text Search commonly returns several departments at one store (for
    # example Walmart, Walmart Pharmacy and Walmart Garden Centre). The rider
    # needs one destination choice per physical address, not duplicate brands.
    unique = []
    seen_addresses = set()
    for item in parsed:
        address = re.sub(r"\s+", " ", str(item[1].get("formatted_address") or "").strip().casefold())
        dedupe_key = address or f"{item[2]:.5f},{item[3]:.5f}"
        if dedupe_key in seen_addresses:
            continue
        seen_addresses.add(dedupe_key)
        unique.append(item)

    # Address-string dedupe only catches departments whose formatted_address
    # matches exactly; same-building listings that differ by a unit token
    # survive it, so named-place results get a second pass on proximity.
    if collapse_colocated:
        unique = _collapse_colocated(unique)

    areas = await _resolve_candidate_areas([(lat, lng) for _distance, _result, lat, lng in unique])
    candidates = []
    for (distance_km, result, lat, lng), area in zip(unique, areas, strict=True):
        candidate = {
            "name": result.get("name"),
            "address": result.get("formatted_address"),
            "lat": lat,
            "lng": lng,
            "in_service_area": area is not None,
            "service_area": (area or {}).get("name"),
        }
        # Google tells us when it guessed; we used to throw that away and hand
        # the model an APPROXIMATE centroid wearing a confident-looking
        # formatted_address (incident: "4321 Wakeling St" — a house number
        # Google lacks — resolved 8.78 km from "4325 Wakeling St").
        quality = _match_quality(result)
        candidate["match_quality"] = quality
        candidate["precise"] = quality in _PRECISE_LOCATION_TYPES
        if distance_km is not None:
            candidate["distance_from_search_km"] = round(distance_km, 1)
        candidates.append(candidate)
    return candidates


def _suggestions_action(query: str, candidates: list, location_role: Optional[str]) -> Dict[str, Any]:
    return {
        "type": "location_suggestions",
        "query": query,
        "location_role": location_role,
        "candidates": candidates[:_PLACE_CANDIDATE_LIMIT],
    }


async def _geocode_with_locality_retry(params: Dict[str, Any], city: Optional[str]) -> Dict[str, Any]:
    """Geocode with a hard `components=locality:<city>` filter when a city is
    known, retrying once without it on ZERO_RESULTS — a mismatched or
    unusually-formatted locality name must degrade to the unfiltered lookup,
    not break the query outright (B7)."""
    if city:
        scoped_params = dict(params)
        scoped_params["components"] = f"locality:{city}|country:CA"
        data = await _maps_get(_GEOCODE_URL, scoped_params)
        await record_call("geocode")
        if data.get("status") != "ZERO_RESULTS":
            return data
    data = await _maps_get(_GEOCODE_URL, params)
    await record_call("geocode")
    return data


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
                # Places API (New) Text Search — locationRestriction here is a
                # RECTANGLE and, unlike the legacy Geocoding/Text-Search bounds
                # param below, a HARD filter: Google cannot return a candidate
                # outside it at all (B5). Falls through to the legacy geocode
                # branch (or an empty result) if nothing matches inside it.
                payload = build_text_search_payload(query, near_lat, near_lng, _PLACE_RADIUS_METERS)
                status_code, data = await _maps_post(
                    PLACES_NEW_TEXT_SEARCH_URL,
                    places_new_headers(api_key, PLACES_NEW_TEXT_SEARCH_FIELD_MASK),
                    payload,
                )
                if status_code != 200:
                    logger.error("ai find_place Places API (New) error: %s", data.get("error") or status_code)
                    return {"error": "place lookup failed — try again or pick the location in the app"}
                await record_call("text_search_new")
                results = legacy_place_results_from_text_search(data)
            else:
                # (No "Places API (New)" equivalent applies here — pure
                # forward-geocoding has no New-API surface; B5 only covers the
                # named-place ["places"] branch above.)
                params = {
                    "address": query,
                    "components": "country:CA",
                    "region": "ca",
                    "key": api_key,
                    "language": "en",
                }
                if near_lat is not None and near_lng is not None:
                    # ~25 km soft-bias box around the rider (matches
                    # _PLACE_RADIUS_METERS). The Geocoding API has no
                    # location/radius bias — bounds is all it offers, and it
                    # is soft, so the nearest-first sort in
                    # _candidates_from_results is the real defence. Without
                    # this, street addresses were geocoded Canada-wide
                    # (incident: a Regina street resolved ~12 km away).
                    dlat = _PLACE_RADIUS_METERS / 111_000
                    dlng = dlat / max(math.cos(math.radians(near_lat)), 0.2)
                    params["bounds"] = f"{near_lat - dlat},{near_lng - dlng}|{near_lat + dlat},{near_lng + dlng}"

                # `components=locality:<city>` is, unlike `bounds`, a HARD
                # filter the Geocoding API cannot ignore — the strongest
                # available fix for cross-city mis-resolution (B7). Only
                # added when the rider's location resolves to a known
                # service area with a populated `city`; an unknown/NULL city
                # degrades to today's unfiltered behavior rather than risk a
                # wrong locality producing ZERO_RESULTS outright.
                city = None
                if near_lat is not None and near_lng is not None:
                    area = await _resolve_area(near_lat, near_lng)
                    city = (area or {}).get("city") or None

                data = await _geocode_with_locality_retry(params, city)
                if data.get("status") not in ("OK", "ZERO_RESULTS"):
                    logger.error("ai find_place maps API error: %s", data.get("status"))
                    return {"error": "place lookup failed — try again or pick the location in the app"}
                results = data.get("results") or []
        except Exception:
            logger.error("ai find_place maps request failed", exc_info=True)
            return {"error": "place lookup failed — try again or pick the location in the app"}

        candidates = await _candidates_from_results(
            results, near_lat=near_lat, near_lng=near_lng, collapse_colocated=(kind == "places")
        )
        if candidates:
            return {"candidates": candidates, "source": kind}

    return {"candidates": []}


async def _rank_named_place_candidates_by_route(
    candidates: list, origin_lat: float, origin_lng: float, api_key: str
) -> tuple[list, bool]:
    """Rank a POI shortlist by Google driving distance from the rider/pickup.

    Places relevance and haversine distance produce the shortlist; road
    topology decides its order.  Directions calls run concurrently so three
    suggestions add one network round trip rather than three.  Unless every
    candidate has a route, ordering stays on the existing proximity fallback;
    an unknown route must not be presented as slower than a known one.
    """

    async def route(candidate: Dict[str, Any]):
        data = await _maps_get(
            _DIRECTIONS_URL,
            {
                "origin": f"{origin_lat},{origin_lng}",
                "destination": f"{candidate['lat']},{candidate['lng']}",
                "mode": "driving",
                "region": "ca",
                "key": api_key,
            },
        )
        await record_call("directions")
        if data.get("status") != "OK" or not data.get("routes"):
            return None
        legs = data["routes"][0].get("legs") or []
        if not legs:
            return None
        distance_m = (legs[0].get("distance") or {}).get("value")
        duration_s = (legs[0].get("duration") or {}).get("value")
        if distance_m is None or duration_s is None:
            return None
        return float(distance_m), float(duration_s)

    results = await asyncio.gather(*(route(candidate) for candidate in candidates), return_exceptions=True)
    routed = 0
    for candidate, result in zip(candidates, results, strict=True):
        if isinstance(result, Exception):
            logger.warning(
                "ai find_place route ranking failed",
                exc_info=(type(result), result, result.__traceback__),
            )
            continue
        if result is None:
            logger.warning("ai find_place route ranking returned no driving route")
            continue
        distance_m, duration_s = result
        candidate["driving_distance_km"] = round(distance_m / 1000, 1)
        candidate["driving_duration_minutes"] = max(1, round(duration_s / 60))
        routed += 1

    if routed != len(candidates):
        return candidates, False
    candidates.sort(
        key=lambda candidate: (
            "driving_distance_km" not in candidate,
            candidate.get("driving_distance_km", float("inf")),
            candidate.get("driving_duration_minutes", float("inf")),
            candidate.get("distance_from_search_km", float("inf")),
        )
    )
    return candidates, True


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


# A last-ride pickup older than this is not a usable "where the rider is"
# hint — falling back to it silently pointed the assistant's pickup at an
# address the rider may not have visited in weeks.
_LAST_RIDE_LOCATION_MAX_AGE_DAYS = 30


def _iso_age_days(value) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400


async def _rider_location_hint(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Rider's best-known location: the device location the app sent with
    this chat turn, else their most recent ride's pickup (only when recent
    enough to plausibly still be "where they are"). Coordinates are
    ephemeral tool data — never logged, never persisted."""
    loc = user.get("_client_location") or {}
    if loc.get("lat") is not None and loc.get("lng") is not None:
        return {"lat": float(loc["lat"]), "lng": float(loc["lng"]), "source": "device"}
    try:
        rows = await db_supabase.get_rows("rides", {"rider_id": user["id"]}, order="created_at", desc=True, limit=1)
    except Exception:
        logger.error("ai rider location hint lookup failed", exc_info=True)
        return None
    if rows and rows[0].get("pickup_lat") is not None and rows[0].get("pickup_lng") is not None:
        as_of = rows[0].get("created_at")
        age_days = _iso_age_days(as_of)
        if age_days is not None and age_days > _LAST_RIDE_LOCATION_MAX_AGE_DAYS:
            return None
        hint = {
            "lat": rows[0]["pickup_lat"],
            "lng": rows[0]["pickup_lng"],
            "source": "last_ride",
            "address": rows[0].get("pickup_address"),
        }
        if as_of:
            hint["as_of"] = as_of
        return hint
    return None


async def get_rider_location(user: Dict[str, Any]) -> Dict[str, Any]:
    hint = await _rider_location_hint(user)
    if not hint:
        return {"error": "no known location for this rider — ask them for a pickup address"}

    result: Dict[str, Any] = {"lat": hint["lat"], "lng": hint["lng"], "source": hint["source"]}
    if hint.get("address"):
        result["address"] = hint["address"]
    elif hint["source"] == "device":
        # Reverse geocode so the model has an address label for the booking
        # card. Budget-gated like every other Maps call; coords still work
        # without it.
        api_key, error = await _places_available()
        if not error:
            try:
                data = await _maps_get(
                    _GEOCODE_URL,
                    {"latlng": f"{hint['lat']},{hint['lng']}", "key": api_key, "language": "en"},
                )
                if data.get("status") == "OK" and data.get("results"):
                    result["address"] = data["results"][0].get("formatted_address")
                    await record_call("geocode")
            except Exception:
                logger.error("ai get_rider_location reverse geocode failed", exc_info=True)
    if hint["source"] == "device":
        result["note"] = "This is a recent fix from the rider's device — confirm the address with them before booking."
    else:
        when = f" (from {str(hint['as_of'])[:10]})" if hint.get("as_of") else ""
        result["note"] = (
            f"This is the pickup of their most recent ride{when} — you MUST confirm "
            "the address with the rider before quoting or booking from it."
        )
        if hint.get("as_of"):
            result["as_of"] = hint["as_of"]
    return result


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

    # No explicit bias from the model → centre the search on the rider's
    # best-known location so "superstore" means THEIR superstore, not one
    # three provinces away.
    bias_source = None
    if near_lat is None or near_lng is None:
        hint = await _rider_location_hint(user)
        if hint:
            near_lat, near_lng = hint["lat"], hint["lng"]
            bias_source = hint["source"]

    lookup = await _lookup_place_candidates(api_key=api_key, query=query, near_lat=near_lat, near_lng=near_lng)
    if lookup.get("error"):
        return {"error": lookup["error"]}
    candidates = lookup.get("candidates") or []
    if not _looks_like_street_address(query):
        candidates = [candidate for candidate in candidates if candidate.get("in_service_area")]
    candidates = candidates[:_PLACE_CANDIDATE_LIMIT]
    if not candidates:
        return {
            "candidates": [],
            "note": (
                "No matching place was found inside Spinr's service area — ask the rider to rephrase, or call "
                "request_map_pin so they get a button to drop a pin on the map."
            ),
        }
    route_ranked = False
    if not _looks_like_street_address(query) and near_lat is not None and near_lng is not None and len(candidates) > 1:
        candidates, route_ranked = await _rank_named_place_candidates_by_route(candidates, near_lat, near_lng, api_key)
    if len(candidates) > 1:
        result = {
            "candidates": candidates,
            "_client_action": _suggestions_action(query, candidates, location_role),
            "note": "Multiple matches — ask the rider which one they mean.",
        }
    else:
        result = {"candidates": candidates}
    if bias_source:
        result["search_biased_by"] = bias_source
    result["ranking_basis"] = "driving_distance" if route_ranked else "straight_line_distance"
    # A biased search whose BEST match still sits outside the bias radius is
    # a red flag (wrong city, mis-typed address). Tell the model so it
    # surfaces the distance to the rider instead of quoting a silent 12 km
    # "same street" trip. APPEND — an ambiguous query whose nearest match is
    # also far needs both instructions, and overwriting dropped the
    # "ask which one they mean" half exactly when it mattered most.
    nearest_km = (candidates[0] or {}).get("distance_from_search_km")
    if nearest_km is not None and nearest_km * 1000 > _PLACE_RADIUS_METERS:
        warning = (
            f"Warning: the closest match is {nearest_km} km from the rider's search area — "
            "confirm the exact address with them before quoting."
        )
        result["note"] = f"{result['note']} {warning}" if result.get("note") else warning

    # The rider named a specific building ("4321 Wakeling St") but Google only
    # produced a street/locality centroid, or admitted a partial match — i.e.
    # it does not have that house number. The point it returned is somewhere in
    # the neighbourhood, not at the address, so quoting on it invents a trip.
    # Only applies to numbered street addresses: a POI search ("Walmart") has
    # no house number to miss, and Places results carry no location_type at all.
    best = candidates[0] or {}
    if _looks_like_street_address(query) and not best.get("precise"):
        imprecise = (
            f"Warning: Google could not pin that exact street address "
            f"(match quality {best.get('match_quality')}) — the coordinate is an approximate "
            "point nearby, not the building. Do NOT quote on it. Ask the rider to confirm the "
            "address (check the house number), or call request_map_pin with this candidate's "
            "coordinates so they can drop a pin at the exact spot — the chat has no map "
            "until you do."
        )
        result["note"] = f"{result['note']} {imprecise}" if result.get("note") else imprecise
        result["imprecise_address"] = True
    return result


async def request_map_pin(
    user: Dict[str, Any],
    location_role: str,
    approx_lat: Optional[float] = None,
    approx_lng: Optional[float] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """Show the rider a 'Drop a pin' button in the chat.

    The chat surface has no map of its own — telling the rider to "drop a pin
    on the map" without this action is a dead-end instruction (incident: the
    imprecise-address refusal kept asking for a pin the UI had no way to
    provide). The button opens the app's pick-on-map screen centred on the
    approximate point; the confirmed pin comes back as the rider's next
    message carrying exact [lat,lng] coordinates.

    Capability-gated: the backend deploys ahead of mobile builds, and an app
    installed before the card shipped renders nothing for this action — the
    assistant would promise a button that isn't there. Clients that can draw
    the card declare "map_pin" in the chat request's capabilities.
    """
    if "map_pin" not in (user.get("_client_capabilities") or ()):
        return {
            "shown": False,
            "note": (
                "This rider's app version cannot show the in-chat map button — do NOT "
                "tell them to drop a pin or mention a map. Ask them to correct the "
                "address in text (exact house number and postal code), or suggest "
                "booking this trip from the app's main booking screen, which has a "
                "map picker."
            ),
        }
    action: Dict[str, Any] = {"type": "open_map_picker", "location_role": location_role}
    if label:
        action["label"] = label[:120]
    if approx_lat is not None and approx_lng is not None:
        action["approx_lat"] = approx_lat
        action["approx_lng"] = approx_lng
    return {
        "shown": True,
        "note": (
            "A 'Drop a pin' button is now visible in the chat. Tell the rider to tap it and "
            "place the pin at the exact spot. Their next message will carry the pin's exact "
            "[lat,lng] coordinates — use them verbatim, never re-geocode them."
        ),
        "_client_action": action,
    }


def _ride_portion(estimate: Dict[str, Any]) -> Decimal:
    """base+dist+time — the only part promos discount (never fees/taxes)."""
    return (
        _money(estimate.get("base_fare", 0))
        + _money(estimate.get("distance_fare", 0))
        + _money(estimate.get("time_fare", 0))
    )


def _best_promo_for(promos: list, portion: Decimal, total: Decimal) -> Optional[Dict[str, Any]]:
    """Pick the promo with the biggest savings for one vehicle quote, using
    the same Decimal math as /promo/available (compute_promo_discount)."""
    try:
        from ..routes.promotions import compute_promo_discount
    except ImportError:
        from routes.promotions import compute_promo_discount

    best_code, best_discount = None, Decimal("0")
    for p in promos or []:
        min_fare = Decimal(str(p.get("min_ride_fare") or 0))
        if min_fare > 0 and portion < min_fare:
            continue  # min-fare eligibility is per vehicle type
        discount = min(compute_promo_discount(p, portion, total), total)
        if discount > best_discount:
            best_code, best_discount = p.get("code"), discount
    if not best_code or best_discount <= 0:
        return None
    return {"code": best_code, "savings": str(_money(best_discount))}


def _trip_distance_km(pickup_lat: float, pickup_lng: float, dropoff_lat: float, dropoff_lng: float) -> float:
    # Lazy dual import (same pattern as _resolve_area) — module-level import of
    # a sibling top-level module is fragile under the absolute-import deploy mode.
    try:
        from ..geo_utils import calculate_distance
    except ImportError:
        from geo_utils import calculate_distance
    return calculate_distance(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)


_STREET_SUFFIX_ALIASES = {
    "street": "st",
    "avenue": "ave",
    "road": "rd",
    "drive": "dr",
    "boulevard": "blvd",
    "crescent": "cres",
    "lane": "ln",
    "court": "crt",
    "place": "pl",
    "highway": "hwy",
    "parkway": "pkwy",
    "terrace": "terr",
}


def _street_key(address: Optional[str]) -> Optional[tuple]:
    """``(street, city)`` for an address, or None when it isn't street-shaped.

    Normalizes so "4325 Wakeling St, Regina, SK S4T 1B2" and
    "4321 wakeling street, Regina" collapse to the same ``("wakeling st",
    "regina")``: drop the leading house number, fold suffix spellings, lowercase,
    strip punctuation. Deliberately simple — this only ever gates a
    *confirmation prompt*, never a silent change, so a miss costs nothing and a
    false positive costs one question.
    """
    if not address:
        return None
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    if len(parts) < 2:
        return None
    street_part, city = parts[0].lower(), parts[1].lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", street_part) if t]
    if not tokens or not tokens[0].isdigit():
        return None  # no house number → not a specific building
    tokens = tokens[1:]  # drop the house number
    if not tokens:
        return None
    tokens = [_STREET_SUFFIX_ALIASES.get(t, t) for t in tokens]
    city = re.sub(r"[^a-z ]+", "", city).strip()
    if not city:
        return None
    return (" ".join(tokens), city)


# Two numbered addresses on the same street in the same city, resolved this far
# apart, means at least one of them geocoded to the wrong place. Residential
# streets do not run for kilometres, and even the longest urban arterials put
# consecutive house numbers nowhere near this distance. Well above the ~250 m
# same-place band so ordinary short same-street trips are unaffected.
_SAME_STREET_MAX_KM = 2.0


def _address_mismatch_refusal(
    pickup_address: Optional[str],
    dropoff_address: Optional[str],
    distance_km: float,
    confirmed: bool,
) -> Optional[Dict[str, Any]]:
    """Refuse to quote when the addresses say "same street" but the coordinates
    say "kilometres apart" — one of them resolved to the wrong point.

    Incident: 4325 Wakeling St → 4321 Wakeling St (adjacent houses) priced as
    8.78 km / 22 min, because Google lacks house number 4321 and returned an
    approximate point elsewhere in the city. Every existing guard passed: the
    same-place check saw 8.78 km, and the road/haversine sanity band only
    validates the route *between* two points, never whether the points are right.
    """
    if confirmed or distance_km <= _SAME_STREET_MAX_KM:
        return None
    pickup_key, dropoff_key = _street_key(pickup_address), _street_key(dropoff_address)
    if not pickup_key or pickup_key != dropoff_key:
        return None
    return {
        "needs_confirmation": "address_mismatch",
        "distance_km": round(distance_km, 2),
        "note": (
            f"'{pickup_address}' and '{dropoff_address}' are on the same street, but the "
            f"coordinates resolved {round(distance_km, 1)} km apart — one of them is almost "
            "certainly wrong, so this fare would be for a trip the rider never asked for. "
            "Do NOT quote it. Tell the rider what you resolved, ask them to confirm the exact "
            "house numbers, and re-resolve with find_place — or call request_map_pin so they "
            "can drop a pin at the exact spot. Only call this tool again with "
            "confirm_same_location=true if they insist the distance is genuinely correct."
        ),
    }


def _same_place_refusal(distance_km: float, confirmed: bool) -> Optional[Dict[str, Any]]:
    """Same-place guardrail: a needs_confirmation result the model must relay,
    or None when the trip is far enough apart (or the rider already confirmed).

    Returned as a normal tool result — not an error — so the model asks the
    rider instead of apologizing for a failure.
    """
    if confirmed or distance_km >= _SAME_PLACE_CONFIRM_KM:
        return None
    return {
        "needs_confirmation": "same_location",
        "distance_meters": int(round(distance_km * 1000)),
        "note": (
            "Pickup and dropoff are essentially the same place — ask the rider "
            "plainly whether they really want this trip (name both addresses). "
            "Only after an explicit yes, call this tool again with "
            "confirm_same_location=true. If they meant a different location "
            "(e.g. another branch of the same store), resolve it with "
            "find_place instead."
        ),
    }


# A dropoff label whose every plausible geocode sits farther than this from
# the claimed pin is describing a different place than the pin. Generous
# enough for POI centroids and big parking lots; far tighter than the
# cross-town drift the incident showed.
_DROPOFF_LABEL_MAX_KM = 1.5

# "50.43500, -104.61000" — the map-pin fallback label. It carries its own
# coordinates, so it is cross-checked numerically instead of geocoded.
_COORD_LIKE_ADDRESS = re.compile(r"^\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$")


def _coordinate_label_tolerance_km(latitude: str, longitude: str) -> float:
    """Return the rounding envelope for a coordinate-formatted label.

    Map-pin fallback labels normally carry five decimal places.  Their pin
    therefore needs to agree within metres, not the kilometre-scale allowance
    used for POI centroids.  The two-metre floor covers harmless floating-point
    and serialization drift while the decimal-derived envelope also handles
    labels rendered at a lower precision.
    """
    lat_step = 10 ** -len(latitude.rsplit(".", 1)[1])
    lng_step = 10 ** -len(longitude.rsplit(".", 1)[1])
    rounding_envelope_km = _trip_distance_km(
        float(latitude),
        float(longitude),
        float(latitude) + lat_step / 2,
        float(longitude) + lng_step / 2,
    )
    return max(0.002, rounding_envelope_km)


def _label_mismatch_refusal(distance_km: float, dropoff_address: str) -> Dict[str, Any]:
    return {
        "needs_correction": "dropoff_label_mismatch",
        "distance_km": round(distance_km, 2),
        "note": (
            f"The dropoff coordinates are {round(distance_km, 1)} km from where "
            f"'{dropoff_address}' actually is — the label and the pin describe two "
            "different places, so this price would be for a trip the rider did not ask "
            "for. Do NOT show this quote or card. Coordinates remembered from earlier "
            "messages belong to earlier trips: re-resolve the dropoff the rider wants "
            "NOW with find_place (or get_saved_places) in this turn, and use that "
            "result's coordinates and address together."
        ),
    }


async def _discard_check(task: "asyncio.Task") -> None:
    """Cancel and drain an overlapped verification whose result no longer
    matters (an earlier guard already refused). Draining prevents 'task was
    destroyed but it is pending' noise; any error it raised is deliberately
    discarded with it."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        # The check failed while being discarded — an earlier guard already
        # refused this trip, so there is nothing actionable; debug-log only.
        logger.debug("discarded dropoff verification failed", exc_info=True)


async def _dropoff_pair_refusal(
    dropoff_lat: float, dropoff_lng: float, dropoff_address: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Refuse when the dropoff label and pin describe two different places.

    Incident: the quote priced the rider's 1.4 km Walmart trip at $7.28; the
    booking card kept the Walmart label but carried Southland Mall
    coordinates recalled from an earlier message — $11.76 to a place the
    rider didn't ask to go, under the right name. Conversation history keeps
    only message text, so each turn must re-resolve; when the model instead
    recycles bracketed coordinates from an older message, the label and pin
    come from different trips. The pickup has _reconcile_pickup; this is the
    dropoff's equivalent check — refuse rather than relocate, because a
    deliberate map pin must never be snapped to a label's centroid.
    """
    if not dropoff_address:
        return None
    coord_label = _COORD_LIKE_ADDRESS.match(dropoff_address)
    if coord_label:
        # The label IS a coordinate pair (map-pin fallback) — no geocode, but
        # its numbers must still agree with the pin being booked: a current
        # pin label glued onto stale coordinates is the same incident.
        label_lat, label_lng = coord_label[1], coord_label[2]
        label_km = _trip_distance_km(dropoff_lat, dropoff_lng, float(label_lat), float(label_lng))
        if label_km <= _coordinate_label_tolerance_km(label_lat, label_lng):
            return None
        return _label_mismatch_refusal(label_km, dropoff_address)
    api_key, availability_error = await _places_available()
    if not api_key:
        # A missing key or exhausted budget makes the pair unverifiable.  Do
        # not disable the stale-coordinate guard in precisely those states.
        return {
            "needs_correction": "dropoff_unverified",
            "note": (availability_error or {}).get("error", "The dropoff could not be verified against its address."),
        }
    lookup = await _lookup_place_candidates(
        api_key=api_key, query=dropoff_address, near_lat=dropoff_lat, near_lng=dropoff_lng
    )
    if lookup.get("error"):
        # Transient Maps failure must fail CLOSED — passing here would wave
        # through exactly the stale pair this guard exists to catch, whenever
        # Google blips. Retryable, and distinct from a mismatch.
        return {
            "needs_correction": "dropoff_unverified",
            "note": (
                "The dropoff could not be verified against its address (maps lookup "
                "failed). Do NOT quote or book this pair yet — retry, or re-resolve "
                "the dropoff with find_place and use that result."
            ),
        }
    candidates = lookup.get("candidates") or []
    if not candidates:
        return None  # genuine ZERO_RESULTS — refusing would block odd-but-real places
    # For a numbered street address, only candidates Google actually pinned
    # (ROOFTOP / RANGE_INTERPOLATED) may vouch for the pair — an APPROXIMATE
    # neighbourhood centroid that happens to sit near a stale pin must not
    # validate it. POI labels resolve via Places (no location_type at all),
    # so the filter applies only when precise candidates exist to prefer.
    if _looks_like_street_address(dropoff_address):
        precise = [c for c in candidates if c.get("precise")]
        if precise:
            candidates = precise
    # Biased near the claimed pin, so if ANY plausible resolution of the label
    # agrees with the pin we pass — this errs against false refusals.
    nearest_km = min(_trip_distance_km(dropoff_lat, dropoff_lng, c["lat"], c["lng"]) for c in candidates)
    if nearest_km <= _DROPOFF_LABEL_MAX_KM:
        return None
    return _label_mismatch_refusal(nearest_km, dropoff_address)


async def get_fare_quote(
    user: Dict[str, Any],
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    pickup_address: Optional[str] = None,
    dropoff_address: Optional[str] = None,
    confirm_same_location: bool = False,
) -> Dict[str, Any]:
    try:
        from ..routes.rides import estimates as _rides_estimates
    except ImportError:
        from routes.rides import estimates as _rides_estimates

    # Dropoff pair verification overlaps the pickup reconcile below — the two
    # are independent Maps calls, so running them concurrently keeps the
    # guard's wall-clock cost near zero on the quote path.
    dropoff_check = asyncio.create_task(_dropoff_pair_refusal(dropoff_lat, dropoff_lng, dropoff_address))

    # Reconcile the pickup exactly like propose_ride_booking does, so the
    # quote card and the confirm card can never be priced on different
    # pickups (incident: the quote used the model's stale coordinate, the
    # card used the reconciled one — $30.92 became $39.44 with no
    # explanation). The device anchor keeps the common "my location" case
    # free of extra Maps traffic.
    pickup_note = None
    if pickup_address:
        (
            pickup_lat,
            pickup_lng,
            pickup_address,
            pickup_in_area,
            pickup_adjusted,
            pickup_drift_km,
        ) = await _reconcile_pickup(
            pickup_lat, pickup_lng, pickup_address, client_location=user.get("_client_location")
        )
        # Same verdict propose_ride_booking enforces. Quoting a pickup the
        # booking step will refuse shows the rider a price and then takes it
        # away — say it once, here, before any number is spoken.
        if not pickup_in_area:
            await _discard_check(dropoff_check)
            return {"error": _OUT_OF_AREA_ERROR}
        if pickup_adjusted:
            pickup_note = (
                f"the pickup pin was moved {pickup_drift_km:.1f} km to match '{pickup_address}' — "
                "tell the rider the exact pickup address"
            )

    trip_km = _trip_distance_km(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
    refusal = _same_place_refusal(trip_km, confirm_same_location) or _address_mismatch_refusal(
        pickup_address, dropoff_address, trip_km, confirm_same_location
    )
    if refusal is None:
        refusal = await dropoff_check
    else:
        await _discard_check(dropoff_check)
    if refusal:
        # A moved pin still has to be disclosed even when the trip is refused —
        # the rider needs to hear which pickup we actually resolved before they
        # answer "yes, same place".
        if pickup_note:
            refusal["pickup_note"] = pickup_note
        return refusal

    try:
        estimate_result = await _rides_estimates.compute_ride_estimates(
            _rides_estimates.RideEstimateRequest(
                pickup_lat=pickup_lat,
                pickup_lng=pickup_lng,
                dropoff_lat=dropoff_lat,
                dropoff_lng=dropoff_lng,
            ),
            user["id"],
            include_polyline=False,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return {
            "error": detail.get("message") or "this trip cannot be quoted",
            "hint": "use get_service_info to list operating cities",
        }

    estimates = estimate_result.get("estimates") or []
    if not estimates:
        return {"error": "no vehicle options are configured for this pickup area"}

    # Best eligible promo, fetched once through the same engine as
    # /promo/available. A promo failure must not kill the quote — surface it
    # in the result instead.
    promos: list = []
    promo_note = None
    try:
        from ..routes.promotions import list_available_promos
    except ImportError:
        from routes.promotions import list_available_promos
    basis = min(
        [e for e in estimates if e.get("available")] or estimates,
        key=lambda e: Decimal(str(e.get("grand_total", 0))),
    )
    try:
        promos = await list_available_promos(
            user["id"],
            ride_fare=float(Decimal(str(basis.get("grand_total", 0)))),
            ride_portion=float(_ride_portion(basis)),
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
        )
    except Exception:
        logger.error("ai get_fare_quote promo lookup failed", exc_info=True)
        promo_note = "promo lookup failed — quote shown without promo savings"

    quotes = []
    unavailable = []
    for e in estimates:
        vt = e.get("vehicle_type") or {}
        if not e.get("available"):
            if vt.get("name"):
                unavailable.append(vt["name"])
            continue
        total = _money(e.get("grand_total", 0))
        promo = _best_promo_for(promos, _ride_portion(e), total)
        quote = {
            "vehicle_type_id": vt.get("id"),
            "vehicle_type": vt.get("name"),
            "capacity": vt.get("capacity"),
            "image_url": vt.get("image_url"),
            "eta_minutes": e.get("eta_minutes"),
            "drivers_nearby": e.get("driver_count"),
            "closest_driver_km": e.get("closest_driver_km"),
            "surge_multiplier": e.get("surge_multiplier"),
            "total": str(total),
            "final_total": str(total - _money(promo["savings"])) if promo else str(total),
            # Labelled line items from the estimate engine (display-only
            # floats) so breakdown questions are answerable pre-booking.
            "breakdown": e.get("fare_breakdown") or [],
        }
        if promo:
            quote["promo_code"] = promo["code"]
            quote["promo_savings"] = promo["savings"]
        quotes.append(quote)

    shared = {
        "distance_km": estimates[0].get("distance_km"),
        "duration_minutes": estimates[0].get("duration_minutes"),
        "currency": "CAD",
        # The exact (post-reconcile) points this quote was priced on. They
        # ride the card so a tapped option sends them back verbatim and the
        # model never re-geocodes a trip the rider already saw priced.
        "pickup_lat": pickup_lat,
        "pickup_lng": pickup_lng,
        "dropoff_lat": dropoff_lat,
        "dropoff_lng": dropoff_lng,
    }
    # Addresses ride along in the card so a tapped option can send a
    # self-contained "Book the X from A to B" message — conversation history
    # keeps only message text, so the next turn must not depend on this
    # turn's tool results (Codex review, PR #1843).
    if pickup_address:
        shared["pickup_address"] = pickup_address
    if dropoff_address:
        shared["dropoff_address"] = dropoff_address

    if not quotes:
        no_drivers = {
            **shared,
            "quotes": [],
            "no_drivers": True,
            "note": (
                "No drivers are available near this pickup right now — tell the rider "
                "plainly and suggest trying again in a few minutes."
            ),
        }
        if pickup_note:
            no_drivers["pickup_note"] = pickup_note
        return no_drivers

    recommended = min(quotes, key=lambda q: Decimal(q["final_total"]))
    # Pin the priced trip for this conversation. Tool results never survive
    # into the next turn, so a rider who TYPES "book it" (instead of tapping
    # the card, whose message carries [lat,lng]) leaves the model with no
    # coordinates — rule 6 then makes it re-resolve the destination, and a
    # fresh Places lookup can land on a different point. Incident: a 15.1 km
    # CA$37.53 Costco quote became CA$40.78 on the booking card, ~1.95 km of
    # drift at the per-km rate. The orchestrator replays this pin into the
    # next turn's prompt so the model books the trip it actually priced.
    await _pin_quote(
        user.get("_conversation_id"),
        {
            "pickup_lat": pickup_lat,
            "pickup_lng": pickup_lng,
            "pickup_address": shared.get("pickup_address") or pickup_address,
            "dropoff_lat": dropoff_lat,
            "dropoff_lng": dropoff_lng,
            "dropoff_address": shared.get("dropoff_address") or dropoff_address,
            "vehicle_type_id": recommended["vehicle_type_id"],
            "vehicle_type": recommended.get("vehicle_type"),
            "total": recommended["final_total"],
            "promo_code": recommended.get("promo_code"),
        },
    )
    result = {
        **shared,
        "quotes": quotes,
        "recommended_vehicle_type_id": recommended["vehicle_type_id"],
        "_client_action": {
            "type": "fare_quote",
            **shared,
            "quotes": quotes,
            "recommended_vehicle_type_id": recommended["vehicle_type_id"],
        },
        "note": (
            "Totals are exact (taxes and fees included); the best eligible promo is "
            "already applied per option — if an option has no promo_code, say plainly "
            "that no promo is currently available. A quote card is now shown to the "
            "rider. Reply in ONE short message: recommended option and final price, "
            "promo savings or 'no promo', trip distance and time, and how close the "
            "nearest driver is. If asked for the fare breakdown before booking, answer "
            "from each option's breakdown lines — never claim breakdowns exist only on "
            "receipts. Then ask if they want to book or see other promo codes."
        ),
    }
    if unavailable:
        result["unavailable_vehicle_types"] = unavailable
    if promo_note:
        result["promo_note"] = promo_note
    if pickup_note:
        result["pickup_note"] = pickup_note
        result["note"] += f" IMPORTANT: {pickup_note}."
    return result


async def _reconcile_pickup(
    pickup_lat: float,
    pickup_lng: float,
    pickup_address: str,
    *,
    client_location: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Re-verify the pickup coordinate against its own address before anyone
    is dispatched. Returns ``(lat, lng, address, in_service_area, adjusted,
    drift_km)`` — ``adjusted`` is True when the supplied coordinate was
    replaced, and callers must then TELL the rider the pin moved.

    The model fills pickup_lat/pickup_lng from conversation context that no
    longer holds the find_place result (history keeps only message text, not
    tool results), so a stale or hallucinated coordinate can travel alongside
    a correct address: the card shows the right street while the driver is
    routed kilometres away.

    Order of trust:
    1. The rider's device fix — a supplied coordinate that agrees with where
       the rider physically is needs no re-geocode at all, and must never be
       snapped to an address centroid: the device knows which side of a
       big-box lot the rider is standing on better than the geocoder does.
    2. The re-geocoded address, biased on the SUPPLIED PICKUP — never the
       dropoff, which once dragged a pickup toward whatever matched near the
       destination — choosing the candidate NEAREST the supplied point, not
       Google's first. A coordinate that already agrees with its address is
       left untouched.
    """
    in_area = await _resolve_area(pickup_lat, pickup_lng) is not None

    device_lat = (client_location or {}).get("lat")
    device_lng = (client_location or {}).get("lng")
    has_device = device_lat is not None and device_lng is not None
    if in_area and has_device:
        device_drift = _trip_distance_km(pickup_lat, pickup_lng, float(device_lat), float(device_lng))
        if device_drift <= _PICKUP_RECONCILE_KM:
            # The rider is physically at (or beside) the supplied point —
            # nothing to verify, and no Maps budget spent.
            return pickup_lat, pickup_lng, pickup_address, True, False, 0.0

    api_key, _error = await _places_available()
    if not api_key:
        # Maps unavailable (no key or budget exhausted) — can't verify; keep
        # the supplied point and let the caller's area check decide.
        return pickup_lat, pickup_lng, pickup_address, in_area, False, 0.0

    near_lat, near_lng = pickup_lat, pickup_lng
    if not in_area and has_device:
        # The supplied point is nowhere we operate — the device fix is the
        # better bias for finding the address the rider means.
        near_lat, near_lng = float(device_lat), float(device_lng)

    lookup = await _lookup_place_candidates(api_key=api_key, query=pickup_address, near_lat=near_lat, near_lng=near_lng)
    in_area_candidates = [c for c in lookup.get("candidates") or [] if c.get("in_service_area")]
    # Never relocate a pin onto a point Google itself won't vouch for: an
    # APPROXIMATE/partial match is a neighbourhood centroid, so "correcting" a
    # real coordinate to it moves the driver somewhere nobody asked for. With
    # no precise candidate we keep what we were given and leave it unadjusted.
    precise_candidates = [c for c in in_area_candidates if c.get("precise")]
    if not precise_candidates:
        return pickup_lat, pickup_lng, pickup_address, in_area, False, 0.0
    geocoded = min(
        precise_candidates,
        key=lambda c: _trip_distance_km(pickup_lat, pickup_lng, c["lat"], c["lng"]),
    )

    drift_km = _trip_distance_km(pickup_lat, pickup_lng, geocoded["lat"], geocoded["lng"])
    if in_area and drift_km <= _PICKUP_RECONCILE_KM:
        return pickup_lat, pickup_lng, pickup_address, True, False, drift_km

    # Out of area, or the address geocodes far from the supplied point — trust
    # the address, which resolves into a service area.
    return geocoded["lat"], geocoded["lng"], geocoded.get("address") or pickup_address, True, True, drift_km


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
    confirm_same_location: bool = False,
    quoted_total: Optional[str] = None,
) -> Dict[str, Any]:
    # The card is the last stop before dispatch — a Walmart label over
    # Southland Mall coordinates must die here even if the quote step was
    # skipped or passed a different pair. Runs concurrently with the pickup
    # reconcile (independent Maps calls); awaited after the free guards.
    dropoff_check = asyncio.create_task(_dropoff_pair_refusal(dropoff_lat, dropoff_lng, dropoff_address))

    pickup_lat, pickup_lng, pickup_address, in_area, pickup_adjusted, pickup_drift_km = await _reconcile_pickup(
        pickup_lat,
        pickup_lng,
        pickup_address,
        client_location=user.get("_client_location"),
    )
    if not in_area:
        await _discard_check(dropoff_check)
        return {"error": _OUT_OF_AREA_ERROR}

    # Guard AFTER reconciliation so it measures the pickup that would actually
    # be dispatched, and on the proposal itself so skipping the quote step
    # cannot bypass it.
    trip_km = _trip_distance_km(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
    refusal = _same_place_refusal(trip_km, confirm_same_location) or _address_mismatch_refusal(
        pickup_address, dropoff_address, trip_km, confirm_same_location
    )
    if refusal is None:
        refusal = await dropoff_check
    else:
        await _discard_check(dropoff_check)
    if refusal:
        return refusal

    proposal = {
        "pickup_lat": pickup_lat,
        "pickup_lng": pickup_lng,
        "pickup_address": pickup_address,
        "dropoff_lat": dropoff_lat,
        "dropoff_lng": dropoff_lng,
        "dropoff_address": dropoff_address,
    }
    if confirm_same_location and trip_km < _SAME_PLACE_CONFIRM_KM:
        # The card passes this through so the client-side proximity guard
        # honours the rider's explicit confirmation at Confirm time.
        proposal["same_location_confirmed"] = True
    if vehicle_type_id:
        proposal["vehicle_type_id"] = vehicle_type_id
    if promo_code:
        proposal["promo_code"] = promo_code
    if scheduled_time:
        proposal["scheduled_time"] = scheduled_time
    if payment_method:
        proposal["payment_method"] = payment_method.lower()
    if quoted_total:
        # Display-only reference: the card compares its fresh estimate against
        # this and shows a "price updated" notice on drift. Never charged —
        # the server prices from the estimate engine. A junk value is dropped
        # rather than failing the proposal.
        try:
            proposal["quoted_total"] = str(_money(quoted_total))
        except Exception:
            logger.warning("ai propose_ride_booking dropped unparseable quoted_total")

    message = (
        "A booking card with the exact fare is now shown to the rider. Ask them to "
        "review it and tap Confirm — do not claim the ride is booked."
    )
    if pickup_adjusted:
        # Never move the pin silently — the incident's confirm screen showed a
        # pickup kilometres from where the rider stood, with no explanation.
        message += (
            f" Note: the pickup pin was moved {pickup_drift_km:.1f} km to match "
            f"'{pickup_address}' — tell the rider the exact pickup address before they confirm."
        )
    return {
        # Lifted out by the orchestrator into an SSE `action` frame; the
        # client renders the native confirmation card from it.
        "_client_action": {"type": "booking_proposal", "proposal": proposal},
        "message": message,
    }


register(
    ToolSpec(
        name="find_place",
        description=(
            "Call this to turn a place the rider names ('downtown Saskatoon', 'the "
            "airport', a street address) into coordinates before quoting or proposing a "
            "ride. For saved places like 'home' or 'work', call get_saved_places instead. "
            "Named-place candidates are ordered closest-first by driving distance from "
            "near_lat/near_lng (or the rider's known location). If multiple candidates "
            "return, show that ordered list and ask the rider to choose."
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
        # Street-address branch: up to two sequential 4 s geocodes plus area
        # resolution — the 5 s global default cut it off mid-lookup.
        timeout_seconds=15.0,
    )
)

register(
    ToolSpec(
        name="request_map_pin",
        description=(
            "Call this whenever you need the rider to mark an exact spot on a map: an "
            "address that only resolved approximately (imprecise_address), an "
            "address_mismatch refusal, or a place search with no match. It shows a "
            "'Drop a pin' button in the chat — the ONLY way the rider can reach a map "
            "from here, so never tell them to drop a pin without calling this. Pass the "
            "best approximate coordinates you have so the map opens near the right area, "
            "and the address they typed as the label. Their next message returns the "
            "pin's exact coordinates."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "location_role": {
                    "type": "string",
                    "enum": ["pickup", "dropoff"],
                    "description": "Which trip endpoint the pin will set.",
                },
                "approx_lat": {
                    "type": "number",
                    "minimum": -90,
                    "maximum": 90,
                    "description": "Approximate latitude to centre the map on (e.g. the imprecise geocode).",
                },
                "approx_lng": {
                    "type": "number",
                    "minimum": -180,
                    "maximum": 180,
                    "description": "Approximate longitude to centre the map on.",
                },
                "label": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "What the rider is pinning, e.g. the address as they typed it.",
                },
            },
            "required": ["location_role"],
        },
        handler=request_map_pin,
        mcp_exposed=False,
    )
)

register(
    ToolSpec(
        name="get_rider_location",
        description=(
            "Call this when the rider says 'from my location', 'near me', 'where I am', "
            "or asks for a ride without giving a pickup. Returns their device location "
            "(if shared with the app) or their most recent ride's pickup, with an "
            "address when known. Confirm it with the rider before booking from it."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_rider_location,
        mcp_exposed=False,
    )
)

register(
    ToolSpec(
        name="get_fare_quote",
        description=(
            "Call this once pickup and dropoff coordinates are known. Returns exact "
            "totals per available vehicle option (taxes, fees and live surge included) "
            "with the best eligible promo already applied, plus ETA and driver "
            "availability — and shows the rider a quote card automatically. Always "
            "pass the resolved pickup_address and dropoff_address so the card's "
            "tap-to-book works. Quote before proposing any booking."
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_COORD_PROPS,
                "pickup_address": {"type": "string", "maxLength": 300},
                "dropoff_address": {"type": "string", "maxLength": 300},
                "confirm_same_location": {
                    "type": "boolean",
                    "description": (
                        "Set true ONLY after the rider explicitly confirms a trip "
                        "whose pickup and dropoff are the same place."
                    ),
                },
            },
            "required": ["pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng"],
        },
        handler=get_fare_quote,
        mcp_exposed=False,
        # Worst case: pickup/dropoff verification each chain two 4 s geocodes
        # (concurrently), then the estimate engine waits 2 s for the road
        # route, plus promo/DB reads — the 5 s global default timed out
        # exactly when a full quote fan-out succeeded slowly.
        timeout_seconds=15.0,
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
                "confirm_same_location": {
                    "type": "boolean",
                    "description": (
                        "Set true ONLY after the rider explicitly confirms a trip "
                        "whose pickup and dropoff are the same place."
                    ),
                },
                "quoted_total": {
                    "type": "string",
                    "maxLength": 16,
                    "description": (
                        "The total from the quote the rider accepted (e.g. '20.92'), "
                        "so the card can warn them if the price has changed since."
                    ),
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
        # vehicle_type_id references a public vehicle-type catalog row, not
        # user-owned data — no ownership check needed.
        public_id_args=frozenset({"vehicle_type_id"}),
        # Same Maps fan-out as get_fare_quote (pickup reconcile + dropoff
        # pair verification run before the card is built).
        timeout_seconds=15.0,
    )
)
