"""Shared helpers and singletons used across rides submodules.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    ROUND_HALF_UP,
    Decimal,
    DispatchService,
    ErrorCode,
    ErrorKeys,
    InvalidOperation,
    List,
    Optional,
    RideNotFoundException,
    SpinrException,
    _httpx,
    _re,
    get_service_area_polygon,
    logger,
    multi_leg_distance,
    point_in_polygon,
)


def _push_in_background(*args, _ctx: str = "", **kwargs) -> None:
    """Fire an informational push without blocking the request path.

    FCM/Expo round-trips run 100–300 ms; awaiting them inline adds that
    straight onto user-facing latency for pushes that are best-effort by
    design (tip received, rating received, trip shared). Failures are
    logged with ``_ctx`` — never re-raised into the caller. Time-critical
    pushes (dispatch/safety) must NOT use this: they have their own
    retry-queue fallback inside send_push_notification.
    """

    async def _send() -> None:
        try:
            await _deps.send_push_notification(*args, **kwargs)
        except Exception:
            logger.error(f"background push failed ({_ctx})", exc_info=True)

    _deps.spawn(_send())


def _decode_polyline(encoded: str) -> list:
    """Decode a Google encoded polyline string to [[lat, lng], ...] list."""
    coords: list = []
    index = 0
    lat = 0
    lng = 0
    while index < len(encoded):
        for is_lng in (False, True):
            result = 0
            shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("Truncated encoded polyline at index %d" % index)
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 32:
                    break
            value = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += value
            else:
                lat += value
        coords.append([lat / 1e5, lng / 1e5])
    return coords


async def _fetch_directions_route(
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    api_key: str,
    waypoints: Optional[list] = None,
) -> Optional[dict]:
    """Call Google Directions API and return the road route.

    Returns ``{"polyline": [[lat, lng], ...], "distance_km": float | None,
    "duration_s": int | None}`` or ``None`` on any failure — callers must treat
    a ``None`` result (or ``None`` fields) as a soft error and fall back to the
    straight-line ``multi_leg_distance`` haversine path.

    ``distance_km`` / ``duration_s`` are summed across ALL legs so a multi-stop
    route accumulates the full road path (pickup → stop → … → dropoff), not
    just the final leg. This is the authoritative road distance the fare is
    priced on (see ``estimates.py``); haversine is only the fallback when this
    is unavailable. Timeout is 3 s, well within the ride-creation SLA.
    waypoints is an optional list of {lat, lng} stop dicts (multi-stop rides).
    """
    if not api_key:
        return None
    try:
        params: dict = {
            "origin": f"{pickup_lat},{pickup_lng}",
            "destination": f"{dropoff_lat},{dropoff_lng}",
            "key": api_key,
        }
        if waypoints:
            params["waypoints"] = "|".join(f"{w['lat']},{w['lng']}" for w in waypoints)
        async with _httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params=params,
            )
            data = resp.json()
        if data.get("status") != "OK" or not data.get("routes"):
            logger.warning(
                "_fetch_directions_route: status=%s — no route returned",
                data.get("status"),
            )
            return None
        route = data["routes"][0]
        encoded = route.get("overview_polyline", {}).get("points", "")
        if not encoded:
            return None
        pts = _decode_polyline(encoded)
        if len(pts) < 2:
            return None
        # Sum distance/duration across every leg so multi-stop rides accumulate
        # the whole road path. Guard each field: for status=OK legs are always
        # present, but a malformed leg must degrade to the haversine fallback
        # (distance_km=None), never poison the fare with a partial sum.
        legs = route.get("legs") or []
        distance_m = 0
        duration_s = 0
        for leg in legs:
            distance_m += int((leg.get("distance") or {}).get("value") or 0)
            duration_s += int((leg.get("duration") or {}).get("value") or 0)
        return {
            "polyline": pts,
            "distance_km": round(distance_m / 1000.0, 3) if distance_m > 0 else None,
            "duration_s": duration_s if duration_s > 0 else None,
        }
    except Exception as exc:
        logger.warning("_fetch_directions_route failed (non-fatal): %s", exc)
        return None


async def _fetch_directions_polyline(
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    api_key: str,
    waypoints: Optional[list] = None,
) -> Optional[list]:
    """Return only the road-following overview polyline (back-compat shim).

    Thin wrapper over :func:`_fetch_directions_route` for callers that render
    the route line but do not price on it. Callers that need the road distance
    should call ``_fetch_directions_route`` directly. Returns None on any
    failure, same contract as before.
    """
    route = await _fetch_directions_route(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng, api_key, waypoints)
    return route["polyline"] if route else None


# Fare-distance basis selection --------------------------------------------
# Straight-line haversine is ALWAYS <= the real road distance, so pricing a
# fare on it systematically undercharges (0.7 km billed vs 1.8 km road route
# in the reported Wakeling->Walmart incident — a one-sided loss on every ride
# because 100% of the fare goes to the driver). We prefer the Directions road
# distance, guarded by a sanity band so a Maps glitch can't over/undercharge,
# and fall back to haversine when the road distance is missing or implausible.
FARE_ROAD_SANITY_MIN_RATIO = 0.95  # road can't be materially shorter than crow-flies (rounding slack)
FARE_ROAD_SANITY_MAX_RATIO = 3.0  # > 3x straight-line => Directions routed somewhere wrong; distrust it


def _road_distance_plausible(haversine_km: float, road_km: Optional[float]) -> bool:
    """True when ``road_km`` is a believable road distance for this trip.

    Guards against a Maps glitch corrupting the fare: the road route must be at
    least ~as long as the straight line (never materially shorter) and no more
    than 3x it. The degenerate pickup==dropoff case (haversine 0) trusts only a
    small positive road distance.
    """
    if road_km is None or road_km <= 0:
        return False
    if haversine_km <= 0:
        return road_km <= 1.0
    ratio = road_km / haversine_km
    return FARE_ROAD_SANITY_MIN_RATIO <= ratio <= FARE_ROAD_SANITY_MAX_RATIO


def select_fare_distance(haversine_km: float, road_km: Optional[float], *, mode: str) -> tuple:
    """Choose the distance (km) a fare is priced on and the basis label.

    Returns ``(billed_km, basis)``. ``mode`` is the ``fare_distance_basis``
    app setting:

      - ``"road"``      price on the road distance when present + inside the
                        sanity band, else haversine (``"haversine_fallback"``).
      - ``"shadow"``    always bill haversine (no money change), but the caller
                        still fetches the road distance to log the delta — used
                        to de-risk the rollout before flipping to ``"road"``.
      - ``"haversine"`` kill switch — legacy straight-line behaviour.

    basis is one of: ``"road_route"``, ``"haversine_fallback"``, ``"haversine"``.
    """
    hv = round(float(haversine_km), 3)
    if mode == "road" and _road_distance_plausible(hv, road_km):
        return round(float(road_km), 3), "road_route"
    if mode == "road":
        # Road distance unavailable or implausible — bill haversine, but flag it
        # so reconciliation/ops can see the road route was missing for this ride.
        return hv, "haversine_fallback"
    # "shadow" and "haversine" both bill on haversine (shadow logs the delta
    # separately in the caller). Unknown modes degrade to the safe legacy path.
    return hv, "haversine"


def resolve_booking_distance(haversine_km: float, token_payload: Optional[dict]) -> tuple:
    """Distance/duration/basis a booking should charge, honoring the token.

    /estimate signs the quoted road distance into the estimate token (``dk``,
    with basis ``db``). When that token comes back on /rides, the booking
    charges *that exact distance* — matching what the rider was shown — instead
    of re-deriving straight-line haversine (the bug that billed 0.7 km for a
    1.8 km road route). Duration is recomputed from the chosen distance with the
    same city-speed model /estimate uses, so the quoted and charged time_fare
    agree. Falls back to haversine when the token has no distance (older
    clients, no token, or an estimate that itself fell back to haversine).

    Returns ``(distance_km, duration_minutes, distance_basis)``.
    """
    hv = round(float(haversine_km), 3)
    dk = (token_payload or {}).get("dk")
    if dk is not None and float(dk) > 0:
        km = round(float(dk), 3)
        basis = str((token_payload or {}).get("db") or "road_route")
        return km, int(km / 30 * 60) + 5, basis
    return hv, int(hv / 30 * 60) + 5, "haversine"


# Below this booked distance a ride is more likely a wrong dropoff coordinate
# than a real hop (the incident booked 0.7 km for a 1.8 km road route).
MIN_PLAUSIBLE_BOOKED_KM = 0.3


def booked_distance_suspect_reason(
    distance_km: float, distance_basis: str, *, floor_km: float = MIN_PLAUSIBLE_BOOKED_KM
) -> Optional[str]:
    """Why a booked distance looks suspect, or None. Detection only — never blocks.

    ``"below_floor"``      implausibly short (likely a bad dropoff coordinate).
    ``"road_route_unavailable"``  the road route couldn't be fetched, so the
                           fare fell back to straight-line and may undercharge.
    """
    try:
        if float(distance_km) < floor_km:
            return "below_floor"
    except (TypeError, ValueError):
        return None
    if distance_basis == "haversine_fallback":
        return "road_route_unavailable"
    return None


async def _get_active_service_area_for_point(
    lat: float,
    lng: float,
    active_areas: List[dict],
) -> Optional[dict]:
    """Return an active service area containing a point.

    Prefer the PostGIS RPC when its area geography column is populated, but
    fall back to the JSON/GeoJSON polygon column used by the admin dashboard.
    Some production rows have polygon coverage without a synced geography value;
    the fallback keeps estimate geofence checks aligned with what admins see.
    """
    matched_area = await _deps.db_supabase.get_service_area_for_point(lat, lng)
    if matched_area and matched_area.get("is_active", True) is not False:
        return matched_area

    for area in active_areas or []:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(lat, lng, poly):
            return area
    return None


async def _require_ride_in_state_rider(ride_id: str, rider_id: str, allowed_states: tuple) -> dict:
    """Load a rider's ride only if it is in one of allowed_states.

    Raises 409 if the ride exists but is in the wrong state.
    Raises 404 if the ride doesn't exist or isn't owned by this rider.
    """
    ride = await _deps.db.find_one(
        "rides",
        {"id": ride_id, "rider_id": rider_id, "status": {"$in": list(allowed_states)}},
    )
    if ride:
        return ride
    existing = await _deps.db.find_one("rides", {"id": ride_id, "rider_id": rider_id})
    if existing:
        current = existing.get("status", "unknown")
        raise SpinrException(
            message=f"Ride is in status '{current}'; cannot perform this action from that state (allowed: {list(allowed_states)}).",
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=409,
            details={"current_status": current, "allowed": list(allowed_states)},
            message_key=ErrorKeys.RIDE_INVALID_STATUS,
        )
    raise RideNotFoundException(
        ride_id=ride_id,
        message_key=ErrorKeys.RIDE_NOT_FOUND,
    )


dispatch = DispatchService(_deps.db_supabase)  # module-level instance for legacy call sites

# ── Decimal helpers for accurate currency arithmetic ──────────────────────────
_TWO_PLACES = Decimal("0.01")


def _d(v) -> Decimal:
    """Convert any numeric value to Decimal safely (avoids float precision loss)."""
    return Decimal(str(v))


def _round(v: Decimal) -> Decimal:
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


async def _reestimate_fare_for_stops(ride: dict, new_stops: list) -> dict:
    """Recalculate distance, duration, fare, taxes, and earnings after a stop mutation.

    Derives per-km and per-minute rates from the values already stored on the
    ride row (distance_fare / (distance_km * surge) and time_fare / (duration *
    surge)), then applies them to the new multi-leg distance.  Recomputes area
    fees, taxes, grand_total, driver/admin earnings, and refreshes the
    fare_breakdown_snapshot so settlement charges exactly what the rider sees.
    Returns a dict suitable for merging into a $set update.
    """
    # Multi-leg distance pickup → stops → dropoff (shared with /rides/estimate).
    new_distance_km = multi_leg_distance(
        ride["pickup_lat"],
        ride["pickup_lng"],
        ride["dropoff_lat"],
        ride["dropoff_lng"],
        new_stops,
    )
    new_duration_minutes = max(5, int(new_distance_km / 30 * 60) + 5)

    surge = _d(ride.get("surge_multiplier", 1.0)) or _d(1)
    old_dist = _d(ride.get("distance_km") or 1)
    old_dur = _d(ride.get("duration_minutes") or 1)

    per_km_effective = _d(ride.get("distance_fare", 0)) / (old_dist * surge)
    per_min_effective = _d(ride.get("time_fare", 0)) / (old_dur * surge)

    new_distance_fare = _round(per_km_effective * _d(new_distance_km) * surge)
    new_time_fare = _round(per_min_effective * _d(new_duration_minutes) * surge)
    booking_fee = _d(ride.get("booking_fee", 0))
    new_total = _round(_d(ride.get("base_fare", 0)) + new_distance_fare + new_time_fare + booking_fee)

    admin_earnings = _round(booking_fee + _d(ride.get("airport_fee", 0) or 0))
    driver_earnings = _round(new_total - admin_earnings)

    fees_result = await _deps.calculate_all_fees(
        ride["pickup_lat"],
        ride["pickup_lng"],
        ride["dropoff_lat"],
        ride["dropoff_lng"],
        round(new_distance_km, 2),
        float(new_total),
    )
    fees_total = _d(fees_result.get("fees_total", 0))
    tax_amount = _d(fees_result.get("tax_amount", 0))
    grand_total = _round(new_total + fees_total + tax_amount)

    result = {
        "distance_km": round(new_distance_km, 2),
        "duration_minutes": new_duration_minutes,
        "distance_fare": _money_str(new_distance_fare),
        "time_fare": _money_str(new_time_fare),
        "estimated_fare": _money_str(new_total),
        "total_fare": _money_str(new_total),
        "grand_total": _money_str(grand_total),
        "tax_amount": float(_round(tax_amount)),
        "tax_breakdown": fees_result.get("tax_breakdown", {}),
        "area_fees_total": float(_round(fees_total)),
        "area_fees_breakdown": fees_result.get("fees", []),
        "driver_earnings": _money_str(driver_earnings),
        "admin_earnings": _money_str(admin_earnings),
    }

    virtual_ride = {**ride, **result}
    snapshot_lines = _build_fare_breakdown(virtual_ride)
    result["fare_breakdown_snapshot"] = {
        "lines": snapshot_lines,
        "grand_total": float(_round(grand_total)),
        "updated_at": _deps.datetime.now(_deps.timezone.utc).isoformat(),
    }

    return result


def _f(v: Decimal) -> float:
    """Convert Decimal to float.

    Reserved for legacy callers that genuinely need a float — Pydantic field
    coercion (Ride model takes float for ``surge_multiplier``), signed-token
    payloads (``sign_estimate_token`` / ``verify_estimate_token``), and
    internal helpers like ``calculate_all_fees`` whose contract is float.

    For JSON wire responses use ``_money_str`` instead — Audit-17 P0-1
    mandates that money fields cross the wire as decimal strings, never
    IEEE-754 floats. See CLAUDE.md § Critical Conventions ("Money arithmetic").
    """
    return float(v)


def _money_str(v: Decimal) -> str:
    """Quantize ``v`` to 2 decimal places and emit as a JSON-safe string.

    Use on every money-shaped value placed into a dict response or
    WebSocket payload. Pydantic response models already route money through
    ``DecimalStr``; this helper covers the remaining hand-built dict
    responses where there is no schema between us and the wire.
    """
    return str(_round(_d(v)))


_HOUSE_NUM_RE = _re.compile(
    r"^\s*#?\d[\d\w/-]*[\s,]+",
)


def _truncate_address(addr: str | None) -> str | None:
    """Strip house/unit numbers from an address, keeping street and city.

    '123 Main Street, Saskatoon, SK' → 'Main Street, Saskatoon, SK'
    '#4-567 Broadway Ave, Regina'    → 'Broadway Ave, Regina'
    """
    if not addr:
        return addr
    truncated = _HOUSE_NUM_RE.sub("", addr, count=1)
    return truncated if truncated else addr


def _redact_driver_location_fields(ride: dict) -> None:
    """Redact addresses to street-level and coordinates to ~110m for drivers."""
    for key in ("pickup_address", "dropoff_address"):
        if key in ride:
            ride[key] = _truncate_address(ride[key])
    for key in ("pickup_lat", "dropoff_lat", "pickup_lng", "dropoff_lng"):
        val = ride.get(key)
        if val is not None:
            try:
                ride[key] = round(float(val), 3)
            except (ValueError, TypeError):
                pass


# Minimum billed-vs-measured divergence before the receipt line says "booked".
# Below this the two round to visually identical values and the suffix is noise.
_BOOKED_DISTANCE_DIVERGENCE_KM = 0.5


def relabel_booked_distance_lines(lines: list, ride: dict) -> list:
    """Mark the frozen ride-fare line as booked-distance when GPS diverged.

    Under fare-lock the served breakdown is the booking-time snapshot, whose
    "Ride fare (X km)" label was built from the straight-line booking
    estimate — while the stats tile shows the GPS-measured distance. When the
    two diverge by more than the threshold, relabel the served line to
    "Ride fare (X km booked)" so the receipt states its own basis instead of
    silently contradicting the measured value. Served representation only:
    the stored snapshot stays frozen and no amount is ever modified.
    """
    booked_km = ride.get("planned_distance_km")
    actual_km = ride.get("actual_distance_km")
    if booked_km is None or actual_km is None:
        return lines
    try:
        if abs(float(actual_km) - float(booked_km)) <= _BOOKED_DISTANCE_DIVERGENCE_KM:
            return lines
        booked_label_km = round(float(booked_km), 1)
    except (TypeError, ValueError):
        return lines
    relabeled = []
    for line in lines:
        if line.get("type") == "ride":
            line = dict(line)
            line["label"] = f"Ride fare ({booked_label_km} km booked)"
        relabeled.append(line)
    return relabeled


def _actual_duration_minutes(ride: dict) -> int | None:
    """Derive the actual trip-in-progress duration in whole minutes.

    Preferred source is ``ride_metrics.phases.trip_in_progress.actual_duration_minutes``
    (migration 89), assembled at completion from ride timestamps. Falls back
    to the GPS-derived ``phase_durations`` (migration 15) for rides completed
    before migration 89 landed. Returns None when neither source is populated
    so callers can fall back to the booking-time estimate.
    """
    trip_phase = ((ride.get("ride_metrics") or {}).get("phases") or {}).get("trip_in_progress") or {}
    persisted = trip_phase.get("actual_duration_minutes")
    if isinstance(persisted, (int, float)) and persisted > 0:
        return int(persisted)
    phase = (ride.get("phase_durations") or {}).get("trip_in_progress")
    if phase is None:
        return None
    try:
        secs = float(phase)
    except (TypeError, ValueError):
        return None
    if secs <= 0:
        return None
    return max(1, int(round(secs / 60)))


def _sum_fare_breakdown(lines: list[dict]) -> float:
    """Sum the numeric `amount` of every fare_breakdown line.

    This IS the rider's bill — the same number the receipt UI computes by
    summing the rendered items. Modifier rows (e.g. surge multiplier) carry
    amount=None and are skipped. Summed in Decimal (HALF_UP) so the returned
    grand_total always equals the exact sum of the line items shown; result
    is rounded to cents and clamped at 0.
    """
    total = Decimal("0")
    for line in lines or []:
        amt = line.get("amount") if isinstance(line, dict) else None
        if amt is None:
            continue
        try:
            total += _d(amt)
        except (TypeError, ValueError, InvalidOperation):
            continue
    return _f(_round(max(Decimal("0"), total)))


def _build_fare_breakdown(ride: dict) -> list[dict]:
    """Build a dynamic fare_breakdown list from ride fields.

    Reused by get_ride and get_ride_history so every surface sees the same
    line-item structure.
    """
    lines: list[dict] = []
    base = _d(ride.get("base_fare", 0))
    dist_surged = _d(ride.get("distance_fare", 0))
    time_surged = _d(ride.get("time_fare", 0))
    booking = _d(ride.get("booking_fee", 0) or 0)
    airport = _d(ride.get("airport_fee", 0) or 0)
    surge = _d(ride.get("surge_multiplier") or 1)

    # Minimum-fare uplift: when total_fare was clamped up to the floor at
    # booking, the amount above the component sum is the driver's (0%
    # commission). Fold it into the ride-fare line so the shown items sum to
    # the amount actually charged and the ride line equals the driver's share
    # (total − booking − airport). Legacy rows without total_fare → 0 uplift.
    components = base + dist_surged + time_surged + booking + airport
    total_fare = _d(ride["total_fare"]) if ride.get("total_fare") not in (None, "") else components
    uplift = max(Decimal("0"), total_fare - components)

    # C4: disclose surge as a real dollar line on the actual bill surfaces
    # (receipt / history / admin), matching the estimate — it was amount:None
    # here. surge multiplies only distance+time; the pre-surge ride fare + the
    # surge delta sum to the same total. Cap the delta at what surge actually
    # added: if the minimum fare clamped the surged fare, surge contributed $0.
    surge_delta = Decimal("0")
    if surge > Decimal("1"):
        surged_dt = dist_surged + time_surged
        unsurged_dt = _round(surged_dt / surge)
        min_clamped = uplift > Decimal("0.005")
        surge_delta = Decimal("0") if min_clamped else _round(surged_dt - unsurged_dt)

    ride_fare_d = base + dist_surged + time_surged - surge_delta + uplift
    if ride_fare_d > 0:
        dist_km = round(float(ride.get("distance_km") or 0), 1)
        lines.append({"label": f"Ride fare ({dist_km} km)", "amount": _f(_round(ride_fare_d)), "type": "ride"})
    if airport > 0:
        lines.append({"label": "Airport surcharge", "amount": ride["airport_fee"], "type": "fee"})
    if booking > 0:
        lines.append({"label": "Booking fee", "amount": ride["booking_fee"], "type": "fee"})
    if surge > Decimal("1"):
        lines.append({"label": f"Surge ({float(surge)}×)", "amount": _f(_round(surge_delta)), "type": "modifier"})
    for af in ride.get("area_fees_breakdown") or []:
        afv = af.get("calculated_value", 0)
        if float(afv) > 0:
            lines.append({"label": af.get("name", "Fee"), "amount": afv, "type": "fee"})
    for tax_name, tax_info in (ride.get("tax_breakdown") or {}).items():
        if tax_info.get("amount", 0) > 0:
            rate = tax_info.get("rate", 0)
            lbl = f"{tax_name} ({rate}%)" if rate else tax_name
            lines.append({"label": lbl, "amount": tax_info["amount"], "type": "tax"})
    if ride.get("discount_amount") and float(ride["discount_amount"]) > 0:
        promo_label = f"Promo ({ride['promo_code']})" if ride.get("promo_code") else "Promo discount"
        # Promos apply to ride fare (driver earnings) only — never to fees or
        # taxes. Cap the displayed discount at ride_fare so legacy rides with
        # an uncapped discount_amount still render a sane breakdown.
        raw_discount = float(ride["discount_amount"])
        # Cap against the full (surged) ride fare including any minimum-fare
        # uplift — the driver's 100%-share base, which the promo discounts —
        # not the pre-surge line shown above.
        ride_fare = float(base + dist_surged + time_surged + uplift)
        capped_discount = min(raw_discount, ride_fare) if ride_fare > 0 else raw_discount
        lines.append({"label": promo_label, "amount": -capped_discount, "type": "discount"})
    if ride.get("tip_amount") and float(ride["tip_amount"]) > 0:
        lines.append({"label": "Tip", "amount": ride["tip_amount"], "type": "tip"})
    return lines


def _is_corporate_paid(
    *,
    payment_method: Optional[str],
    work_profile: Optional[bool],
    corporate_account_id: Optional[str],
) -> bool:
    """True when the ride will be settled against a corporate account.

    Surge does not apply to corporate-paid rides (CLAUDE.md Surge rules:
    "Surge does not apply to corporate account-paid rides"). The caller
    needs the answer before fare arithmetic so the multiplier can be
    pinned to 1.0× before distance/time fares are computed.

    Two booking shapes route to corporate billing:
      1. ``payment_method == "company_allowance"`` — the rider explicitly
         picked Company Allowance.
      2. ``work_profile=True`` with a ``corporate_account_id`` — the
         rider toggled Work mode; rides.py:907 reclassifies these to
         ``payment_method="company_allowance"`` at persist time.
    Both paths must bypass surge before the fare is locked in, otherwise
    the rider would see the surged estimate and the company would be
    billed for it.
    """
    if not corporate_account_id:
        return False
    if (payment_method or "").lower() == "company_allowance":
        return True
    if work_profile:
        return True
    return False


def _rider_visible_photo(user: Optional[dict]) -> Optional[str]:
    """Driver avatar shown to RIDERS, gated on moderation status.

    Driver photos upload as 'pending_review' and must be admin-approved before
    riders see them (identity/safety). 'pending_review'/'rejected' → hidden.
    Legacy photos with no status are treated as visible.
    """
    if not user:
        return None
    if user.get("profile_image_status") in ("pending_review", "rejected"):
        return None
    return user.get("profile_image")
