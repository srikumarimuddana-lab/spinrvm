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
    driver_tax_portion,
    get_current_user,
    get_service_area_polygon,
    logger,
    parse_iso_utc,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    serialize_doc,
)

router = APIRouter()


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

    # R-P1-28: Strip PII fields from the rider object — drivers only need
    # first name + profile photo for the in-app UI. Phone, email, and
    # Stripe customer ID must never be exposed to the driver.
    safe_rider = None
    if rider:
        raw = serialize_doc(rider)
        safe_rider = {k: raw[k] for k in raw if k not in {"phone", "email", "stripe_customer_id"}}

    # Enrich with incentives + quest progress for driver_assigned rides
    # so fetchActiveRide never strips enrichment data from the offer panel.
    incentives = None
    total_bonus = None
    quest_hint = None
    if ride.get("status") == RideStatus.DRIVER_ASSIGNED.value:
        try:
            iq = (
                db_supabase.supabase.table("ride_incentives")
                .select("name, bonus_amount, incentive_type, service_area_id, vehicle_type_id")
                .eq("is_active", True)
            )
            sa_id = ride.get("service_area_id")
            if sa_id:
                iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")
            ir = await db_supabase.run_sync(iq.execute)
            vt_id = ride.get("vehicle_type_id")
            _inc_list = []
            _bonus = 0.0
            for inc in ir.data or []:
                if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
                    continue
                ba = float(inc.get("bonus_amount") or 0)
                _inc_list.append(
                    {
                        "name": inc["name"],
                        "bonus_amount": ba,
                        "incentive_type": inc.get("incentive_type", "per_ride"),
                    }
                )
                _bonus += ba
            if _inc_list:
                incentives = _inc_list
                total_bonus = _bonus
        except Exception as e:
            logger.error(f"get_active_ride: incentive lookup failed: {e}", exc_info=True)

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
        "ride": serialize_doc(ride),
        "rider": safe_rider,
        "vehicle_type": serialize_doc(vehicle_type) if vehicle_type else None,
        "incentives": incentives,
        "total_bonus": total_bonus,
        "quest_hint": quest_hint,
        "service_area_polygon": service_area_polygon,
    }


@router.get("/rides/history")
async def get_ride_history(
    limit: int = Query(20),
    offset: int = Query(0),
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
            claims = (
                db_supabase.supabase.table("ride_incentive_claims")
                .select("ride_id, bonus_amount")
                .in_("ride_id", ride_ids)
                .execute()
            ).data or []
            for c in claims:
                rid = str(c.get("ride_id", ""))
                incentive_map[rid] = incentive_map.get(rid, 0) + float(c.get("bonus_amount") or 0)
        except Exception:
            logger.debug("ride_incentive_claims lookup failed", exc_info=True)

    for r in rides:
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
            tax = float(driver_tax_portion(r) or 0)
            if tax == 0 and not r.get("tax_split"):
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

    return {"total": total, "rides": [serialize_doc(r) for r in rides]}
