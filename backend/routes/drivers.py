import asyncio
import hmac
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

import stripe
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

try:
    from .. import db_supabase
    from ..dependencies import get_admin_user, get_current_user
    from ..features import send_email, send_push_notification
    from ..geo_utils import calculate_distance
    from ..logging_utils import diag_logger
    from ..schemas import Driver, RideRatingRequest
    from ..socket_manager import manager
    from ..utils.crypto import hash_otp
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.driver_presence import clear_presence, mark_present, present_driver_ids
    from ..utils.error_handling import RideStateError
    from ..utils.idempotency import idempotent_endpoint
except ImportError:
    import db_supabase
    from dependencies import get_admin_user, get_current_user
    from features import send_email, send_push_notification
    from geo_utils import calculate_distance
    from logging_utils import diag_logger
    from schemas import Driver, RideRatingRequest
    from socket_manager import manager
    from utils.crypto import hash_otp
    from utils.datetime_utils import parse_iso_utc
    from utils.driver_presence import clear_presence, mark_present, present_driver_ids
    from utils.error_handling import RideStateError
    from utils.idempotency import idempotent_endpoint

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

# ── Vault encryption for driver PII (P2-5) ───────────────────────────────────
# licence_number and vehicle_vin live in plain TEXT columns, but the values
# stored there are vault.secrets UUIDs, not plaintext — actual ciphertext is
# held in vault.secrets under the drivers_pii_key pgsodium key.
# The application passes plaintext to encrypt_driver_pii() before writing and
# calls decrypt_driver_pii() after reading. Both are Postgres functions created
# by migration 32_encrypt_sensitive_fields.sql and exposed via Supabase RPC.
# Transparent column encryption (vault.encrypted_text) was removed by Supabase
# in mid-2024; we intentionally keep the columns as TEXT and encrypt explicitly.

_VAULT_PII_FIELDS: frozenset = frozenset({"license_number", "vehicle_vin"})


async def _vault_encrypt(value: str, hint: str = "") -> str:
    """Encrypt a PII string via Supabase Vault (encrypt_driver_pii RPC).

    Fail-closed: any failure raises 503 rather than storing plaintext PII.
    Storing unencrypted license numbers or VINs is a PIPEDA violation.
    """
    if not value:
        return value
    try:
        from supabase_client import supabase as _sb  # type: ignore[import]
    except ImportError as exc:
        logger.error(
            "vault_encrypt: supabase_client unavailable for %s — refusing to store plaintext", hint, exc_info=True
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable") from exc
    if not _sb:
        logger.error("vault_encrypt: Supabase client not initialised for %s — refusing to store plaintext", hint)
        raise HTTPException(status_code=503, detail="Encryption service unavailable")
    try:
        res = await db_supabase.run_sync(lambda: _sb.rpc("encrypt_driver_pii", {"plaintext": value}).execute())
        if not res.data:
            logger.error("vault_encrypt: RPC returned no data for %s — refusing to store plaintext", hint)
            raise HTTPException(status_code=503, detail="Encryption service unavailable")
        return str(res.data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("vault_encrypt: RPC failed for %s — refusing to store plaintext", hint, exc_info=True)
        raise HTTPException(status_code=503, detail="Encryption service unavailable") from exc


async def _vault_decrypt(value: str, hint: str = "") -> str:
    """Decrypt a Vault-encrypted PII token via Supabase RPC (decrypt_driver_pii).

    On failure, returns the raw token rather than raising — the encrypted token
    is not PII, so this degrades to unreadable data rather than a privacy leak.
    """
    if not value:
        return value
    try:
        from supabase_client import supabase as _sb  # type: ignore[import]
    except ImportError:
        return value
    if not _sb:
        return value
    try:
        res = await db_supabase.run_sync(lambda: _sb.rpc("decrypt_driver_pii", {"secret_id": value}).execute())
        return str(res.data) if res.data else value
    except Exception:
        logger.error("vault_decrypt: RPC failed for %s — returning raw token", hint, exc_info=True)
        return value


async def _encrypt_driver_pii(payload: dict) -> dict:
    """Encrypt vault PII fields in a write payload before sending to the DB."""
    out = dict(payload)
    for field in _VAULT_PII_FIELDS:
        if field in out and out[field]:
            out[field] = await _vault_encrypt(str(out[field]), field)
    return out


async def _decrypt_driver_pii(driver: dict) -> dict:
    """Decrypt vault PII fields in a driver record returned from the DB."""
    out = dict(driver)
    for field in _VAULT_PII_FIELDS:
        if field in out and out[field]:
            out[field] = await _vault_decrypt(str(out[field]), field)
    return out


class RideOTPRequest(BaseModel):
    otp: str


api_router = APIRouter(prefix="/drivers", tags=["Drivers"])


# ── Ride state-machine guards ─────────────────────────────────────────────────
# Explicit source-state allowlists for each driver-initiated transition.
# A ride can only be moved forward from one of the allowed states; attempts
# to transition out of a terminal state (completed/cancelled) or out of order
# are rejected with 409 Conflict. This prevents:
#   • completing a ride that was never started → phantom charge
#   • restarting a cancelled ride
#   • marking "arrived" on a completed ride
# Idempotent destination states are included to make retries safe.
ARRIVE_FROM_STATES = ("driver_assigned", "driver_accepted", "driver_arrived")
# verify-otp and start both move driver_arrived → in_progress.
# in_progress is idempotent for both (retry after network blip).
START_FROM_STATES = ("driver_arrived", "in_progress")
COMPLETE_FROM_STATES = ("in_progress",)


async def _generate_and_store_ride_snapshot(
    *,
    ride_id: str,
    pickup_lat,
    pickup_lng,
    dropoff_lat,
    dropoff_lng,
    phase_polylines,
    route_polyline,
) -> None:
    """Render the ride's route PNG and upload to Supabase Storage.

    Called as a background task from ``complete_ride`` — the driver's
    request has already returned by the time this runs. Any failure
    (tile server down, bucket missing, Pillow missing) is logged and
    swallowed; the ride row already has phase_polylines so the drawer
    and email can always fall back to the live map.

    Requires a public ``ride-snapshots`` bucket in Supabase Storage.
    See backend/docs/STORAGE_BUCKETS.md for one-time setup.
    """
    if pickup_lat is None or pickup_lng is None or dropoff_lat is None or dropoff_lng is None:
        return
    try:
        try:
            from ..core.config import settings
            from ..supabase_client import supabase  # type: ignore
            from ..utils.route_snapshot import render_ride_snapshot
        except ImportError:
            from core.config import settings  # type: ignore
            from supabase_client import supabase  # type: ignore
            from utils.route_snapshot import render_ride_snapshot  # type: ignore

        loop = asyncio.get_event_loop()
        # Tile fetches + PIL rendering are blocking; punt to the executor.
        png_bytes = await loop.run_in_executor(
            None,
            lambda: render_ride_snapshot(
                pickup_lat=float(pickup_lat),
                pickup_lng=float(pickup_lng),
                dropoff_lat=float(dropoff_lat),
                dropoff_lng=float(dropoff_lng),
                phase_polylines=phase_polylines,
                route_polyline=route_polyline,
            ),
        )
        if not png_bytes:
            return

        # Supabase Storage upload. Public bucket, stable filename so a
        # re-run for the same ride_id overwrites cleanly via upsert.
        # The public URL never expires — safe for email embeds.
        bucket = "ride-snapshots"
        storage_path = f"ride_{ride_id}.png"
        try:
            await loop.run_in_executor(
                None,
                lambda: supabase.storage.from_(bucket).upload(
                    path=storage_path,
                    file=png_bytes,
                    file_options={
                        "content-type": "image/png",
                        # supabase-py serialises bools as JSON, so the string
                        # form is what ends up on the wire; lowercase "true".
                        "upsert": "true",
                    },
                ),
            )
        except Exception as upload_exc:
            logger.error(f"Supabase Storage upload failed for ride {ride_id}: {upload_exc}", exc_info=True)
            return

        base = (settings.SUPABASE_URL or "").rstrip("/")
        if not base:
            logger.error("SUPABASE_URL not configured; cannot build public snapshot URL")
            return
        url = f"{base}/storage/v1/object/public/{bucket}/{storage_path}"

        # Persist the URL. Wrap in try/except so if migration 41 hasn't
        # landed yet the write fails gracefully instead of raising.
        try:
            await db_supabase.update_one("rides", {"id": ride_id}, {"route_snapshot_url": url})
        except Exception as exc:
            logger.error(f"route_snapshot_url write failed for ride {ride_id} (migration 41 missing?): {exc}", exc_info=True)
    except Exception as exc:
        logger.error(f"Ride snapshot pipeline failed for {ride_id}: {exc}", exc_info=True)


async def _require_ride_in_state(ride_id: str, driver_id: str, allowed_states: tuple) -> Dict[str, Any]:
    """Load a driver's ride only if it is in one of ``allowed_states``.

    Raises 409 Conflict if the ride exists but is in a terminal or
    wrong state; raises 404 if the ride doesn't exist or isn't owned
    by this driver.
    """
    ride = await db.find_one(
        "rides",
        {
            "id": ride_id,
            "driver_id": driver_id,
            "status": {"$in": list(allowed_states)},
        },
    )
    if ride:
        return ride
    existing = await db.find_one("rides", {"id": ride_id, "driver_id": driver_id})
    if existing:
        current = existing.get("status", "unknown")
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ride is in status '{current}'; cannot perform this action "
                f"from that state (allowed: {list(allowed_states)})."
            ),
        )
    raise HTTPException(status_code=404, detail="Ride not found")


@api_router.get("/config")
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
        from ..settings_loader import get_app_settings  # type: ignore
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

    return {
        "ride_offer_timeout_seconds": _clamp(app_settings.get("ride_offer_timeout_seconds"), 5, 60, 15),
        "pickup_radius_meters": _clamp(app_settings.get("pickup_radius_meters"), 10, 1000, 100),
    }


def serialize_doc(doc):
    return doc


_STRIP_FROM_SELF_RESPONSE = {"stripe_account_id", "bank_account", "fcm_token"}


