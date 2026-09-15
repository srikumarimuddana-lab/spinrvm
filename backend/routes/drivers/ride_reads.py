"""Driver-side ride reads: active ride and history.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from ._deps import (  # noqa: F401
    Any,
    APIRouter,
    Depends,
    Dict,
    HTTPException,
    Optional,
    Query,
    RideStatus,
    datetime,
    db_supabase,
    diag_logger,
    get_current_user,
    get_service_area_polygon,
    logger,
    parse_iso_utc,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    serialize_doc,
    serialize_ride_for_driver,
)

try:
    from ...services.incentive_service import (
        incentive_display_payload,
        match_ride_incentives,
    )
except ImportError:
    from services.incentive_service import (  # type: ignore
        incentive_display_payload,
        match_ride_incentives,
    )

router = APIRouter()

# Fields the driver-facing active-ride UI renders for the rider. Anything not on
# this list (password_hash, fcm_token, email, phone, stripe_customer_id, session
# state, …) must never reach the driver client. See get_active_ride below.
_RIDER_PUBLIC_FIELDS = ("id", "first_name", "last_name", "name", "rating", "profile_image")


# ==========================================
# RIDE MANAGEMENT ENDPOINTS
# ==========================================


@router.get("/rides/active")
async def get_active_ride(current_user: dict = Depends(get_current_user)):
    """Get the driver's current active ride."""
    diag_logger.info(f"[ACTIVE] called by user_id={current_user.get('id')}")
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        diag_logger.info(f"[ACTIVE] no driver row for user_id={current_user.get('id')}")
        raise HTTPException(status_code=404, detail="Driver not found")

    diag_logger.info(f"[ACTIVE] lookup user_id={current_user.get('id')} driver_id={driver.get('id')}")

    # improved query to catch any active state
    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": {"$in": list(RideStatus.active_statuses() - {RideStatus.SEARCHING})},
            },
            limit=1,
        )
    )

    if not ride:
        # Batch dispatch keeps rides in 'searching' without setting driver_id.
        # Check ride_offers for a pending offer so the driver app can recover
        # the offer state after restart/reconnect.
        try:
            pending_offer = await db_supabase.run_sync(
                lambda: (
                    db_supabase.supabase.table("ride_offers")
                    .select("ride_id")
                    .eq("driver_id", driver["id"])
                    .eq("status", "pending")
                    .limit(1)
                    .execute()
                )
            )
            if pending_offer.data:
                offer_ride_id = pending_offer.data[0]["ride_id"]
                ride = await db_supabase.get_ride(offer_ride_id)
                if ride and ride.get("status") == RideStatus.SEARCHING:
                    diag_logger.info(
                        f"[ACTIVE] found pending batch offer ride_id={offer_ride_id} for driver_id={driver['id']}"
                    )
                    # Mark as driver_assigned for the client — the offer is
                    # logically assigned even though the ride row isn't updated
                    # until acceptance.
                    ride["status"] = RideStatus.DRIVER_ASSIGNED
                else:
                    ride = None
        except Exception as e:
            diag_logger.warning(f"[ACTIVE] ride_offers lookup failed: {e}")
            ride = None

    if not ride:
        try:
            recent = await db_supabase.get_rows("rides", {"driver_id": driver["id"]}, limit=5)
            recent_summary = [
                {
                    "id": r.get("id"),
                    "status": r.get("status"),
                    "driver_id": r.get("driver_id"),
                }
                for r in (recent or [])
            ]
        except Exception as e:
            recent_summary = f"(failed to load recent: {e})"
        diag_logger.info(
            f"[ACTIVE] no active ride for driver_id={driver['id']}. recent_rides_by_driver={recent_summary}"
        )
        return {"ride": None}

    diag_logger.info(
        f"[ACTIVE] found ride_id={ride.get('id')} status={ride.get('status')} "
        f"driver_id={ride.get('driver_id')} rider_id={ride.get('rider_id')}"
    )

    # Get rider info. `db.user_profiles` does not exist as a registered
    # collection in db.py — the rider is a row in `users`. The old name
    # raised AttributeError, which made this endpoint return 500 and
    # silently broke the driver-app's active-ride fetch (activeRide stayed
    # null → ActiveRidePanel returned null → driver saw a blank map after
    # accepting).
    try:
        rider = await db_supabase.get_user_by_id(ride["rider_id"])
    except Exception as e:
        logger.error(
            f"get_active_ride: failed to load rider {ride['rider_id']}: {e}",
            exc_info=True,
        )
        rider = None
    try:
        vehicle_type = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("vehicle_types", {"id": ride["vehicle_type_id"]}, limit=1)
        )
    except Exception as e:
        logger.error(
            f"get_active_ride: failed to load vehicle_type {ride['vehicle_type_id']}: {e}",
            exc_info=True,
        )
        vehicle_type = None

    # R-P1-28 / finding 9: project the rider object down to an explicit
    # allowlist. The old three-field blocklist over a `select("*")` row shipped
    # every other users column — password_hash, fcm_token, session/auth state —
    # to the driver client. An allowlist ships only what the active-ride UI
    # renders (name + photo + rating) and can never leak a newly-added column.
    safe_rider = None
    if rider:
        raw = serialize_doc(rider)
        safe_rider = {k: raw[k] for k in _RIDER_PUBLIC_FIELDS if k in raw}

    # Enrich with incentives so fetchActiveRide never strips enrichment data
    # from the offer panel — and, past acceptance, so the in-trip earnings
    # figure still includes the bonus the offer promised. `rides.driver_earnings`
    # is fare-only by design (the bonus lives in ride_incentive_claims, written
    # at completion), so a driver_assigned-only projection left every
    # post-acceptance screen quoting the fare alone.
    incentives = None
    total_bonus = None
    quest_hint = None
    try:
        # [] / 0.0 when the lookup succeeded and matched nothing; both stay None
        # only when the lookup itself failed. The client needs to tell those
        # apart — otherwise a transient DB blip mid-trip is indistinguishable
        # from "this ride has no bonus" and the earnings headline drops by the
        # bonus and pops back on the next poll.
        incentives, total_bonus = incentive_display_payload(await match_ride_incentives(db_supabase, ride))
    except Exception as e:
        logger.error(f"get_active_ride: incentive lookup failed: {e}", exc_info=True)

    if ride.get("status") == RideStatus.DRIVER_ASSIGNED.value:
        try:
            driver_uid = current_user["id"]
            qr = await db_supabase.run_sync(
                db_supabase.supabase.table("quest_progress")
                .select("current_value, status, quest:quests(title, target_value, reward_amount)")
                .eq("driver_id", driver_uid)
                .eq("status", "active")
                .limit(1)
                .execute
            )
            if qr.data:
                qp = qr.data[0]
                q = qp.get("quest") or {}
                tv = float(q.get("target_value") or 1)
                cv = float(qp.get("current_value") or 0)
                quest_hint = {
                    "title": q.get("title", ""),
                    "current_value": cv,
                    "target_value": tv,
                    "progress_pct": round(min(cv / tv, 1.0) * 100, 1) if tv else 0,
                    "reward_amount": float(q.get("reward_amount") or 0),
                }
        except Exception as e:
            logger.warning(f"get_active_ride: quest hint lookup non-fatal: {e}")

    # Include service area polygon so the driver-app can render the zone
    # boundary overlay on the map — fetched once on every active-ride load
    # (cold start / reconnect path). The polygon is non-sensitive geodata.
    service_area_polygon = None
    sa_id = ride.get("service_area_id")
    if sa_id:
        try:
            sa = await db_supabase.find_one("service_areas", {"id": sa_id})
            service_area_polygon = get_service_area_polygon(sa or {}) or None
        except Exception as e:
            logger.warning(f"get_active_ride: service_area polygon fetch non-fatal: {e}")

    return {
        "ride": serialize_ride_for_driver(ride),
        "rider": safe_rider,
        "vehicle_type": serialize_doc(vehicle_type) if vehicle_type else None,
        "incentives": incentives,
        "total_bonus": total_bonus,
        "quest_hint": quest_hint,
        "service_area_polygon": service_area_polygon,
    }


