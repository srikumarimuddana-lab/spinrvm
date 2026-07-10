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
    _fetch_directions_polyline,
    _get_active_service_area_for_point,
    _is_corporate_paid,
    _money_str,
    _round,
)

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
    # Price the actual route through any intermediate stops, not the straight
    # pickup→dropoff line — otherwise adding a stop never changes the quote.
    distance_km = multi_leg_distance(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng, body.stops)
    duration_minutes = int(distance_km / 30 * 60) + 5

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

    # Kick off the Directions polyline fetch NOW so its round-trip overlaps
    # the driver/fare work below instead of stacking on top of it (the fare
    # estimate budget is <300ms P95; Directions alone can take seconds).
    # Placed after the geofence gates so rejected requests never spend a
    # Maps API call. Never raises — resolves to None on any failure.
    async def _polyline_fetch() -> Optional[list]:
        try:
            _ps = await _deps.get_app_settings()
            _maps_key = (_ps or {}).get("google_maps_api_key", "")
            if not _maps_key:
                return None
            return await _fetch_directions_polyline(
                body.pickup_lat,
                body.pickup_lng,
                body.dropoff_lat,
                body.dropoff_lng,
                _maps_key,
                waypoints=body.stops or [],
            )
        except Exception as _poly_err:
            logger.warning("[estimate] polyline fetch failed (non-fatal): %s", _poly_err)
            return None

    polyline_task = _deps.spawn(_polyline_fetch()) if include_polyline else None

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
    if polyline_task is not None:
        try:
            route_polyline = await asyncio.wait_for(polyline_task, timeout=0.5)
        except asyncio.TimeoutError:
            logger.info("[estimate] polyline not ready within budget — returning without it (non-fatal)")
        except Exception as _poly_err:  # defensive — _polyline_fetch traps its own errors
            logger.warning("[estimate] polyline await failed (non-fatal): %s", _poly_err)

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
