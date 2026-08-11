"""Trip sharing links, shared contacts, live-activity registration.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Depends,
    Field,
    HTTPException,
    Optional,
    Request,
    RideStatus,
    api_rate_limit,
    datetime,
    first_name_only,
    get_current_user,
    logger,
    ride_action_limit,
    secrets,
    timezone,
    uuid,
)
from ._shared import (  # noqa: F401
    _push_in_background,
    _rider_visible_photo,
)

router = APIRouter()


# ============================================================
# GAP FIX: Share Ride Link (Uber/Lyft standard feature)
# ============================================================


@router.get("/{ride_id}/share")
async def get_share_trip_link(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Generate a shareable trip tracking link for safety contacts (like Uber's 'Share My Trip')."""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # B16: the driver's own Safety overlay needs "Share Live Trip Link" too
    # (previously rider-only). Same driver-lookup pattern as
    # routes/rides/safety.py::trigger_emergency's membership check.
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]
    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to share this ride")

    if ride.get("status") in RideStatus.terminal_statuses():
        raise HTTPException(status_code=400, detail="Cannot share a completed or cancelled ride")

    # Generate or reuse a share token (with creation timestamp for expiry)
    share_token = ride.get("shared_trip_token")
    if not share_token:
        share_token = secrets.token_urlsafe(32)
        await _deps.db_supabase.update_ride(ride_id, {"shared_trip_token": share_token})

    # Clean customer-facing link: {tracking-domain}/{token}. The tracking
    # domain (e.g. track.spinr.ca) rewrites /{token} → /track/{token} server-side.
    share_url = f"{_deps._settings.TRACKING_BASE_URL}/{share_token}"

    return {
        "success": True,
        "share_token": share_token,
        "share_url": share_url,
        "ride_id": ride_id,
    }


class ShareTripWithContactRequest(BaseModel):
    contact_name: str
    contact_phone: str


@router.post("/{ride_id}/share")
@api_rate_limit
async def share_trip_with_contact(
    ride_id: str,
    body: ShareTripWithContactRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Share trip with a specific contact and send them a notification."""
    ride = await _deps.db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") in RideStatus.terminal_statuses():
        raise HTTPException(status_code=400, detail="Cannot share a completed or cancelled ride")

    # Get or create share token
    share_token = ride.get("shared_trip_token")
    if not share_token:
        share_token = secrets.token_urlsafe(32)
        await _deps.db.update_one(
            "rides",
            {"id": ride_id},
            {
                "$set": {
                    "shared_trip_token": share_token,
                    "shared_trip_token_created_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    # Record the contact in shared_with list
    shared_with = ride.get("shared_with") or []
    contact_entry = {
        "name": body.contact_name,
        "phone": body.contact_phone,
        "shared_at": datetime.now(timezone.utc).isoformat(),
    }
    # Avoid duplicates by phone
    if not any(c.get("phone") == body.contact_phone for c in shared_with):
        shared_with.append(contact_entry)
        await _deps.db.update_one(
            "rides",
            {"id": ride_id},
            {"$set": {"shared_with": shared_with}},
        )

    # Clean customer-facing link: {tracking-domain}/{token}. The tracking
    # domain (e.g. track.spinr.ca) rewrites /{token} → /track/{token} server-side.
    share_url = f"{_deps._settings.TRACKING_BASE_URL}/{share_token}"

    # Send push notification to contact if they're a registered user
    contact_user = await _deps.db.find_one("users", {"phone": body.contact_phone})
    if contact_user:
        rider = await _deps.db.find_one("users", {"id": current_user["id"]})
        # PIPEDA (C5): the rider's FIRST name only to their chosen contact —
        # never the legal surname in a cleartext push title.
        rider_name = first_name_only(rider, "Someone")
        _push_in_background(
            contact_user["id"],
            f"{rider_name} is sharing their ride with you",
            # PIPEDA (C5): no exact pickup/dropoff addresses in the push body —
            # FCM is cleartext in the device tray and stored in Google/US infra.
            # The contact taps through to live tracking via the share token; the
            # body needs no address detail.
            "Tap to follow their live trip location.",
            data={
                "type": "trip_shared",
                "share_token": share_token,
                "ride_id": ride_id,
            },
            _ctx=f"[SHARE] contact {contact_user['id']}",
        )

    return {
        "success": True,
        "share_token": share_token,
        "share_url": share_url,
        "contact_notified": contact_user is not None,
        "shared_with": shared_with,
    }


@router.get("/{ride_id}/shared-contacts")
async def get_shared_contacts(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get list of contacts this ride has been shared with."""
    ride = await _deps.db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"contacts": ride.get("shared_with") or []}