@router.get("/rides/{ride_id}/offer")
async def get_ride_offer(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Authenticated fetch-by-ride_id for a live ride offer (#1231 finding 15,
    remaining half).

    Lets the driver-app's background/killed-app FCM handler
    (driver-app/services/backgroundMessaging.ts) hydrate the offer detail
    itself instead of the detail riding along in the FCM `data` payload, once
    `minimal_fcm_offer_payload_enabled` (app_settings) drops precise
    pickup/dropoff coordinates and rider_rating from that payload in
    routes/rides/matching.py's `_FCM_EXCLUDE`. The WebSocket
    ``dispatch_payload`` the foreground app already uses is untouched by
    either change.

    Authorization mirrors decline_ride's WS-18 ownership guard (read-only
    here — a SELECT, never decline_ride's UPDATE): only a driver who
    currently holds a live offer (a pending ``ride_offers`` row from batch
    dispatch, or a live admin direct-assignment with no ``ride_offers`` row)
    for THIS ride may fetch it. An unauthorized or nonexistent combination
    gets 404 either way — never 403 — so a wrong-driver call can't be told
    apart from a not-found one and can't confirm another driver's offer
    exists. An offer that existed for this driver but is no longer pending
    (declined, expired, claimed) gets 410, so the client can show "offer no
    longer available" instead of hanging.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Offer not found")

    # Batch dispatch never sets rides.driver_id until acceptance (see
    # get_active_ride above) — only an admin direct-assignment does, while
    # still awaiting this driver's accept/decline.
    is_direct_assigned = ride.get("driver_id") == driver["id"] and ride.get("status") == RideStatus.DRIVER_ASSIGNED

    offer_row: Optional[Dict[str, Any]] = None
    if not is_direct_assigned:
        try:
            _res = await db_supabase.run_sync(
                lambda: (
                    db_supabase.supabase.table("ride_offers")
                    .select("status, expires_at")
                    .eq("ride_id", ride_id)
                    .eq("driver_id", driver["id"])
                    .limit(1)
                    .execute()
                )
            )
            offer_row = _res.data[0] if getattr(_res, "data", None) else None
        except Exception as e:
            logger.error(
                f"get_ride_offer: ride_offers lookup failed ride_id={ride_id} driver_id={driver['id']}: {e}",
                exc_info=True,
            )
            raise HTTPException(status_code=503, detail="Could not verify offer status") from None

    is_pending_offer = bool(offer_row and offer_row.get("status") == "pending")
    if not (is_direct_assigned or is_pending_offer):
        # offer_row not None means a ride_offers row exists for this driver but
        # has already moved past 'pending' (declined/expired/accepted) — Gone,
        # not Not Found, so the client can tell "was live, now isn't" apart
        # from "never had one".
        if offer_row is not None:
            raise HTTPException(status_code=410, detail="Offer no longer available")
        raise HTTPException(status_code=404, detail="Offer not found")

    # Re-fetch `ride` fresh here rather than reusing the snapshot from the
    # very first read above (spinr-dispatch-reviewer finding, PR #5382): a
    # losing driver's own `ride_offers` row can still read "pending" for a
    # real, multi-round-trip window after the winner's accept_ride has
    # already flipped `rides.status` to driver_accepted — accept_ride only
    # flips OTHER drivers' `ride_offers` rows to 'preempted' several awaits
    # later (re-read ride, cache invalidation, insurance-period write,
    # acceptance-rate update, then the ride_offers updates themselves).
    # Using the pre-`ride_offers`-check snapshot for the status gate below
    # would let a request that lands in that exact window return 200 with
    # full offer detail (precise GPS + rider_rating) for a ride the calling
    # driver has already lost — leaking exactly the PII this endpoint exists
    # to protect, in the highest-contention case (a batch-dispatched offer
    # multiple drivers are racing). Re-fetching immediately before the
    # authorization decision shrinks that window to this one round trip.
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Offer not found")

    if is_direct_assigned:
        # Re-verify the direct assignment itself against the fresh read, not
        # just its status: an admin could have reassigned the ride to a
        # different driver (or moved it past driver_assigned) in the gap
        # since the first read, and a status-only check wouldn't catch a
        # reassignment that leaves the ride in the same driver_assigned state.
        if not (ride.get("driver_id") == driver["id"] and ride.get("status") == RideStatus.DRIVER_ASSIGNED):
            raise HTTPException(status_code=410, detail="Offer no longer available")
    else:
        # Batch-dispatch path: defense in depth alongside the ride_offers
        # status check above — mirrors decline_ride's own ride.status gate
        # (WS-18), now checked against the fresh read.
        if ride.get("status") not in (RideStatus.SEARCHING, RideStatus.DRIVER_ASSIGNED):
            raise HTTPException(status_code=410, detail="Offer no longer available")

    try:
        from ...utils.pii import first_name_only
    except ImportError:
        from utils.pii import first_name_only  # type: ignore

    try:
        rider = await db_supabase.get_user_by_id(ride["rider_id"])
    except Exception as e:
        logger.error(f"get_ride_offer: failed to load rider {ride.get('rider_id')}: {e}", exc_info=True)
        rider = None

    incentives, total_bonus = None, None
    try:
        incentives, total_bonus = incentive_display_payload(await match_ride_incentives(db_supabase, ride))
    except Exception as e:
        logger.error(f"get_ride_offer: incentive lookup failed: {e}", exc_info=True)

    quest_hint = None
    try:
        _qr = await db_supabase.run_sync(
            db_supabase.supabase.table("quest_progress")
            .select("current_value, status, quest:quests(title, target_value, reward_amount)")
            .eq("driver_id", current_user["id"])
            .eq("status", "active")
            .limit(1)
            .execute
        )
        if _qr.data:
            _qp = _qr.data[0]
            _q = _qp.get("quest") or {}
            _tv = float(_q.get("target_value") or 1)
            _cv = float(_qp.get("current_value") or 0)
            quest_hint = {
                "title": _q.get("title", ""),
                "current_value": _cv,
                "target_value": _tv,
                "progress_pct": round(min(_cv / _tv, 1.0) * 100, 1) if _tv else 0,
                "reward_amount": float(_q.get("reward_amount") or 0),
            }
    except Exception as e:
        logger.warning(f"get_ride_offer: quest hint lookup non-fatal: {e}")

    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    try:
        offer_timeout = int((await get_app_settings()).get("ride_offer_timeout_seconds", 15))
    except Exception:
        offer_timeout = 15

    # Same-shape expiry as matching.py's dispatch_payload / migration 224:
    # prefer the persisted ride_offers.expires_at (batch dispatch); fall back
    # to driver_notified_at + timeout for the admin direct-assign path, which
    # has no ride_offers row.
    offer_expires_at = offer_row.get("expires_at") if offer_row else None
    if not offer_expires_at and ride.get("driver_notified_at"):
        try:
            _notified_dt = parse_iso_utc(ride["driver_notified_at"])
            if _notified_dt:
                offer_expires_at = (_notified_dt + timedelta(seconds=offer_timeout)).isoformat()
        except Exception:
            offer_expires_at = None

    _surge_mult = float(ride.get("surge_multiplier") or 1.0)

    # Deliberately excludes offer_card_url, service_area_polygon and
    # planned_route_polyline: the first isn't removed from the FCM `data`
    # payload by minimal_fcm_offer_payload_enabled (only precise coordinates
    # and rider_rating are), so the client keeps reading it from `data`
    # unchanged; the latter two were never in the FCM payload in the first
    # place (existing `_FCM_EXCLUDE` entries, size-driven) and aren't needed
    # to render the offer panel before acceptance.
    return {
        "ride_id": ride["id"],
        "booking_id": ride["id"],
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "pickup_lat": ride.get("pickup_lat"),
        "pickup_lng": ride.get("pickup_lng"),
        "pickup_nav_lat": ride.get("pickup_nav_lat"),
        "pickup_nav_lng": ride.get("pickup_nav_lng"),
        "dropoff_lat": ride.get("dropoff_lat"),
        "dropoff_lng": ride.get("dropoff_lng"),
        "fare": ride.get("driver_earnings"),
        "distance_km": ride.get("distance_km"),
        "duration_minutes": ride.get("duration_minutes"),
        "rider_name": first_name_only(rider) or None,
        "rider_rating": (rider or {}).get("rating"),
        "requires_wav": bool(ride.get("requires_wav")),
        "quiet_mode": bool(ride.get("quiet_mode")),
        "is_scheduled": bool(ride.get("is_scheduled")),
        "scheduled_time": ride.get("scheduled_time"),
        "countdown_seconds": offer_timeout,
        "offer_expires_at": offer_expires_at,
        "surge_multiplier": _surge_mult if _surge_mult > 1.0 else None,
        "incentives": incentives,
        "total_bonus": total_bonus if total_bonus else None,
        "quest_hint": quest_hint,
        "payment_method": ride.get("payment_method"),
    }