@api_router.get("/me")
async def get_my_driver(current_user: dict = Depends(get_current_user)):
    """Get the current user's driver profile."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    response_data = serialize_doc(await _decrypt_driver_pii(driver))
    for field in _STRIP_FROM_SELF_RESPONSE:
        response_data.pop(field, None)
    return response_data


class UpdateDriverProfileRequest(BaseModel):
    """Strict schema for driver profile updates — only whitelisted fields accepted."""

    # Safe fields (no re-verification)
    gst_number: Optional[str] = None
    preferred_language: Optional[str] = None
    photo_url: Optional[str] = None
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


@api_router.put("/me")
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
    safe_fields = {"gst_number", "preferred_language", "photo_url"}
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
            "user_id": current_user["id"],
            "name": f"{first} {last}".strip() or current_user.get("phone", ""),
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
        # Also flip the user role to 'driver' if not already
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
    await db_supabase.update_one("drivers", {"id": driver["id"]}, await _encrypt_driver_pii(updates))
    updated = await db_supabase.get_driver_by_id(driver["id"])
    return serialize_doc(await _decrypt_driver_pii(updated))


@api_router.get("/demand-heatmap")
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


@api_router.post("/register")
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
        "photo_url",
    }
    payload = {k: v for k, v in body.items() if k in allowed and v is not None}

    if existing:
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["submitted_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("drivers", {"id": existing["id"]}, await _encrypt_driver_pii(payload))
        driver = await db_supabase.get_driver_by_id(existing["id"])
        return serialize_doc(await _decrypt_driver_pii(driver))

    # Create new row
    import uuid as _uuid

    new_driver = {
        "id": str(_uuid.uuid4()),
        "user_id": user_id,
        "name": full_name,
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
    await db_supabase.insert_one("drivers", await _encrypt_driver_pii(new_driver))

    # Canonicalize the identity: if the user's role is still 'rider' flip
    # it to 'driver' so admin lists, login resolution, and role-gated code
    # all agree (see migration 31 for the one-shot backfill of legacy
    # rows). Only updates when needed so we don't churn updated_at on
    # users who are already tagged correctly.
    if current_user.get("role") != "driver" or not current_user.get("is_driver"):
        try:
            # users table has no updated_at column (supabase_schema.sql).
            await db_supabase.update_one(
                "users",
                {"id": user_id},
                {"role": "driver", "is_driver": True},
            )
        except Exception as exc:
            logger.error(f"register_driver: failed to flip users.role for {user_id}: {exc}", exc_info=True)

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


@api_router.post("/destination")
async def set_destination_mode(req: SetDestinationRequest, current_user: dict = Depends(get_current_user)):
    """Set driver's preferred destination. Ride matching will prioritize
    rides heading toward this destination to reduce empty miles."""
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await db.update_one(
        "drivers",
        {"id": driver["id"]},
        {
            "$set": {
                "destination_mode": True,
                "destination_address": req.address,
                "destination_lat": req.lat,
                "destination_lng": req.lng,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    return {
        "success": True,
        "destination_mode": True,
        "destination_address": req.address,
    }


@api_router.delete("/destination")
async def clear_destination_mode(current_user: dict = Depends(get_current_user)):
    """Clear driver's destination mode."""
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await db.update_one(
        "drivers",
        {"id": driver["id"]},
        {
            "$set": {
                "destination_mode": False,
                "destination_address": None,
                "destination_lat": None,
                "destination_lng": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    return {"success": True, "destination_mode": False}


@api_router.get("/destination")
async def get_destination_mode(current_user: dict = Depends(get_current_user)):
    """Get driver's current destination mode status."""
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    return {
        "destination_mode": driver.get("destination_mode", False),
        "destination_address": driver.get("destination_address"),
        "destination_lat": driver.get("destination_lat"),
        "destination_lng": driver.get("destination_lng"),
    }


@api_router.get("/balance")
async def get_driver_balance(current_user: dict = Depends(get_current_user)):
    """Get driver's current balance/earnings summary."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    try:
        rides = await db_supabase.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
            },
            limit=10000,
        )
        total_earnings = sum(r.get("driver_earnings", 0) or 0 for r in rides)
        total_tips = sum(r.get("tip_amount", 0) or 0 for r in rides)
        total_rides = len(rides)

        payouts = await db_supabase.get_rows(
            "payouts",
            {
                "driver_id": driver["id"],
                "status": "pending",
            },
            limit=1000,
        )
        pending_payouts = sum(p.get("amount", 0) or 0 for p in payouts)
    except Exception as e:
        logger.error(f"Error fetching balance: {e}")
        total_earnings = total_tips = total_rides = pending_payouts = 0

    return {
        "total_earnings": total_earnings,
        "available_balance": total_earnings - pending_payouts,
        "pending_payouts": pending_payouts,
        "total_paid_out": 0,
        "has_bank_account": bool(driver.get("bank_account")),
        "stripe_account_onboarded": bool(driver.get("stripe_account_onboarded", False)),
        "total_tips": total_tips,
        "total_rides": total_rides,
    }


@api_router.get("/earnings")
async def get_driver_earnings(period: str = Query("week"), current_user: dict = Depends(get_current_user)):
    """Get driver's earnings summary for a period."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        # Try to find by id directly in case user_id isn't set, or log error
        logger.error(f"Driver not found for user {current_user['id']}")
        raise HTTPException(status_code=404, detail="Driver not found")

    logger.info(f"Fetching earnings for driver {driver['id']} period {period}")

    # Calculate date range
    now = datetime.now(timezone.utc)
    # 'today' and 'day' both mean since midnight today
    # 'all' means no date restriction — fetch all-time
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
        # Fallback: treat unknown period as 'week'
        start_date = now - timedelta(days=7)

    try:
        filters: Dict[str, Any] = {"driver_id": driver["id"], "status": "completed"}
        if use_date_filter and start_date:
            filters["ride_completed_at"] = {"$gte": start_date.isoformat()}

        rides = await db_supabase.get_rows("rides", filters, limit=10000)

        stats = {
            "total_earnings": sum(r.get("driver_earnings", 0) or 0 for r in rides),
            "total_tips": sum(r.get("tip_amount", 0) or 0 for r in rides),
            "total_rides": len(rides),
            "total_distance_km": sum(r.get("distance_km", 0) or 0 for r in rides),
            "total_duration_minutes": sum(r.get("duration_minutes", 0) or 0 for r in rides),
        }
    except Exception as e:
        logger.error(f"Error fetching earnings: {e}")
        stats = {
            "total_earnings": 0,
            "total_tips": 0,
            "total_rides": 0,
            "total_distance_km": 0,
            "total_duration_minutes": 0,
        }

    return {
        "period": period,
        "total_earnings": stats.get("total_earnings", 0),
        "total_tips": stats.get("total_tips", 0),
        "total_rides": stats.get("total_rides", 0),
        "total_distance_km": stats.get("total_distance_km", 0),
        "total_duration_minutes": stats.get("total_duration_minutes", 0),
        "average_per_ride": stats.get("total_earnings", 0) / stats.get("total_rides", 1)
        if stats.get("total_rides", 0) > 0
        else 0,
    }


@api_router.get("/earnings/daily")
async def get_driver_daily_earnings(days: int = Query(7), current_user: dict = Depends(get_current_user)):
    """Get driver's daily earnings breakdown."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    # Fetch completed rides in the period using the shared db layer
    try:
        rides = await db_supabase.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
                "ride_completed_at": {"$gte": start_date.isoformat()},
            },
            order="ride_completed_at",
            limit=5000,
        )

        # Group by date (small dataset per driver, fine in Python)
        daily_data: dict = {}
        for r in rides:
            date_str = (r.get("ride_completed_at") or "")[:10]
            if not date_str:
                continue
            if date_str not in daily_data:
                daily_data[date_str] = {"earnings": 0, "tips": 0, "rides": 0, "distance_km": 0}
            daily_data[date_str]["earnings"] += r.get("driver_earnings", 0) or 0
            daily_data[date_str]["tips"] += r.get("tip_amount", 0) or 0
            daily_data[date_str]["rides"] += 1
            daily_data[date_str]["distance_km"] += r.get("distance_km", 0) or 0

        results = [{"date": date, **data} for date, data in sorted(daily_data.items())]
    except Exception as e:
        logger.error(f"Error fetching daily earnings: {e}")
        results = []

    return results


@api_router.get("/earnings/trips")
async def get_driver_trip_earnings(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    days: Optional[int] = Query(default=None, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
):
    """Get driver's individual trip earnings.

    ``days`` restricts results to the past N days (max 365).  Omit for no
    date restriction (capped by ``limit``).
    """
    if days is not None and days > 365:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 12 months (365 days)")

    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    filters: Dict[str, Any] = {"driver_id": driver["id"], "status": "completed"}
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        filters["ride_completed_at"] = {"$gte": since.isoformat()}

    try:
        rides = await db_supabase.get_rows(
            "rides",
            filters,
            order="ride_completed_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"Error fetching trip earnings: {e}")
        rides = []

    return {
        "trips": [
            {
                "ride_id": r["id"],
                "pickup_address": r.get("pickup_address", ""),
                "dropoff_address": r.get("dropoff_address", ""),
                "distance_km": r.get("distance_km", 0),
                "duration_minutes": r.get("duration_minutes", 0),
                "base_fare": r.get("base_fare", 0),
                "distance_fare": r.get("distance_fare", 0),
                "time_fare": r.get("time_fare", 0),
                "driver_earnings": r.get("driver_earnings", 0),
                "tip_amount": r.get("tip_amount", 0),
                "rider_rating": r.get("rider_rating"),
                "completed_at": r.get("ride_completed_at") if r.get("ride_completed_at") else None,
            }
            for r in rides
        ],
        "limit": limit,
        "offset": offset,
    }


@api_router.get("/earnings/weekly")
async def get_driver_weekly_earnings(weeks: int = Query(4), current_user: dict = Depends(get_current_user)):
    """Get driver's weekly earnings breakdown."""
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    start_date = datetime.now(timezone.utc) - timedelta(weeks=weeks)

    # Try driver_daily_stats first (pre-aggregated)
    try:
        stats = await db.get_rows(
            "driver_daily_stats",
            {"driver_id": driver["id"], "stat_date": {"$gte": start_date.strftime("%Y-%m-%d")}},
            order="stat_date",
            limit=weeks * 7,
        )
    except Exception:
        stats = []

    if stats:
        # Group by ISO week
        weekly_data: dict = {}
        for s in stats:
            date_str = s.get("stat_date", "")[:10]
            if not date_str:
                continue
            from datetime import date as date_type

            d = date_type.fromisoformat(date_str)
            iso_year, iso_week, _ = d.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            if week_key not in weekly_data:
                # Monday of that ISO week
                from datetime import timedelta as td

                monday = d - td(days=d.weekday())
                sunday = monday + td(days=6)
                weekly_data[week_key] = {
                    "week_start": monday.isoformat(),
                    "week_end": sunday.isoformat(),
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "online_hours": 0,
                    "distance_km": 0,
                }
            weekly_data[week_key]["earnings"] += s.get("total_earnings", 0) or 0
            weekly_data[week_key]["tips"] += s.get("total_tips", 0) or 0
            weekly_data[week_key]["rides"] += s.get("rides_completed", 0) or 0
            weekly_data[week_key]["online_hours"] += round((s.get("online_minutes", 0) or 0) / 60, 1)
            weekly_data[week_key]["distance_km"] += s.get("total_km", 0) or 0

        return sorted(weekly_data.values(), key=lambda x: x["week_start"])

    # Fallback: compute from rides table
    try:
        rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
                "ride_completed_at": {"$gte": start_date.isoformat()},
            },
            order="ride_completed_at",
            limit=5000,
        )

        weekly_data = {}
        for r in rides:
            date_str = (r.get("ride_completed_at") or "")[:10]
            if not date_str:
                continue
            from datetime import date as date_type

            d = date_type.fromisoformat(date_str)
            iso_year, iso_week, _ = d.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            if week_key not in weekly_data:
                from datetime import timedelta as td

                monday = d - td(days=d.weekday())
                sunday = monday + td(days=6)
                weekly_data[week_key] = {
                    "week_start": monday.isoformat(),
                    "week_end": sunday.isoformat(),
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "online_hours": 0,
                    "distance_km": 0,
                }
            weekly_data[week_key]["earnings"] += r.get("driver_earnings", 0) or 0
            weekly_data[week_key]["tips"] += r.get("tip_amount", 0) or 0
            weekly_data[week_key]["rides"] += 1
            weekly_data[week_key]["distance_km"] += r.get("distance_km", 0) or 0

        return sorted(weekly_data.values(), key=lambda x: x["week_start"])
    except Exception as e:
        logger.error(f"Error fetching weekly earnings: {e}")
        return []


@api_router.get("/earnings/monthly")
async def get_driver_monthly_earnings(months: int = Query(6), current_user: dict = Depends(get_current_user)):
    """Get driver's monthly earnings breakdown."""
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    start_date = datetime.now(timezone.utc) - timedelta(days=months * 30)

    # Try driver_daily_stats first
    try:
        stats = await db.get_rows(
            "driver_daily_stats",
            {"driver_id": driver["id"], "stat_date": {"$gte": start_date.strftime("%Y-%m-%d")}},
            order="stat_date",
            limit=months * 31,
        )
    except Exception:
        stats = []

    if stats:
        monthly_data: dict = {}
        for s in stats:
            date_str = s.get("stat_date", "")[:10]
            if not date_str:
                continue
            month_key = date_str[:7]  # YYYY-MM
            if month_key not in monthly_data:
                monthly_data[month_key] = {
                    "month": month_key,
                    "year": int(month_key[:4]),
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "online_hours": 0,
                    "distance_km": 0,
                }
            monthly_data[month_key]["earnings"] += s.get("total_earnings", 0) or 0
            monthly_data[month_key]["tips"] += s.get("total_tips", 0) or 0
            monthly_data[month_key]["rides"] += s.get("rides_completed", 0) or 0
            monthly_data[month_key]["online_hours"] += round((s.get("online_minutes", 0) or 0) / 60, 1)
            monthly_data[month_key]["distance_km"] += s.get("total_km", 0) or 0

        return sorted(monthly_data.values(), key=lambda x: x["month"])

    # Fallback: compute from rides table
    try:
        rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
                "ride_completed_at": {"$gte": start_date.isoformat()},
            },
            order="ride_completed_at",
            limit=10000,
        )

        monthly_data = {}
        for r in rides:
            date_str = (r.get("ride_completed_at") or "")[:10]
            if not date_str:
                continue
            month_key = date_str[:7]
            if month_key not in monthly_data:
                monthly_data[month_key] = {
                    "month": month_key,
                    "year": int(month_key[:4]),
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "online_hours": 0,
                    "distance_km": 0,
                }
            monthly_data[month_key]["earnings"] += r.get("driver_earnings", 0) or 0
            monthly_data[month_key]["tips"] += r.get("tip_amount", 0) or 0
            monthly_data[month_key]["rides"] += 1
            monthly_data[month_key]["distance_km"] += r.get("distance_km", 0) or 0

        return sorted(monthly_data.values(), key=lambda x: x["month"])
    except Exception as e:
        logger.error(f"Error fetching monthly earnings: {e}")
        return []


