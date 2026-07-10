"""Rider-side ride lifecycle actions (arrival sim, start, complete).

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    EVENT_END,
    EVENT_UPDATE,
    APIRouter,
    Decimal,
    Depends,
    HTTPException,
    Request,
    RideStatus,
    api_rate_limit,
    build_earnings_snapshot,
    datetime,
    get_current_user,
    logger,
    ride_action_limit,
    send_live_activity_update,
    timezone,
    uuid,
)
from ._shared import (  # noqa: F401
    _d,
)

router = APIRouter()


@router.post("/{ride_id}/simulate-arrival")
@api_rate_limit
async def simulate_driver_arrival(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Dev/test only: Simulate driver arriving at pickup, returns OTP."""
    if _deps._settings.ENV.lower() == "production":
        raise HTTPException(status_code=403, detail="Not available in production")
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    await _deps.db_supabase.update_ride(
        ride_id,
        {
            "status": RideStatus.DRIVER_ARRIVED,
            "driver_arrived_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    updated_ride = await _deps.db_supabase.get_ride(ride_id)
    return {"success": True, "pickup_otp": updated_ride.get("pickup_otp", "0000")}


@router.post("/{ride_id}/start")
@ride_action_limit
async def rider_start_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Start a ride. Restricted to the assigned driver only (R-P1-17)."""
    # R-P1-17: Only the assigned driver may mark a ride as started.
    if not current_user.get("is_driver"):
        raise HTTPException(status_code=403, detail="ERR_DRIVER_ONLY")
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    # Verify this driver is the one assigned to the ride
    driver_row = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver_row or ride.get("driver_id") != driver_row["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != RideStatus.DRIVER_ARRIVED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start ride with status: {ride.get('status')}",
        )

    # Atomic transition guards against duplicate taps / a concurrent driver-side
    # start. update_one returns None when zero rows matched (status already moved).
    guard = await _deps.db_supabase.update_one(
        "rides",
        {"id": ride_id, "driver_id": driver_row["id"], "status": RideStatus.DRIVER_ARRIVED},
        {
            "status": RideStatus.IN_PROGRESS,
            "ride_started_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in driver_arrived state")

    # Insurance Period 3 (passenger aboard — full TNC commercial coverage).
    # Only recorded once the transition actually took effect. Compliance-grade:
    # record_period_transition logs+swallows on failure, never blocks the start.
    await _deps.record_period_transition(driver_row["id"], 3, ride_id=ride_id)

    # Every state change must emit a WS event to both parties (CLAUDE.md). Without
    # this the rider's client stays on "driver arrived" until its next poll.
    rider_id = ride.get("rider_id")
    if rider_id:
        await _deps.manager.send_personal_message({"type": "ride_started", "ride_id": ride_id}, f"rider_{rider_id}")
        _deps.spawn(
            _deps.send_push_notification(
                rider_id,
                "Ride Started! ▶️",
                "Your ride has started. Have a safe trip!",
                data={"type": "ride_started", "ride_id": str(ride_id)},
            )
        )
    await _deps.manager.broadcast_ride_status(ride_id, RideStatus.IN_PROGRESS, rider_id=rider_id)
    # Update the rider's live activity to the in-progress state.
    _deps.spawn(send_live_activity_update({**ride, "status": RideStatus.IN_PROGRESS}, EVENT_UPDATE))
    return {"success": True}


@router.post("/{ride_id}/complete")
@ride_action_limit
async def rider_complete_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider-initiated ride completion (early end-ride).

    The rider pays the full agreed fare. We mark the ride completed and
    free the driver, mirroring the essential parts of
    drivers.py::complete_ride but skipping GPS aggregation (that data
    is still captured and available for admin review).
    """
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != RideStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Ride is not in progress")

    now = datetime.now(timezone.utc)
    update_fields = {
        "status": RideStatus.COMPLETED,
        "ride_completed_at": now,
        "updated_at": now,
    }
    # Atomic transition: only complete from in_progress. Guards against a
    # concurrent driver-side complete double-running the settlement/incentive
    # logic below. update_one returns None when zero rows matched.
    guard = await _deps.db_supabase.update_one(
        "rides", {"id": ride_id, "status": RideStatus.IN_PROGRESS}, update_fields
    )
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in progress")

    driver_id = ride.get("driver_id")
    driver_user_id = None
    if driver_id:
        await _deps.db_supabase.set_driver_available(driver_id, available=True, total_rides_inc=1)
        try:
            await _deps.record_period_transition(driver_id, 1)
        except Exception:
            logger.error(
                f"rider_complete_ride: period transition failed for driver {driver_id}",
                exc_info=True,
            )
        driver_row = await _deps.db_supabase.get_driver_by_id(driver_id)
        driver_user_id = driver_row.get("user_id") if driver_row else None

        # Daily Spinr Pass allowance: flip the driver offline now (DB-level) if
        # this completion used their last ride for the day. Driver WS notice is
        # sent at the end, after the ride_completed events.
        try:
            from ...utils.spinr_pass import force_offline_if_exhausted
        except ImportError:
            from utils.spinr_pass import force_offline_if_exhausted  # type: ignore
        try:
            # Pass the home service area explicitly so the quota day is anchored
            # on the driver's local timezone even if driver_row is unexpectedly
            # missing (force_offline only auto-resolves it from a dict driver).
            _quota_offline = await force_offline_if_exhausted(
                driver_row or driver_id, area_id=(driver_row or {}).get("service_area_id")
            )
        except Exception:
            _quota_offline = None
            logger.error("rider_complete_ride: quota offline check failed for driver=%s", driver_id, exc_info=True)
    else:
        _quota_offline = None

    # ── Record incentive claims (same logic as drivers.py complete_ride) ──
    _rider_incentive_total = Decimal("0")
    if driver_id:
        try:
            sa_id = ride.get("service_area_id")
            vt_id = ride.get("vehicle_type_id")
            iq = (
                _deps.db_supabase.supabase.table("ride_incentives")
                .select("id, bonus_amount, vehicle_type_id")
                .eq("is_active", True)
            )
            if sa_id:
                iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")
            else:
                iq = iq.is_("service_area_id", "null")
            inc_result = await _deps.db_supabase.run_sync(iq.execute)
            for inc in inc_result.data or []:
                if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
                    continue
                ba = Decimal(str(inc.get("bonus_amount") or 0))
                if ba <= 0:
                    continue
                await _deps.db_supabase.insert_one(
                    "ride_incentive_claims",
                    {
                        "id": str(uuid.uuid4()),
                        "ride_id": ride_id,
                        "driver_id": driver_id,
                        "incentive_id": inc["id"],
                        "bonus_amount": float(ba.quantize(Decimal("0.01"))),
                        "claimed_at": now.isoformat(),
                    },
                )
            _rider_incentive_total = sum(
                Decimal(str(inc.get("bonus_amount") or 0))
                for inc in (inc_result.data or [])
                if (not inc.get("vehicle_type_id") or inc["vehicle_type_id"] == vt_id)
                and Decimal(str(inc.get("bonus_amount") or 0)) > 0
            )
        except Exception:
            _rider_incentive_total = Decimal("0")
            logger.error(
                "rider_complete_ride: incentive claim failed for ride %s",
                ride_id,
                exc_info=True,
            )

    # ── Driver earnings snapshot ──
    try:
        _fare_d = _d(ride.get("base_fare") or 0) + _d(ride.get("distance_fare") or 0) + _d(ride.get("time_fare") or 0)
        await _deps.db_supabase.update_one(
            "rides",
            {"id": ride_id},
            {
                "driver_earnings_snapshot": build_earnings_snapshot(
                    fare=_fare_d,
                    tip=ride.get("tip_amount") or 0,
                    incentive=_rider_incentive_total,
                    tax=ride.get("tax_amount") or 0,
                    cancel_fee=ride.get("cancellation_fee_driver") or 0,
                )
            },
        )
    except Exception:
        logger.error("rider_complete_ride: driver_earnings_snapshot failed for ride %s", ride_id, exc_info=True)

    completed_ride = await _deps.db_supabase.get_ride(ride_id)
    total_fare = (completed_ride or {}).get("total_fare", ride.get("total_fare", 0))
    rider_bill = (completed_ride or {}).get("grand_total") or total_fare

    if driver_user_id:
        await _deps.manager.send_personal_message(
            {"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare},
            f"driver_{driver_user_id}",
        )
    await _deps.manager.send_personal_message(
        {"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare, "grand_total": rider_bill},
        f"rider_{current_user['id']}",
    )
    await _deps.manager.broadcast_ride_status(
        ride_id,
        RideStatus.COMPLETED,
        rider_id=current_user["id"],
        driver_user_id=driver_user_id,
        total_fare=total_fare,
    )
    # End the rider's live activity on trip completion.
    _deps.spawn(send_live_activity_update(completed_ride or {"id": ride_id, "status": RideStatus.COMPLETED}, EVENT_END))
    try:
        await _deps.manager.broadcast_to_admins(
            {
                "type": "ride_completed",
                "ride_id": ride_id,
                "total_fare": total_fare,
                "completed_by": "rider",
            }
        )
    except Exception as _bcast_err:
        logger.warning("admin broadcast failed for ride_completed %s: %s", ride_id, _bcast_err)

    # Advance any active driver quests this completion contributes to. Runs once
    # per ride because the atomic in_progress→completed guard above lets only the
    # winning completion path reach here. Scheduled as a background task so the
    # per-quest queries/updates never block the completion response (the ride is
    # already completed); the tracker swallows its own errors internally.
    if driver_id:
        try:
            try:
                from ...utils.quest_tracker import update_quest_progress_on_ride_complete
            except ImportError:
                from utils.quest_tracker import update_quest_progress_on_ride_complete
            _deps.spawn(update_quest_progress_on_ride_complete(driver_id, completed_ride or ride))
        except Exception:
            logger.error(
                "rider_complete_ride: scheduling quest progress update failed for ride %s", ride_id, exc_info=True
            )

    # Notify the driver (and admins) if this completion took them offline for
    # the day. Reuses the existing 'auto_offline' client handler.
    if _quota_offline and driver_user_id:
        _reset_h = round(_quota_offline.get("hours_until_reset") or 0)
        try:
            await _deps.manager.send_personal_message(
                {
                    "type": "auto_offline",
                    "reason": "quota_exhausted",
                    "message": (
                        f"You've used all {_quota_offline.get('rides_per_day')} Spinr Pass rides for "
                        f"today. You're now offline — your allowance resets in about {_reset_h}h."
                    ),
                    "quota_resets_at": _quota_offline.get("quota_resets_at"),
                },
                f"driver_{driver_user_id}",
            )
            await _deps.manager.broadcast_to_admins(
                {"type": "driver_status_changed", "driver_id": driver_id, "is_online": False}
            )
        except Exception:
            logger.warning("rider_complete_ride: quota auto_offline notify failed for driver=%s", driver_id)
        # Push so the driver sees it even with the app backgrounded.
        try:
            await _deps.send_push_notification(
                driver_user_id,
                "Daily ride limit reached",
                (
                    f"You've used all {_quota_offline.get('rides_per_day')} Spinr Pass rides for today. "
                    f"You're now offline — your allowance resets in about {_reset_h}h."
                ),
                data={"type": "quota_exhausted", "driver_id": str(driver_id)},
                target_app="driver",
            )
        except Exception:
            logger.warning("rider_complete_ride: quota push failed for driver=%s", driver_id)

    return completed_ride or ride
