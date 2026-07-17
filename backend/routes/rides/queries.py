"""Rider-facing ride reads: active, history, stats, scheduled, detail.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    Decimal,
    Depends,
    DriverPublicView,
    ErrorKeys,
    HTTPException,
    Optional,
    Query,
    Request,
    RideNotFoundException,
    RideStatus,
    datetime,
    get_current_user,
    logger,
    ride_read_limit,
    timedelta,
)
from ._shared import (  # noqa: F401
    _actual_duration_minutes,
    _build_fare_breakdown,
    _d,
    _f,
    _redact_driver_location_fields,
    _rider_visible_photo,
    _round,
    _sum_fare_breakdown,
)

router = APIRouter()


@ride_read_limit
@router.get("/active")
async def get_active_ride(request: Request = None, current_user: dict = Depends(get_current_user)):
    """Get rider's current active/pending ride (if any). Used on app launch to resume."""
    # First check for rides that need payment (completed but not paid)
    # Then check for active rides
    active_statuses = list(RideStatus.active_statuses())

    # Check for unpaid completed ride first (must pay before new ride).
    # ``waived_admin`` is the terminal value written by admin force-complete
    # (admin/rides.py admin_complete_ride) — no real charge happened, but
    # the rider must not be trapped on the payment screen.
    unpaid_ride = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows(
            "rides",
            {
                "rider_id": current_user["id"],
                "status": RideStatus.COMPLETED,
                "payment_status": {"$nin": ["paid", "waived_admin"]},
            },
            limit=1,
        )
    )
    if unpaid_ride:
        ride = unpaid_ride
    else:
        ride = (lambda _r: _r[0] if _r else None)(
            await _deps.db_supabase.get_rows(
                "rides",
                {
                    "rider_id": current_user["id"],
                    "status": {"$in": active_statuses},
                },
                limit=1,
            )
        )

    if not ride:
        return {"active": False, "ride": None}

    # Attach driver info if assigned
    driver = None
    if ride.get("driver_id"):
        driver = await _deps.db_supabase.get_driver_by_id(ride["driver_id"])
        if driver:
            user = await _deps.db_supabase.get_user_by_id(driver.get("user_id"))
            driver = {
                "id": driver["id"],
                "driver_code": driver.get("driver_code"),
                "name": (f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Driver"),
                "rating": driver.get("rating", 4.8),
                "total_rides": driver.get("total_rides", 0),
                # Driver photo lives on the USER row (users.profile_image),
                # shown to riders only once admin-approved.
                "photo_url": _rider_visible_photo(user),
                "vehicle_make": driver.get("vehicle_make"),
                "vehicle_model": driver.get("vehicle_model"),
                "vehicle_color": driver.get("vehicle_color"),
                "vehicle_year": driver.get("vehicle_year"),
                "license_plate": driver.get("license_plate"),
                "lat": driver.get("lat"),
                "lng": driver.get("lng"),
                "heading": driver.get("heading"),
            }

    def serialize_doc(doc):
        return doc

    ride_data = serialize_doc(ride)
    ride_data["driver"] = driver
    return {"active": True, "ride": ride_data}


_RIDE_HISTORY_VISIBLE_OR = (
    f"status.eq.{RideStatus.COMPLETED.value},and(status.eq.{RideStatus.CANCELLED.value},driver_id.not.is.null)"
)


def _ride_history_cursor_or(cursor_ts: Optional[str], before: Optional[str]) -> Optional[str]:
    if not cursor_ts or not before:
        return None
    return f"created_at.lt.{cursor_ts},and(created_at.eq.{cursor_ts},id.lt.{before})"


async def _fetch_ride_history_page(
    *,
    rider_id: str,
    limit: int,
    cursor_ts: Optional[str],
    before: Optional[str],
) -> list[dict]:
    """Fetch one stable ride-history page, including one extra row for has-more."""
    if not _deps.db_supabase.supabase:
        return []

    cursor_clause = _ride_history_cursor_or(cursor_ts, before)

    def _fn():
        q = _deps.db_supabase.supabase.table("rides").select("*").eq("rider_id", rider_id).or_(_RIDE_HISTORY_VISIBLE_OR)
        if cursor_clause:
            q = q.or_(cursor_clause)
        q = q.order("created_at", desc=True).order("id", desc=True).limit(limit + 1)
        res = q.execute()
        return getattr(res, "data", None) or []

    return await _deps.db_supabase.run_sync(_fn)


@ride_read_limit
@router.get("/history")
async def get_ride_history(
    request: Request = None,
    limit: int = Query(default=20, ge=1, le=100),
    before: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Get rider's past rides for the activity tab with cursor-based pagination.

    Pass ``before=<ride_id>`` to fetch the page of rides older than that ride.
    Returns ``next_cursor`` (the id of the last ride in this page) to use as
    the next ``before`` value, or ``null`` when there are no more pages.
    """
    # Resolve cursor to a timestamp so we can push the predicate to the DB.
    # Fetching 500 rows and slicing in Python caused O(n) reads on busy accounts.
    cursor_ts = None
    if before:
        cursor_ride = await _deps.db_supabase.find_one("rides", {"id": before, "rider_id": current_user["id"]})
        if cursor_ride:
            cursor_ts = cursor_ride.get("created_at")

    candidates = await _fetch_ride_history_page(
        rider_id=current_user["id"],
        limit=limit,
        cursor_ts=cursor_ts,
        before=before,
    )
    rides = candidates[:limit]
    has_more = len(candidates) > limit

    try:
        _settings = await _deps.get_app_settings()
        _fare_locked = _settings.get("fare_lock_enabled", False) if _settings else False
    except Exception:
        _fare_locked = False

    for r in rides:
        snap = r.get("fare_breakdown_snapshot")
        if _fare_locked and snap and isinstance(snap, dict) and snap.get("lines"):
            lines = list(snap["lines"])
            ride_tip = float(_d(r.get("tip_amount") or 0))
            has_tip_line = any(ln.get("type") == "tip" for ln in lines)
            if ride_tip > 0 and not has_tip_line:
                lines.append({"label": "Tip", "amount": _f(_d(ride_tip)), "type": "tip"})
            r["fare_breakdown"] = lines
            r["grand_total"] = _sum_fare_breakdown(lines)
            r["fare_locked"] = True
        else:
            r["fare_breakdown"] = _build_fare_breakdown(r)
            r["grand_total"] = _sum_fare_breakdown(r["fare_breakdown"])
            r["fare_locked"] = False
        r["actual_duration_minutes"] = _actual_duration_minutes(r)

    next_cursor = rides[-1]["id"] if has_more and rides else None

    return {"rides": rides, "limit": limit, "next_cursor": next_cursor}


@router.get("/stats")
async def get_rider_stats(
    period: str = Query(default="today"),
    current_user: dict = Depends(get_current_user),
):
    """Aggregated trip stats for the rider activity summary card.

    Timezone is derived from the service area of the rider's most recent completed
    ride so 'today' aligns with the driver's local calendar day, not UTC midnight.
    """
    from zoneinfo import ZoneInfo

    _tz_name = "America/Regina"
    _recent = await _deps.db_supabase.get_rows(
        "rides",
        {"rider_id": current_user["id"], "status": RideStatus.COMPLETED},
        order="ride_completed_at",
        desc=True,
        limit=1,
    )
    if _recent and _recent[0].get("service_area_id"):
        _sa = await _deps.db_supabase.get_rows("service_areas", {"id": _recent[0]["service_area_id"]}, limit=1)
        if _sa and _sa[0].get("timezone"):
            _tz_name = _sa[0]["timezone"]

    now = datetime.now(ZoneInfo(_tz_name))
    use_date_filter = True
    if period in ("today", "day"):
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = now - timedelta(days=7)
    elif period == "month":
        start_date = now - timedelta(days=30)
    elif period == "all":
        use_date_filter = False
        start_date = None
    else:
        start_date = now - timedelta(days=7)

    filters: dict = {
        "rider_id": current_user["id"],
        "status": RideStatus.COMPLETED,
    }
    if use_date_filter and start_date:
        filters["ride_completed_at"] = {"$gte": start_date.isoformat()}

    rides = await _deps.db_supabase.get_rows("rides", filters, limit=10000)

    # Seed the sums with Decimal(0): sum() of an empty iterable returns the
    # start value, and a bare sum() defaults to int 0 — which then blows up in
    # _round()'s Decimal.quantize() when a rider has no completed rides.
    total_distance = sum((_d(r.get("distance_km") or 0) for r in rides), _d(0))
    total_rides = len(rides)
    total_saved = sum((_d(r.get("discount_amount") or 0) for r in rides), _d(0))
    # CO2 saving vs. driving solo: 0.12 kg per km (rideshare vs. personal vehicle)
    co2_saved_kg = round(float(total_distance) * 0.12, 2)

    return {
        "period": period,
        "total_rides": total_rides,
        "total_distance_km": round(float(total_distance), 1),
        "total_saved": str(_round(total_saved)),
        "co2_saved_kg": co2_saved_kg,
    }


@ride_read_limit
@router.get("/scheduled")
async def get_scheduled_rides(request: Request = None, current_user: dict = Depends(get_current_user)):
    """Get all upcoming scheduled rides for the current rider."""
    rides = await _deps.db_supabase.get_rows(
        "rides",
        {
            "rider_id": current_user["id"],
            "is_scheduled": True,
            "status": {"$nin": [RideStatus.COMPLETED, RideStatus.CANCELLED]},
        },
        order="scheduled_time",
        desc=False,
        limit=50,
    )
    return rides


@ride_read_limit
@router.get("/{ride_id}")
async def get_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Fetch details of a specific ride"""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise RideNotFoundException(
            ride_id=ride_id,
            message_key=ErrorKeys.RIDE_NOT_FOUND,
        )

    # Security check: must be rider or driver of this ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        # Admin check
        if current_user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not authorized to view this ride")

    # Include driver details if assigned.
    # Previously this dumped the ENTIRE driver row to the rider, which
    # included license_number, insurance_expiry_date,
    # background_check_expiry_date, work_eligibility_expiry_date,
    # vehicle_vin, document URLs, and the driver's stored phone — a
    # material PII leak to any rider on any ride. Only surface the
    # fields the rider actually needs to identify the driver and the
    # car pulling up (name, plate + make/model/color, rating, and the
    # live coordinates used for the map marker).
    if ride.get("driver_id"):
        assigned_driver = await _deps.db_supabase.get_driver_by_id(ride["driver_id"])
        if assigned_driver:
            # Driver photo lives on the user row (users.profile_image), shown to
            # riders only once admin-approved.
            _drv_user = await _deps.db_supabase.get_user_by_id(assigned_driver.get("user_id"))
            ride["driver"] = DriverPublicView(
                id=assigned_driver.get("id", ""),
                name=assigned_driver.get("name", ""),
                rating=assigned_driver.get("rating"),
                total_rides=assigned_driver.get("total_rides"),
                photo_url=_rider_visible_photo(_drv_user),
                vehicle_make=assigned_driver.get("vehicle_make"),
                vehicle_model=assigned_driver.get("vehicle_model"),
                vehicle_color=assigned_driver.get("vehicle_color"),
                license_plate=assigned_driver.get("license_plate"),
                vehicle_year=assigned_driver.get("vehicle_year"),
                lat=assigned_driver.get("lat"),
                lng=assigned_driver.get("lng"),
            ).dict()

    # Derive free_cancel_seconds_remaining + cancellation_fee from app_settings (UX-001).
    # These allow the frontend to show accurate countdown/fee without hardcoding.
    try:
        from settings_loader import get_app_settings  # type: ignore
    except ImportError:
        try:
            from ...settings_loader import get_app_settings  # type: ignore
        except ImportError:
            get_app_settings = None  # type: ignore

    free_cancel_window = 120
    cancellation_fee_amount = Decimal("4.50")
    if get_app_settings:
        try:
            settings = await get_app_settings()
            # Per-service-area overrides take priority over global settings
            area = None
            if ride.get("service_area_id"):
                area = await _deps.db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
            if area and area.get("free_cancel_window_seconds") is not None:
                free_cancel_window = int(area["free_cancel_window_seconds"])
            else:
                free_cancel_window = int(settings.get("free_cancel_window_seconds", 120))
            fee_admin = Decimal(
                str((area or {}).get("cancel_fee_admin_share") or settings.get("cancellation_fee_admin", "0.50"))
            )
            fee_driver = Decimal(
                str((area or {}).get("cancel_fee_driver_share") or settings.get("cancellation_fee_driver", "4.00"))
            )
            cancellation_fee_amount = fee_admin + fee_driver
        except Exception:
            logger.error("Failed to fetch app settings for cancellation config", exc_info=True)

    driver_accepted_at = ride.get("driver_accepted_at")
    ride_status = ride.get("status")

    # Once the driver has physically arrived, the free-cancel window is over
    # regardless of elapsed time — the driver already spent fuel/time.
    if ride_status == RideStatus.DRIVER_ARRIVED:
        ride["free_cancel_seconds_remaining"] = 0
    elif driver_accepted_at:
        from datetime import datetime, timezone

        try:
            if isinstance(driver_accepted_at, str):
                accepted_dt = datetime.fromisoformat(driver_accepted_at.replace("Z", "+00:00"))
            else:
                accepted_dt = driver_accepted_at
            if accepted_dt.tzinfo is None:
                accepted_dt = accepted_dt.replace(tzinfo=timezone.utc)
            elapsed = int((datetime.now(timezone.utc) - accepted_dt).total_seconds())
            ride["free_cancel_seconds_remaining"] = max(0, free_cancel_window - elapsed)
        except Exception:
            ride["free_cancel_seconds_remaining"] = 0
    else:
        ride["free_cancel_seconds_remaining"] = None  # driver not yet accepted

    ride["free_cancel_window_seconds"] = free_cancel_window
    ride["cancellation_fee"] = cancellation_fee_amount

    # No-show timer: once the driver arrives, they must wait 5 minutes
    # before they can mark the rider as a no-show. Expose the countdown
    # so both apps can show an accurate timer.
    noshow_wait_seconds = 300
    if get_app_settings:
        try:
            settings = await get_app_settings()
            # Per-service-area override → global fallback
            if area and area.get("noshow_wait_seconds") is not None:
                noshow_wait_seconds = int(area["noshow_wait_seconds"])
            else:
                noshow_wait_seconds = int(settings.get("noshow_wait_seconds", 300))
        except Exception:
            logger.warning("Failed to load settings or service area override for noshow_wait_seconds")
    ride["noshow_wait_seconds"] = noshow_wait_seconds

    if ride_status == RideStatus.DRIVER_ARRIVED:
        driver_arrived_at = ride.get("driver_arrived_at")
        if driver_arrived_at:
            from datetime import datetime, timezone

            try:
                if isinstance(driver_arrived_at, str):
                    arrived_dt = datetime.fromisoformat(driver_arrived_at.replace("Z", "+00:00"))
                else:
                    arrived_dt = driver_arrived_at
                if arrived_dt.tzinfo is None:
                    arrived_dt = arrived_dt.replace(tzinfo=timezone.utc)
                elapsed = int((datetime.now(timezone.utc) - arrived_dt).total_seconds())
                ride["noshow_seconds_remaining"] = max(0, noshow_wait_seconds - elapsed)
                ride["noshow_eligible"] = elapsed >= noshow_wait_seconds
            except Exception:
                ride["noshow_seconds_remaining"] = noshow_wait_seconds
                ride["noshow_eligible"] = False
        else:
            ride["noshow_seconds_remaining"] = noshow_wait_seconds
            ride["noshow_eligible"] = False
    else:
        ride["noshow_seconds_remaining"] = None
        ride["noshow_eligible"] = False

    # M-4: Expose offer expiry so the rider app can show an accurate
    # countdown progress bar while waiting for the driver to accept.
    # Only meaningful in driver_assigned state; cleared once accepted.
    if ride.get("status") == RideStatus.DRIVER_ASSIGNED:
        driver_notified_at = ride.get("driver_notified_at")
        offer_timeout_seconds = 15
        if get_app_settings:
            try:
                settings = await get_app_settings()
                offer_timeout_seconds = int(settings.get("ride_offer_timeout_seconds", 15))
            except Exception:
                # Non-fatal: fall back to hardcoded default if settings fetch fails
                logger.error(
                    "Failed to fetch app settings for offer timeout config",
                    exc_info=True,
                )
        ride["offer_timeout_seconds"] = offer_timeout_seconds
        if driver_notified_at:
            try:
                from datetime import datetime, timedelta, timezone

                if isinstance(driver_notified_at, str):
                    notified_dt = datetime.fromisoformat(driver_notified_at.replace("Z", "+00:00"))
                else:
                    notified_dt = driver_notified_at
                if notified_dt.tzinfo is None:
                    notified_dt = notified_dt.replace(tzinfo=timezone.utc)
                expires_dt = notified_dt + timedelta(seconds=offer_timeout_seconds + 15)
                ride["offer_expires_at"] = expires_dt.isoformat()
            except Exception:
                ride["offer_expires_at"] = None
        else:
            ride["offer_expires_at"] = None
    else:
        ride["offer_expires_at"] = None
        ride["offer_timeout_seconds"] = None

    # PIPEDA / threat-model RI-2: drivers see street-level (no house number)
    # addresses and block-level coordinates for completed rides. Exact
    # addresses are stripped to mitigate address-based stalking (RAT-1)
    # while still giving drivers useful trip history (Uber/Lyft pattern).
    if is_driver and not is_rider:
        ride.pop("pickup_otp", None)
        if ride.get("status") in RideStatus.terminal_statuses():
            _redact_driver_location_fields(ride)

    # When a fare snapshot exists and fare_lock is enabled, the snapshot
    # IS the bill — use it verbatim instead of recomputing from ride fields
    # that may have been adjusted at completion.
    snapshot = ride.get("fare_breakdown_snapshot")
    fare_locked = False
    if snapshot and isinstance(snapshot, dict) and snapshot.get("lines"):
        try:
            settings = await get_app_settings()
            fare_locked = settings.get("fare_lock_enabled", False) if settings else False
        except Exception:
            fare_locked = False
    if fare_locked and snapshot:
        lines = list(snapshot["lines"])
        # Ensure tip is reflected even for snapshots that pre-date the tip update
        ride_tip = float(_d(ride.get("tip_amount") or 0))
        has_tip_line = any(ln.get("type") == "tip" for ln in lines)
        if ride_tip > 0 and not has_tip_line:
            lines.append({"label": "Tip", "amount": _f(_d(ride_tip)), "type": "tip"})
        ride["fare_breakdown"] = lines
        ride["grand_total"] = _sum_fare_breakdown(lines)
        ride["fare_locked"] = True
    else:
        ride["fare_breakdown"] = _build_fare_breakdown(ride)
        ride["grand_total"] = _sum_fare_breakdown(ride["fare_breakdown"])
        ride["fare_locked"] = False
    ride["actual_duration_minutes"] = _actual_duration_minutes(ride)

    # Enrich with incentive claims and cancellation fee for this ride.
    # run_sync is mandatory here: a bare .execute() is a synchronous HTTP
    # round-trip that freezes the event loop — and with it every concurrent
    # request on this replica — for the full Supabase latency.
    try:
        _claims_res = await _deps.db_supabase.run_sync(
            lambda: (
                _deps.db_supabase.supabase.table("ride_incentive_claims")
                .select("bonus_amount, incentive_id")
                .eq("ride_id", ride_id)
                .execute()
            )
        )
        _claims = _claims_res.data or []
        _incentive_total = sum(float(c.get("bonus_amount") or 0) for c in _claims)
        ride["incentive_amount"] = round(_incentive_total, 2)
    except Exception:
        logger.debug("ride incentive_claims lookup failed", exc_info=True)
        ride["incentive_amount"] = 0

    # Prefer the frozen driver_earnings_snapshot when available
    des = ride.get("driver_earnings_snapshot")
    if des and isinstance(des, dict) and "total" in des:
        ride["fare_only"] = round(float(des.get("fare") or 0), 2)
        ride["cancel_fee_earned"] = round(float(des.get("cancel_fee") or 0), 2)
        ride["tax_amount_total"] = round(float(des.get("tax") or 0), 2)
        _snap_incentive = float(des.get("incentive") or 0)
        if _snap_incentive > 0:
            ride["incentive_amount"] = round(_snap_incentive, 2)
        tip = float(des.get("tip") or 0)
        ride["total_earned"] = round(
            ride["fare_only"] + tip + ride["incentive_amount"] + ride["cancel_fee_earned"] + ride["tax_amount_total"],
            2,
        )
    else:
        tip = float(ride.get("tip_amount") or 0)
        fare_only = (
            float(ride.get("base_fare") or 0)
            + float(ride.get("distance_fare") or 0)
            + float(ride.get("time_fare") or 0)
        )
        cancel_fee = float(ride.get("cancellation_fee_driver") or 0)
        tax = float(ride.get("tax_amount") or 0)
        if tax == 0:
            snap = ride.get("fare_breakdown_snapshot") or {}
            for ln in snap.get("lines") or []:
                if ln.get("type") in ("tax", "gst", "pst"):
                    tax += float(ln.get("amount") or 0)
            tax = round(tax, 2)
        ride["fare_only"] = round(fare_only, 2)
        ride["cancel_fee_earned"] = round(cancel_fee, 2)
        ride["tax_amount_total"] = tax
        ride["total_earned"] = round(fare_only + tip + ride["incentive_amount"] + cancel_fee + tax, 2)

    return ride