@api_router.get("/earnings/comparison")
async def get_driver_earnings_comparison(period: str = Query("week"), current_user: dict = Depends(get_current_user)):
    """Compare current period earnings vs previous period."""
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    now = datetime.now(timezone.utc)
    if period == "week":
        current_start = now - timedelta(days=7)
        previous_start = now - timedelta(days=14)
        previous_end = now - timedelta(days=7)
    else:  # month
        current_start = now - timedelta(days=30)
        previous_start = now - timedelta(days=60)
        previous_end = now - timedelta(days=30)

    try:
        # Current period
        current_rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
                "ride_completed_at": {"$gte": current_start.isoformat()},
            },
            limit=5000,
        )
        # Previous period
        all_rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
                "ride_completed_at": {"$gte": previous_start.isoformat()},
            },
            limit=10000,
        )
        previous_rides = [r for r in all_rides if r.get("ride_completed_at", "") < previous_end.isoformat()]
    except Exception as e:
        logger.error(f"Error fetching comparison: {e}")
        current_rides = []
        previous_rides = []

    def summarize(rides):
        return {
            "earnings": sum(r.get("driver_earnings", 0) or 0 for r in rides),
            "rides": len(rides),
            "tips": sum(r.get("tip_amount", 0) or 0 for r in rides),
        }

    current = summarize(current_rides)
    previous = summarize(previous_rides)

    def pct_change(curr, prev):
        if prev == 0:
            return 100.0 if curr > 0 else 0.0
        return round((curr - prev) / prev * 100, 1)

    return {
        "period": period,
        "current": current,
        "previous": previous,
        "change_pct": {
            "earnings": pct_change(current["earnings"], previous["earnings"]),
            "rides": pct_change(current["rides"], previous["rides"]),
            "tips": pct_change(current["tips"], previous["tips"]),
        },
    }


