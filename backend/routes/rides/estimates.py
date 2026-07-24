"""Fare/ETA estimates shown to the rider before booking.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Decimal,
    Depends,
    Field,
    HTTPException,
    List,
    Optional,
    Request,
    _metric_timed,
    api_rate_limit,
    asyncio,
    build_fare_breakdown_lines,
    calculate_fare,
    dispatch_geo_bounds,
    get_current_user,
    logger,
    multi_leg_distance,
)
from ._shared import (  # noqa: F401
    _d,
    _f,
    _fetch_directions_route,
    _get_active_service_area_for_point,
    _is_corporate_paid,
    _money_str,
    _round,
    select_fare_distance,
)

# Bounded wait (s) for the Directions road route before the fare loop. The
# fetch is kicked off right after the geofence gates and overlaps the driver +
# fee work, so by this point it is usually already done — this cap only bites
# on a cold/slow Directions call, trading a little estimate latency for a
# correct road-distance price. Haversine mode skips the wait entirely.
_PRICING_ROUTE_WAIT_S = 1.5

# km buckets for the shadow-rollout delta histogram (road vs haversine).
_FARE_DELTA_KM_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)

router = APIRouter()


class RideEstimateRequest(BaseModel):
    pickup_lat: float = Field(..., ge=-90, le=90)
    pickup_lng: float = Field(..., ge=-180, le=180)
    dropoff_lat: float = Field(..., ge=-90, le=90)
    dropoff_lng: float = Field(..., ge=-180, le=180)
    stops: Optional[List[dict]] = None
    # Corporate-billing context — when present, surge is suppressed so the
    # quote shown to the rider matches what the company will be invoiced.
    # Optional for backwards compatibility with consumer-only callers.
    payment_method: Optional[str] = None
    corporate_account_id: Optional[str] = None
    work_profile: Optional[bool] = False
    requires_wav: bool = False


async def _filter_reachable_drivers(all_drivers: list) -> list:
    """Drop ghost drivers from a DB-online pool for rider-facing counts.

    A driver is kept iff their Redis presence key is still alive (heartbeat
    within the TTL) AND their durable intent is online. Mirrors the guard in
    /drivers/nearby and dispatch so the estimate "X drivers" badge matches
    what dispatch can actually reach.

    Redis-outage policy (matches /drivers/nearby):
      * reachable + present set → filter to truly reachable drivers
      * reachable + empty set   → everyone is offline; an empty result is
        correct, not a bug
      * NOT reachable (Redis configured but down) → presence is unknowable,
        so fall back to DB state (still applying intent_online to catch a
        stale is_online column). Dispatch presence-filters before any offer,
        so a ghost that slips through here cannot actually accept a ride.

    Any unexpected error degrades to DB state rather than blanking the count.
    """
    try:
        from ...utils.driver_online import intent_online
        from ...utils.driver_presence import present_driver_ids_checked
    except ImportError:  # pragma: no cover - dual import path
        from utils.driver_online import intent_online  # type: ignore
        from utils.driver_presence import present_driver_ids_checked  # type: ignore

    driver_ids = [d["id"] for d in all_drivers if d.get("id")]
    if not driver_ids:
        return all_drivers

    try:
        present, reachable = await present_driver_ids_checked(driver_ids)
    except Exception as exc:
        logger.warning("[estimate] presence filter error, using DB state: %s", exc)
        return [d for d in all_drivers if intent_online(d)]

    if reachable:
        before = len(all_drivers)
        filtered = [d for d in all_drivers if d.get("id") in present and intent_online(d)]
        logger.info("[estimate] presence filter: %d/%d driver(s) reachable", len(filtered), before)
        return filtered

    # Redis configured but unreachable — keep DB state, log warning.
    filtered = [d for d in all_drivers if intent_online(d)]
    logger.warning(
        "[estimate] presence store unreachable, using DB state (%d drivers after intent_online filter)",
        len(filtered),
    )
    return filtered


async def _track_price_search(rider_id: str, service_area_id: Optional[str]) -> None:
    """Best-effort log of one rider price search for the ops funnel.

    Fire-and-forget: a failure here must never surface to the rider or slow
    the /estimate hot path (P95 < 300 ms). PIPEDA-safe — id + area only, no
    coordinates. See migration 226_price_searches.sql.
    """
    try:
        await _deps.db_supabase.insert_one(
            "price_searches",
            {"user_id": rider_id, "service_area_id": service_area_id},
        )
    except Exception:  # noqa: BLE001 — analytics write, never fatal to the quote
        # error (not warning) so a broken price_searches table/RLS surfaces
        # loudly instead of the funnel silently flatlining at zero. Still
        # swallowed: a tracking failure must never fail or slow a fare quote.
        logger.error("[estimate] price-search tracking write failed", exc_info=True)


async def compute_ride_estimates(
    body: RideEstimateRequest,
    rider_id: str,
    *,
    include_polyline: bool = True,
    track_search: bool = False,
) -> dict:
    """Shared estimate engine behind POST /rides/estimate.

    Single fare path for every quoting surface (rider app, AI assistant
    get_fare_quote): geofence gates, live driver availability/ETA, surge,
    area fees + taxes, and per-vehicle surge-locked estimate tokens.
    Raises HTTPException(400 OUTSIDE_SERVICE_AREA) exactly like the route.
    ``include_polyline=False`` skips the Directions fetch for callers that
    don't render a map (saves a Maps API call and its latency).
    """
    _deps.validate_ride_location(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)
    # Straight-line haversine through any stops — the FALLBACK and sanity
    # reference for the road distance resolved below (just before the fare
    # loop). Never the primary billing distance when a road route is available:
    # crow-flies is always <= the real road distance, so pricing on it
    # systematically undercharges (0.7 km vs 1.8 km road in the reported
    # incident). ``distance_km`` / ``duration_minutes`` are set after the
    # Directions route resolves.
    haversine_km = multi_leg_distance(
        body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng, body.stops
    )

    fares = await _deps.get_fares_for_location(body.pickup_lat, body.pickup_lng)

    # Resolve service area once for fees/taxes — shared across all vehicle-type iterations
    # so calculate_all_fees doesn't re-fetch service_areas N times.
    _est_all_areas = await _deps.db_supabase.get_rows("service_areas", {"is_active": True}, limit=500)

    # Use PostGIS RPC for geofence checks, with a JSON/GeoJSON polygon
    # fallback for service-area rows whose geography column is not synced.
    _est_matched_area = await _get_active_service_area_for_point(
        body.pickup_lat,
        body.pickup_lng,
        _est_all_areas,
    )
    if _est_matched_area:
        logger.info(
            "[estimate] matched service area '%s' for fees",
            _est_matched_area.get("name", _est_matched_area.get("id")),
        )
    else:
        logger.info(
            "[estimate] no service area matched pickup (%.5f, %.5f) — area fees will be empty",
            body.pickup_lat,
            body.pickup_lng,
        )

    # Geofence gates — pickup, dropoff, and every stop must be inside an
    # active service area before we show prices. The lookup combines PostGIS
    # with the admin-visible polygon JSON fallback above. Fail-open when no
    # active areas are configured (DB outage or fresh install).
    if _est_all_areas:
        if _est_matched_area is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "OUTSIDE_SERVICE_AREA",
                    "message": (
                        "Sorry, your pickup location is outside our coverage area. "
                        "Please choose a pickup within a serviced zone."
                    ),
                },
            )
        _dropoff_area = await _get_active_service_area_for_point(
            body.dropoff_lat,
            body.dropoff_lng,
            _est_all_areas,
        )
        if _dropoff_area is None:
            logger.info(
                "[estimate] reject dropoff=(%.5f,%.5f) — outside service areas",
                body.dropoff_lat,
                body.dropoff_lng,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "OUTSIDE_SERVICE_AREA",
                    "message": (
                        "Sorry, your dropoff location is outside our coverage area. "
                        "Please choose a dropoff within a serviced zone."
                    ),
                },
            )
        for idx, stop in enumerate(body.stops or []):
            s_lat, s_lng = stop.get("lat"), stop.get("lng")
            if s_lat is None or s_lng is None:
                continue
            _stop_area = await _get_active_service_area_for_point(s_lat, s_lng, _est_all_areas)
            if _stop_area is None:
                logger.info(
                    "[estimate] reject stop[%d]=(%.5f,%.5f) — outside service areas",
                    idx,
                    s_lat,
                    s_lng,
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "OUTSIDE_SERVICE_AREA",
                        "message": (
                            "Sorry, one of your stops is outside our coverage area. "
                            "Please choose stops within a serviced zone."
                        ),
                    },
                )

    # Resolve app settings once: the Maps key for the Directions call and the
    # ``fare_distance_basis`` mode that decides whether the fare is priced on
    # the road route or straight-line haversine. Read here (after the geofence
    # gates) so a rejected request never spends a settings round-trip or a Maps
    # API call.
    _app_settings = await _deps.get_app_settings()
    _maps_key = (_app_settings or {}).get("google_maps_api_key", "")
    _fare_mode = str((_app_settings or {}).get("fare_distance_basis", "shadow")).lower()

    # Kick off the Directions road-route fetch NOW so its round-trip overlaps
    # the driver/fare work below instead of stacking on top of it (the fare
    # estimate budget is <300ms P95; Directions alone can take seconds). The
    # single call yields both the road distance (for pricing) and the polyline
    # (for the rendered route line). We only need it when the mode isn't the
    # haversine kill-switch, or when a polyline is requested. Never raises —
    # resolves to None on any failure.
    async def _route_fetch() -> Optional[dict]:
        if not _maps_key:
            return None
        try:
            return await _fetch_directions_route(
                body.pickup_lat,
                body.pickup_lng,
                body.dropoff_lat,
                body.dropoff_lng,
                _maps_key,
                waypoints=body.stops or [],
            )
        except Exception as _route_err:
            logger.warning("[estimate] route fetch failed (non-fatal): %s", _route_err)
            return None

    _need_route = include_polyline or _fare_mode != "haversine"
    route_task = _deps.spawn(_route_fetch()) if _need_route else None

    # Fetch nearby online+available drivers once, geo-bounded to a box around
    # the pickup (same dispatch_geo_bounds the dispatch path uses) so the
    # 200-row cap applies to in-area drivers only — the rider's "X drivers"
    # badge must be computed from the same pool dispatch would select from,
    # not an arbitrary province-wide page. Order by went_online_at DESC so
    # recently-toggled-online drivers fill the page first: ghost drivers
    # (is_available=True in DB but heartbeat expired) tend to carry stale
    # went_online_at values and fall toward the tail, so they are less likely
    # to crowd real drivers out of the cap before the presence filter below.
    # Scan cost is bounded by idx_drivers_online_available_recency
    # (migration 138, partial WHERE is_online AND is_available, ordered by
    # went_online_at DESC): the planner walks it in order and the lat/lng box
    # only filters within that online-fleet-sized walk, so the geo predicates
    # need no index of their own (see the dispatch-path note above).
    all_drivers = await _deps.db_supabase.get_rows(
        "drivers",
        {
            "is_online": True,
            "is_available": True,
            # 10 km matches the exact haversine gate in the loop below.
            "$and": dispatch_geo_bounds(body.pickup_lat, body.pickup_lng, 10.0),
        },
        # P1: same base projection as the dispatch pool — never encrypted PII.
        # is_online + went_online_at/went_offline_at are REQUIRED here (the
        # dispatch pool presence-filters directly and does not need them):
        # _filter_reachable_drivers below composes presence with intent_online(d),
        # and intent_online reads exactly those three columns. Omitting them made
        # intent_online see every field as NULL -> bool(None) -> False for every
        # driver, so the estimate dropped the entire pool and every vehicle type
        # showed "No cars available" even with a live, in-range, present driver.
        columns="id,user_id,lat,lng,rating,is_wav,acceptance_rate,destination_mode,destination_lat,destination_lng,vehicle_type_id,is_online,went_online_at,went_offline_at",
        order="went_online_at",
        desc=True,
        limit=200,
    )

    logger.info(
        "[estimate] fetched %d online+available drivers from DB",
        len(all_drivers),
    )

    # Presence filter: strip drivers whose heartbeat has expired (app crashed /
    # network lost) so the rider's "X drivers" badge matches what dispatch can
    # actually reach. See _filter_reachable_drivers for the Redis-outage policy.
    all_drivers = await _filter_reachable_drivers(all_drivers)

    # Filter to drivers within 10km radius and group by vehicle_type_id.
    # Exclude drivers without a user_id — those are orphan/demo rows that
    # cannot be dispatched to, and counting them would inflate the rider's
    # "X drivers available" badge and cause rides to fail at dispatch time.
    from collections import defaultdict

    drivers_by_type = defaultdict(list)
    skipped_reasons: dict = defaultdict(int)
    for d in all_drivers:
        if not d.get("user_id"):
            skipped_reasons["no_user_id"] += 1
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        if not d_lat or not d_lng:
            skipped_reasons["no_lat_lng"] += 1
            continue
        dist = _deps.calculate_distance(body.pickup_lat, body.pickup_lng, d_lat, d_lng)
        if dist > 10.0:
            skipped_reasons["outside_10km"] += 1
            continue
        vt_id = d.get("vehicle_type_id")
        if not vt_id:
            skipped_reasons["no_vehicle_type_id"] += 1
            continue
        drivers_by_type[vt_id].append(
            {
                "driver": d,
                "distance_km": dist,
            }
        )

    if skipped_reasons:
        logger.info("[estimate] skipped drivers: %s", dict(skipped_reasons))
    logger.info(
        "[estimate] matched drivers by vehicle_type: %s",
        {k: len(v) for k, v in drivers_by_type.items()},
    )

    # Check airport surcharge (pickup, dropoff, or any stop in airport sub-region)
    airport_result = await _deps.calculate_airport_fee(
        body.pickup_lat,
        body.pickup_lng,
        body.dropoff_lat,
        body.dropoff_lng,
        stops=body.stops,
    )
    airport_fee = airport_result.get("airport_fee", 0.0)

    # CLAUDE.md: surge does not apply to corporate-paid rides. Resolve the
    # bypass once per request — fares list is per-vehicle, not per-payment.
    corporate_bypass = _is_corporate_paid(
        payment_method=body.payment_method,
        work_profile=body.work_profile,
        corporate_account_id=body.corporate_account_id,
    )

    logger.info(
        "[estimate] fares=%d vehicle_types=%s",
        len(fares),
        [f.get("vehicle_type", {}).get("name", "?") for f in fares],
    )

    # Fix 3: pre-resolve the cascade map once so each vehicle-type iteration can check
    # whether cascade upgrade types have drivers even when the exact type has none.
    # Child areas inherit the parent's map (same pattern as dispatch).
    _est_cascade_map: list = []
    if _est_matched_area:
        _est_cascade_map = _est_matched_area.get("vehicle_cascade_map") or []
        if not _est_cascade_map and _est_matched_area.get("parent_service_area_id"):
            try:
                _est_parent_area = await _deps.db_supabase.find_one(
                    "service_areas", {"id": _est_matched_area["parent_service_area_id"]}
                )
                _est_cascade_map = (_est_parent_area or {}).get("vehicle_cascade_map") or []
            except Exception as _est_casc_exc:
                logger.debug("[estimate] parent cascade map fetch skipped: %s", _est_casc_exc)

    # Resolve the distance the fare is priced on BEFORE the per-vehicle loop.
    # Prefer the Directions road distance (sanity-banded); fall back to
    # haversine when unavailable/implausible or when the mode is
    # haversine/shadow. In road/shadow mode we grant the route a bounded wait
    # so a slightly slower Directions call still yields the road figure; in
    # haversine mode we never block pricing on it (the polyline is still
    # awaited at the end for rendering). The task was started concurrently, so
    # this usually resolves with ~no added latency.
    road_km: Optional[float] = None
    if route_task is not None and _fare_mode != "haversine":
        try:
            _done, _ = await asyncio.wait({route_task}, timeout=_PRICING_ROUTE_WAIT_S)
            if route_task in _done:
                _route = route_task.result()
                if _route:
                    road_km = _route.get("distance_km")
        except Exception as _await_err:  # defensive — _route_fetch traps its own errors
            logger.warning("[estimate] route await failed (non-fatal): %s", _await_err)

    distance_km, distance_basis = select_fare_distance(haversine_km, road_km, mode=_fare_mode)
    # Duration derives from the chosen (road) distance with the standard
    # city-speed model — NOT the Directions ETA. Booking recomputes duration
    # from the same distance with the same formula, so the time_fare charged
    # matches the time_fare quoted; using Directions' ETA here would diverge
    # from booking and over/undercharge the rider on the time component.
    duration_minutes = int(distance_km / 30 * 60) + 5

    # Observability for the shadow->road rollout: the basis actually billed, and
    # (whenever a road distance is known) the km delta vs haversine — the
    # systematic-undercharge signal, which should be strongly one-sided.
    _deps._metric_inc("spinr_fare_distance_basis_total", {"basis": distance_basis})
    if road_km is not None:
        _deps._metric_observe(
            "spinr_fare_distance_delta_km",
            abs(round(float(road_km) - float(haversine_km), 3)),
            None,
            _FARE_DELTA_KM_BUCKETS,
        )
        if _fare_mode == "shadow":
            logger.info(
                "[estimate] shadow fare-distance: haversine=%.3f road=%.3f delta=%.3f (billing haversine)",
                haversine_km,
                road_km,
                road_km - haversine_km,
            )

    estimates = []
    for fare_info in fares:
        surge = Decimal("1.0") if corporate_bypass else _d(fare_info.get("surge_multiplier", 1.0))
        fb = calculate_fare(
            fare_info,
            distance_km,
            duration_minutes,
            surge=surge,
            airport_fee=airport_fee,
        )

        # Calculate area fees + taxes so the rider sees them before booking.
        # Pass the pre-resolved area to avoid a redundant DB fetch per vehicle type.
        fees_result = {}
        try:
            fees_result = await _deps.calculate_all_fees(
                body.pickup_lat,
                body.pickup_lng,
                body.dropoff_lat,
                body.dropoff_lng,
                distance_km,
                _f(fb.total_fare),
                _all_areas=_est_all_areas,
                _matched_area=_est_matched_area,
            )
        except Exception as e:
            logger.error("[estimate] calculate_all_fees failed: %s", e, exc_info=True)

        area_fees_total = fees_result.get("fees_total", 0)
        tax_amount = fees_result.get("tax_amount", 0)
        grand_total = _f(_round(fb.total_fare + _d(area_fees_total) + _d(tax_amount)))

        # Check real driver availability for this vehicle type
        vt_id = fare_info["vehicle_type"].get("id")
        nearby_for_type = drivers_by_type.get(vt_id, [])
        driver_count = len(nearby_for_type)
        is_available = driver_count > 0
        wav_available = sum(1 for entry in nearby_for_type if entry["driver"].get("is_wav"))

        # Calculate ETA: closest driver's distance / avg speed (30km/h in city)
        eta_minutes = None
        closest_driver_km = None
        if nearby_for_type:
            closest = min(nearby_for_type, key=lambda x: x["distance_km"])
            closest_driver_km = round(closest["distance_km"], 1)
            eta_minutes = max(2, int(closest["distance_km"] / 30 * 60) + 1)

        # Fix 3: when no exact-type drivers are nearby, check cascade upgrade types.
        # If cascade drivers exist the vehicle type is still bookable — dispatch will
        # find them — so we must not show it as unavailable.
        if not is_available and _est_cascade_map:
            _est_casc_to = next(
                (rule.get("to") or [] for rule in _est_cascade_map if rule.get("from") == vt_id),
                [],
            )
            for _est_casc_vt_id in _est_casc_to:
                _est_casc_drivers = drivers_by_type.get(_est_casc_vt_id, [])
                if _est_casc_drivers:
                    is_available = True
                    driver_count = len(_est_casc_drivers)
                    _est_casc_closest = min(_est_casc_drivers, key=lambda x: x["distance_km"])
                    closest_driver_km = round(_est_casc_closest["distance_km"], 1)
                    eta_minutes = max(2, int(_est_casc_closest["distance_km"] / 30 * 60) + 1)
                    break

        # P0-4 surge-lock: sign a token per vehicle_type so POST /rides can
        # reuse the surge_multiplier shown here instead of re-reading the
        # service area (which may have changed between estimate + confirm).
        estimate_token = _deps.sign_estimate_token(
            rider_id=rider_id,
            vehicle_type_id=vt_id,
            pickup_lat=body.pickup_lat,
            pickup_lng=body.pickup_lng,
            dropoff_lat=body.dropoff_lat,
            dropoff_lng=body.dropoff_lng,
            surge_multiplier=round(float(surge), 2),
            total_fare=_f(fb.total_fare),
            # Carry the quoted road distance + basis to /rides so booking charges
            # exactly what the rider was shown, not a re-derived haversine.
            distance_km=round(distance_km, 3),
            distance_basis=distance_basis,
        )

        fare_breakdown_lines = build_fare_breakdown_lines(
            fb,
            distance_km=distance_km,
            duration_minutes=duration_minutes,
            area_fees=fees_result.get("fees", []),
            tax_breakdown=fees_result.get("tax_breakdown", {}),
        )

        estimates.append(
            {
                "vehicle_type": fare_info["vehicle_type"],
                "distance_km": round(distance_km, 2),
                "distance_basis": distance_basis,
                "duration_minutes": duration_minutes,
                "base_fare": _money_str(fb.base_fare),
                "distance_fare": _money_str(fb.distance_fare),
                "time_fare": _money_str(fb.time_fare),
                "booking_fee": _money_str(fb.booking_fee),
                "surge_multiplier": round(float(surge), 2),
                "total_fare": _money_str(fb.total_fare),
                "area_fees": fees_result.get("fees", []),
                "area_fees_total": area_fees_total,
                "tax_breakdown": fees_result.get("tax_breakdown", {}),
                "tax_amount": tax_amount,
                "grand_total": grand_total,
                "fare_breakdown": fare_breakdown_lines,
                "available": is_available,
                "eta_minutes": eta_minutes,
                "closest_driver_km": closest_driver_km,
                "driver_count": driver_count,
                "wav_available": wav_available,
                "estimate_token": estimate_token,
            }
        )

    # Collect the road-following polyline started before the driver/fare
    # work. Same for all vehicle types, so fetched once; the rider app uses
    # it to render the gradient route line without a client-side Directions
    # call. Only a short top-up wait is granted here — a slow Directions API
    # must not drag the estimate past its latency budget. On timeout the
    # task is cancelled and the app falls back to straight-line rendering.
    route_polyline = None
    if route_task is not None:
        try:
            _final_route = await asyncio.wait_for(route_task, timeout=0.5)
            route_polyline = _final_route.get("polyline") if _final_route else None
        except asyncio.TimeoutError:
            logger.info("[estimate] route polyline not ready within budget — returning without it (non-fatal)")
        except Exception as _poly_err:  # defensive — _route_fetch traps its own errors
            logger.warning("[estimate] route await failed (non-fatal): %s", _poly_err)

    logger.info(
        "[estimate] returning %d estimates (polyline=%d pts): %s",
        len(estimates),
        len(route_polyline) if route_polyline else 0,
        [(e["vehicle_type"].get("name", "?"), e["available"], e["driver_count"]) for e in estimates],
    )

    # Ops-funnel tracking — count this as a rider "price search" (top of the
    # funnel). Only when called via the rider-app /estimate route (not AI
    # quotes). _deps.spawn (not bare create_task) keeps a strong reference so
    # the task can't be GC'd before the insert lands — same as every other
    # fire-and-forget in this package, and it never touches the latency budget.
    if track_search:
        _est_area_id = _est_matched_area.get("id") if _est_matched_area else None
        _deps.spawn(_track_price_search(rider_id, _est_area_id))

    return {"estimates": estimates, "route_polyline": route_polyline}


@router.post("/estimate")
@api_rate_limit
@_metric_timed("spinr_fare_calc_duration_ms")
async def estimate_ride(
    body: RideEstimateRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    return await compute_ride_estimates(body, current_user["id"], track_search=True)
