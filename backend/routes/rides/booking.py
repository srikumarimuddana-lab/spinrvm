"""Ride creation: preauthorization, insert, dispatch kickoff.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps, matching
from ._deps import (  # noqa: F401
    ROUND_HALF_UP,
    APIRouter,
    CreateRideRequest,
    Decimal,
    Depends,
    ErrorCode,
    ErrorKeys,
    EstimateTokenError,
    HTTPException,
    Optional,
    Request,
    Ride,
    RideStatus,
    SpinrException,
    calculate_fare,
    dataclass,
    dataclass_field,
    datetime,
    db_error_text,
    generate_pickup_otp,
    generate_ride_code,
    get_current_user,
    idempotent_endpoint,
    log_user_action,
    logger,
    multi_leg_distance,
    pg_error_code,
    ride_request_limit,
    timezone,
    verify_estimate_token,
)
from ._shared import (  # noqa: F401
    _build_fare_breakdown,
    _d,
    _f,
    _fetch_directions_polyline,
    _fetch_directions_route,
    _get_active_service_area_for_point,
    _is_corporate_paid,
    _round,
    _sum_fare_breakdown,
    booked_distance_suspect_reason,
    resolve_booking_distance,
    select_fare_distance,
)

router = APIRouter()


# Decline codes where re-authorizing at a LOWER amount (fare without the buffer)
# can still succeed — the card is fundable, the buffer just tipped a thin balance
# over. Any other decline (lost/stolen/generic) is the card itself being bad, so
# a smaller hold won't help and we block instead of retrying.
_RETRYABLE_AT_LOWER_AMOUNT = frozenset({"insufficient_funds"})


@dataclass
class _PreauthOutcome:
    """Result of a booking-time card pre-authorization attempt.

    ``fields`` are merged into ``ride_data`` (empty when no hold was placed).
    ``requires_action`` signals the rider-app must complete an on-device SCA /
    Apple Pay confirm for ``client_secret`` and then re-book passing
    ``payment_intent_id`` back as ``preauthorized_payment_intent_id``.
    """

    fields: dict = dataclass_field(default_factory=dict)
    requires_action: bool = False
    client_secret: Optional[str] = None
    payment_intent_id: Optional[str] = None


def _card_declined_result(decline_code: Optional[str], block_on_decline: bool) -> _PreauthOutcome:
    """Terminal decline of a pre-auth hold. At interactive booking
    (``block_on_decline=True``) raise 402 so the rider fixes their card before a
    driver is disturbed. At scheduled dispatch (``block_on_decline=False``) the
    rider is not present, so degrade to no hold and let post-trip settlement +
    the retry/unpaid-block safety net handle it — never strand a scheduled ride.
    """
    if not block_on_decline:
        return _PreauthOutcome()
    raise HTTPException(
        status_code=402,
        detail={
            "code": "CARD_DECLINED",
            "message": "Your card was declined. Please update your payment method and try booking again.",
            "decline_code": decline_code,
        },
    )


async def _attach_preauthorized_hold(
    *,
    ride_id: str,
    rider_id: str,
    payment_intent_id: str,
    stripe_customer_id: Optional[str],
    min_amount: Decimal,
) -> dict:
    """Verify a PaymentIntent the rider-app already confirmed on-device (SCA /
    Apple Pay two-step) and return the ride fields to persist. A failed/abandoned
    authentication raises 402 so the rider re-tries — we never attach an
    unconfirmed hold, and we re-read the PI from Stripe rather than trusting the
    client. ``authorized_amount`` is taken from Stripe (the true held amount).

    SECURITY: the PI must (a) belong to this rider's Stripe customer, (b) hold at
    least the ride fare, and (c) not already be attached to another ride — so a
    client cannot attach someone else's hold, replay a cheaper one, or reuse a PI
    across rides."""
    # Fail CLOSED: ownership is verified against the rider's Stripe customer, so
    # we cannot attach a client-supplied PI when we have no customer to check it
    # against (e.g. a crafted work_profile card request). Reject rather than skip.
    if not stripe_customer_id:
        logger.error("[preauth][security] no Stripe customer to verify PI ownership for ride=%s", ride_id)
        raise HTTPException(
            status_code=402,
            detail={"code": "CARD_DECLINED", "message": "No payment method on file. Please add a card first."},
        )

    # (c) Reuse guard: a PI already on another ride must not be re-attached.
    try:
        existing = await _deps.db_supabase.get_rows(
            "rides",
            {"payment_intent_id": payment_intent_id},
            limit=1,
            columns="id",
        )
    except Exception as e:
        # Fail OPEN here is acceptable: ownership + amount are still enforced
        # against Stripe below, so a degraded reuse-lookup can't bypass security.
        logger.error("[preauth] PI-reuse lookup failed for ride=%s pi=%s: %s", ride_id, payment_intent_id, e)
        existing = []
    if existing and existing[0].get("id") != ride_id:
        logger.error("[preauth][security] PI already attached to ride=%s (declining)", existing[0].get("id"))
        raise HTTPException(
            status_code=402,
            detail={
                "code": "CARD_DECLINED",
                "message": "This authorization can't be reused. Please try booking again.",
            },
        )

    min_cents = int((_round(_d(min_amount)) * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))
    outcome = await _deps.verify_authorization(
        ride_id=ride_id,
        payment_intent_id=payment_intent_id,
        expected_customer_id=stripe_customer_id,
        min_amount_cents=min_cents,
    )
    if outcome.status == "authorized":
        return {
            "payment_intent_id": outcome.payment_intent_id,
            "authorized_amount": _f(outcome.charged_amount),
            "auth_status": "authorized",
        }
    if outcome.status == "declined":
        logger.info("[preauth] on-device auth not completed for ride=%s pi=%s", ride_id, payment_intent_id)
        raise HTTPException(
            status_code=402,
            detail={
                "code": "CARD_DECLINED",
                "message": "Card authentication wasn't completed. Please try booking again.",
            },
        )
    # unconfigured (dev) / failed (ops) — degrade to no hold; post-trip settles.
    logger.error(
        "[preauth] verify hold failed for ride=%s pi=%s: %s", ride_id, payment_intent_id, outcome.error_message
    )
    return {}


async def _preauthorize_ride_card(
    *,
    ride_id: str,
    rider_id: str,
    grand_total: Decimal,
    stripe_customer_id: Optional[str],
    payment_method_id: Optional[str],
    block_on_decline: bool = True,
) -> _PreauthOutcome:
    """Place a buffered card hold at booking; return a ``_PreauthOutcome``.

    Holds ``grand_total + RIDE_AUTH_BUFFER_CAD`` via a manual-capture
    PaymentIntent so a post-trip tip can later be captured on the SAME intent
    (one Stripe fee) and a dead card is surfaced BEFORE a driver is dispatched.
    The held PaymentIntent id reuses the existing ``payment_intent_id`` column.

    Outcomes:
      - hold placed → ``fields`` populated (authorized / fare_only).
      - ``requires_action`` → ``requires_action=True`` with client_secret +
        payment_intent_id so the caller drives the on-device SCA / Apple Pay
        two-step (interactive booking only).
      - genuine decline → raises 402 when ``block_on_decline`` (interactive
        booking); returns empty when not (scheduled dispatch — never strand it).
      - ``failed`` / ``unconfigured`` / no card on file → empty fields, ride
        proceeds on the post-trip settlement path.
    """
    if not stripe_customer_id or not payment_method_id:
        # No saved card to hold against — leave settlement to the post-trip path.
        return _PreauthOutcome()

    buffer = _round(_d(_deps._settings.RIDE_AUTH_BUFFER_CAD))
    hold_amount = _round(_d(grand_total) + buffer)
    _ride_stub = {"id": ride_id, "payment_method": "card"}

    outcome = await _deps.authorize_ride(
        ride=_ride_stub,
        rider_id=rider_id,
        amount=hold_amount,
        payment_method_id=payment_method_id,
        stripe_customer_id=stripe_customer_id,
    )

    if outcome.status == "authorized":
        return _PreauthOutcome(
            fields={
                "payment_intent_id": outcome.payment_intent_id,
                "authorized_amount": _f(hold_amount),
                "auth_status": "authorized",
            }
        )

    if outcome.status == "requires_action":
        # 3DS / Apple Pay biometric needed — hand the client_secret to the app to
        # confirm on-device, then re-book passing the PI back. Scheduled dispatch
        # (block_on_decline=False) can't drive an on-device sheet → degrade.
        if not block_on_decline:
            logger.info("[preauth] SCA needed at scheduled dispatch for ride=%s — degrading to post-trip", ride_id)
            return _PreauthOutcome()
        logger.info("[preauth] card requires SCA at booking for ride=%s — returning client_secret", ride_id)
        return _PreauthOutcome(
            requires_action=True,
            client_secret=outcome.client_secret,
            payment_intent_id=outcome.payment_intent_id,
        )

    if outcome.status == "declined":
        if outcome.decline_code in _RETRYABLE_AT_LOWER_AMOUNT:
            # Buffer tipped a thin balance over — retry holding the fare only so
            # a rider who can afford the ride still rides (loses single-fee tips).
            fare_outcome = await _deps.authorize_ride(
                ride=_ride_stub,
                rider_id=rider_id,
                amount=_round(_d(grand_total)),
                payment_method_id=payment_method_id,
                stripe_customer_id=stripe_customer_id,
            )
            if fare_outcome.status == "authorized":
                logger.info(
                    "[preauth] buffered hold declined (insufficient_funds); fare-only hold placed for ride=%s",
                    ride_id,
                )
                return _PreauthOutcome(
                    fields={
                        "payment_intent_id": fare_outcome.payment_intent_id,
                        "authorized_amount": _f(_round(_d(grand_total))),
                        "auth_status": "fare_only",
                    }
                )
            if fare_outcome.status == "requires_action":
                if not block_on_decline:
                    return _PreauthOutcome()
                return _PreauthOutcome(
                    requires_action=True,
                    client_secret=fare_outcome.client_secret,
                    payment_intent_id=fare_outcome.payment_intent_id,
                )
            if fare_outcome.status == "declined":
                logger.info("[preauth] fare-only hold also declined for ride=%s", ride_id)
                return _card_declined_result(fare_outcome.decline_code, block_on_decline)
            # fare-only hit ops / unconfigured — degrade to no hold.
            return _PreauthOutcome()

        # Hard decline (lost/stolen/generic) — a smaller hold won't help.
        logger.info("[preauth] hard card decline (code=%s) for ride=%s", outcome.decline_code, ride_id)
        return _card_declined_result(outcome.decline_code, block_on_decline)

    # failed (Stripe ops) / unconfigured — proceed without a hold; the post-trip
    # settlement path (and its retry loop) remains the safety net.
    if outcome.status == "failed":
        logger.error("[preauth] authorization ops error for ride=%s: %s", ride_id, outcome.error_message)
    return _PreauthOutcome()


@router.post("")
@ride_request_limit
@idempotent_endpoint(scope="ride_create")
async def create_ride(
    body: CreateRideRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    # SlowAPI's @ride_request_limit needs a parameter literally named
    # ``request`` that is a starlette Request; otherwise it raises
    # "parameter `request` must be an instance of starlette.requests.Request"
    # for every call. The Pydantic body is ``body`` — do not rename it
    # back to ``request`` without also reworking the rate-limit decorator.
    _deps.validate_ride_location(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)

    # R-P1-7: Idempotency — if the client retries after a network drop, return
    # the previously-created ride instead of charging the rider twice.
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = await _deps.db_supabase.find_one(
            "rides",
            {"idempotency_key": idempotency_key, "rider_id": current_user["id"]},
        )
        if existing:
            return existing

    # Ban check + payment method validation share a single users-row read.
    # Previously this was two round-trips: ``find_one(users)`` (for card path)
    # + ``get_user_status`` (always). ``status`` lives on the same row.
    rider_row = await _deps.db.find_one("users", {"id": current_user["id"]})
    user_status = (rider_row or {}).get("status", "active")
    if user_status == "banned":
        raise HTTPException(
            status_code=403,
            detail=(rider_row or {}).get("status_reason")
            or "Your account has been deactivated due to policy violations. Please contact support.",
        )
    if user_status == "suspended":
        # A temporary suspension (suspended_until set) auto-lifts once that time
        # passes — the rider can book again without an admin reactivating.
        suspended_until = (rider_row or {}).get("suspended_until")
        still_suspended = True
        if suspended_until:
            try:
                still_suspended = datetime.fromisoformat(str(suspended_until).replace("Z", "+00:00")) > datetime.now(
                    timezone.utc
                )
            except ValueError:
                still_suspended = True
        if still_suspended:
            raise HTTPException(
                status_code=403,
                detail=(rider_row or {}).get("status_reason")
                or "Your account is currently suspended. Please contact support.",
            )
    # Only a ride that will ACTUALLY settle against a corporate account is exempt
    # from the personal-card checks — i.e. company_allowance, or work_profile +
    # corporate_account_id (which is reclassified to company_allowance below and
    # routed to settle_corporate). A bare corporate_account_id with
    # payment_method="card" and no work_profile is NOT corporate-billed: it
    # settles through settle_card() against the rider's card, so it must still
    # pin one. Keying the exemption on corporate_account_id alone (an earlier
    # attempt) re-opened the stale-default-card charge for exactly that shape —
    # use the canonical predicate instead.
    _corporate_billed = _is_corporate_paid(
        payment_method=body.payment_method,
        work_profile=body.work_profile,
        corporate_account_id=body.corporate_account_id,
    )
    if body.payment_method == "card" and not _corporate_billed:
        # Demo/local mode (Stripe intentionally unconfigured) has no real Stripe
        # customers or cards; card rides settle through charge_ride's
        # "unconfigured" path (marked paid, no charge). Only enforce the
        # card-on-file requirements when Stripe is actually configured —
        # otherwise demo/staging could never book a card ride.
        _stripe_configured = bool((await _deps.get_app_settings()).get("stripe_secret_key"))
        if _stripe_configured:
            if not rider_row or not rider_row.get("stripe_customer_id"):
                raise HTTPException(
                    status_code=400,
                    detail="No payment method on file. Please add a card first.",
                )
            # Defense-in-depth (money): a card ride must name the exact card to
            # charge. Without an explicit payment_method_id, settle_card() falls
            # back to the Stripe customer's default_payment_method — which is how
            # a stale/forgotten card (e.g. an old 4242 test card) ends up charged
            # for a ride the rider never knowingly put on it. The rider app
            # already guards this in the UI; this server check closes the same
            # gap for any direct API caller or client-state race. The SCA
            # two-step second leg is exempt: its hold
            # (preauthorized_payment_intent_id) already pins a real card, and
            # settlement captures that hold rather than the customer default.
            if not body.payment_method_id and not body.preauthorized_payment_intent_id:
                raise HTTPException(
                    status_code=400,
                    detail="Select a payment card before booking.",
                )

    active_statuses = list(RideStatus.active_statuses())
    existing_ride = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows(
            "rides",
            {"rider_id": current_user["id"], "status": {"$in": active_statuses}},
            limit=1,
        )
    )
    if existing_ride:
        raise SpinrException(
            message="You already have an active ride",
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=409,
            message_key=ErrorKeys.RIDE_INVALID_STATUS,
        )

    unpaid_rides = await _deps.db_supabase.get_rows(
        "rides",
        {
            "rider_id": current_user["id"],
            "status": RideStatus.COMPLETED,
            "payment_status": "failed",
        },
        limit=1,
    )
    if unpaid_rides:
        raise SpinrException(
            message="You have an unpaid ride. Please update your payment method to continue booking.",
            error_code=ErrorCode.PAYMENT_UNPAID_RIDE_BLOCK,
            status_code=402,
            details={"unpaid_ride_id": unpaid_rides[0]["id"]},
            message_key=ErrorKeys.PAYMENT_UNPAID_RIDE_BLOCK,
        )

    # Straight-line haversine through any stops. This is only the FALLBACK: when
    # the rider sends back the estimate_token (below) it carries the quoted road
    # distance, and the booking charges that instead so the booked fare matches
    # the road-route quote shown at /rides/estimate rather than under-billing on
    # crow-flies (0.7 km vs 1.8 km in the reported incident).
    haversine_km = multi_leg_distance(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng, body.stops)
    distance_km = round(haversine_km, 3)
    duration_minutes = int(distance_km / 30 * 60) + 5
    distance_basis = "haversine"  # overridden from the estimate token when present

    # Fetch service_areas ONCE for this request and share across:
    # (1) fare resolution, (2) airport-fee lookup, (3) area-fees/taxes,
    # (4) service_area_id resolution. Previously each of these hit the
    # table independently — 3-4 full scans per POST /rides.
    try:
        all_areas = await _deps.db_supabase.get_rows("service_areas", {"is_active": True}, limit=500)
    except Exception as e:
        logger.error(f"Failed to fetch service areas: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Unable to verify service area. Please try again.",
        ) from e

    # Resolve the pickup service area once and pass the match downstream.
    matched_area = await _get_active_service_area_for_point(
        body.pickup_lat,
        body.pickup_lng,
        all_areas,
    )
    service_area_id = matched_area["id"] if matched_area else None

    # Geofence gate: pickup must fall inside an active service area. Without
    # this the request silently dispatches against drivers anywhere on the
    # map, which is what produced the "I'm outside the zone but the app is
    # still searching for drivers" report. Dropoff and intermediate stops are
    # checked below with the same service-area matcher.
    if matched_area is None and all_areas:
        logger.info(
            "[geofence] reject pickup=%s — outside %d active service area(s)",
            _deps.geohash(body.pickup_lat, body.pickup_lng),
            len(all_areas),
        )
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

    # Geofence gate: dropoff must also fall inside an active service area.
    # Uses PostGIS plus the admin-visible polygon JSON fallback.
    if all_areas:
        _dropoff_area = await _get_active_service_area_for_point(
            body.dropoff_lat,
            body.dropoff_lng,
            all_areas,
        )
        if _dropoff_area is None:
            logger.info(
                "[geofence] reject dropoff=%s — outside service areas",
                _deps.geohash(body.dropoff_lat, body.dropoff_lng),
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

    # Geofence gate: every intermediate stop must also be in coverage.
    if all_areas:
        for idx, stop in enumerate(body.stops or []):
            s_lat, s_lng = stop.get("lat"), stop.get("lng")
            if s_lat is None or s_lng is None:
                continue
            _stop_area = await _get_active_service_area_for_point(s_lat, s_lng, all_areas)
            if _stop_area is None:
                logger.info(
                    "[geofence] reject stop[%d]=%s — outside service areas",
                    idx,
                    _deps.geohash(s_lat, s_lng),
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

    # Vehicle types are also needed by fare building — fetch once, reuse.
    vehicle_types = await _deps.db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)

    fares = await _deps._fares_for_location_impl(
        body.pickup_lat,
        body.pickup_lng,
        all_areas=all_areas,
        vehicle_types=vehicle_types,
    )

    fare_info = next(
        (f for f in fares if f["vehicle_type"]["id"] == body.vehicle_type_id),
        None,
    )

    if not fare_info:
        logger.info(
            "[create_ride] reject vehicle_type_id=%s — not configured for pickup service area",
            body.vehicle_type_id,
        )
        raise HTTPException(status_code=400, detail="Invalid vehicle type for this service area")

    # Use Decimal for all monetary arithmetic (CQ-009 — eliminates float rounding errors)
    # P0-4 surge-lock: if the client sent back the estimate_token we issued
    # from /rides/estimate, reuse that surge instead of re-reading the area.
    # Invalid / expired tokens fall back to current surge — we do not 400
    # the request, because that would strand a rider mid-confirm if the
    # token just expired. The worst case of a bad token is parity with the
    # pre-token behavior (read current surge).
    current_surge = _d(fare_info.get("surge_multiplier", 1.0))
    surge = current_surge
    if body.estimate_token:
        try:
            payload = verify_estimate_token(
                body.estimate_token,
                rider_id=current_user["id"],
                vehicle_type_id=body.vehicle_type_id,
                pickup_lat=body.pickup_lat,
                pickup_lng=body.pickup_lng,
                dropoff_lat=body.dropoff_lat,
                dropoff_lng=body.dropoff_lng,
            )
            surge = _d(payload["sm"])
            logger.info(
                f"Surge locked from estimate_token for rider={current_user['id']} "
                f"vt={body.vehicle_type_id}: {float(surge)} (current was {float(current_surge)})"
            )
            # Charge the quoted road distance the rider was shown, not a
            # re-derived haversine. Duration recomputed from it with the same
            # model /estimate used, so quoted and charged time_fare agree.
            distance_km, duration_minutes, distance_basis = resolve_booking_distance(haversine_km, payload)
            if distance_basis != "haversine":
                logger.info(
                    f"Distance locked from estimate_token for rider={current_user['id']}: "
                    f"{distance_km}km basis={distance_basis} (haversine was {round(haversine_km, 3)}km)"
                )
        except EstimateTokenError as e:
            logger.warning(
                f"estimate_token rejected ({e}); falling back to current surge "
                f"{float(current_surge)} for rider={current_user['id']}"
            )

    # Road-basis safety net. If no valid token supplied a road distance (token
    # absent/expired, or a non-app client), booking must STILL price on the road
    # route when fare_distance_basis is "road" — never silently bill straight-line
    # haversine, which systematically undercharges (0.7 km vs 1.8 km). Re-derive
    # the road distance from a fresh Directions call; only if that is unavailable
    # do we keep haversine, now flagged "haversine_fallback" so the booked-distance
    # suspect check + nightly reconciliation catch it (plain "haversine" was
    # invisible to both). Under fare-lock, settlement no longer corrects this at
    # trip end, so booking is the only place to get it right.
    if distance_basis == "haversine":
        try:
            _bk_settings = await _deps.get_app_settings() or {}
        except Exception:
            _bk_settings = {}
        _bk_mode = str(_bk_settings.get("fare_distance_basis", "road")).lower()
        if _bk_mode == "road":
            _bk_road = await _fetch_directions_route(
                body.pickup_lat,
                body.pickup_lng,
                body.dropoff_lat,
                body.dropoff_lng,
                _bk_settings.get("google_maps_api_key", ""),
                waypoints=body.stops or [],
            )
            _bk_road_km = _bk_road.get("distance_km") if _bk_road else None
            distance_km, distance_basis = select_fare_distance(haversine_km, _bk_road_km, mode="road")
            duration_minutes = int(distance_km / 30 * 60) + 5
            logger.info(
                f"Distance re-derived at booking (no valid token) for rider={current_user['id']}: "
                f"{distance_km}km basis={distance_basis} (haversine was {round(haversine_km, 3)}km)"
            )

    # Corporate surge bypass — applied AFTER estimate_token resolution so the
    # corporate flag wins over any token-locked multiplier. Without this
    # ordering a rider could pin a surged token in personal mode, switch the
    # toggle to Work, and still bill the company at the surged rate.
    if _is_corporate_paid(
        payment_method=body.payment_method,
        work_profile=body.work_profile,
        corporate_account_id=body.corporate_account_id,
    ) and surge != Decimal("1.0"):
        logger.info(
            f"Corporate surge bypass for rider={current_user['id']} "
            f"corp={body.corporate_account_id}: forcing surge {float(surge)} → 1.0"
        )
        surge = Decimal("1.0")

    # Scheduled ride surge exclusion (policy): a rider who books a scheduled ride
    # locks in the fare at booking time, before the surge window opens. Applying
    # the current area surge at dispatch time would retroactively charge a higher
    # multiplier than the rider was shown — a hidden-fee violation. Reset to 1.0
    # unconditionally when is_scheduled is True or a scheduled_time is present.
    if (body.is_scheduled or body.scheduled_time) and surge > Decimal("1.0"):
        logger.info(f"Scheduled ride: surge reset from {float(surge)} to 1.0 for rider={current_user['id']}")
        surge = Decimal("1.0")

    # Airport surcharge (pickup, dropoff, or any stop in airport sub-region).
    # Reuses the same all_areas list — filters for is_airport locally.
    airport_result = await _deps.calculate_airport_fee(
        body.pickup_lat,
        body.pickup_lng,
        body.dropoff_lat,
        body.dropoff_lng,
        stops=body.stops,
        _all_areas=all_areas,
    )
    airport_fee = _d(airport_result.get("airport_fee", 0.0))
    airport_zone_name = airport_result.get("airport_zone_name")

    fb = calculate_fare(fare_info, distance_km, duration_minutes, surge=surge, airport_fee=airport_fee)
    base_fare = fb.base_fare
    distance_fare = fb.distance_fare
    time_fare = fb.time_fare
    booking_fee = fb.booking_fee
    total_fare = fb.total_fare
    driver_earnings = fb.driver_earnings
    admin_earnings = fb.admin_earnings

    # Calculate area fees + taxes (reuses all_areas + pre-resolved match)
    try:
        fees_result = await _deps.calculate_all_fees(
            body.pickup_lat,
            body.pickup_lng,
            body.dropoff_lat,
            body.dropoff_lng,
            distance_km,
            _f(total_fare),
            _all_areas=all_areas,
            _matched_area=matched_area,
        )
    except Exception as e:
        logger.error(f"Failed to calculate area fees: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Unable to calculate fees and taxes. Please try again.",
        ) from e

    area_fees_total = fees_result.get("fees_total", 0)
    tax_amount = fees_result.get("tax_amount", 0)
    grand_total = _f(_round(total_fare + _d(area_fees_total) + _d(tax_amount)))

    # Payment pre-validation: reject the ride before dispatching if the rider
    # clearly cannot pay. Wallet rides require sufficient balance upfront;
    # corporate rides have their own policy check below.
    if body.payment_method == "wallet":
        wallet = await _deps.db_supabase.find_one("wallets", {"user_id": current_user["id"]})
        wallet_balance = _d((wallet or {}).get("balance", 0))
        if wallet_balance < _d(grand_total):
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient wallet balance. Estimated fare is ${grand_total}, wallet has ${_f(wallet_balance)}. Please top up your wallet or switch to card payment.",
            )

    # CR-1: a ride booked for a future time must NOT dispatch a live driver now.
    # It is parked in SCHEDULED and the scheduled-ride dispatcher loop flips it
    # to SEARCHING at its scheduled_time. A request is deferred whenever a
    # future scheduled_time is present — the CreateRideRequest validator
    # guarantees that time is ≥5 min out. We key off scheduled_time (not the
    # is_scheduled flag) because a client can send scheduled_time while leaving
    # is_scheduled at its False default; the surge/fare logic above already
    # treats scheduled_time presence as scheduled, and dispatching such a ride
    # immediately would route a live driver hours early.
    _is_deferred_schedule = body.scheduled_time is not None
    # Corporate time-window policy is enforced against the *pickup* time, so a
    # deferred ride must be evaluated against its scheduled_time, not the moment
    # of booking — otherwise a rider booking inside company hours for an
    # out-of-hours pickup (or vice-versa) is wrongly allowed/blocked.
    _policy_pickup_time = body.scheduled_time if _is_deferred_schedule else datetime.now(timezone.utc)

    # Pre-dispatch corporate policy check (spec §4 — booking phase).
    # Only runs when rider explicitly books with company_allowance payment method.
    _corp_member_id: Optional[str] = None
    if body.corporate_account_id and body.payment_method == "company_allowance":
        _policy_result = await _deps.evaluate_policy_for_ride(
            corporate_account_id=body.corporate_account_id,
            rider_id=current_user["id"],
            estimated_fare=total_fare,
            ride_type="standard",
            pickup_time=_policy_pickup_time,
        )
        if not _policy_result.passed:
            reasons = []
            for rule in _policy_result.failed_rules:
                if rule == "max_fare_per_ride":
                    reasons.append("Fare exceeds company limit per ride.")
                elif rule == "time_window":
                    reasons.append("Booking is outside allowed company hours.")
                elif rule == "allowed_payment_source":
                    reasons.append("Insufficient corporate allowance balance.")
                else:
                    reasons.append(f"Violated corporate policy: {rule}.")
            message = "Blocked by company policy:\n" + "\n".join(reasons) if reasons else "Violated corporate policy."
            raise HTTPException(
                status_code=403,
                detail={
                    "message": message,
                    "failed_rules": _policy_result.failed_rules,
                },
            )
        _corp_members = await _deps.db_supabase.get_rows(
            "corporate_members",
            {
                "company_id": body.corporate_account_id,
                "user_id": current_user["id"],
                "status": "active",
            },
            limit=1,
        )
        if _corp_members:
            _corp_member_id = _corp_members[0]["id"]

    # _is_deferred_schedule is computed above (before the corporate policy
    # check). Deferred rides are parked in SCHEDULED; the scheduled-ride
    # dispatcher loop (utils/scheduled_rides.py) flips them to SEARCHING and
    # runs driver matching when scheduled_time arrives. A ride with
    # is_scheduled but no time falls through to immediate dispatch.
    _initial_status = RideStatus.SCHEDULED if _is_deferred_schedule else RideStatus.SEARCHING

    pickup_otp_plain = generate_pickup_otp()
    ride = Ride(
        rider_id=current_user["id"],
        vehicle_type_id=body.vehicle_type_id,
        pickup_address=body.pickup_address,
        pickup_lat=body.pickup_lat,
        pickup_lng=body.pickup_lng,
        dropoff_address=body.dropoff_address,
        dropoff_lat=body.dropoff_lat,
        dropoff_lng=body.dropoff_lng,
        distance_km=round(distance_km, 2),
        duration_minutes=duration_minutes,
        base_fare=_f(base_fare),
        distance_fare=_f(distance_fare),
        time_fare=_f(time_fare),
        booking_fee=_f(booking_fee),
        surge_multiplier=round(float(surge), 2),
        total_fare=_f(total_fare),
        stops=body.stops,
        # Persist is_scheduled=True for any deferred ride even if the client
        # left the flag at its default — the scheduled-ride dispatcher filters
        # on {is_scheduled: True, status: 'scheduled'}, so a scheduled_time-only
        # request must carry the flag or it would never be picked up.
        is_scheduled=bool(body.is_scheduled or _is_deferred_schedule),
        requires_wav=body.requires_wav,
        quiet_mode=body.quiet_mode,
        rider_notes=body.rider_notes,
        scheduled_time=body.scheduled_time,
        driver_earnings=_f(driver_earnings),
        admin_earnings=_f(admin_earnings),
        payment_method=body.payment_method,
        payment_method_id=body.payment_method_id,
        # NOTE: ``requires_wav`` was passed twice (once at line ~1090, again
        # here) — Python 3.11+ raises SyntaxError, blocking module import
        # and the entire test suite. Drop-in fix unblocks Audit-17 Phase 1c.
        status=_initial_status,
        pickup_otp=pickup_otp_plain,
        ride_requested_at=datetime.now(timezone.utc),
    )

    ride_data = ride.dict()
    # Pickup road-snap (pickup_nav_*) happens in _prep_and_dispatch after the
    # insert — the Roads API round-trip was blocking the booking response.
    # Readers fall back to pickup_lat/lng while pickup_nav_* is NULL.
    if body.corporate_account_id:
        ride_data["corporate_account_id"] = body.corporate_account_id
    if _corp_member_id:
        ride_data["corporate_member_id"] = _corp_member_id
    if service_area_id:
        ride_data["service_area_id"] = service_area_id
    # Preserve the original planned (straight-line) distance. ride.distance_km
    # will be overwritten with the actual GPS-measured distance on completion.
    ride_data["planned_distance_km"] = round(distance_km, 2)
    if body.planned_route_polyline:
        ride_data["planned_route_polyline"] = body.planned_route_polyline
    # Only store airport surcharge when it actually applies
    if airport_fee > 0:
        ride_data["airport_fee"] = _f(airport_fee)
        if airport_zone_name:
            ride_data["airport_zone_name"] = airport_zone_name

    ride_data["area_fees_breakdown"] = fees_result.get("fees", [])
    ride_data["area_fees_total"] = area_fees_total
    ride_data["tax_amount"] = tax_amount
    ride_data["tax_breakdown"] = fees_result.get("tax_breakdown", {})
    ride_data["grand_total"] = grand_total
    if idempotency_key:
        ride_data["idempotency_key"] = idempotency_key

    # ── Corporate work-profile pre-dispatch check ─────────────────────────────
    # Activated when the rider sends work_profile=true + corporate_account_id.
    # Policy violation or insufficient funds → reject before a driver is
    # disturbed. Personal rides (no work_profile flag) skip this block entirely.
    if body.work_profile and body.corporate_account_id:
        _corp_company_id = body.corporate_account_id

        # 1. Resolve active membership
        _memberships = await _deps.db_supabase.list_active_memberships_for_user(current_user["id"])
        _membership = next((m for m in _memberships if m.get("company_id") == _corp_company_id), None)
        if not _membership:
            raise HTTPException(
                status_code=400,
                detail={"reason": "no_corporate_membership"},
            )

        # 2–4. Fetch allowance + policy, evaluate all rules.
        _policy_result = await _deps.evaluate_policy_for_ride(
            corporate_account_id=_corp_company_id,
            rider_id=current_user["id"],
            estimated_fare=total_fare,
            ride_type=body.vehicle_type_id or "standard",
            pickup_time=_policy_pickup_time,
            policy_override=_membership.get("policy_override", False),
        )
        if not _policy_result.passed:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": "policy_violation",
                    "failed_rules": _policy_result.failed_rules,
                },
            )

        # 5. Pre-auth buffer check (skip for unlimited allowances).
        # Uses allowance + policy surfaced by evaluate_policy_for_ride so we
        # don't need a second round-trip to fetch them separately.
        _allowance = _policy_result.allowance
        _policy = _policy_result.policy
        if _allowance.get("type") != "unlimited":
            _remaining = _d(str(_allowance.get("amount") or 0)) - max(_d(str(_allowance.get("used") or 0)), _d("0"))
            _master_permitted = _policy.get("allowed_payment_source", "both") in (
                "master_only",
                "both",
            )
            if _remaining < _round(_d(str(_f(total_fare))) * _d("1.5")) and not _master_permitted:
                raise HTTPException(
                    status_code=400,
                    detail={"reason": "allowance_low"},
                )

        # 6. Tag ride as corporate
        ride_data["corporate_account_id"] = _corp_company_id
        ride_data["payment_method"] = "company_allowance"

    # ── Card pre-authorization (buffered hold) ────────────────────────────────
    # For an immediate CARD ride, place a manual-capture hold of
    # (grand_total + RIDE_AUTH_BUFFER_CAD) BEFORE the ride is inserted/dispatched.
    # This catches a dead card up front (a true decline raises 402 here, before a
    # driver is disturbed) and reserves headroom so a post-trip tip captures on
    # the same PaymentIntent. Skipped for wallet/corporate (different settlement)
    # and for scheduled rides (authorized at dispatch time — see scheduled_rides).
    # Any non-decline failure degrades to "no hold" and the existing post-trip
    # settlement path; the buffer never blocks a ride the rider could take.
    if ride_data.get("payment_method") == "card" and not _is_deferred_schedule:
        if body.preauthorized_payment_intent_id:
            # SCA two-step, second leg: the app already confirmed this hold
            # on-device (3DS / Apple Pay). Verify with Stripe and attach it —
            # do NOT authorize again (that would place a second hold).
            ride_data.update(
                await _attach_preauthorized_hold(
                    ride_id=ride_data["id"],
                    rider_id=current_user["id"],
                    payment_intent_id=body.preauthorized_payment_intent_id,
                    stripe_customer_id=(rider_row or {}).get("stripe_customer_id"),
                    min_amount=_d(grand_total),
                )
            )
        else:
            _preauth = await _preauthorize_ride_card(
                ride_id=ride_data["id"],
                rider_id=current_user["id"],
                grand_total=_d(grand_total),
                stripe_customer_id=(rider_row or {}).get("stripe_customer_id"),
                payment_method_id=body.payment_method_id,
            )
            if _preauth.requires_action:
                # SCA two-step, first leg: the hold needs an on-device confirm.
                # Do NOT create the ride yet — hand the client_secret back; the
                # app runs the Stripe sheet, then re-books with
                # preauthorized_payment_intent_id set. Keeps the ride state
                # machine clean: no ride exists until the hold is real.
                return {
                    "requires_action": True,
                    "payment_authorization": {
                        "client_secret": _preauth.client_secret,
                        "payment_intent_id": _preauth.payment_intent_id,
                    },
                }
            ride_data.update(_preauth.fields)

    fresh_ride, _idempotent_reuse = await _insert_ride_with_code(ride_data, current_user["id"])
    if _idempotent_reuse:
        # DB-enforced idempotency: a concurrent duplicate request already
        # created this ride — return it instead of double-charging.
        return fresh_ride

    # Booking distance sanity (P2.1b): a booked distance below the plausible
    # floor (likely a wrong dropoff coordinate — the incident's 0.7 km) or a
    # road route we couldn't fetch (haversine fallback, so possibly undercharged)
    # is worth flagging for ops + the reconciliation job. Detection only — the
    # booking already succeeded and short trips are legitimate.
    try:
        _suspect_reason = booked_distance_suspect_reason(distance_km, distance_basis)
        if _suspect_reason and fresh_ride.get("id"):
            try:
                from ...utils.distance_integrity import record_integrity_event
            except ImportError:
                from utils.distance_integrity import record_integrity_event  # type: ignore
            _deps.spawn(
                record_integrity_event(
                    fresh_ride["id"],
                    "booked_distance_suspect",
                    {
                        "reason": _suspect_reason,
                        "booked_km": round(float(distance_km), 3),
                        "haversine_km": round(float(haversine_km), 3),
                        "distance_basis": distance_basis,
                    },
                )
            )
            _deps._metric_inc("spinr_rides_booked_distance_suspect_total", {"reason": _suspect_reason})
    except Exception:
        logger.error("booking distance sanity check failed for ride %s", fresh_ride.get("id"), exc_info=True)

    # ── Apply promo code if provided ──
    if body.promo_code:
        try:
            try:
                from ...routes.promotions import (
                    _record_promo_application,
                    _validate_promo_for_user,
                )
            except ImportError:
                from routes.promotions import (
                    _record_promo_application,
                    _validate_promo_for_user,
                )

            server_fare = _d(fresh_ride.get("total_fare", 0))
            ride_portion = (
                _d(fresh_ride.get("base_fare") or 0)
                + _d(fresh_ride.get("distance_fare") or 0)
                + _d(fresh_ride.get("time_fare") or 0)
            ) or server_fare
            validation = await _validate_promo_for_user(
                code=body.promo_code,
                user_id=current_user["id"],
                ride_fare=ride_portion,
                ride_id=ride.id,
                grand_total=_d(fresh_ride.get("grand_total") or server_fare),
            )
            if validation.get("valid"):
                discount = _d(validation["discount_amount"])
                # Backstop cap: promos discount the ride fare only (driver
                # earnings = base+dist+time), never fees or taxes. _validate_promo_for_user
                # already caps for flat/percentage promos; mirror it here so any
                # future validation path cannot store a discount > ride_portion.
                # free_ride covers the full grand_total by design and bypasses this cap.
                if not validation.get("free_ride") and ride_portion > 0:
                    discount = min(discount, ride_portion)
                application_id = await _record_promo_application(
                    promo_id=validation["promo_id"],
                    code=validation["code"],
                    user_id=current_user["id"],
                    discount=discount,
                )
                discounted_grand = _f(_round(_d(fresh_ride.get("grand_total", server_fare)) - discount))
                # NOTE: do NOT mutate total_fare. total_fare is the fare-side
                # subtotal (base+dist+time+booking+airport) used by area-fee
                # and tax math downstream. The rider's effective bill goes on
                # grand_total only. Overwriting total_fare to server_fare-discount
                # produced bills like total_fare=$2.14 (12.14-10) while
                # grand_total=$5.30 — and any rider-app path that fell back to
                # total_fare displayed the wrong amount to the customer.
                # subtotal_fare records the pre-promo subtotal for receipts.
                await _deps.db_supabase.update_one(
                    "rides",
                    {"id": ride.id},
                    {
                        "subtotal_fare": _f(server_fare),
                        "discount_amount": _f(discount),
                        "promo_code": validation["code"],
                        "promo_application_id": application_id,
                        "grand_total": discounted_grand,
                    },
                )
                fresh_ride["subtotal_fare"] = _f(server_fare)
                fresh_ride["discount_amount"] = _f(discount)
                fresh_ride["promo_code"] = validation["code"]
                fresh_ride["promo_application_id"] = application_id
                fresh_ride["grand_total"] = discounted_grand
        except HTTPException as he:
            logger.warning(
                "create_ride: promo '%s' rejected for rider %s on ride %s: %s",
                body.promo_code,
                current_user["id"],
                ride.id,
                he.detail,
            )
            fresh_ride["promo_error"] = he.detail if isinstance(he.detail, str) else str(he.detail)
        except Exception as e:
            logger.error(
                f"create_ride: promo application failed for code={body.promo_code}: {e}",
                exc_info=True,
            )
            fresh_ride["promo_error"] = "Promo could not be applied"

    # ── Fare breakdown snapshot ──
    # Frozen receipt: the exact line items shown to the rider at booking.
    # Only stores lines + grand_total — all component fields (base_fare,
    # tax_breakdown, promo_code, etc.) already live on the ride row.
    # When fare_lock_enabled, GET /rides/{id} serves this instead of
    # recomputing from (potentially recalculated) ride fields.
    snapshot_lines = _build_fare_breakdown(fresh_ride)
    fare_snapshot = {
        "lines": snapshot_lines,
        "grand_total": _sum_fare_breakdown(snapshot_lines),
        "locked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await _deps.db_supabase.update_one(
            "rides",
            {"id": ride.id},
            {"fare_breakdown_snapshot": fare_snapshot},
        )
        fresh_ride["fare_breakdown_snapshot"] = fare_snapshot
    except Exception as snap_err:
        logger.error(f"create_ride: fare snapshot save failed: {snap_err}", exc_info=True)

    # ── Route snapshot at creation ──
    # Generate a PNG map of the planned route and upload to Supabase
    # Storage. Available even if the ride is later cancelled/failed.
    planned_poly = fresh_ride.get("planned_route_polyline")
    logger.info(
        f"create_ride: planned_route_polyline check — "
        f"present={planned_poly is not None}, "
        f"type={type(planned_poly).__name__}, "
        f"len={len(planned_poly) if isinstance(planned_poly, list) else 'N/A'}"
    )
    if planned_poly and isinstance(planned_poly, list) and len(planned_poly) >= 2:

        async def _create_planned_snapshot():
            try:
                try:
                    from ..drivers import _generate_and_store_ride_snapshot
                except ImportError:
                    from routes.drivers import _generate_and_store_ride_snapshot
                logger.info(
                    f"create_ride: generating planned route snapshot for ride {ride.id} with {len(planned_poly)} points"
                )
                await _generate_and_store_ride_snapshot(
                    ride_id=ride.id,
                    pickup_lat=fresh_ride.get("pickup_lat"),
                    pickup_lng=fresh_ride.get("pickup_lng"),
                    dropoff_lat=fresh_ride.get("dropoff_lat"),
                    dropoff_lng=fresh_ride.get("dropoff_lng"),
                    phase_polylines=None,
                    route_polyline=planned_poly,
                )
                logger.info(f"create_ride: planned route snapshot completed for ride {ride.id}")
            except Exception as exc:
                logger.error(f"create_ride: planned route snapshot failed: {exc}", exc_info=True)

        _deps.spawn(_create_planned_snapshot())

    # Let admin live-monitoring see the request before dispatch starts —
    # previously the dashboard only observed a ride once a driver accepted,
    # which made it impossible to watch an unassigned ride sit in queue.
    # Deferred scheduled rides are NOT live requests yet; the scheduler
    # broadcasts ride_requested when it flips them to SEARCHING.
    #
    # The payload must match the dashboard's MonitoringRide contract (a nested
    # ``ride`` object — see admin-dashboard/.../monitoring/types.ts); the
    # handler calls applyRide(event.ride) to add the new row.
    if not _is_deferred_schedule:
        try:
            from ..admin.monitoring import build_monitoring_ride
        except ImportError:
            from routes.admin.monitoring import build_monitoring_ride
        try:
            await _deps.manager.broadcast_to_admins(
                {
                    "type": "ride_requested",
                    "ride": build_monitoring_ride(fresh_ride, rider=current_user),
                }
            )
        except Exception as _exc:  # pragma: no cover - best effort
            logger.warning(f"create_ride: admin broadcast failed: {_exc}")

    # Booking-latency fix: the pickup road-snap, the server-side Directions
    # polyline, and the entire matching pipeline (driver scan, Redis
    # presence, ETA matrix, offer inserts, per-driver quest reads) used to
    # run inline here, so the rider's booking request blocked on all of it
    # (multiple seconds against a <2s dispatch SLA). They now run in
    # _prep_and_dispatch as a background task; the rider gets the inserted
    # ride back immediately and learns about assignment via the
    # ride_status_changed WS event — the same path scheduled rides and
    # offer-timeout re-dispatch already use.
    #
    # CR-1: deferred scheduled rides still skip dispatch (the scheduler loop
    # dispatches at scheduled_time); the task still prepares nav + polyline
    # so the eventual offers carry a road-following route.
    if _is_deferred_schedule:
        logger.info(
            f"create_ride: ride {ride.id} scheduled for {body.scheduled_time} — "
            "parked in 'scheduled', deferring dispatch to scheduler loop"
        )
    _deps.spawn(
        _prep_and_dispatch(
            ride.id,
            fresh_ride,
            pickup_lat=body.pickup_lat,
            pickup_lng=body.pickup_lng,
            stops=body.stops or [],
            dispatch=not _is_deferred_schedule,
        )
    )

    # Dispatch may have set driver_id / status; read the current state
    # for the response. Skipping this extra fetch would mean the rider
    # app sees "searching" even when a driver was already assigned in
    # the same request, so we keep this one round-trip on purpose.
    updated_ride = await _deps.db_supabase.get_ride(ride.id)

    # Small helper to ensure we return a clean dict
    def serialize_doc(doc):
        return doc

    if updated_ride and updated_ride.get("status") == RideStatus.SEARCHING:
        _deps.spawn(matching.ride_search_timeout(ride.id))

    _deps.spawn(
        log_user_action(
            current_user,
            "ride_created",
            "rides",
            ride.id,
            {"status": (updated_ride.get("status") if updated_ride else RideStatus.SEARCHING)},
        )
    )

    # Carry forward transient fields that don't live in the DB but the
    # client needs (promo_error is set when promo validation fails).
    if fresh_ride.get("promo_error") and updated_ride:
        updated_ride["promo_error"] = fresh_ride["promo_error"]

    return serialize_doc(updated_ride)


async def _insert_ride_with_code(ride_data: dict, rider_id: str) -> tuple[dict, bool]:
    """Insert a ride row, allocating a unique SPR-XXXXXX ride_code.

    Shared by create_ride and the corporate guest-booking service so the
    collision/idempotency semantics live in one place. Returns
    ``(ride_row, idempotent_reuse)`` — when ``idempotent_reuse`` is True the
    row is a previously-created ride matched by idempotency_key and the
    caller must NOT run post-insert side effects (promo, dispatch) again.

    ``insert_ride`` returns the row Supabase just wrote — used directly
    instead of a follow-up ``get_ride`` round-trip; falls back to the local
    ride_data if the driver returns None (e.g. stub DB in tests).

    ride_code (migration 40) is a short SPR-XXXXXX string operators and
    riders can quote. The UUID in ride_data["id"] stays primary key. On the
    astronomically-unlikely unique-constraint collision we retry with a
    fresh code; on PGRST204 ("column does not exist", migration hasn't
    landed yet) fall back to inserting without the code.
    """
    inserted = None
    last_exc: Optional[Exception] = None
    for _attempt in range(3):
        ride_data["ride_code"] = generate_ride_code()
        try:
            inserted = await _deps.db_supabase.insert_ride(ride_data)
            break
        except Exception as e:
            last_exc = e
            # db_supabase.run_sync wraps PostgREST errors in DatabaseError/
            # DuplicateRecordError, so str(e) is a generic sentinel — the SQLSTATE
            # and constraint name live in details['original']/__cause__. Match the
            # real text + structured code, not str(e) (which silently missed every
            # branch below and fell through to a generic 409/503).
            msg = db_error_text(e)
            code = pg_error_code(e).upper()
            if code == "PGRST204" or "pgrst204" in msg or "column" in msg:
                ride_data.pop("ride_code", None)
                inserted = await _deps.db_supabase.insert_ride(ride_data)
                break
            if "rides_one_active_per_rider" in msg:
                raise HTTPException(status_code=409, detail="You already have an active ride") from e
            if "idx_rides_rider_idempotency_key" in msg:
                # Concurrent duplicate request lost the race — surface the
                # already-created ride to the caller.
                existing = await _deps.db_supabase.find_one(
                    "rides",
                    {"idempotency_key": ride_data.get("idempotency_key"), "rider_id": rider_id},
                )
                if existing:
                    return existing, True
                raise HTTPException(status_code=409, detail="Duplicate ride request") from e
            if code == "23505" or "unique" in msg or "duplicate" in msg:
                continue  # retry with a new code for ride_code conflicts
            raise
    else:
        logger.error(f"_insert_ride_with_code: could not allocate unique ride_code after 3 tries: {last_exc}")
        raise HTTPException(status_code=503, detail="Could not allocate ride code")

    return inserted or ride_data, False


async def _prep_and_dispatch(
    ride_id: str,
    fresh_ride: dict,
    *,
    pickup_lat: float,
    pickup_lng: float,
    stops: list,
    dispatch: bool,
) -> None:
    """Post-booking pipeline: pickup road-snap → server polyline → dispatch.

    Runs as a background task so the booking response never blocks on the
    Roads API, Directions, or the matching pipeline. ``dispatch=False``
    (deferred scheduled rides) still prepares nav/polyline but leaves
    dispatch to the scheduler loop. Dispatch failures surface loudly —
    ride_search_timeout and the stuck-ride sweeper remain the safety nets
    for a ride stranded in 'searching'.
    """
    try:
        # Pickup snap: best-effort; the rider's exact pin (pickup_lat/lng) is
        # untouched, and readers fall back to it while pickup_nav_* is NULL.
        try:
            try:
                from ...utils.route_distance import snap_to_road
            except ImportError:
                from utils.route_distance import snap_to_road  # type: ignore
            _snapped = await snap_to_road(pickup_lat, pickup_lng)
            if _snapped:
                await _deps.db_supabase.update_ride(
                    ride_id, {"pickup_nav_lat": _snapped[0], "pickup_nav_lng": _snapped[1]}
                )
                fresh_ride["pickup_nav_lat"], fresh_ride["pickup_nav_lng"] = _snapped
        except Exception:
            logger.warning("pickup snap_to_road failed; using original pin", exc_info=True)

        # Server-side planned_route_polyline: only when the rider-app didn't
        # send one (race between booking tap and Directions finishing, or no
        # Maps key on client) — every offer should carry a road-following
        # polyline instead of the dashed straight-line driver-app fallback.
        _existing_poly = fresh_ride.get("planned_route_polyline")
        if not _existing_poly or (isinstance(_existing_poly, list) and len(_existing_poly) < 2):
            try:
                _s = await _deps.get_app_settings()
                _maps_key = (_s or {}).get("google_maps_api_key", "")
                if _maps_key:
                    _computed = await _fetch_directions_polyline(
                        fresh_ride["pickup_lat"],
                        fresh_ride["pickup_lng"],
                        fresh_ride["dropoff_lat"],
                        fresh_ride["dropoff_lng"],
                        _maps_key,
                        waypoints=stops,
                    )
                    if _computed:
                        await _deps.db_supabase.update_ride(ride_id, {"planned_route_polyline": _computed})
                        fresh_ride["planned_route_polyline"] = _computed
                        logger.info(
                            "create_ride: server-computed polyline (%d pts) stored for ride %s",
                            len(_computed),
                            ride_id,
                        )
            except Exception as _poly_err:
                logger.warning("create_ride: polyline computation failed (non-fatal): %s", _poly_err)

        if dispatch:
            # Pass the fresh ride through so the dispatch path doesn't
            # re-fetch the row we just inserted.
            await matching.match_driver_to_ride(ride_id, ride=fresh_ride)
    except Exception:
        # A failure here strands the ride in 'searching' with no offers —
        # dispatch-domain errors must never vanish into a dropped task.
        logger.error(f"[DISPATCH] post-booking pipeline failed for ride {ride_id}", exc_info=True)