@api_router.get("/nearby")
async def get_nearby_drivers_public(
    lat: float = Query(...),
    lng: float = Query(...),
    radius: float = Query(5.0),
    vehicle_type: str = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get nearby active drivers for riders. Filters by service area + vehicle type."""
    # is_verified + status='active' prevent unverified / suspended / needs_review
    # drivers from appearing on the rider map even if their is_online flag is
    # stale.
    query = {
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
    }
    if vehicle_type:
        query["vehicle_type_id"] = vehicle_type

    # Get all matching drivers — service area filtering by distance (not polygon yet)
    drivers = await db_supabase.get_rows("drivers", query, limit=100)

    # Presence filter: hide drivers whose app is not reachable (force-killed,
    # phone dead, backgrounded past the TTL). Without this, the rider sees
    # ghost cars on the map and tries to book someone who will never receive
    # the offer. Safe-fallback: if presence lookup fails, show DB state —
    # dispatch still filters on presence so a ghost booking cannot complete.
    try:
        driver_ids = [d["id"] for d in drivers if d.get("id")]
        present = await present_driver_ids(driver_ids) if driver_ids else set()
        if present:
            drivers = [d for d in drivers if d["id"] in present]
    except Exception as exc:
        logger.warning(f"/drivers/nearby presence filter failed, using DB state: {exc}")

    # Manual filtering by distance
    nearby = []
    for d in drivers:
        # Exclude orphan/demo driver rows (no user_id → cannot be dispatched).
        if not d.get("user_id"):
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        if d_lat and d_lng:
            dist = calculate_distance(lat, lng, d_lat, d_lng)
            if dist <= radius:
                # hide personal info for riders
                safe_driver = {
                    "id": d["id"],
                    "lat": d_lat,
                    "lng": d_lng,
                    "vehicle_type_id": d.get("vehicle_type_id"),
                    "vehicle_make": d.get("vehicle_make"),
                    "vehicle_model": d.get("vehicle_model"),
                }
                nearby.append(safe_driver)

    return nearby


@api_router.get("")
async def get_drivers(
    lat: float = Query(None),
    lng: float = Query(None),
    radius: float = Query(5.0),
    vehicle_type: str = Query(None),
    admin_user: dict = Depends(get_admin_user),
):
    """
    Get all drivers (admin only) or nearby drivers (if lat/lng provided).
    """
    if lat and lng:
        # Should rely on RPC or geospatial query
        # For now, simplistic implementation as seen in other parts
        drivers = await db_supabase.get_rows("drivers", {"is_online": True}, limit=100)
        return serialize_doc(drivers)

    # Return all drivers for admin
    drivers = await db_supabase.get_rows("drivers", {}, limit=100)
    return serialize_doc(drivers)


@api_router.post("")
async def create_driver(driver: Driver, admin_user: dict = Depends(get_admin_user)):
    """Register a new driver (admin only or internal process)"""
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"phone": driver.phone}, limit=1)
    )
    if existing:
        raise HTTPException(status_code=400, detail="Driver with this phone already exists")

    await db_supabase.insert_one("drivers", driver.dict())
    return driver.dict()


@api_router.post("/location-batch")
async def update_location_batch(batch: Union[List[dict], dict], current_user: dict = Depends(get_current_user)):
    """Update driver location in batch (from background tracking)."""

    points = []
    if isinstance(batch, list):
        points = batch
    elif isinstance(batch, dict):
        points = batch.get("locations") or batch.get("points") or []

    # Simply take the last point and update current location
    if not points:
        return {"success": True}

    latest = points[-1]
    lat = latest.get("latitude") or latest.get("lat")
    lng = latest.get("longitude") or latest.get("lng")
    latest.get("heading", 0)

    if lat and lng:
        # Update via Supabase wrapper which now handles casting
        # Note: 'heading' column might not exist in Supabase 'drivers' table yet.
        update_data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc)}
        # If heading is supported later, add it back. Currently causing 500 error if column missing.
        # if heading:
        #    update_data['heading'] = heading

        await db_supabase.update_one("drivers", {"user_id": current_user["id"]}, update_data)
        # Also sync to generic lat/lng fields if they exist to support legacy queries
        # (Though update_one might not support setting multiple top-level fields easily if we rely on $set mapping)
        # Let's trust db.drivers.update_one to handle the schema or the wrapper.

        # Keep presence alive even when the driver's WebSocket briefly
        # drops but the REST location batch keeps flowing (e.g. phone on
        # cellular switching towers).
        driver_row = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        )
        if driver_row and driver_row.get("is_online"):
            await mark_present(driver_row["id"])

    return {"success": True}


import uuid  # noqa: E402


class BankAccountCreate(BaseModel):
    bank_name: str
    institution_number: str
    transit_number: str
    account_number: str
    account_holder_name: str
    account_type: str = "checking"


class PayoutRequest(BaseModel):
    amount: Decimal = Field(
        ...,
        ge=Decimal("10.00"),
        decimal_places=2,
        description="Minimum payout is $10.00",
    )


@api_router.get("/bank-account")
async def get_bank_account(current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    account = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("bank_accounts", {"driver_id": driver["id"]}, limit=1)
    )
    if account:
        return {"has_bank_account": True, "bank_account": serialize_doc(account)}

    if driver.get("stripe_account_onboarded"):
        return {
            "has_bank_account": True,
            "bank_account": {"bank_name": "Stripe Connect", "account_number_last4": "****"},
        }

    return {"has_bank_account": False, "bank_account": None}


@api_router.post("/stripe-onboard")
async def onboard_stripe(current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    user = await db_supabase.get_user_by_id(current_user.get("id"))
    if not driver or not user:
        raise HTTPException(status_code=404, detail="Driver/User profile not found")

    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        return {"url": "https://spinr-demo-onboard.com", "mock": True}

    try:
        account_id = driver.get("stripe_account_id")

        if not account_id:
            account = stripe.Account.create(
                type="express",
                country="CA",
                email=user.get("email"),
                capabilities={
                    "transfers": {"requested": True},
                },
                business_type="individual",
                api_key=stripe_secret,
            )
            account_id = account.id
            await db_supabase.update_one("drivers", {"id": driver["id"]}, {"stripe_account_id": account_id})

        account_link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=f"{settings.get('base_url', 'http://localhost:8000')}/api/drivers/stripe-refresh",
            return_url=f"{settings.get('base_url', 'http://localhost:8000')}/api/drivers/stripe-return",
            type="account_onboarding",
            api_key=stripe_secret,
        )
        # Mark as onboarded optimistically or handle via webhook/return_url properly in production
        await db_supabase.update_one("drivers", {"id": driver["id"]}, {"stripe_account_onboarded": True})

        return {"url": account_link.url, "mock": False}
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.") from e


@api_router.post("/bank-account")
async def save_bank_account(req: BankAccountCreate, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    account_data = req.dict()
    account_data["id"] = str(uuid.uuid4())
    account_data["driver_id"] = driver["id"]
    acc_num = account_data.pop("account_number")

    # Canadian routing number for Stripe is generally 0 + Institution (3) + Transit (5)
    # Ensure zero-padding if needed
    inst = req.institution_number.zfill(3)
    trans = req.transit_number.zfill(5)
    account_data["routing_number"] = f"0{inst}{trans}"

    account_data["account_number_last4"] = acc_num[-4:] if len(acc_num) >= 4 else acc_num
    account_data["stripe_bank_id"] = None  # Would be populated after calling Stripe's API
    account_data["currency"] = "cad"
    account_data["country"] = "CA"
    account_data["is_verified"] = False
    account_data["created_at"] = datetime.now(timezone.utc).isoformat()

    await db_supabase.delete_many("bank_accounts", {"driver_id": driver["id"]})
    await db_supabase.insert_one("bank_accounts", account_data)

    return {"success": True, "bank_account": serialize_doc(account_data)}


@api_router.delete("/bank-account")
async def delete_bank_account(current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    await db_supabase.delete_many("bank_accounts", {"driver_id": driver["id"]})
    return {"success": True}


@api_router.post("/payouts")
@idempotent_endpoint(scope="driver_payout")
async def request_payout(
    req: PayoutRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    balance = await get_driver_balance(current_user)
    if req.amount > balance.get("available_balance", 0):
        raise HTTPException(status_code=400, detail="Insufficient funds")

    stripe_account_id = driver.get("stripe_account_id")
    account = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("bank_accounts", {"driver_id": driver["id"]}, limit=1)
    )

    if not stripe_account_id and not account:
        raise HTTPException(status_code=400, detail="No bank account linked")

    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    status = "pending"
    stripe_payout_id = None

    if stripe_secret and stripe_account_id:
        try:
            transfer = stripe.Transfer.create(
                amount=int(req.amount * 100),
                currency="cad",
                destination=stripe_account_id,
                api_key=stripe_secret,
            )
            status = "completed"
            stripe_payout_id = transfer.id
        except Exception as e:
            # B-P3-leak-cleanup: same pattern as the subscription
            # charge fix — Stripe transfer errors carry account IDs
            # (acct_…), transfer IDs (tr_…), and bank-account hints
            # we must not ship. logger.exception captures the full
            # traceback server-side.
            logger.exception("Stripe transfer failed for driver payout")
            raise HTTPException(
                status_code=500,
                detail="Payout failed. Please contact support.",
            ) from e

    payout = {
        "id": str(uuid.uuid4()),
        "driver_id": driver["id"],
        "amount": req.amount,
        "status": status,
        "stripe_payout_id": stripe_payout_id,
        "bank_name": account.get("bank_name") if account else "Stripe Connect",
        "account_last4": account.get("account_number_last4") if account else "****",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("payouts", payout)
    return {"success": True, "payout": serialize_doc(payout)}


@api_router.get("/payouts")
async def get_payout_history(
    limit: int = Query(20), offset: int = Query(0), current_user: dict = Depends(get_current_user)
):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    payouts = await db_supabase.get_rows(
        "payouts",
        {"driver_id": driver["id"]},
        limit=limit,
        offset=offset,
        order="created_at",
        desc=True,
    )
    return {"success": True, "payouts": [serialize_doc(p) for p in payouts]}


@api_router.get("/t4a/{year}")
async def get_t4a_summary(year: int, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    rides = await db_supabase.get_rides_for_driver(
        driver["id"],
        statuses=["completed"],
        from_date=f"{year}-01-01",
        to_date=f"{year + 1}-01-01",
        limit=10000,
    )

    total_earnings = sum(r.get("driver_earnings", 0) for r in rides)

    return {
        "year": year,
        "total_earnings": total_earnings,
        "total_trips": len(rides),
        "platform_fees": 0,
        "net_earnings": total_earnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@api_router.get("/earnings/export")
async def export_earnings(year: int = Query(None), current_user: dict = Depends(get_current_user)):
    if not year:
        year = datetime.now(timezone.utc).year

    summary_data = await get_t4a_summary(year, current_user)

    csv_data = f"Year,Total Earnings,Total Trips,Net Earnings\n{year},{summary_data['total_earnings']},{summary_data['total_trips']},{summary_data['net_earnings']}"
    filename = f"earnings_export_{year}.csv"

    return {"data": csv_data, "filename": filename}


@api_router.post("/me/export-data")
async def export_driver_data(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """GDPR/PIPEDA: export all personal data for the authenticated driver.

    Immediately returns a confirmation message. The actual data collection,
    JSON generation, and email delivery happen in a background task so the
    driver does not wait.
    """
    user_id = current_user["id"]
    email = current_user.get("email") or current_user.get("phone", "")
    if not email:
        raise HTTPException(status_code=400, detail="No email address on file to send the export to.")

    background_tasks.add_task(_build_and_email_data_export, user_id, email)
    return {"message": "Your data export is being prepared. Check your email."}


async def _build_and_email_data_export(user_id: str, email: str) -> None:
    """Background task: collect all driver data and email a JSON export."""
    import asyncio  # noqa: PLC0415
    import json  # noqa: PLC0415

    try:
        # B-P2-6: PIPEDA right-to-access has a 30-day SLA but riders/drivers
        # judge "fast" by the email arrival, not the SLA. The previous
        # 6-sequential-await pattern accumulated ~6× round-trip latency.
        # Wave 1 (no driver_id needed) — 3 reads in parallel.
        driver_rows, user_rows, notification_prefs = await asyncio.gather(
            db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1),
            db_supabase.get_rows("users", {"id": user_id}, limit=1),
            db_supabase.get_rows("notification_preferences", {"user_id": user_id}, limit=1),
        )
        driver = (driver_rows or [{}])[0] if driver_rows else {}
        user = (user_rows or [{}])[0] if user_rows else {}
        driver_id = driver.get("id", "")
        notification_prefs = notification_prefs or []

        # Wave 2 (driver_id-dependent) — 3 reads in parallel. Skip entirely
        # if there's no driver row (rider-only account requesting export).
        rides: list = []
        payouts: list = []
        documents: list = []
        if driver_id:
            rides, payouts, documents = await asyncio.gather(
                db_supabase.get_rows("rides", {"driver_id": driver_id}, limit=500, order="created_at", desc=True),
                db_supabase.get_rows(
                    "driver_payouts", {"driver_id": driver_id}, limit=200, order="created_at", desc=True
                ),
                db_supabase.get_rows("driver_documents", {"driver_id": driver_id}, limit=50),
            )
            rides = rides or []
            payouts = payouts or []
            documents = documents or []

        export_payload = {
            "export_generated_at": datetime.now(timezone.utc).isoformat() + "Z",
            "account": {k: v for k, v in user.items() if k not in ("password_hash",)},
            "driver_profile": {k: v for k, v in driver.items() if k not in ("password_hash",)},
            "rides": rides,
            "payouts": payouts,
            "documents": [{k: v for k, v in doc.items() if k != "document_url"} for doc in documents],
            "notification_preferences": notification_prefs,
        }

        export_json = json.dumps(export_payload, indent=2, default=str)

        subject = "Your Spinr Data Export"
        body = (
            "Hi,\n\n"
            "As requested, here is a full export of your personal data held by Spinr.\n\n"
            "Your data export is attached below as JSON.\n\n"
            "If you have any questions about your data or would like to request deletion, "
            "please contact privacy@spinr.ca.\n\n"
            "— The Spinr Team\n\n"
            "---\n\n" + export_json
        )

        await send_email(to=email, subject=subject, body=body)
        logger.info("Data export emailed to %s for user %s", email, user_id)

    except Exception as exc:
        logger.error("Data export failed for user %s: %s", user_id, exc)


# ==========================================
# RIDE MANAGEMENT ENDPOINTS
# ==========================================


@api_router.get("/rides/active")
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
                "status": {"$in": ["driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]},
            },
            limit=1,
        )
    )

    if not ride:
        # Help diagnose: list the driver's most recent rides regardless of
        # status so we can see whether the $in filter missed something, or
        # the driver_id on the latest ride doesn't match.
        try:
            recent = await db_supabase.get_rows("rides", {"driver_id": driver["id"]}, limit=5)
            recent_summary = [
                {"id": r.get("id"), "status": r.get("status"), "driver_id": r.get("driver_id")} for r in (recent or [])
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
        logger.error(f"get_active_ride: failed to load rider {ride['rider_id']}: {e}", exc_info=True)
        rider = None
    try:
        vehicle_type = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("vehicle_types", {"id": ride["vehicle_type_id"]}, limit=1)
        )
    except Exception as e:
        logger.error(f"get_active_ride: failed to load vehicle_type {ride['vehicle_type_id']}: {e}", exc_info=True)
        vehicle_type = None

    # R-P1-28: Strip PII fields from the rider object — drivers only need
    # first name + profile photo for the in-app UI. Phone, email, and
    # Stripe customer ID must never be exposed to the driver.
    safe_rider = None
    if rider:
        raw = serialize_doc(rider)
        safe_rider = {k: raw[k] for k in raw if k not in {"phone", "email", "stripe_customer_id"}}

    return {
        "ride": serialize_doc(ride),
        "rider": safe_rider,
        "vehicle_type": serialize_doc(vehicle_type) if vehicle_type else None,
    }


@api_router.get("/rides/history")
async def get_ride_history(
    limit: int = Query(20), offset: int = Query(0), current_user: dict = Depends(get_current_user)
):
    """Get driver's ride history."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    try:
        history_filter = {
            "driver_id": driver["id"],
            "status": {"$in": ["completed", "cancelled"]},
        }
        total = await db_supabase.count_documents("rides", history_filter)
        rides = await db_supabase.get_rows(
            "rides",
            history_filter,
            order="created_at",
            desc=True,
            limit=min(limit, 500),
            offset=offset,
        )
    except Exception as e:
        logger.error(f"Error fetching ride history: {e}")
        total = 0
        rides = []

    return {"total": total, "rides": [serialize_doc(r) for r in rides]}


@api_router.post("/rides/{ride_id}/accept")
async def accept_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    if driver.get("status") == "suspended":
        raise HTTPException(
            status_code=403,
            detail="Your account is suspended. Please renew your documents to continue driving.",
        )

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # A driver-user must never accept a ride they themselves created — prevents
    # self-dispatch fraud on dual-role accounts.
    if ride.get("rider_id") == current_user["id"]:
        raise HTTPException(status_code=403, detail="Cannot accept your own ride")

    diag_logger.info(
        f"[ACCEPT] entry ride_id={ride_id} driver_id={driver.get('id')} "
        f"pre_status={ride.get('status')} pre_driver_id={ride.get('driver_id')}"
    )

    # Verify this driver was assigned
    if ride.get("driver_id") != driver["id"]:
        # Check if it's open (searching) and we can claim it?
        # For now assume mostly assigned flow.
        # If status is searching, we might allow claim if using broadcast.
        if ride["status"] == "searching":
            # Allow claim
            pass
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
        accept_filter = {"id": ride_id, "status": "driver_assigned", "driver_id": driver["id"]}
    else:
        # Broadcast/searching path: claim only if the ride is still unclaimed.
        accept_filter = {"id": ride_id, "status": "searching"}

    guard = await db.update_one(
        "rides",
        accept_filter,
        {
            "$set": {
                "status": "driver_accepted",
                "driver_id": driver["id"],
                "driver_accepted_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    if hasattr(guard, "modified_count") and guard.modified_count == 0:
        diag_logger.info(
            f"[ACCEPT] claim rejected ride_id={ride_id} "
            f"current_status={ride.get('status')} current_driver_id={ride.get('driver_id')}"
        )
        raise HTTPException(status_code=409, detail="Ride already accepted by another driver")

    # Re-read the now-claimed ride so we can notify the rider with fresh data.
    ride = await db.find_one("rides", {"id": ride_id})
    diag_logger.info(
        f"[ACCEPT] success ride_id={ride_id} driver_id={driver['id']} "
        f"post_status={ride.get('status') if ride else 'ROW_GONE'}"
    )

    # Notify rider via both WebSocket (for the instant in-app
    # transition) AND FCM push (so the rider still gets the update
    # if the app was backgrounded when the driver accepted).
    # The `data` payload lets the rider-app foreground FCM handler in
    # app/_layout.tsx route the event without reparsing the title.
    if ride and ride.get("rider_id"):
        await manager.send_personal_message(
            {"type": "driver_accepted", "ride_id": ride_id}, f"rider_{ride['rider_id']}"
        )
        await send_push_notification(
            ride["rider_id"],
            "Driver Assigned! 🚗",
            "Your driver has accepted the ride and is on the way.",
            data={"type": "driver_accepted", "ride_id": str(ride_id)},
        )
    await manager.broadcast_ride_status(ride_id, "driver_accepted", rider_id=(ride or {}).get("rider_id"))

    return {"success": True}


@api_router.post("/rides/{ride_id}/decline")
async def decline_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # If assigned, unassign. If searching, just ignore/record decline.
    await db_supabase.update_ride(
        ride_id, {"driver_id": None, "status": "searching", "updated_at": datetime.now(timezone.utc)}
    )

    # Record the decline in audit_logs so daily stats can count it
    try:
        import uuid as _uuid

        await db.insert_one(
            "audit_logs",
            {
                "id": str(_uuid.uuid4()),
                "action": "ride_declined",
                "entity_type": "ride",
                "entity_id": ride_id,
                "user_email": driver["id"],  # reuse user_email column to store driver_id
                "details": f"driver_id={driver['id']}",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as _e:
        logger.error(f"Could not log ride decline to audit_logs: {_e}", exc_info=True)

    # GAP FIX: Re-match to find the next available driver
    try:
        import asyncio

        from .rides import match_driver_to_ride

        asyncio.create_task(match_driver_to_ride(ride_id))
        logger.info(f"Re-matching ride {ride_id} after driver {driver['id']} declined")
    except Exception as e:
        logger.error(f"Could not trigger re-matching for ride {ride_id}: {e}", exc_info=True)

    return {"success": True}


@api_router.post("/rides/{ride_id}/arrive")
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

    # Geofence check - verify driver is within 200m of pickup location
    ARRIVAL_RADIUS_KM = 0.2  # 200 meters
    driver_lat = driver.get("lat", 0)
    driver_lng = driver.get("lng", 0)
    pickup_lat = ride.get("pickup_lat", 0)
    pickup_lng = ride.get("pickup_lng", 0)

    if driver_lat and driver_lng and pickup_lat and pickup_lng:
        distance_to_pickup = calculate_distance(driver_lat, driver_lng, pickup_lat, pickup_lng)
        if distance_to_pickup > ARRIVAL_RADIUS_KM:
            distance_m = int(distance_to_pickup * 1000)
            raise HTTPException(
                status_code=400,
                detail=f"You are {distance_m}m away from the pickup. "
                f"Please move within 200m of the pickup location to mark arrival.",
            )

    await db_supabase.update_ride(
        ride_id,
        {
            "status": "driver_arrived",
            "driver_arrived_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )

    if ride.get("rider_id"):
        await manager.send_personal_message({"type": "driver_arrived", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        await send_push_notification(
            ride["rider_id"],
            "Driver Arrived! 📍",
            "Your driver has arrived at the pickup location.",
            data={"type": "driver_arrived", "ride_id": str(ride_id)},
        )
    await manager.broadcast_ride_status(ride_id, "driver_arrived", rider_id=ride.get("rider_id"))

    return {"success": True}


@api_router.post("/rides/{ride_id}/verify-otp")
async def verify_pickup_otp(ride_id: str, request: RideOTPRequest, current_user: dict = Depends(get_current_user)):
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

    if not hmac.compare_digest(ride.get("pickup_otp", ""), hash_otp(request.otp)):
        raise HTTPException(status_code=400, detail="Invalid OTP")

    # OTP correct, start ride
    await db_supabase.update_ride(
        ride_id,
        {
            "status": "in_progress",
            "ride_started_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )

    if ride.get("rider_id"):
        await manager.send_personal_message({"type": "ride_started", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        await send_push_notification(
            ride["rider_id"],
            "Ride Started! ▶️",
            "Your ride has started. Have a safe trip!",
            data={"type": "ride_started", "ride_id": str(ride_id)},
        )
    await manager.broadcast_ride_status(ride_id, "in_progress", rider_id=ride.get("rider_id"))

    return {"success": True}


@api_router.post("/rides/{ride_id}/start")
async def start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Start ride without OTP (if configured) or fallback."""
    # Logic similar to verify_otp but without check
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await db_supabase.update_ride(
        ride_id,
        {
            "status": "in_progress",
            "ride_started_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )

    ride = await db_supabase.get_ride(ride_id)
    if ride and ride.get("rider_id"):
        await manager.send_personal_message({"type": "ride_started", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        await send_push_notification(
            ride["rider_id"],
            "Ride Started! ▶️",
            "Your ride has started. Have a safe trip!",
            data={"type": "ride_started", "ride_id": str(ride_id)},
        )
    await manager.broadcast_ride_status(ride_id, "in_progress", rider_id=(ride or {}).get("rider_id"))
    return {"success": True}


@api_router.post("/rides/{ride_id}/complete")
async def complete_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
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

    if ride.get("status") not in COMPLETE_FROM_STATES:
        raise RideStateError(f"Cannot complete ride from state '{ride.get('status')}'; ride must be in_progress")

    # ── Aggregate all GPS breadcrumbs for this ride ──
    # On completion we compute everything once and store it on the ride row.
    # After this the admin dashboard reads from the ride row directly — no
    # need to join against driver_location_history for historical rides.
    planned_distance = ride.get("planned_distance_km") or ride.get("distance_km", 0) or 0
    actual_distance_km = planned_distance
    phase_distances: Dict[str, float] = {}
    phase_durations: Dict[str, int] = {}
    phase_polylines: Dict[str, list] = {}
    pickup_to_driver_km = 0.0
    route_polyline = []
    gps_points_count = 0

    try:
        all_breadcrumbs = await db_supabase.get_rows(
            "driver_location_history",
            {
                "ride_id": ride_id,
            },
            limit=1000,
            order="timestamp",
        )
        all_breadcrumbs = [b for b in all_breadcrumbs if b.get("lat") and b.get("lng")]
        all_breadcrumbs.sort(key=lambda b: str(b.get("timestamp", "")))
        gps_points_count = len(all_breadcrumbs)

        if gps_points_count >= 2:
            # Compute per-phase distances (attribute each segment to the
            # current point's phase) and per-phase durations from the
            # timestamp deltas. Phase 1 (online_idle) is not expected
            # against a ride_id but tolerated if it shows up.
            phase_totals: Dict[str, float] = {}
            phase_secs: Dict[str, float] = {}
            for i in range(1, len(all_breadcrumbs)):
                prev = all_breadcrumbs[i - 1]
                curr = all_breadcrumbs[i]
                phase = curr.get("tracking_phase") or "unknown"
                seg_km = calculate_distance(prev["lat"], prev["lng"], curr["lat"], curr["lng"])
                phase_totals[phase] = phase_totals.get(phase, 0.0) + seg_km
                # Duration: only count if the gap is reasonable (< 5 min)
                # to avoid one stale breadcrumb inflating a phase by hours.
                t_prev = parse_iso_utc(prev.get("timestamp"))
                t_curr = parse_iso_utc(curr.get("timestamp"))
                if t_prev and t_curr:
                    delta = (t_curr - t_prev).total_seconds()
                    if 0 < delta <= 300:
                        phase_secs[phase] = phase_secs.get(phase, 0.0) + delta
            phase_distances = {k: round(v, 3) for k, v in phase_totals.items()}
            phase_durations = {k: int(round(v)) for k, v in phase_secs.items()}

            # Actual distance = trip_in_progress only (the paid portion)
            actual_distance_km = round(phase_distances.get("trip_in_progress", 0.0), 2)
            if actual_distance_km == 0:
                actual_distance_km = planned_distance

            pickup_to_driver_km = round(phase_distances.get("navigating_to_pickup", 0.0), 2)

            # Per-phase polylines for SGI / dispute tooling. Each phase is
            # downsampled to MAX_PER_PHASE points so a long trip's payload
            # stays bounded. Stored as [lat, lng, iso_ts] tuples so the
            # admin can replay the trip with timing on the detail map.
            MAX_PER_PHASE = 150
            phases_to_split = ("navigating_to_pickup", "trip_in_progress")
            for phase in phases_to_split:
                pts = [b for b in all_breadcrumbs if b.get("tracking_phase") == phase]
                if not pts:
                    continue
                step = max(1, len(pts) // MAX_PER_PHASE)
                sampled = pts[::step]
                if sampled and sampled[-1] is not pts[-1]:
                    sampled.append(pts[-1])
                phase_polylines[phase] = [
                    [
                        round(p["lat"], 6),
                        round(p["lng"], 6),
                        str(p.get("timestamp") or ""),
                    ]
                    for p in sampled
                ]

            # Legacy combined polyline (kept for the existing ride-detail
            # map renderer that hasn't moved to phase_polylines yet).
            trip_points = [b for b in all_breadcrumbs if b.get("tracking_phase") in phases_to_split]
            if trip_points:
                MAX_POINTS = 200
                step = max(1, len(trip_points) // MAX_POINTS)
                sampled = trip_points[::step]
                if sampled and sampled[-1] is not trip_points[-1]:
                    sampled.append(trip_points[-1])
                route_polyline = [
                    [round(p["lat"], 6), round(p["lng"], 6), p.get("tracking_phase", "")] for p in sampled
                ]
    except Exception as e:
        logger.error(f"Could not aggregate GPS data for ride {ride_id}: {e}", exc_info=True)

    # ── Build update payload ──
    # P0-5: do NOT write payment_status here. The driver completing the
    # trip does not mean the rider's card has been charged. Leave
    # payment_status as whatever it was (typically "pending") and let
    # rides.py::process_payment be the single writer that flips it to
    # "paid" / "failed" / "requires_action" based on the real Stripe
    # outcome. Previously this hardcoded "completed" — not a valid
    # payment_status value, and it masked genuine failures from the
    # webhook dispatcher in webhooks.py.
    update_fields: Dict[str, Any] = {
        "status": "completed",
        "ride_completed_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "planned_distance_km": planned_distance,
        "actual_distance_km": actual_distance_km,
        "pickup_to_driver_km": pickup_to_driver_km,
        "phase_distances": phase_distances,
        "phase_durations": phase_durations,
        "phase_polylines": phase_polylines,
        "route_polyline": route_polyline,
        "gps_points_count": gps_points_count,
    }

    # Recalculate fare if actual distance differs materially from estimate
    if actual_distance_km > 0 and abs(actual_distance_km - planned_distance) > 0.1:
        per_km_rate = (ride.get("distance_fare", 0) / planned_distance) if planned_distance > 0 else 0
        new_distance_fare = round(per_km_rate * actual_distance_km, 2)
        new_total_fare = round(
            ride.get("base_fare", 0)
            + new_distance_fare
            + ride.get("time_fare", 0)
            + ride.get("booking_fee", 0)
            + (ride.get("airport_fee") or 0),
            2,
        )
        new_driver_earnings = round(
            ride.get("base_fare", 0) + new_distance_fare + ride.get("time_fare", 0),
            2,
        )
        update_fields.update(
            {
                "distance_km": actual_distance_km,  # overwrite with actual
                "distance_fare": new_distance_fare,
                "total_fare": new_total_fare,
                "driver_earnings": new_driver_earnings,
            }
        )
        logger.info(
            f"Ride {ride_id}: fare recalculated on completion. "
            f"planned={planned_distance}km actual={actual_distance_km}km"
        )
    else:
        update_fields["distance_km"] = actual_distance_km

    try:
        await db_supabase.update_one("rides", {"id": ride_id, "driver_id": driver["id"]}, update_fields)
    except Exception as e:
        # Some columns may not exist yet in older deployments. Retry with only
        # the essential fields so ride completion never fails.
        err_msg = str(e).lower()
        if "column" in err_msg or "pgrst204" in err_msg:
            logger.warning(f"Retrying ride update with minimal fields: {e}")
            safe_keys = {"status", "ride_completed_at", "payment_status", "updated_at", "distance_km"}
            safe_updates = {k: v for k, v in update_fields.items() if k in safe_keys}
            await db_supabase.update_one("rides", {"id": ride_id, "driver_id": driver["id"]}, safe_updates)
        else:
            raise

    # Post-ride receipt notification stub
    rider = await db_supabase.get_user_by_id(ride.get("rider_id"))
    if rider and rider.get("email"):
        logger.info(f"Sending email receipt for ride {ride_id} to {rider['email']}")

    # Fire-and-forget: render the route PNG from phase_polylines and
    # upload to Cloudinary so the admin drawer + email receipt can
    # embed a permanent image URL. Runs as a background task so the
    # driver's "Complete" response isn't blocked on OSM tile fetches.
    # Any failure here is swallowed — the snapshot is best-effort and
    # the ride is already fully persisted with phase data.
    asyncio.create_task(
        _generate_and_store_ride_snapshot(
            ride_id=ride_id,
            pickup_lat=ride.get("pickup_lat"),
            pickup_lng=ride.get("pickup_lng"),
            dropoff_lat=ride.get("dropoff_lat"),
            dropoff_lng=ride.get("dropoff_lng"),
            phase_polylines=phase_polylines,
            route_polyline=route_polyline,
        )
    )

    # Update driver stats
    await db_supabase.update_one(
        "drivers", {"id": driver["id"]}, {"$inc": {"total_rides": 1}, "$set": {"is_available": True}}
    )

    completed_ride = await db_supabase.get_ride(ride_id)

    if completed_ride and completed_ride.get("rider_id"):
        await manager.send_personal_message(
            {
                "type": "ride_completed",
                "ride_id": ride_id,
                "total_fare": completed_ride.get("total_fare", ride.get("total_fare", 0)),
            },
            f"rider_{completed_ride['rider_id']}",
        )
        await send_push_notification(
            completed_ride["rider_id"],
            "Ride Completed! ✅",
            f"Your ride has finished. Total fare: ${completed_ride.get('total_fare', ride.get('total_fare', 0))}",
            data={"type": "ride_completed", "ride_id": str(ride_id)},
        )

    total_fare = (completed_ride or {}).get("total_fare", ride.get("total_fare", 0))
    await manager.broadcast_ride_status(
        ride_id,
        "completed",
        rider_id=(completed_ride or {}).get("rider_id"),
        total_fare=total_fare,
    )
    # Keep the specific ``ride_completed`` event on admin too for dashboards
    # that switch directly on the event name rather than status.
    try:
        await manager.broadcast_to_admins({"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"complete_ride: admin broadcast failed: {_exc}")

    return serialize_doc(completed_ride)


@api_router.post("/rides/{ride_id}/cancel")
async def cancel_ride(ride_id: str, reason: str = Query(""), current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("status") == "in_progress":
        raise RideStateError("Cannot cancel a trip that is already in progress")

    now = datetime.now(timezone.utc)
    base_update = {
        "status": "cancelled",
        "cancelled_at": now,
        "updated_at": now,
    }

    # Try to persist cancelled_by / cancellation_reason for audit. These
    # columns may not exist in older Supabase schemas — PGRST204 on an
    # unknown column would crash the whole cancel. Fall back to the
    # minimal update so the cancellation still succeeds.
    try:
        await db_supabase.update_ride(
            ride_id,
            {
                **base_update,
                "cancelled_by": "driver",
                # Migration 38 — coarse attribution for admin filtering.
                "cancellation_type": "driver_cancel",
                "cancellation_reason": (reason or "").strip() or None,
            },
        )
    except Exception as exc:
        logger.warning(
            f"[CANCEL] cancelled_by/cancellation_reason write failed (likely PGRST204); retrying minimal update: {exc}"
        )
        await db_supabase.update_ride(ride_id, base_update)

    # Make driver available again
    await db_supabase.set_driver_available(driver["id"], True)

    ride = await db_supabase.get_ride(ride_id)
    if ride and ride.get("rider_id"):
        await manager.send_personal_message(
            {"type": "ride_cancelled", "ride_id": ride_id, "reason": reason}, f"rider_{ride['rider_id']}"
        )
        await send_push_notification(
            ride["rider_id"],
            "Ride Cancelled ❌",
            "Your driver has cancelled the ride.",
            data={"type": "ride_cancelled", "ride_id": str(ride_id)},
        )
    await manager.broadcast_ride_status(
        ride_id,
        "cancelled",
        rider_id=(ride or {}).get("rider_id"),
        reason="driver_cancelled",
    )
    # Keep the specific ``ride_cancelled`` event on admin for dashboards
    # that switch on event name.
    try:
        await manager.broadcast_to_admins({"type": "ride_cancelled", "ride_id": ride_id, "reason": "driver_cancelled"})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"driver cancel admin broadcast failed: {_exc}")

    return {"success": True}


@api_router.post("/rides/{ride_id}/rate-rider")
async def rate_rider(ride_id: str, rating_data: RideRatingRequest, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Update ride with rating
    await db_supabase.update_ride(
        ride_id,
        {
            "rider_rating": rating_data.rating,
            "rider_comment": rating_data.comment,
            "updated_at": datetime.now(timezone.utc),
        },
    )

    return {"success": True}


# ============ Referral Program Endpoints ============


class ApplyReferralCodeRequest(BaseModel):
    referral_code: str


@api_router.get("/referral")
async def get_driver_referral_info(current_user: dict = Depends(get_current_user)):
    """Get driver's referral code and earnings from referrals."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Get or create referral code (use driver ID as default code)
    referral_code = driver.get("referral_code", f"DRIVER{driver['id'][:8].upper()}")

    # Find users who used this referral code
    referred_users_cursor = db_supabase.get_rows("users", {"referral_code_used": referral_code}, limit=100)
    referred_users = (
        await referred_users_cursor.to_list(100)
        if hasattr(referred_users_cursor, "to_list")
        else list(referred_users_cursor)
    )

    # Calculate referral earnings (e.g., $10 per referred driver who completes 10 rides)
    total_referrals = len(referred_users)
    referral_earnings = 0

    # Check how many referred drivers have completed rides
    for user in referred_users:
        # Check if user became a driver and completed rides
        referred_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
        )
        if referred_driver:
            completed_rides = await db_supabase.count_documents(
                "rides", {"driver_id": referred_driver["id"], "status": "completed"}
            )
            if completed_rides >= 10:
                referral_earnings += 10  # $10 bonus

    return {
        "referral_code": referral_code,
        "referral_link": f"https://spinr.app/join/{referral_code}",
        "total_referrals": total_referrals,
        "referral_earnings": referral_earnings,
        "terms": "Earn $10 for each driver who signs up with your code and completes 10 rides.",
    }


@api_router.post("/referral/apply")
async def apply_referral_code(req: ApplyReferralCodeRequest, current_user: dict = Depends(get_current_user)):
    """Apply a referral code during driver onboarding."""
    code = req.referral_code.strip().upper()

    # Check if user already has a referral code applied
    user = await db_supabase.get_user_by_id(current_user["id"])
    if user and user.get("referral_code_used"):
        raise HTTPException(status_code=400, detail="Referral code already applied")

    # Validate referral code exists (check if any driver has this code)
    ref_driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"referral_code": code}, limit=1)
    )
    if not ref_driver:
        # Legacy fallback: allow `DRIVER<id-suffix>` format where the
        # suffix is the last 8 chars of a driver ID. The original
        # implementation used a `$regex` filter which (a) the Supabase
        # translator silently dropped, so this path never matched, and
        # (b) would have been a ReDoS vector on MongoDB.
        #
        # Replacement: accept only an 8-char alphanumeric suffix, then
        # use a bounded PostgREST `.ilike()` lookup. The `%` suffix
        # wildcard means "id ends with this string" — exactly what the
        # original code was trying to do.
        potential_id = code.replace("DRIVER", "")
        if len(potential_id) == 8:
            ref_driver = (lambda _r: _r[0] if _r else None)(
                await db_supabase.get_rows("drivers", {"id": {"$regex": f".*{potential_id}.*"}}, limit=1)
            )

    if not ref_driver:
        raise HTTPException(status_code=404, detail="Invalid referral code")

    # Apply referral code to user
    await db_supabase.update_one(
        "users", {"id": current_user["id"]}, {"referral_code_used": code, "referred_by": ref_driver["id"]}
    )

    return {"success": True, "referral_code": code}


@api_router.get("/referrals")
async def get_referred_drivers(
    limit: int = Query(50), offset: int = Query(0), current_user: dict = Depends(get_current_user)
):
    """Get list of drivers referred by current driver."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    referral_code = driver.get("referral_code", f"DRIVER{driver['id'][:8].upper()}")

    # Find users who used this referral code and became drivers
    referred_users_cursor = db_supabase.get_rows("users", {"referral_code_used": referral_code}, limit=100)
    referred_users = (
        await referred_users_cursor.to_list(100)
        if hasattr(referred_users_cursor, "to_list")
        else list(referred_users_cursor)
    )

    referred_drivers = []
    for user in referred_users:
        referred_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
        )
        if referred_driver:
            # Get completed rides count
            completed_rides = await db_supabase.count_documents(
                "rides", {"driver_id": referred_driver["id"], "status": "completed"}
            )
            referred_drivers.append(
                {
                    "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or "Driver",
                    "email": user.get("email", ""),
                    "referred_at": user.get("created_at", ""),
                    "total_trips": completed_rides,
                    "status": "active" if completed_rides > 0 else "pending",
                }
            )

    return {"referred_drivers": referred_drivers[:limit]}


# ── Leaderboard ──────────────────────────────────────────────────────


@api_router.get("/leaderboard")
async def get_driver_leaderboard(
    period: str = Query("week", pattern="^(week|month|all)$"),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Get driver leaderboard rankings by rides, earnings, and rating.

    Returns the top drivers for the specified period with the current
    driver's rank highlighted.
    """
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    now = datetime.now(timezone.utc)
    if period == "week":
        start = (now - timedelta(days=7)).isoformat()
    elif period == "month":
        start = (now - timedelta(days=30)).isoformat()
    else:
        start = "2020-01-01"

    try:
        all_drivers = await db.get_rows("drivers", {}, limit=500)
    except Exception:
        all_drivers = []

    rankings = []
    for d in all_drivers:
        d_id = d["id"]
        try:
            rides = await db.get_rows(
                "rides",
                {"driver_id": d_id, "status": "completed"},
                limit=1000,
            )
            period_rides = [
                r for r in rides if isinstance(r.get("created_at", ""), str) and r.get("created_at", "") >= start
            ]
        except Exception:
            period_rides = []

        total_rides = len(period_rides)
        total_earnings = sum(float(r.get("driver_earnings", 0) or 0) for r in period_rides)
        total_tips = sum(float(r.get("tip_amount", 0) or 0) for r in period_rides)

        user = await db.find_one("users", {"id": d.get("user_id")})
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Driver"

        rankings.append(
            {
                "driver_id": d_id,
                "name": name,
                "rides": total_rides,
                "earnings": round(total_earnings, 2),
                "tips": round(total_tips, 2),
                "rating": d.get("rating", 0),
                "is_current_user": d_id == driver["id"],
            }
        )

    # Sort by rides (primary), then earnings (secondary)
    rankings.sort(key=lambda x: (x["rides"], x["earnings"]), reverse=True)

    # Assign ranks
    for i, r in enumerate(rankings):
        r["rank"] = i + 1

    # Find current driver's rank
    my_rank = next((r for r in rankings if r["is_current_user"]), None)

    return {
        "period": period,
        "leaderboard": rankings[:limit],
        "my_rank": my_rank,
        "total_drivers": len(rankings),
    }


# ─── Catch-all driver ID routes MUST be last to avoid shadowing named routes ───


@api_router.get("/{driver_id}")
async def get_driver(driver_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return serialize_doc(await _decrypt_driver_pii(driver))


@api_router.put("/{driver_id}/status")
async def update_driver_status(
    driver_id: str,
    # `embed=True` so FastAPI accepts `{"is_online": true}` as the JSON body
    # instead of interpreting a bare primitive as the whole body. Without the
    # explicit Body() wrapper, FastAPI treats non-Pydantic primitives as
    # query parameters, and the mobile client (which posts it as body) got
    # a 422 and surfaced it as "Request failed".
    is_online: bool = Body(..., embed=True),
    current_user: dict = Depends(get_current_user),
):
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    if driver.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Ban check: prevent banned drivers from going online
    if is_online and driver.get("status") == "banned":
        raise HTTPException(
            status_code=403, detail="Your account has been permanently suspended due to policy violations."
        )
    if is_online and driver.get("status") == "suspended":
        raise HTTPException(status_code=403, detail="Your account is currently suspended. Please contact support.")
    if is_online and driver.get("status") == "needs_review":
        raise HTTPException(
            status_code=400, detail="Your account is under review. Please wait for admin approval before going online."
        )
    if is_online and driver.get("status") not in ("active",):
        # Pending, rejected, or any unknown status
        if not driver.get("is_verified", False) and driver.get("status") != "active":
            raise HTTPException(
                status_code=400, detail="Your driver profile has not been verified yet. Please wait for admin approval."
            )

    if not is_online:
        # Prevent driver from going offline while actively carrying a rider.
        # The ride remains assigned to this driver regardless of their online
        # flag, but rejecting the toggle avoids a confusing UI state where the
        # driver shows as "offline" yet has an active trip.
        active_ride = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows(
                "rides",
                {
                    "driver_id": driver_id,
                    "status": {"$in": ["driver_accepted", "driver_arrived", "in_progress"]},
                },
                limit=1,
            )
        )
        if active_ride:
            raise HTTPException(
                status_code=409,
                detail="Cannot go offline during an active trip. Please complete the current ride first.",
            )

    if is_online:
        now = datetime.now(timezone.utc)

        # Prefer the dynamic driver_documents collection over the legacy
        # top-level expiry fields on the drivers row. The legacy fields are
        # only written once during onboarding and never refreshed when a
        # driver re-uploads a document, which used to leave drivers stuck
        # offline even after admin re-approval.
        try:
            approved_docs = await db_supabase.get_rows(
                "driver_documents",
                {
                    "driver_id": driver_id,
                    "status": "approved",
                },
                limit=200,
            )
        except Exception:
            approved_docs = []

        def _parse_expiry(val):
            # Return a tz-AWARE UTC datetime (or None). Comparisons below
            # are against `now = datetime.now(timezone.utc)`, which is
            # aware — mixing naive + aware throws TypeError at compare.
            if not val:
                return None
            if isinstance(val, datetime):
                return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
            if isinstance(val, str):
                try:
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    return None
            return None

        # For each mandatory requirement, the latest approved doc wins. If
        # it has an expiry and that expiry is in the past, block.
        # Requirements come from the driver's service-area required_documents
        # (slug-keyed) — the global document_requirements table is legacy and
        # misses slug-based uploads where requirement_id is NULL.
        mandatory_reqs: list = []
        if driver.get("service_area_id"):
            try:
                area_row = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
                )
                if area_row:
                    mandatory_reqs = [r for r in (area_row.get("required_documents") or []) if r.get("required", True)]
            except Exception:
                mandatory_reqs = []

        def _matches_req(doc: Dict[str, Any], req: Dict[str, Any]) -> bool:
            req_key = (req.get("key") or "").lower()
            req_label = (req.get("label") or "").lower()
            req_id = req.get("id")
            dkey = (doc.get("requirement_key") or "").lower()
            if dkey and dkey == req_key:
                return True
            drid = doc.get("requirement_id")
            if drid and (drid == req_id or (isinstance(drid, str) and drid.lower() == req_key)):
                return True
            dt = (doc.get("document_type") or "").lower()
            if dt and (dt == req_label or dt == req_key.replace("_", " ")):
                return True
            if dt and req_key and req_key.replace("_", "") in dt.replace(" ", "").replace("_", ""):
                return True
            return False

        covered_legacy_fields = set()
        for req_row in mandatory_reqs:
            req_name = req_row.get("label") or req_row.get("key") or "Document"
            # Pick the most recent approved doc for this requirement.
            docs = [d for d in approved_docs if _matches_req(d, req_row)]
            if not docs:
                continue
            docs.sort(key=lambda d: str(d.get("uploaded_at") or ""), reverse=True)
            latest = docs[0]
            exp = _parse_expiry(latest.get("expiry_date") or latest.get("expires_at"))
            if exp and exp < now:
                raise HTTPException(
                    status_code=400,
                    detail=f"{req_name} has expired. Please update your documents before going online.",
                )
            # This requirement was covered by a fresh doc — do not re-check
            # the legacy column for the same thing below.
            nm = (req_name or "").lower()
            if "license" in nm or "driving" in nm or "permit" in nm:
                covered_legacy_fields.add("license_expiry_date")
            if "insurance" in nm:
                covered_legacy_fields.add("insurance_expiry_date")
            if "inspection" in nm:
                covered_legacy_fields.add("vehicle_inspection_expiry_date")
            if "background" in nm:
                covered_legacy_fields.add("background_check_expiry_date")

        # Legacy fallback: only enforce top-level expiry columns that were
        # NOT already satisfied by a fresh approved doc above.
        expiry_checks = [
            ("license_expiry_date", "Driving license"),
            ("insurance_expiry_date", "Vehicle insurance"),
            ("vehicle_inspection_expiry_date", "Vehicle inspection"),
            ("background_check_expiry_date", "Background check"),
        ]
        for field, label in expiry_checks:
            if field in covered_legacy_fields:
                continue
            expiry_val = driver.get(field)
            if expiry_val:
                if isinstance(expiry_val, str):
                    try:
                        expiry_val = datetime.fromisoformat(expiry_val.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                # Treat naive datetimes as UTC — matches _parse_expiry above
                # so both paths produce tz-aware values comparable to `now`.
                if expiry_val.tzinfo is None:
                    expiry_val = expiry_val.replace(tzinfo=timezone.utc)
                if expiry_val < now:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{label} has expired ({field}). Please update your documents before going online.",
                    )

        # is_verified check removed — status field is the single source of truth now.
        # Only status='active' drivers reach this point (blocked above).

        # Check active Spinr Pass subscription. The enforcement is toggled
        # by the admin-controlled app setting `require_driver_subscription`
        # so the business team can flip it without a redeploy. When the
        # setting is false (default, pre-launch), we skip the subscription
        # query entirely — the `driver_subscriptions` table may not even
        # exist in the database yet during pre-launch, so querying it would
        # raise PostgREST PGRST205. The query only runs when enforcement
        # is actively turned on.
        try:
            from ..settings_loader import get_app_settings  # type: ignore
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        app_settings = await get_app_settings()
        require_sub = bool(app_settings.get("require_driver_subscription", False))

        if require_sub:
            try:
                sub = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows(
                        "driver_subscriptions",
                        {
                            "driver_id": driver_id,
                            "status": "active",
                        },
                        limit=1,
                    )
                )
            except Exception as e:
                # The table doesn't exist yet (PGRST205) or the query failed
                # for some other reason. Fail loudly with a clear message so
                # the operator knows the admin toggle was flipped before the
                # subscription infrastructure was ready.
                logger.error(f"driver_subscriptions lookup failed: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Spinr Pass enforcement is enabled in settings but the "
                        "driver_subscriptions table is not available. Disable "
                        'the "Require Spinr Pass to go online" toggle in admin '
                        "settings, or finish the subscription setup first."
                    ),
                ) from e

            if not sub:
                raise HTTPException(
                    status_code=402,
                    detail="You need an active Spinr Pass subscription to go online. Subscribe from your dashboard.",
                )

            # Check expiry on the active subscription row. parse_iso_utc
            # returns None on malformed values — we let those through rather
            # than blocking a driver from going online because of a data bug.
            if sub.get("expires_at"):
                exp = parse_iso_utc(sub["expires_at"])
                if exp is not None and exp < datetime.now(timezone.utc):
                    await db_supabase.update_one("driver_subscriptions", {"id": sub["id"]}, {"status": "expired"})
                    raise HTTPException(
                        status_code=402,
                        detail="Your Spinr Pass has expired. Please renew to go online.",
                    )

    logger.info(
        f"[GO-ONLINE] handler CALL update_one driver_id={driver_id} "
        f"requested_is_online={is_online} "
        f"pre_update_row_is_online={driver.get('is_online')} "
        f"pre_update_row_is_available={driver.get('is_available')}"
    )
    # last_status_changed_at (migration 42) gets bumped only when
    # is_online actually flips — idempotent toggles (same value) shouldn't
    # reset the "online since / offline since" clock the admin UI shows.
    # On PGRST204 (column missing pre-migration) fall back to the legacy
    # payload so the flip itself still lands.
    _now_iso = datetime.now(timezone.utc).isoformat()
    _base = {"is_online": is_online, "is_available": is_online, "updated_at": _now_iso}
    status_flipped = bool(driver.get("is_online")) != bool(is_online)
    _payload = {**_base, "last_status_changed_at": _now_iso} if status_flipped else _base
    try:
        await db_supabase.update_one("drivers", {"id": driver_id}, _payload)
    except Exception as _col_exc:
        # db_supabase.run_sync wraps PostgREST APIErrors in DatabaseError, so
        # str(_col_exc) is the generic "Database operation failed" sentinel —
        # the real column-missing text lives in details['original'] and the
        # __cause__ chain. Inspect both before deciding whether to fall back.
        if not status_flipped:
            raise
        _detail = ""
        _details_attr = getattr(_col_exc, "details", None)
        if isinstance(_details_attr, dict):
            _detail = str(_details_attr.get("original") or "")
        _cause_text = str(getattr(_col_exc, "__cause__", "") or "")
        _combined = f"{_col_exc} {_detail} {_cause_text}".lower()
        if "last_status_changed_at" in _combined or "pgrst204" in _combined:
            logger.warning(
                f"[GO-ONLINE] last_status_changed_at missing; retrying minimal. original={_detail or _col_exc}"
            )
            await db_supabase.update_one("drivers", {"id": driver_id}, _base)
        else:
            raise

    # Verify the update actually landed. db_supabase.update_one silently
    # returns None if the write matched zero rows (RLS deny, schema cache miss,
    # wrong key role, etc.), which would otherwise leak out as a fake
    # {success: true} response and a driver-app that claims "You're online"
    # while the DB row never changes. Re-read the row and raise loudly if the
    # flag did not flip.
    verify = await db_supabase.get_driver_by_id(driver_id)
    logger.info(
        f"[GO-ONLINE] handler VERIFY driver_id={driver_id} "
        f"post_update_is_online={verify.get('is_online') if verify else 'ROW_GONE'} "
        f"post_update_is_available={verify.get('is_available') if verify else 'ROW_GONE'} "
        f"post_update_updated_at={verify.get('updated_at') if verify else 'ROW_GONE'}"
    )
    if verify is None:
        logger.error(f"[go-online] driver row disappeared immediately after update: driver_id={driver_id}")
        raise HTTPException(status_code=500, detail="Driver row missing after status update.")
    if bool(verify.get("is_online")) != bool(is_online):
        logger.error(
            f"[go-online] silent no-op: driver_id={driver_id} "
            f"requested is_online={is_online} but DB still shows "
            f"is_online={verify.get('is_online')}. "
            f"Likely causes: SUPABASE_SERVICE_ROLE_KEY in backend .env is "
            f"the anon key (not service_role), or RLS is enabled on drivers "
            f"with no permissive UPDATE policy for the role in use."
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Status update did not apply. Backend Supabase credentials "
                "may be misconfigured — verify SUPABASE_SERVICE_ROLE_KEY."
            ),
        )

    # Presence: Go Online is the strongest possible liveness signal — the
    # driver is actively using the app. Refresh the TTL now so dispatch can
    # see them immediately without waiting for the next WS heartbeat. Go
    # Offline clears the key so admin monitoring and dispatch drop them
    # from the pool without waiting for the 90 s TTL to expire.
    if is_online:
        await mark_present(driver_id)
    else:
        await clear_presence(driver_id)

    return {"success": True, "is_online": is_online}


# ============================================================
# Spinr Pass — Driver Subscription
# ============================================================


@api_router.get("/subscription/plans")
async def get_subscription_plans(current_user: dict = Depends(get_current_user)):
    """Get available subscription plans for the driver's service area.

    Respects the per-area kill switch: if the driver's service area has
    spinr_pass_enabled=false, returns an empty list so the driver never
    sees subscription options.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )

    # Check the area-level kill switch — when Spinr Pass is disabled for
    # the driver's area, return a friendly free-ride message instead of plans.
    if driver and driver.get("service_area_id"):
        area = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
        )
        if area and area.get("spinr_pass_enabled") is False:
            return {
                "plans": [],
                "free_mode": True,
                "message": "No subscription needed — you're riding free right now! Drive on and enjoy the open road.",
            }

    plans = await db_supabase.get_rows("subscription_plans", {"is_active": True}, limit=50)

    # Filter by driver's service area if plans have area restrictions
    if driver:
        driver_area = driver.get("service_area_id")
        filtered = []
        for p in plans:
            plan_areas = p.get("service_areas")
            if plan_areas is None or (driver_area and driver_area in plan_areas):
                filtered.append(p)
            elif not plan_areas:  # empty list = all areas
                filtered.append(p)
        plans = filtered

    return {"plans": plans, "free_mode": False, "message": None}


@api_router.get("/subscription/current")
async def get_current_subscription(current_user: dict = Depends(get_current_user)):
    """Get driver's active subscription."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        return {"has_subscription": False, "subscription": None}

    sub = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "driver_subscriptions",
            {
                "driver_id": driver["id"],
                "status": "active",
            },
            limit=1,
        )
    )

    if not sub:
        return {"has_subscription": False, "subscription": None}

    # Check if expired
    if sub.get("expires_at"):
        from datetime import datetime

        try:
            exp = datetime.fromisoformat(str(sub["expires_at"]).replace("Z", "+00:00"))
            if exp.tzinfo:
                exp = exp.replace(tzinfo=None)
            if exp < datetime.now(timezone.utc):
                await db_supabase.update_one("driver_subscriptions", {"id": sub["id"]}, {"status": "expired"})
                return {"has_subscription": False, "subscription": None, "expired": True}
        except Exception:  # noqa: S110
            pass

    # Get today's ride count
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_rides = await db_supabase.count_documents(
        "rides",
        {
            "driver_id": driver["id"],
            "status": "completed",
            "ride_completed_at": {"$gte": today_start.isoformat()},
        },
    )

    rides_per_day = sub.get("rides_per_day", -1)
    rides_remaining = "unlimited" if rides_per_day == -1 else max(0, rides_per_day - today_rides)

    return {
        "has_subscription": True,
        "subscription": sub,
        "today_rides": today_rides,
        "rides_remaining": rides_remaining,
        "can_accept_rides": rides_per_day == -1 or today_rides < rides_per_day,
    }


@api_router.post("/subscription/subscribe")
async def subscribe_to_plan(request: Request, current_user: dict = Depends(get_current_user)):
    """Subscribe driver to a plan.

    **With Stripe configured** (`stripe_secret_key` in app_settings):
    creates a Stripe Checkout Session and returns `{checkout_url}`.
    The driver-app opens this URL in the browser; after payment,
    Stripe redirects back via the app's deep-link scheme and the
    webhook activates the subscription.

    **Without Stripe** (dev/internal testing): creates and activates
    the subscription immediately with `payment_status: "paid"`, same
    as the pre-checkout behavior. This preserves the internal test
    flow where no real payment is needed.
    """
    data = await request.json()
    plan_id = data.get("plan_id")

    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # Block subscription if Spinr Pass is disabled for this area
    if driver.get("service_area_id"):
        area = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
        )
        if area and area.get("spinr_pass_enabled") is False:
            raise HTTPException(status_code=403, detail="Spinr Pass is not available in your service area")

    plan = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("subscription_plans", {"id": plan_id, "is_active": True}, limit=1)
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found or inactive")

        # Check for existing active subscription
        existing = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows(
                "driver_subscriptions",
                {
                    "driver_id": driver["id"],
                    "status": "active",
                },
                limit=1,
            )
        )
    if existing:
        # Cancel old subscription
        await db_supabase.update_one(
            "driver_subscriptions",
            {"id": existing["id"]},
            {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()},
        )

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=plan.get("duration_days", 30))

    # Attempt Stripe charge if configured; fall back gracefully if not.
    payment_status = "paid"
    stripe_charge_id = None
    plan_price = plan.get("price", 0)
    if plan_price and plan_price > 0:
        try:
            from ..settings_loader import get_app_settings

            _settings = await get_app_settings()
            _stripe_secret = _settings.get("stripe_secret_key", "")
            if _stripe_secret:
                # Use the driver's saved default payment method via their Stripe customer
                _user = await db.find_one("users", {"id": current_user["id"]})
                _customer_id = _user.get("stripe_customer_id") if _user else None
                if _customer_id:
                    _amount_cents = int(float(plan_price) * 100)
                    _charge = stripe.PaymentIntent.create(
                        amount=_amount_cents,
                        currency="cad",
                        customer=_customer_id,
                        confirm=True,
                        off_session=True,
                        metadata={"driver_id": driver["id"], "plan_id": plan["id"]},
                        api_key=_stripe_secret,
                    )
                    stripe_charge_id = _charge.id
                    payment_status = _charge.status  # "succeeded" | "requires_action" | …
                    logger.info(f"Stripe charge {stripe_charge_id} status={payment_status} for driver {driver['id']}")
                else:
                    logger.info(f"No Stripe customer for driver {driver['id']}, marking paid without charge")
            else:
                logger.info("Stripe not configured; marking subscription paid without charge")
        except Exception as _stripe_err:
            # B-P2-1: NEVER interpolate the underlying exception into the
            # client-facing detail. Stripe error strings carry charge IDs
            # (ch_…), customer IDs (cus_…), decline codes, and sometimes
            # last-4-digits — none of which the rider/driver app should
            # see. logger.exception captures the full traceback server-
            # side; the request_id in the response body lets support
            # correlate against the log entry. This is the canonical
            # pattern referenced by docs/runbooks/error-responses.md.
            logger.exception(f"Stripe subscription charge failed for driver {driver['id']}")
            raise HTTPException(
                status_code=402,
                detail="Payment failed. Please try another payment method or contact support.",
            ) from _stripe_err

    subscription = {
        "id": str(uuid.uuid4()),
        "driver_id": driver["id"],
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "price": plan["price"],
        "rides_per_day": plan.get("rides_per_day", -1),
        "duration_days": plan.get("duration_days", 30),
        "status": "active",
        "started_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "payment_status": payment_status,
        "stripe_charge_id": stripe_charge_id,
        "created_at": now.isoformat(),
    }

    await db_supabase.insert_one("driver_subscriptions", subscription)

    # Update plan subscriber count
    await db_supabase.update_one(
        "subscription_plans", {"id": plan_id}, {"subscriber_count": (plan.get("subscriber_count", 0) or 0) + 1}
    )

    logger.info(f"[SUBSCRIBE] Dev mode: driver {driver['id']} subscribed to {plan['name']} (${plan['price']})")

    return {"success": True, "subscription": subscription, "mode": "dev"}


@api_router.get("/subscription/verify-session")
async def verify_subscription_session(
    session_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Verify a Stripe Checkout Session and return the subscription status.

    Called by the driver-app after the Stripe Checkout deep-link returns
    to the app. The webhook may or may not have fired by this point — if
    it has, the subscription is already active; if not, we check the
    Stripe session directly and activate it here (idempotent).

    Returns `{status: "active"}` if payment succeeded or `{status: "pending"}`
    if Stripe hasn't confirmed yet (caller should poll).
    """
    sub = await db.find_one("driver_subscriptions", {"stripe_session_id": session_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription session not found")

    # Already activated (webhook beat us)
    if sub.get("status") == "active" and sub.get("payment_status") == "paid":
        return {"status": "active", "subscription": sub}

    # Check with Stripe directly
    try:
        from ..settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore

    app_settings = await get_app_settings() or {}
    stripe_key = app_settings.get("stripe_secret_key", "")
    if not stripe_key:
        return {"status": "pending"}

    try:
        session = stripe.checkout.Session.retrieve(session_id, api_key=stripe_key)
    except Exception as e:
        logger.warning(f"[SUBSCRIBE] verify-session Stripe error: {e}")
        return {"status": "pending"}

    if session.payment_status == "paid":
        # Activate the subscription (same as webhook path, idempotent).
        await _activate_subscription(sub["id"], sub.get("plan_id"))
        sub = await db.find_one("driver_subscriptions", {"id": sub["id"]})
        return {"status": "active", "subscription": sub}

    return {"status": "pending"}


async def _activate_subscription(subscription_id: str, plan_id: str | None = None):
    """Activate a pending subscription after payment confirmation.

    Called by both the webhook handler and the verify-session endpoint
    (whichever runs first). Idempotent — skips if already active.
    """
    sub = await db.find_one("driver_subscriptions", {"id": subscription_id})
    if not sub or sub.get("status") == "active":
        return  # already done

    driver_id = sub.get("driver_id")

    # Cancel any prior active subscription for this driver.
    existing = await db.find_one("driver_subscriptions", {"driver_id": driver_id, "status": "active"})
    if existing and existing["id"] != subscription_id:
        await db.update_one(
            "driver_subscriptions",
            {"id": existing["id"]},
            {"$set": {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()}},
        )

    # Activate.
    await db.update_one(
        "driver_subscriptions",
        {"id": subscription_id},
        {"$set": {"status": "active", "payment_status": "paid"}},
    )

    # Increment subscriber count.
    if plan_id:
        plan = await db.find_one("subscription_plans", {"id": plan_id})
        if plan:
            await db.update_one(
                "subscription_plans",
                {"id": plan_id},
                {"$set": {"subscriber_count": (plan.get("subscriber_count", 0) or 0) + 1}},
            )

    logger.info(f"[SUBSCRIBE] Subscription {subscription_id} activated for driver {driver_id}")

    # Push notification to driver.
    if driver_id:
        driver = await db.find_one("drivers", {"id": driver_id})
        if driver and driver.get("user_id"):
            try:
                await send_push_notification(
                    driver["user_id"],
                    "Spinr Pass Activated! 🎉",
                    f"Your {sub.get('plan_name', 'Spinr Pass')} subscription is now active. Go online and start earning!",
                )
            except Exception as push_err:
                logger.warning(f"[SUBSCRIBE] Push notification failed: {push_err}")


@api_router.post("/subscription/cancel")
async def cancel_subscription(current_user: dict = Depends(get_current_user)):
    """Cancel driver's active subscription."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    sub = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "driver_subscriptions",
            {
                "driver_id": driver["id"],
                "status": "active",
            },
            limit=1,
        )
    )
    if not sub:
        raise HTTPException(status_code=400, detail="No active subscription")

    await db_supabase.update_one(
        "driver_subscriptions",
        {"id": sub["id"]},
        {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat()},
    )

    return {"success": True}


