"""Driver profile, registration, config, heatmap, destination mode.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps, _shared
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Body,
    Depends,
    HTTPException,
    Optional,
    datetime,
    db_supabase,
    generate_driver_code,
    get_current_user,
    logger,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    _STRIP_FROM_SELF_RESPONSE,
    serialize_doc,
)

router = APIRouter()


@router.get("/config")
async def get_driver_config(current_user: dict = Depends(get_current_user)):
    """Return operational settings the driver-app should honor at runtime.

    Driver-app constants that used to live hardcoded in
    `driver-app/shared/config/spinr.config.ts` and
    `driver-app/store/driverStore.ts` are now served from the backend
    so operations can tune them per deploy without shipping a new app
    build. Fields fall back to sensible defaults when the DB
    `settings` row doesn't include them yet.

    * ``ride_offer_timeout_seconds`` — how long a driver has to
      accept/decline a ride offer before it auto-declines. Default 15.
      Capped to [5, 60] so a bad admin input can't brick the UX.
    * ``pickup_radius_meters`` — how close the driver must be to the
      pickup point to mark "arrived" (geofence check). Default 100.
      Capped to [10, 1000].
    """
    try:
        from ...settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore

    try:
        app_settings = await get_app_settings() or {}
    except Exception as e:
        logger.error(f"get_driver_config: failed to read app_settings: {e}", exc_info=True)
        app_settings = {}

    def _clamp(value, lo, hi, default):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, n))

    # Admin-uploaded mp3/wav URL the driver-app plays as the ride-offer
    # ping. Null/empty → driver-app falls back to the bundled placeholder.
    ride_offer_sound_url = app_settings.get("ride_offer_sound_url") or None
    return {
        "ride_offer_timeout_seconds": _clamp(app_settings.get("ride_offer_timeout_seconds"), 5, 60, 15),
        "pickup_radius_meters": _clamp(app_settings.get("pickup_radius_meters"), 10, 1000, 100),
        "ride_offer_sound_url": ride_offer_sound_url,
    }


@router.get("/me")
async def get_my_driver(current_user: dict = Depends(get_current_user)):
    """Get the current user's driver profile."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    response_data = serialize_doc(await _shared._decrypt_driver_pii(driver))
    for field in _STRIP_FROM_SELF_RESPONSE:
        response_data.pop(field, None)
    return response_data


class UpdateDriverProfileRequest(BaseModel):
    """Strict schema for driver profile updates — only whitelisted fields accepted."""

    # Safe fields (no re-verification)
    gst_registered: Optional[bool] = None
    gst_bn: Optional[str] = None  # CRA Business Number, format 123456789RT0001
    preferred_language: Optional[str] = None
    photo_url: Optional[str] = None
    is_wav: Optional[bool] = None
    # Vehicle/document fields (triggers re-review on verified drivers)
    vehicle_type_id: Optional[str] = None
    vehicle_make: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_color: Optional[str] = None
    vehicle_year: Optional[int] = None
    license_plate: Optional[str] = None
    vehicle_vin: Optional[str] = None
    license_number: Optional[str] = None
    license_expiry_date: Optional[str] = None
    insurance_expiry_date: Optional[str] = None
    vehicle_inspection_expiry_date: Optional[str] = None
    background_check_expiry_date: Optional[str] = None
    work_eligibility_expiry_date: Optional[str] = None
    city: Optional[str] = None
    service_area_id: Optional[str] = None