@router.get("/rides/history")
async def get_ride_history(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    period: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get driver's ride history with optional status/period filtering."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    def history_start_for_period(period_value: Optional[str]) -> Optional[datetime]:
        if not period_value or period_value == "all":
            return None

        now = datetime.now(timezone.utc)
        if period_value == "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period_value == "week":
            return now - timedelta(days=7)
        if period_value == "month":
            return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return None

    def history_date_field(status_value: str) -> str:
        if status_value == RideStatus.COMPLETED.value:
            return "ride_completed_at"
        if status_value == RideStatus.CANCELLED.value:
            return "cancelled_at"
        if status_value == RideStatus.SCHEDULED.value:
            return "scheduled_time"
        return "created_at"

    def history_sort_key(ride: Dict[str, Any]) -> datetime:
        value = (
            ride.get("ride_completed_at")
            or ride.get("cancelled_at")
            or ride.get("scheduled_time")
            or ride.get("created_at")
        )
        return parse_iso_utc(value) or datetime.min.replace(tzinfo=timezone.utc)

    if status and status in ("completed", "cancelled", "scheduled"):
        status_filter = status
    else:
        status_filter = {"$in": list(RideStatus.terminal_statuses())}

    history_filter: Dict[str, Any] = {
        "driver_id": driver["id"],
        "status": status_filter,
    }
    period_start = history_start_for_period(period)

    if period_start and isinstance(status_filter, dict):
        total = 0
        rides = []
        page_limit = min(limit, 500)
        fetch_limit = offset + page_limit
        for terminal_status in (RideStatus.COMPLETED.value, RideStatus.CANCELLED.value):
            date_field = history_date_field(terminal_status)
            status_history_filter = {
                "driver_id": driver["id"],
                "status": terminal_status,
                date_field: {"$gte": period_start.isoformat()},
            }
            logger.info(f"[ride-history] driver={driver['id']} filter={status_history_filter}")
            total += await db_supabase.count_documents("rides", status_history_filter)
            rides.extend(
                await db_supabase.get_rows(
                    "rides",
                    status_history_filter,
                    order=date_field,
                    desc=True,
                    limit=fetch_limit,
                    offset=0,
                )
            )
        rides = sorted(rides, key=history_sort_key, reverse=True)[offset : offset + page_limit]
    else:
        order_field = "created_at"
        if isinstance(status_filter, str):
            order_field = history_date_field(status_filter)
            if period_start:
                history_filter[order_field] = {"$gte": period_start.isoformat()}

        logger.info(f"[ride-history] driver={driver['id']} filter={history_filter}")
        total = await db_supabase.count_documents("rides", history_filter)
        rides = await db_supabase.get_rows(
            "rides",
            history_filter,
            order=order_field,
            desc=True,
            limit=min(limit, 500),
            offset=offset,
        )
    logger.info(f"[ride-history] total={total} returned={len(rides)}")

    try:
        from ..rides import _redact_driver_location_fields
    except ImportError:
        from routes.rides import _redact_driver_location_fields
    for r in rides:
        _redact_driver_location_fields(r)

    # Enrich rides with incentive claims and earnings breakdown
    ride_ids = [r["id"] for r in rides if r.get("id")]
    incentive_map: Dict[str, float] = {}
    if ride_ids:
        try:
            claims_res = await db_supabase.run_sync(
                lambda: (
                    db_supabase.supabase.table("ride_incentive_claims")
                    .select("ride_id, bonus_amount")
                    .in_("ride_id", ride_ids)
                    .execute()
                )
            )
            claims = getattr(claims_res, "data", None) or []
            for c in claims:
                rid = str(c.get("ride_id", ""))
                incentive_map[rid] = incentive_map.get(rid, 0) + float(c.get("bonus_amount") or 0)
        except Exception:
            logger.debug("ride_incentive_claims lookup failed", exc_info=True)

    # Same flag + gating condition as routes/rides/queries.py's get_ride/{ride_id}
    # and get_ride_history's show_legacy_badge — fetched once per page here.
    # See docs/legacy-ride-history-presentation-plan.md Item 2/Section 6: only
    # the single-ride detail endpoint computed this until now, so a driver
    # scrolling their trip list saw no "Imported" distinction until tapping
    # into a specific ride.
    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    try:
        _settings = await get_app_settings() or {}
    except Exception:
        _settings = {}
    _legacy_badge_enabled = bool(_settings.get("legacy_ride_badge_enabled", False))

    for r in rides:
        r["show_legacy_badge"] = bool(_legacy_badge_enabled and r.get("legacy_import_metadata"))
        rid = str(r.get("id", ""))
        des = r.get("driver_earnings_snapshot")
        if des and isinstance(des, dict) and "total" in des:
            r["fare_only"] = round(float(des.get("fare") or 0), 2)
            r["cancel_fee_earned"] = round(float(des.get("cancel_fee") or 0), 2)
            r["tax_amount_total"] = round(float(des.get("tax") or 0), 2)
            _snap_inc = float(des.get("incentive") or 0)
            r["incentive_amount"] = round(max(_snap_inc, incentive_map.get(rid, 0)), 2)
            tip = float(des.get("tip") or 0)
            r["total_earned"] = round(
                r["fare_only"] + tip + r["incentive_amount"] + r["cancel_fee_earned"] + r["tax_amount_total"],
                2,
            )
        else:
            tip = float(r.get("tip_amount") or 0)
            fare_only = (
                float(r.get("base_fare") or 0) + float(r.get("distance_fare") or 0) + float(r.get("time_fare") or 0)
            )
            incentive = incentive_map.get(rid, 0)
            cancel_fee = float(r.get("cancellation_fee_driver") or 0)
            tax = float(r.get("tax_amount") or 0)
            if tax == 0:
                snap = r.get("fare_breakdown_snapshot") or {}
                for ln in snap.get("lines") or []:
                    if ln.get("type") in ("tax", "gst", "pst"):
                        tax += float(ln.get("amount") or 0)
                tax = round(tax, 2)
            r["fare_only"] = round(fare_only, 2)
            r["incentive_amount"] = round(incentive, 2)
            r["tax_amount_total"] = tax
            r["cancel_fee_earned"] = round(cancel_fee, 2)
            r["total_earned"] = round(fare_only + tip + incentive + cancel_fee + tax, 2)

    return {"total": total, "rides": [serialize_ride_for_driver(r) for r in rides]}