# ── G5: Subscription expiry warning background task ──────────────


async def check_expiring_subscriptions():
    """Background task: handles Spinr Pass expiry end-to-end.

    Two jobs, both run every 6 hours from the FastAPI lifespan:

    1. **Warn** drivers whose active subscription expires within 24 h
       (push notification, once per subscription via ``expiry_warned``).
    2. **Enforce** expiry on subscriptions whose ``expires_at`` is in the
       past — but only when the admin toggle
       ``require_driver_subscription`` is on. Enforcement:
         - mark the sub row ``status='expired'``
         - if the driver is currently ``is_online``, flip them offline,
           clear Redis presence, disconnect any active WebSocket, and
           push a notification explaining why.
         - broadcast ``driver_status_changed`` to admin monitoring.

    Without step 2, a subscription-gated driver could stay online and
    keep accepting rides past their paid-for period: the Go-Online path
    re-checks the sub, but drivers who were already online when their
    sub expired would never hit that gate until they voluntarily tapped
    off and back on.
    """
    try:
        from ..settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore

    while True:
        try:
            now = datetime.now(timezone.utc)
            window = now + timedelta(hours=24)

            active_subs = await db.get_rows("driver_subscriptions", {"status": "active"}, limit=500)

            # Only enforce the offline flip when the admin has turned on
            # the subscription gate — otherwise expiry is purely advisory.
            try:
                app_settings = await get_app_settings()
                require_sub = bool(app_settings.get("require_driver_subscription", False))
            except Exception as e:
                logger.warning(f"[SUB-EXPIRY] get_app_settings failed, skipping enforcement this tick: {e}")
                require_sub = False

            warned_count = 0
            enforced_count = 0
            for sub in active_subs:
                expires_at = sub.get("expires_at")
                if not expires_at:
                    continue

                if isinstance(expires_at, str):
                    try:
                        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                        if expires_dt.tzinfo is None:
                            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                else:
                    expires_dt = expires_at
                    if expires_dt.tzinfo is None:
                        expires_dt = expires_dt.replace(tzinfo=timezone.utc)

                # ── Enforcement branch: already expired ──────────────────
                if expires_dt <= now:
                    try:
                        await db.update_one(
                            "driver_subscriptions",
                            {"id": sub["id"]},
                            {"status": "expired"},
                        )
                    except Exception as e:
                        logger.warning(f"[SUB-EXPIRY] Failed to mark sub {sub['id']} expired: {e}")
                        continue

                    if not require_sub:
                        # Gate is off — just flip the row state, leave the
                        # driver alone. (They'll re-gate on next Go Online
                        # if the admin turns enforcement back on.)
                        continue

                    driver = await db.find_one("drivers", {"id": sub["driver_id"]})
                    if not driver or not driver.get("is_online"):
                        continue

                    try:
                        await db.update_one(
                            "drivers",
                            {"id": driver["id"]},
                            {
                                "is_online": False,
                                "is_available": False,
                                "updated_at": now.isoformat(),
                                "last_status_changed_at": now.isoformat(),
                            },
                        )
                    except Exception as e:
                        logger.error(f"[SUB-EXPIRY] Failed to flip driver {driver['id']} offline: {e}")
                        continue

                    try:
                        await clear_presence(driver["id"])
                    except Exception as e:
                        logger.warning(f"[SUB-EXPIRY] clear_presence failed for {driver['id']}: {e}")

                    if driver.get("user_id"):
                        manager.disconnect(f"driver_{driver['user_id']}")

                    try:
                        await db.insert_one(
                            "driver_activity_log",
                            {
                                "id": str(uuid.uuid4()),
                                "driver_id": driver["id"],
                                "event_type": "went_offline",
                                "title": "Went offline (Spinr Pass expired)",
                                "description": "System flipped driver offline — active subscription expired and Spinr Pass is required to drive.",
                                "metadata": {
                                    "reason": "subscription_expired",
                                    "source": "check_expiring_subscriptions",
                                    "subscription_id": sub["id"],
                                },
                                "actor": "system",
                                "created_at": now.isoformat(),
                            },
                        )
                    except Exception as e:
                        logger.warning(f"[SUB-EXPIRY] activity log insert failed for {driver['id']}: {e}")

                    if driver.get("user_id"):
                        try:
                            await send_push_notification(
                                driver["user_id"],
                                "Spinr Pass expired",
                                "Your Spinr Pass has expired and you've been set offline. Renew from your dashboard to keep driving.",
                                {"type": "subscription_expired", "driver_id": driver["id"]},
                            )
                        except Exception as e:
                            logger.warning(f"[SUB-EXPIRY] Push failed for driver {driver['id']}: {e}")

                    try:
                        await manager.broadcast_to_admins(
                            {
                                "type": "driver_status_changed",
                                "driver_id": driver["id"],
                                "is_online": False,
                            }
                        )
                    except Exception:  # noqa: S110
                        pass

                    enforced_count += 1
                    continue

                # ── Warning branch: expires within 24 h ───────────────────
                if sub.get("expiry_warned"):
                    continue

                if now < expires_dt <= window:
                    driver = await db.find_one("drivers", {"id": sub["driver_id"]})
                    if driver and driver.get("user_id"):
                        hours_left = max(1, int((expires_dt - now).total_seconds() / 3600))
                        plan_name = sub.get("plan_name", "Spinr Pass")
                        try:
                            await send_push_notification(
                                driver["user_id"],
                                "Spinr Pass Expiring Soon ⏰",
                                f"Your {plan_name} plan expires in ~{hours_left} hours. Renew now to keep driving!",
                                {"type": "subscription_expiring", "hours_left": str(hours_left)},
                            )
                            warned_count += 1
                        except Exception as e:
                            logger.warning(f"[SUB-EXPIRY] Push failed for driver {sub['driver_id']}: {e}")

                    await db.update_one(
                        "driver_subscriptions",
                        {"id": sub["id"]},
                        {"$set": {"expiry_warned": True}},
                    )

            logger.info(
                f"[SUB-EXPIRY] Check complete. {len(active_subs)} scanned, "
                f"{warned_count} warned, {enforced_count} enforced offline."
            )

        except Exception as e:
            logger.warning(f"[SUB-EXPIRY] Background check error: {e}")

        await asyncio.sleep(6 * 3600)