@router.put("/me")
async def update_my_driver(body: UpdateDriverProfileRequest, current_user: dict = Depends(get_current_user)):
    """Update the current user's driver profile.

    Accepts vehicle info, personal details, and preferences. When a
    verified driver changes vehicle fields, they are automatically
    un-verified and must wait for admin re-approval.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )

    # Fields that always update without affecting verification
    safe_fields = {
        "gst_registered",
        "gst_bn",
        "preferred_language",
        "is_wav",
    }
    # Vehicle/doc fields — changing these on a verified driver triggers re-review
    vehicle_fields = {
        "vehicle_type_id",
        "vehicle_make",
        "vehicle_model",
        "vehicle_color",
        "vehicle_year",
        "license_plate",
        "vehicle_vin",
        "license_number",
        "license_expiry_date",
        "insurance_expiry_date",
        "vehicle_inspection_expiry_date",
        "background_check_expiry_date",
        "work_eligibility_expiry_date",
        "city",
        "service_area_id",
    }
    allowed_fields = safe_fields | vehicle_fields

    updates = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in allowed_fields}
    if not updates:
        return {"success": True}

    # Auto-create a driver row if one doesn't exist yet (new driver adding
    # vehicle details for the first time from the vehicle-info screen).
    if not driver:
        import uuid

        first = current_user.get("first_name", "")
        last = current_user.get("last_name", "")
        new_driver = {
            "id": str(uuid.uuid4()),
            "driver_code": generate_driver_code(),
            "user_id": current_user["id"],
            "name": f"{first} {last}".strip() or current_user.get("phone", ""),
            "first_name": first or None,
            "last_name": last or None,
            "phone": current_user.get("phone", ""),
            "status": "pending",
            "is_verified": False,
            "is_online": False,
            "is_available": False,
            "rating": 5.0,
            "total_rides": 0,
            "lat": 0,
            "lng": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **updates,
        }
        await db_supabase.insert_one("drivers", new_driver)
        # Mark as driver; is_rider is intentionally left unchanged so a
        # driver who was already riding keeps both flags (dual-role).
        await db_supabase.update_one("users", {"id": current_user["id"]}, {"role": "driver", "is_driver": True})
        return serialize_doc(new_driver)

    # Check if an active driver changed vehicle/document fields → needs review
    changed_vehicle = any(k in vehicle_fields for k in updates)
    if changed_vehicle and driver.get("status") == "active":
        updates["status"] = "needs_review"
        updates["is_online"] = False
        updates["is_available"] = False
        logger.info(f"[DRIVER] Driver {driver['id']} updated vehicle info → status set to needs_review")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db_supabase.update_one("drivers", {"id": driver["id"]}, await _shared._encrypt_driver_pii(updates))
    # Append-only vehicle/identity change history (SGI/insurance audit). Uses
    # the pre-update `driver` row as the "before" snapshot.
    if changed_vehicle:
        try:
            from ...utils.vehicle_history import record_vehicle_changes
        except ImportError:
            from utils.vehicle_history import record_vehicle_changes  # type: ignore
        await record_vehicle_changes(
            driver["id"], driver, updates, changed_by_user_id=current_user["id"], role="driver"
        )
    # M-5: SGI insurance period audit — vehicle/document edits flip an
    # active driver to needs_review and force them offline. If they were
    # actually online before this update, that's a 1→0 transition.
    if changed_vehicle and driver.get("status") == "active" and driver.get("is_online"):
        await _deps.record_period_transition(driver["id"], 0)
    updated = await db_supabase.get_driver_by_id(driver["id"])
    return serialize_doc(await _shared._decrypt_driver_pii(updated))


@router.get("/demand-heatmap")
async def get_demand_heatmap(current_user: dict = Depends(get_current_user)):
    """Return recent ride pickup locations as heatmap points for the driver.

    Scoped to the driver's service area (if set) and the last 7 days.
    Only returns data when the admin has enabled `show_demand_heatmap`
    on the driver's service area.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )

    # Check if heatmap is enabled for this driver's service area
    service_area = None
    if driver and driver.get("service_area_id"):
        service_area = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
        )

    enabled = bool(service_area and service_area.get("show_demand_heatmap"))
    if not enabled:
        return {"enabled": False, "points": [], "total_rides": 0}

    query_filters: dict = {}

    # Last 7 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    query_filters["created_at"] = {"$gte": cutoff}
    query_filters["service_area_id"] = driver["service_area_id"]

    rides = await db_supabase.get_rows(
        "rides",
        query_filters,
        order="created_at",
        desc=True,
        limit=2_000,
        columns="pickup_lat,pickup_lng",
    )

    points = []
    for r in rides:
        lat = r.get("pickup_lat")
        lng = r.get("pickup_lng")
        if lat is not None and lng is not None:
            points.append([float(lat), float(lng), 1])

    return {"enabled": True, "points": points, "total_rides": len(rides)}


