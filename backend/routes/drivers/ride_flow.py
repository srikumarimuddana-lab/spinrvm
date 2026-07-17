"""Driver ride flow: accept, decline, arrive, OTP verify, start.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    EVENT_START,
    EVENT_UPDATE,
    AccountDisabledException,
    APIRouter,
    Depends,
    ErrorCode,
    ErrorKeys,
    HTTPException,
    RideStatus,
    SpinrException,
    _metric_inc,
    _metric_observe,
    asyncio,
    calculate_distance,
    datetime,
    db_supabase,
    diag_logger,
    get_current_user,
    hmac,
    invalidate_active_rides_cache,
    logger,
    parse_iso_utc,
    reset_miss_streak,
    send_live_activity_update,
    spawn,
    timezone,
)
from ._shared import (  # noqa: F401
    RideOTPRequest,
)

router = APIRouter()


@router.post("/rides/{ride_id}/accept")
async def accept_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    # F7: the driver profile and the ride row are independent reads — overlap
    # them instead of paying two serial round-trips inside the <2s accept
    # budget. Validation order below is unchanged.
    _driver_rows, ride = await asyncio.gather(
        db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1),
        db_supabase.get_ride(ride_id),
    )
    driver = _driver_rows[0] if _driver_rows else None
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    if driver.get("status") == "suspended":
        raise AccountDisabledException(
            message="Your account is suspended. Please renew your documents to continue driving.",
            message_key=ErrorKeys.AUTH_ACCOUNT_SUSPENDED,
            action_hint="Contact support",
        )

    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # A driver-user must never accept a ride they themselves created — prevents
    # self-dispatch fraud on dual-role accounts.
    if ride.get("rider_id") == current_user["id"]:
        raise HTTPException(status_code=403, detail="Cannot accept your own ride")

    # Idempotent replay: this driver already owns the ride. Duplicate accepts
    # are real — double-tap, the Notifee notification Accept action firing
    # alongside the in-app button, or a client retry after a dropped response.
    # Answering 409 "already accepted by another driver" here tells the WINNING
    # driver they lost their own ride. Checked before the subscription/quota
    # gates too: those were already enforced by the request that won.
    _POST_ACCEPT_STATUSES = (
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
        RideStatus.IN_PROGRESS,
    )
    if ride.get("driver_id") == driver["id"] and ride.get("status") in _POST_ACCEPT_STATUSES:
        diag_logger.info(
            f"[ACCEPT] idempotent replay ride_id={ride_id} driver_id={driver['id']} status={ride.get('status')}"
        )
        return {"success": True, "already_accepted": True}

    # Subscription guard: if the ride's service area requires a Spinr Pass,
    # verify the driver has an active subscription before allowing acceptance.
    # This is the last-resort gate — go-online and dispatch already block
    # unsubscribed drivers, but a driver whose subscription expired mid-shift
    # (or who was grandfathered online before the policy was enabled) could
    # still reach this point.
    if ride.get("service_area_id"):
        try:
            # F7: fetch the area row and the driver's active subscription
            # concurrently — the sub row is only *used* when the area (or its
            # parent) requires a pass, but in pass-required markets that's the
            # common case, and the speculative read is one indexed lookup.
            _ride_area, _sub_rows = await asyncio.gather(
                db_supabase.find_one("service_areas", {"id": ride["service_area_id"]}),
                db_supabase.get_rows(
                    "driver_subscriptions",
                    {"driver_id": driver["id"], "status": "active"},
                    limit=1,
                ),
            )
            # Finding F: child areas (airport sub-regions) inherit subscription_required
            # from their parent; check parent when child flag is False.
            _accept_sub_required = bool(_ride_area and _ride_area.get("subscription_required"))
            if not _accept_sub_required and _ride_area and _ride_area.get("parent_service_area_id"):
                _parent = await db_supabase.find_one("service_areas", {"id": _ride_area["parent_service_area_id"]})
                _accept_sub_required = bool(_parent and _parent.get("subscription_required"))
            if _accept_sub_required:
                _active_sub = _sub_rows[0] if _sub_rows else None
                # Expiry check: the background sweeper runs periodically so an
                # active row may have passed expires_at before it was flipped.
                if _active_sub and _active_sub.get("expires_at"):
                    _exp = parse_iso_utc(_active_sub["expires_at"])
                    if _exp is not None and _exp < datetime.now(timezone.utc):
                        await db_supabase.update_one(
                            "driver_subscriptions", {"id": _active_sub["id"]}, {"status": "expired"}
                        )
                        _active_sub = None
                # Plan scope checks: service_areas and vehicle_types allowlists.
                # Plans with null/empty lists apply globally.
                if _active_sub:
                    _sub_plan = await db_supabase.find_one("subscription_plans", {"id": _active_sub.get("plan_id")})
                    if _sub_plan:
                        _plan_areas = _sub_plan.get("service_areas")
                        if _plan_areas and ride["service_area_id"] not in _plan_areas:
                            # Also accept plans covering the parent area (child areas inherit).
                            _accept_parent_id = (_ride_area or {}).get("parent_service_area_id")
                            if not (_accept_parent_id and _accept_parent_id in _plan_areas):
                                _active_sub = None
                        if _active_sub:
                            _plan_vt = _sub_plan.get("vehicle_types")
                            if _plan_vt and driver.get("vehicle_type_id") not in _plan_vt:
                                _active_sub = None
                if not _active_sub:
                    raise SpinrException(
                        message="An active Spinr Pass subscription is required to accept rides in this area.",
                        error_code=ErrorCode.PAYMENT_FAILED,
                        status_code=402,
                        message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                        action_hint="Subscribe to Spinr Pass",
                    )
        except SpinrException:
            raise
        except Exception:
            # Last-resort gate: fail closed so a DB error cannot bypass
            # subscription enforcement in a required area.
            logger.error("accept_ride: subscription check failed for driver=%s", driver["id"], exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Could not verify subscription for this area. Please try again.",
            ) from None

    # Daily ride-allowance gate — independent of area. Whenever the accepting
    # driver holds a finite Spinr Pass that's used up for the local calendar
    # day, block the accept (403) until it resets at midnight. No-op for
    # unlimited / no pass / rides-remaining; fails open on lookup error so a
    # transient fault never strands an otherwise-eligible driver.
    try:
        from ...utils.spinr_pass import assert_quota_available
    except ImportError:
        from utils.spinr_pass import assert_quota_available  # type: ignore
    # Quota day anchored on the driver's home service-area timezone (Regina
    # fallback), matching go-online and the /subscription/current display.
    await assert_quota_available(driver["id"], area_id=driver.get("service_area_id"))

    diag_logger.info(
        f"[ACCEPT] entry ride_id={ride_id} driver_id={driver.get('id')} "
        f"pre_status={ride.get('status')} pre_driver_id={ride.get('driver_id')}"
    )

    # Verify this driver was assigned
    if ride.get("driver_id") != driver["id"]:
        # Broadcast/searching path: only allow if a pending ride_offers row
        # exists for this driver. Without this check, any driver who learns a
        # ride_id (from WS, logs, or guessing) can bypass dispatch rules —
        # proximity, WAV eligibility, verification status, fraud filters.
        if ride["status"] == RideStatus.SEARCHING:
            pending_offer = None
            try:
                offer_rows = await db_supabase.get_rows(
                    "ride_offers",
                    {"ride_id": ride_id, "driver_id": driver["id"], "status": "pending"},
                    limit=1,
                )
                pending_offer = offer_rows[0] if offer_rows else None
            except Exception:
                logger.error(
                    "accept_ride: ride_offers lookup failed ride=%s driver=%s", ride_id, driver["id"], exc_info=True
                )
            if not pending_offer:
                raise HTTPException(status_code=403, detail="No active offer for this ride")
        else:
            diag_logger.info(
                f"[ACCEPT] ride_id={ride_id} not assigned to this driver: "
                f"ride.driver_id={ride.get('driver_id')} != this_driver.id={driver['id']} "
                f"and status={ride.get('status')} != 'searching'"
            )
            raise HTTPException(status_code=400, detail="Ride not assigned to you")

    # Atomic conditional UPDATE — only succeeds if the ride is still in the
    # expected pre-acceptance state. Prevents the double-accept race where two
    # concurrent requests both pass the read-based check above and both write.
    if ride.get("driver_id") == driver["id"]:
        accept_filter = {
            "id": ride_id,
            "status": RideStatus.DRIVER_ASSIGNED,
            "driver_id": driver["id"],
        }
    else:
        # Broadcast/searching path: claim only if the ride is still unclaimed
        # AND no driver_id is set. Belt-and-suspenders against the offer-expiry
        # race where the revert-to-searching window has a stale request from a
        # previously-assigned driver still in flight.
        accept_filter = {"id": ride_id, "status": RideStatus.SEARCHING, "driver_id": None}

    guard = await _deps.db.update_one(
        "rides",
        accept_filter,
        {
            "$set": {
                "status": RideStatus.DRIVER_ACCEPTED,
                "driver_id": driver["id"],
                "driver_accepted_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    # update_one returns None when no rows matched the filter (race lost).
    # The old `hasattr(guard, "modified_count")` check was never true for
    # Supabase responses, so the double-accept guard was silently disabled.
    if guard is None:
        # Before declaring the race lost, re-read: a concurrent duplicate from
        # THIS driver (double-tap / retry) may have won the claim between our
        # initial read and this update. That's a success for this driver, not
        # "taken by another driver".
        current = await _deps.db.find_one("rides", {"id": ride_id})
        if current and current.get("driver_id") == driver["id"] and current.get("status") in _POST_ACCEPT_STATUSES:
            diag_logger.info(
                f"[ACCEPT] concurrent duplicate accept won by same driver "
                f"ride_id={ride_id} driver_id={driver['id']} status={current.get('status')}"
            )
            return {"success": True, "already_accepted": True}
        diag_logger.info(
            f"[ACCEPT] claim rejected ride_id={ride_id} "
            f"current_status={(current or ride).get('status')} "
            f"current_driver_id={(current or ride).get('driver_id')}"
        )
        raise SpinrException(
            message="Ride already accepted by another driver",
            error_code=ErrorCode.RESOURCE_CONFLICT,
            status_code=409,
            message_key=ErrorKeys.RIDE_TAKEN,
            action_hint="Pick another ride",
        )

    # Re-read the now-claimed ride so we can notify the rider with fresh data.
    ride = await _deps.db.find_one("rides", {"id": ride_id})
    diag_logger.info(
        f"[ACCEPT] success ride_id={ride_id} driver_id={driver['id']} "
        f"post_status={ride.get('status') if ride else 'ROW_GONE'}"
    )

    await reset_miss_streak(driver["id"])

    # The WS location hot path caches the driver's active rides for 5s
    # (B3.1) — drop it so the first post-accept pings attach to this ride
    # and reach the rider, instead of being served a stale empty list.
    await invalidate_active_rides_cache(driver["id"])

    # Insurance Period 2 (en route to pickup — TNC primary commercial coverage).
    # In the batch-offer dispatch model the driver becomes obligated to the ride
    # at acceptance (searching/driver_assigned → driver_accepted), so Period 2
    # begins here, not at a separate driver_assigned step. It stays open through
    # driver_arrived until verify-otp/start flips the driver to Period 3
    # (passenger aboard). record_period_transition is compliance-grade — it logs
    # at ERROR and swallows on failure so it never blocks acceptance.
    await _deps.record_period_transition(driver["id"], 2, ride_id=ride_id)

    # ── Batch dispatch: resolve offers for this ride ──────────────
    try:
        from ...repositories.driver_repo import update_acceptance_rate
    except ImportError:
        from repositories.driver_repo import update_acceptance_rate  # type: ignore

    await update_acceptance_rate(driver["id"], accepted=True)
    _metric_inc("spinr_dispatch_offer_accepted_total")

    # Mark winner's offer as accepted, expire losers, release them
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        winner_res = await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("ride_offers")
                .update({"status": "accepted", "responded_at": now_iso})
                .eq("ride_id", ride_id)
                .eq("driver_id", driver["id"])
                .eq("status", "pending")
                .execute()
            )
        )
        # Offer-to-accept latency from the winner's own offer row (KPI:
        # P95 dispatch offer → accept < 2s). Direct-assignment rides have
        # no pending offer row — counter only, no duration sample.
        winner_rows = getattr(winner_res, "data", None) or []
        offered_at = parse_iso_utc(winner_rows[0].get("offered_at")) if winner_rows else None
        if offered_at:
            _metric_observe(
                "spinr_dispatch_offer_to_accept_duration_ms",
                (datetime.now(timezone.utc) - offered_at).total_seconds() * 1000.0,
            )
        losers = await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("ride_offers")
                .select("driver_id")
                .eq("ride_id", ride_id)
                .eq("status", "pending")
                .execute()
            )
        )
        loser_ids = [r["driver_id"] for r in (losers.data or [])]
        if loser_ids:
            # 'preempted' (not 'expired'): these drivers didn't ignore/time out —
            # another driver simply accepted first. Analytics must not count this
            # against their ignore rate.
            await db_supabase.run_sync(
                lambda: (
                    db_supabase.supabase.table("ride_offers")
                    .update({"status": "preempted", "responded_at": now_iso})
                    .eq("ride_id", ride_id)
                    .eq("status", "pending")
                    .execute()
                )
            )

            # P2: each loser is an independent driver — release + notify them
            # concurrently instead of one serial chain of DB/WS round-trips.
            async def _release_loser(lid: str) -> None:
                await db_supabase.set_driver_available(lid, True)
                await _deps.record_period_transition(lid, 1)
                try:
                    loser_drv = await db_supabase.get_driver_by_id(lid)
                    loser_uid = (loser_drv or {}).get("user_id")
                    if loser_uid:
                        await _deps.manager.send_personal_message(
                            {"type": "ride_taken", "ride_id": ride_id},
                            f"driver_{loser_uid}",
                        )
                except Exception as e:
                    logger.warning(f"Failed to send ride_taken WS to loser driver {lid}: {e}")

            await asyncio.gather(*(_release_loser(lid) for lid in loser_ids))
    except Exception as e:
        logger.error(f"[ACCEPT] batch offer cleanup failed for ride {ride_id}: {e}", exc_info=True)

    # Capture the pickup-leg ESTIMATE shown to the rider at the moment of
    # acceptance. This is the only piece of ride_metrics with no other home —
    # everything else is either already on the row (planned/actual trip
    # distance & duration) or derived at completion from driver_insurance_periods.
    # Failure here must not block acceptance; ride_metrics is a read cache, not
    # a regulatory artefact.
    try:
        pickup_lat = (ride or {}).get("pickup_lat")
        pickup_lng = (ride or {}).get("pickup_lng")
        driver_lat = driver.get("lat")
        driver_lng = driver.get("lng")
        if pickup_lat and pickup_lng and driver_lat and driver_lng:
            pickup_km = round(
                calculate_distance(driver_lat, driver_lng, pickup_lat, pickup_lng),
                3,
            )
            # Mirror the rider-app ETA formula used at /rides/estimate so the
            # value we store equals the value the rider was shown.
            pickup_minutes = max(2, int(pickup_km / 30 * 60) + 1)
            existing_metrics = (ride or {}).get("ride_metrics") or {}
            phases = dict(existing_metrics.get("phases") or {})
            phases["navigating_to_pickup"] = {
                "estimated_distance_km": pickup_km,
                "estimated_duration_minutes": pickup_minutes,
            }
            await _deps.db.update_one(
                "rides",
                {"id": ride_id},
                {"ride_metrics": {**existing_metrics, "phases": phases}},
            )
    except Exception as metrics_err:
        logger.error(
            "accept_ride: ride_metrics pickup-leg write failed for ride %s: %s",
            ride_id,
            metrics_err,
            exc_info=True,
        )

    # Notify rider via both WebSocket (for the instant in-app
    # transition) AND FCM push (so the rider still gets the update
    # if the app was backgrounded when the driver accepted).
    # The `data` payload lets the rider-app foreground FCM handler in
    # app/_layout.tsx route the event without reparsing the title.
    if ride and ride.get("rider_id"):
        await _deps.manager.send_personal_message(
            {"type": "driver_accepted", "ride_id": ride_id}, f"rider_{ride['rider_id']}"
        )
        # Backgrounded like the arrive/start siblings: FCM is a slow external
        # round-trip and accept sits inside the <2s dispatch SLA. The WS send
        # above stays awaited — it drives the instant in-app transition.
        spawn(
            _deps.send_push_notification(
                ride["rider_id"],
                "Driver Assigned! 🚗",
                "Your driver has accepted the ride and is on the way.",
                data={"type": "driver_accepted", "ride_id": str(ride_id)},
            )
        )
        if ride.get("guest_booking"):
            # Corporate guest customer (no app): driver + vehicle + pickup
            # OTP + live tracking link by SMS. No-ops if the customer
            # claimed their account since booking (is_guest flipped — the
            # push above already reaches them).
            try:
                from ...services.guest_notification_service import notify_guest_driver_assigned
            except ImportError:
                from services.guest_notification_service import notify_guest_driver_assigned  # type: ignore
            spawn(notify_guest_driver_assigned(dict(ride), dict(driver)))
    await _deps.manager.broadcast_ride_status(
        ride_id,
        RideStatus.DRIVER_ACCEPTED,
        rider_id=(ride or {}).get("rider_id"),
        # Post-claim re-read (line ~235): rides.version bumped by the migration-225
        # trigger on the accept UPDATE. Lets clients order this driver_accepted
        # event ahead of any stale searching/offer event still in flight.
        version=(ride or {}).get("version"),
    )

    # Start the rider's live activity (no-op until the app registers its token).
    if ride:
        spawn(send_live_activity_update(ride, EVENT_START))

    return {"success": True}


@router.post("/rides/{ride_id}/decline")
async def decline_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("status") not in ("searching", "driver_assigned"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot decline ride in status '{ride.get('status')}'",
        )

    # ── Batch dispatch: update this driver's offer row ───────────
    try:
        from ...repositories.driver_repo import update_acceptance_rate
    except ImportError:
        from repositories.driver_repo import update_acceptance_rate  # type: ignore

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("ride_offers")
                .update({"status": "declined", "responded_at": now_iso})
                .eq("ride_id", ride_id)
                .eq("driver_id", driver["id"])
                .eq("status", "pending")
                .execute()
            )
        )
    except Exception as e:
        logger.error(f"[DECLINE] ride_offers update failed: {e}", exc_info=True)

    await update_acceptance_rate(driver["id"], accepted=False)

    # Release this driver back to available
    await db_supabase.set_driver_available(driver["id"], True)
    await _deps.record_period_transition(driver["id"], 1)
    await reset_miss_streak(driver["id"])

    # Record the decline in audit_logs so daily stats can count it
    try:
        import uuid as _uuid

        await _deps.db.insert_one(
            "audit_logs",
            {
                "id": str(_uuid.uuid4()),
                "action": "ride_declined",
                "entity_type": "ride",
                "entity_id": ride_id,
                "actor_id": driver["id"],
                "details": {"driver_id": driver["id"]},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as _e:
        logger.error(f"Could not log ride decline to audit_logs: {_e}", exc_info=True)

    # Cooldown: skip this driver for 5 minutes on the next dispatch cycle
    try:
        from ...utils.redis_client import redis_set as _redis_set  # type: ignore
    except ImportError:
        from utils.redis_client import redis_set as _redis_set  # type: ignore
    try:
        await _redis_set(f"spinr:offer_skip:{ride_id}:{driver['id']}", "1", ttl=300)
    except Exception as _e:
        logger.error(f"Could not set offer cooldown key for ride {ride_id}: {_e}", exc_info=True)

    # Early resolution: if no pending offers remain and ride is still
    # searching, re-dispatch immediately instead of waiting for batch timeout.
    try:
        try:
            from ..rides import match_driver_to_ride
        except ImportError:
            from rides import match_driver_to_ride  # type: ignore

        fresh_ride = await db_supabase.get_ride(ride_id)
        if fresh_ride and fresh_ride.get("status") == "searching":
            remaining = await db_supabase.run_sync(
                lambda: (
                    db_supabase.supabase.table("ride_offers")
                    .select("id")
                    .eq("ride_id", ride_id)
                    .eq("status", "pending")
                    .execute()
                )
            )
            if not (remaining.data or []):
                spawn(match_driver_to_ride(ride_id))
                logger.info(f"[DECLINE] all offers resolved for ride {ride_id} — re-dispatching")
            else:
                logger.info(f"[DECLINE] ride {ride_id} still has {len(remaining.data)} pending offer(s)")
    except Exception as e:
        logger.error(f"Could not check/trigger re-match for ride {ride_id}: {e}", exc_info=True)

    return {"success": True}


@router.post("/rides/{ride_id}/arrive")
async def arrive_at_pickup(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver["id"]}, limit=1)
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Geofence check - verify driver is within 200m of pickup location.
    # Accept arrival within radius of EITHER the rider's exact pin OR the
    # road-snapped nav point we actually navigated the driver to — for a pin
    # dropped inside a mall/airport these can differ by more than the radius, and
    # a driver who followed our navigation must not be rejected as too far.
    ARRIVAL_RADIUS_KM = 0.2  # 200 meters
    driver_lat = driver.get("lat", 0)
    driver_lng = driver.get("lng", 0)
    targets = []
    if ride.get("pickup_lat") and ride.get("pickup_lng"):
        targets.append((ride["pickup_lat"], ride["pickup_lng"]))
    if ride.get("pickup_nav_lat") is not None and ride.get("pickup_nav_lng") is not None:
        targets.append((ride["pickup_nav_lat"], ride["pickup_nav_lng"]))

    if driver_lat and driver_lng and targets:
        nearest_km = min(calculate_distance(driver_lat, driver_lng, t[0], t[1]) for t in targets)
        if nearest_km > ARRIVAL_RADIUS_KM:
            distance_m = int(nearest_km * 1000)
            raise HTTPException(
                status_code=400,
                detail=f"You are {distance_m}m away from the pickup. "
                f"Please move within 200m of the pickup location to mark arrival.",
            )

    guard = await _deps.db.update_one(
        "rides",
        {
            "id": ride_id,
            "driver_id": driver["id"],
            "status": RideStatus.DRIVER_ACCEPTED,
        },
        {
            "$set": {
                "status": RideStatus.DRIVER_ARRIVED,
                "driver_arrived_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in driver_accepted state")

    if ride.get("rider_id"):
        await _deps.manager.send_personal_message(
            {"type": "driver_arrived", "ride_id": ride_id}, f"rider_{ride['rider_id']}"
        )
        spawn(
            _deps.send_push_notification(
                ride["rider_id"],
                "Driver Arrived! 📍",
                "Your driver has arrived at the pickup location.",
                data={"type": "driver_arrived", "ride_id": str(ride_id)},
            )
        )
        if ride.get("guest_booking"):
            # Guest customer: "give the driver code XXXX" by SMS.
            try:
                from ...services.guest_notification_service import notify_guest_driver_arrived
            except ImportError:
                from services.guest_notification_service import notify_guest_driver_arrived  # type: ignore
            spawn(notify_guest_driver_arrived(dict(ride)))
    await _deps.manager.broadcast_ride_status(ride_id, RideStatus.DRIVER_ARRIVED, rider_id=ride.get("rider_id"))

    # Update the rider's live activity to "driver arrived".
    spawn(send_live_activity_update({**ride, "status": RideStatus.DRIVER_ARRIVED}, EVENT_UPDATE))

    return {"success": True}


@router.post("/rides/{ride_id}/verify-otp")
async def verify_pickup_otp(
    ride_id: str,
    request: RideOTPRequest,
    current_user: dict = Depends(get_current_user),
):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver["id"]}, limit=1)
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    stored_otp = ride.get("pickup_otp", "")
    if not hmac.compare_digest(stored_otp, request.otp):
        raise HTTPException(status_code=400, detail="Invalid OTP")

    # OTP correct — atomic transition guards against duplicate taps/retries
    guard = await _deps.db.update_one(
        "rides",
        {"id": ride_id, "driver_id": driver["id"], "status": RideStatus.DRIVER_ARRIVED},
        {
            "$set": {
                "status": RideStatus.IN_PROGRESS,
                "ride_started_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in driver_arrived state")
    # M-5: SGI insurance period audit — in_progress = period 3 (passenger
    # aboard, full TNC commercial coverage). Only record when transition took effect.
    await _deps.record_period_transition(driver["id"], 3, ride_id=ride_id)

    if ride.get("rider_id"):
        await _deps.manager.send_personal_message(
            {"type": "ride_started", "ride_id": ride_id}, f"rider_{ride['rider_id']}"
        )
        spawn(
            _deps.send_push_notification(
                ride["rider_id"],
                "Ride Started! ▶️",
                "Your ride has started. Have a safe trip!",
                data={"type": "ride_started", "ride_id": str(ride_id)},
            )
        )
    await _deps.manager.broadcast_ride_status(ride_id, RideStatus.IN_PROGRESS, rider_id=ride.get("rider_id"))

    # Update the rider's live activity to "trip in progress".
    spawn(send_live_activity_update({**ride, "status": RideStatus.IN_PROGRESS}, EVENT_UPDATE))

    return {"success": True}


@router.post("/rides/{ride_id}/start")
async def start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Start ride without OTP — disabled in production.

    In production all trip starts must go through POST /rides/{id}/verify-otp
    so the rider's presence is confirmed before the meter starts. The no-OTP
    path exists only as a dev/staging fallback (e.g. automated E2E tests).
    """
    try:
        from ...core.config import settings as _settings
    except ImportError:
        from core.config import settings as _settings  # type: ignore

    if _settings.ENV.lower() == "production":
        raise HTTPException(
            status_code=410,
            detail="Use POST /rides/{ride_id}/verify-otp to start a ride in production.",
        )
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver["id"]}, limit=1)
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    guard = await _deps.db.update_one(
        "rides",
        {"id": ride_id, "driver_id": driver["id"], "status": RideStatus.DRIVER_ARRIVED},
        {
            "$set": {
                "status": RideStatus.IN_PROGRESS,
                "ride_started_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in driver_arrived state")
    # M-5: SGI insurance period audit — in_progress = period 3 (passenger
    # aboard, full TNC commercial coverage). Only record when transition took effect.
    await _deps.record_period_transition(driver["id"], 3, ride_id=ride_id)

    if ride.get("rider_id"):
        await _deps.manager.send_personal_message(
            {"type": "ride_started", "ride_id": ride_id}, f"rider_{ride['rider_id']}"
        )
        spawn(
            _deps.send_push_notification(
                ride["rider_id"],
                "Ride Started! ▶️",
                "Your ride has started. Have a safe trip!",
                data={"type": "ride_started", "ride_id": str(ride_id)},
            )
        )
    await _deps.manager.broadcast_ride_status(ride_id, RideStatus.IN_PROGRESS, rider_id=ride.get("rider_id"))
    # Update the rider's live activity to "trip in progress" (dev/staging path).
    spawn(send_live_activity_update({**ride, "status": RideStatus.IN_PROGRESS}, EVENT_UPDATE))
    return {"success": True}
