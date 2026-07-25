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

import logging
import math
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
    from ..utils.maps_budget import check_budget, record_call
except ImportError:
    import db_supabase
    from settings_loader import get_app_settings
    from utils.maps_budget import check_budget, record_call

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_TEXT_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_HTTP_TIMEOUT = 4.0
_CENT = Decimal("0.01")
_PLACE_RADIUS_METERS = 25000
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


async def _candidates_from_results(
    results,
    near_lat: Optional[float] = None,
    near_lng: Optional[float] = None,
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
    candidates = []
    for distance_km, result, lat, lng in parsed[:3]:
        area = await _resolve_area(lat, lng)
        candidate = {
            "name": result.get("name"),
            "address": result.get("formatted_address"),
            "lat": lat,
            "lng": lng,
            "in_service_area": area is not None,
            "service_area": (area or {}).get("name"),
        }
        if distance_km is not None:
            candidate["distance_from_search_km"] = round(distance_km, 1)
        candidates.append(candidate)
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
                data = await _maps_get(_GEOCODE_URL, params)
                allowed = ("OK", "ZERO_RESULTS")
        except Exception:
            logger.error("ai find_place maps request failed", exc_info=True)
            return {"error": "place lookup failed — try again or pick the location in the app"}

        if data.get("status") not in allowed:
            logger.error("ai find_place maps API error: %s", data.get("status"))
            return {"error": "place lookup failed — try again or pick the location in the app"}

        await record_call("places_text_search" if kind == "places" else "geocode")
        candidates = await _candidates_from_results(data.get("results") or [], near_lat=near_lat, near_lng=near_lng)
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


async def _rider_location_hint(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Rider's best-known location: the device location the app sent with
    this chat turn, else their most recent ride's pickup. Coordinates are
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
        return {
            "lat": rows[0]["pickup_lat"],
            "lng": rows[0]["pickup_lng"],
            "source": "last_ride",
            "address": rows[0].get("pickup_address"),
        }
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
    result["note"] = (
        "This is a recent fix from the rider's device — confirm the address with them before booking."
        if hint["source"] == "device"
        else "This is the pickup of their most recent ride — confirm it's still where they are."
    )
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
    if not candidates:
        return {"candidates": [], "note": "No matching place found — ask the rider to rephrase or pick on the map."}
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
    # A biased search whose BEST match still sits outside the bias radius is
    # a red flag (wrong city, mis-typed address). Tell the model so it
    # surfaces the distance to the rider instead of quoting a silent 12 km
    # "same street" trip.
    nearest_km = (candidates[0] or {}).get("distance_from_search_km")
    if nearest_km is not None and nearest_km * 1000 > _PLACE_RADIUS_METERS:
        result["note"] = (
            f"Warning: the closest match is {nearest_km} km from the rider's search area — "
            "confirm the exact address with them before quoting."
        )
    return result


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
            _in_area,
            pickup_adjusted,
            pickup_drift_km,
        ) = await _reconcile_pickup(
            pickup_lat, pickup_lng, pickup_address, client_location=user.get("_client_location")
        )
        if pickup_adjusted:
            pickup_note = (
                f"the pickup pin was moved {pickup_drift_km:.1f} km to match '{pickup_address}' — "
                "tell the rider the exact pickup address"
            )

    refusal = _same_place_refusal(
        _trip_distance_km(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng),
        confirm_same_location,
    )
    if refusal:
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
    if not in_area_candidates:
        return pickup_lat, pickup_lng, pickup_address, in_area, False, 0.0
    geocoded = min(
        in_area_candidates,
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
    pickup_lat, pickup_lng, pickup_address, in_area, pickup_adjusted, pickup_drift_km = await _reconcile_pickup(
        pickup_lat,
        pickup_lng,
        pickup_address,
        client_location=user.get("_client_location"),
    )
    if not in_area:
        return {"error": "pickup is outside Spinr's service areas — booking is not possible there"}

    # Guard AFTER reconciliation so it measures the pickup that would actually
    # be dispatched, and on the proposal itself so skipping the quote step
    # cannot bypass it.
    trip_km = _trip_distance_km(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
    refusal = _same_place_refusal(trip_km, confirm_same_location)
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
    )
)