@router.get("/track/{share_token}")
async def track_shared_ride(share_token: str):
    """Public endpoint - Get ride status via share token (no auth required)."""
    ride = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows("rides", {"shared_trip_token": share_token}, limit=1)
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Shared ride not found or link expired")

    # Expire share tokens after 24 hours
    token_created = ride.get("shared_trip_token_created_at")
    if token_created:
        from datetime import timedelta

        try:
            created_dt = datetime.fromisoformat(token_created) if isinstance(token_created, str) else token_created
            if datetime.now(timezone.utc) - created_dt > timedelta(hours=24):
                raise HTTPException(status_code=404, detail="Share link has expired")
        except (ValueError, TypeError):
            pass  # Malformed timestamp — allow access but log
            logger.error(f"Malformed shared_trip_token_created_at for ride {ride.get('id')}")

    if ride.get("status") in RideStatus.terminal_statuses():
        return {
            "status": ride.get("status"),
            "message": "This ride has ended.",
            "pickup_address": ride.get("pickup_address"),
            "dropoff_address": ride.get("dropoff_address"),
        }

    # Get driver location for live tracking — surface what a safety contact
    # legitimately needs to see the live map (driver coords + plate to
    # identify the car) without leaking PII (phone, email, license number).
    driver_info = None
    eta_minutes: Optional[int] = None
    if ride.get("driver_id"):
        driver = await _deps.db_supabase.get_driver_by_id(ride["driver_id"])
        if driver:
            _drv_user = await _deps.db_supabase.get_user_by_id(driver.get("user_id"))
            driver_info = {
                "name": driver.get("name", "Driver"),
                "lat": driver.get("lat"),
                "lng": driver.get("lng"),
                "vehicle_make": driver.get("vehicle_make"),
                "vehicle_model": driver.get("vehicle_model"),
                "vehicle_color": driver.get("vehicle_color"),
                "vehicle_year": driver.get("vehicle_year"),
                "license_plate": driver.get("license_plate"),
                "rating": driver.get("rating"),
                # users.profile_image, shown to riders only once admin-approved.
                "photo_url": _rider_visible_photo(_drv_user),
            }
            # Cheap ETA: straight-line driver→dropoff at 30 km/h city speed.
            # Same formula used at /rides/estimate so the number stays consistent.
            d_lat, d_lng = driver.get("lat"), driver.get("lng")
            tgt_lat, tgt_lng = ride.get("dropoff_lat"), ride.get("dropoff_lng")
            if d_lat is not None and d_lng is not None and tgt_lat is not None and tgt_lng is not None:
                try:
                    km = _deps.calculate_distance(d_lat, d_lng, tgt_lat, tgt_lng)
                    eta_minutes = max(1, int(km / 30 * 60) + 1)
                except Exception:
                    eta_minutes = None

    return {
        "status": ride.get("status"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "pickup_lat": ride.get("pickup_lat"),
        "pickup_lng": ride.get("pickup_lng"),
        "dropoff_lat": ride.get("dropoff_lat"),
        "dropoff_lng": ride.get("dropoff_lng"),
        "ride_code": ride.get("ride_code"),
        "eta_minutes": eta_minutes,
        "driver": driver_info,
    }


class LiveActivityRegisterRequest(BaseModel):
    """Rider app registers its live-activity push token for a ride."""

    platform: str = Field(..., pattern="^(ios|android)$")
    # Real tokens are small (APNs ≤100, FCM ≤256 chars); 512 is generous headroom
    # and bounds abuse. Mirrored by a CHECK on the column (migration 195). The
    # charset accepts iOS ActivityKit hex AND Android FCM (base64url + ':') while
    # blocking path/structural chars (the iOS token is later interpolated into the
    # APNs URL path — apns_client also re-validates).
    push_token: str = Field(..., min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_.:-]+$")


@router.post("/{ride_id}/live-activity/register")
@ride_action_limit
async def register_live_activity(
    ride_id: str,
    body: LiveActivityRegisterRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Store the rider app's live-activity push token for a ride.

    Called when the rider app starts a Live Activity (iOS) / ongoing
    notification (Android) at ``driver_accepted``. The backend reads this token
    on each ride-state transition to push an update (Phase 3 — Phase 1 only
    logs). Upsert: one token per ``(ride_id, platform)``; re-registration
    (reinstall / new activity) replaces it and clears any prior ``ended_at``.
    """
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    existing = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows(
            "ride_live_activities",
            {"ride_id": ride_id, "platform": body.platform},
            limit=1,
        )
    )
    if existing:
        await _deps.db_supabase.update_one(
            "ride_live_activities",
            {"id": existing["id"]},
            {"push_token": body.push_token, "ended_at": None, "updated_at": now},
        )
        activity_id = existing["id"]
    else:
        activity_id = str(uuid.uuid4())
        await _deps.db_supabase.insert_one(
            "ride_live_activities",
            {
                "id": activity_id,
                "ride_id": ride_id,
                "rider_id": current_user["id"],
                "platform": body.platform,
                "push_token": body.push_token,
                "started_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
    return {"success": True, "activity_id": activity_id}