@router.post("/register")
async def register_driver(
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Create or update the driver row for the authenticated user (become-driver flow).

    Called by the driver app's `registerDriver()` in authStore after the user
    submits vehicle + document info. Upsert so re-submission updates the row
    rather than erroring.
    """
    user_id = current_user["id"]
    user_phone = current_user.get("phone", "")

    # Build name/phone from user if not supplied
    first_name = body.get("first_name") or ""
    last_name = body.get("last_name") or ""
    full_name = (
        f"{first_name} {last_name}".strip() or current_user.get("name") or current_user.get("full_name") or "Driver"
    )
    # Derive split first/last from whatever source produced full_name. Mirrors
    # the migration backfill logic so a fresh row matches what the migration
    # would produce.
    if not first_name and not last_name:
        _parts = full_name.split(" ", 1)
        _first_name_split = _parts[0] if _parts else ""
        _last_name_split = _parts[1].strip() if len(_parts) > 1 else ""
    else:
        _first_name_split = first_name
        _last_name_split = last_name

    existing = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1))

    # Reject registration attempts that would collide with an existing
    # driver record owned by someone else — prevents the phone-level
    # duplicates we saw in migration 30_identity_audit. Only enforced when
    # creating (no `existing` row for this user); updates of your own
    # record aren't blocked.
    if not existing and user_phone:
        phone_match = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"phone": user_phone}, limit=1)
        )
        if phone_match and phone_match.get("user_id") != user_id:
            raise HTTPException(
                status_code=409,
                detail="A driver account with this phone already exists. Log in to that account instead.",
            )

    # Fields the client is allowed to set on register
    allowed = {
        "first_name",
        "last_name",
        "email",
        "gender",
        "city",
        "service_area_id",
        "vehicle_type_id",
        "vehicle_make",
        "vehicle_model",
        "vehicle_color",
        "vehicle_year",
        "license_plate",
        "vehicle_vin",
        "license_number",
        "license_expiry_date",
        "insurance_expiry_date",
        "vehicle_inspection_expiry_date",
        "background_check_expiry_date",
        "work_eligibility_expiry_date",
        "documents",
    }
    payload = {k: v for k, v in body.items() if k in allowed and v is not None}

    if existing:
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["submitted_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("drivers", {"id": existing["id"]}, await _shared._encrypt_driver_pii(payload))
        driver = await db_supabase.get_driver_by_id(existing["id"])
        return serialize_doc(await _shared._decrypt_driver_pii(driver))

    # Create new row
    import uuid as _uuid

    new_driver = {
        "id": str(_uuid.uuid4()),
        "driver_code": generate_driver_code(),
        "user_id": user_id,
        "name": full_name,
        "first_name": _first_name_split or None,
        "last_name": _last_name_split or None,
        "phone": current_user.get("phone", ""),
        "rating": 5.0,
        "total_rides": 0,
        "is_online": False,
        "is_available": False,
        "is_verified": False,
        "status": "pending",
        "lat": 0.0,
        "lng": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    await db_supabase.insert_one("drivers", await _shared._encrypt_driver_pii(new_driver))

    # Canonicalize is_driver flag. is_rider is intentionally NOT cleared here
    # so that a driver who already has is_rider=true keeps dual-role status.
    if not current_user.get("is_driver"):
        try:
            # users table has no updated_at column (supabase_schema.sql).
            await db_supabase.update_one(
                "users",
                {"id": user_id},
                {"role": "driver", "is_driver": True},
            )
        except Exception as exc:
            logger.error(
                f"register_driver: failed to flip users.role for {user_id}: {exc}",
                exc_info=True,
            )

    return serialize_doc(new_driver)


# Push token registration happens via POST /notifications/register-token
# (routes/notifications.py), which writes to both push_tokens and users.fcm_token.
# The previous POST /drivers/push-token duplicated that surface without the
# push_tokens row, so it was removed to keep a single registration path.
#
# Driver online/offline toggling happens via PUT /drivers/{driver_id}/status
# (further down in this file). POST /drivers/status was a never-wired
# duplicate and has been removed.


# ── Destination Mode ─────────────────────────────────────────────────


class SetDestinationRequest(BaseModel):
    address: str
    lat: float
    lng: float


@router.post("/destination")
async def set_destination_mode(req: SetDestinationRequest, current_user: dict = Depends(get_current_user)):
    """Set driver's preferred destination. Ride matching will prioritize
    rides heading toward this destination to reduce empty miles."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await _deps.db.update_one(
        "drivers",
        {"id": driver["id"]},
        {
            "destination_mode": True,
            "destination_address": req.address,
            "destination_lat": req.lat,
            "destination_lng": req.lng,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "success": True,
        "destination_mode": True,
        "destination_address": req.address,
    }


@router.delete("/destination")
async def clear_destination_mode(current_user: dict = Depends(get_current_user)):
    """Clear driver's destination mode."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await _deps.db.update_one(
        "drivers",
        {"id": driver["id"]},
        {
            "destination_mode": False,
            "destination_address": None,
            "destination_lat": None,
            "destination_lng": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True, "destination_mode": False}


@router.get("/destination")
async def get_destination_mode(current_user: dict = Depends(get_current_user)):
    """Get driver's current destination mode status."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    return {
        "destination_mode": driver.get("destination_mode", False),
        "destination_address": driver.get("destination_address"),
        "destination_lat": driver.get("destination_lat"),
        "destination_lng": driver.get("destination_lng"),
    }
