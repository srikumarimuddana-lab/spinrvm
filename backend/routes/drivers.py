import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

import stripe
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

try:
    from .. import db_supabase
    from ..dependencies import get_admin_user, get_current_user
    from ..features import send_email, send_push_notification
    from ..geo_utils import calculate_distance, get_service_area_polygon
    from ..logging_utils import diag_logger
    from ..models.ride_status import RideStatus
    from ..schemas import Driver, RideRatingRequest
    from ..services.fare_service import recalculate_fare_for_distance
    from ..socket_manager import manager
    from ..utils.breadcrumb_buffer import flush_driver_breadcrumbs
    from ..utils.breadcrumbs import invalidate_active_rides_cache
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.driver_code import generate_driver_code
    from ..utils.driver_online import intent_online
    from ..utils.driver_presence import (
        clear_presence,
        mark_present,
        present_driver_ids_checked,
        reset_miss_streak,
    )
    from ..utils.earnings_snapshot import build_earnings_snapshot
    from ..utils.error_handling import (
        AccountDisabledException,
        ErrorCode,
        RideStateError,
        SpinrException,
        db_error_text,
        pg_error_code,
    )
    from ..utils.error_keys import ErrorKeys
    from ..utils.idempotency import idempotent_endpoint
    from ..utils.insurance_periods import record_period_transition
    from ..utils.live_activity import (
        EVENT_END,
        EVENT_START,
        EVENT_UPDATE,
        send_live_activity_update,
    )
    from ..utils.metrics import inc as _metric_inc
    from ..utils.metrics import observe as _metric_observe
    from ..utils.money import dollars_to_cents, to_decimal
    from ..utils.rate_limiter import dsar_export_limit
    from ..utils.referral_terms import paid_referral_earnings, resolve_referral_terms
    from ..utils.t4a_pdf import generate_t4a_pdf
except ImportError:
    import db_supabase
    from dependencies import get_admin_user, get_current_user
    from features import send_email, send_push_notification
    from geo_utils import calculate_distance, get_service_area_polygon
    from logging_utils import diag_logger
    from models.ride_status import RideStatus  # noqa: F401
    from schemas import Driver, RideRatingRequest
    from services.fare_service import recalculate_fare_for_distance
    from socket_manager import manager
    from utils.breadcrumb_buffer import flush_driver_breadcrumbs  # type: ignore
    from utils.breadcrumbs import invalidate_active_rides_cache  # type: ignore
    from utils.datetime_utils import parse_iso_utc
    from utils.driver_code import generate_driver_code  # type: ignore
    from utils.driver_online import intent_online  # type: ignore
    from utils.driver_presence import (
        clear_presence,
        mark_present,
        present_driver_ids_checked,
        reset_miss_streak,
    )
    from utils.earnings_snapshot import build_earnings_snapshot
    from utils.error_handling import (
        AccountDisabledException,
        ErrorCode,
        RideStateError,
        SpinrException,
        db_error_text,
        pg_error_code,
    )
    from utils.error_keys import ErrorKeys
    from utils.idempotency import idempotent_endpoint
    from utils.insurance_periods import record_period_transition  # type: ignore[assignment]
    from utils.live_activity import (  # type: ignore
        EVENT_END,
        EVENT_START,
        EVENT_UPDATE,
        send_live_activity_update,
    )
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore
    from utils.money import dollars_to_cents, to_decimal
    from utils.rate_limiter import dsar_export_limit
    from utils.referral_terms import paid_referral_earnings, resolve_referral_terms  # type: ignore
    from utils.t4a_pdf import generate_t4a_pdf  # noqa: F401 – used in download_t4a_pdf

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")


def _d(v) -> Decimal:
    """Parse a money value to an exact 2-dp Decimal. Money is Decimal-only —
    never accumulate fares/bonuses/payouts as float."""
    from decimal import InvalidOperation

    try:
        return Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, InvalidOperation):
        return Decimal("0")


def _money_str(v) -> str:
    """Serialise a money value as an exact 2-dp Decimal string (never float)."""
    from decimal import InvalidOperation

    try:
        return str(Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    except (TypeError, ValueError, InvalidOperation):
        return "0.00"


def _ride_income(r: dict) -> Decimal:
    """A completed ride's driver income as a Decimal — the canonical
    driver_earnings, falling back to the fare components only for legacy rows
    that predate that column. Shared by the earnings + T4A summaries."""
    if r.get("driver_earnings") is not None:
        return _d(r.get("driver_earnings"))
    return _d(r.get("base_fare")) + _d(r.get("distance_fare")) + _d(r.get("time_fare")) + _d(r.get("tip_amount"))


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
            "vault_encrypt: supabase_client unavailable for %s — refusing to store plaintext",
            hint,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable") from exc
    if not _sb:
        logger.error(
            "vault_encrypt: Supabase client not initialised for %s — refusing to store plaintext",
            hint,
        )
        raise HTTPException(status_code=503, detail="Encryption service unavailable")
    try:
        res = await db_supabase.run_sync(lambda: _sb.rpc("encrypt_driver_pii", {"plaintext": value}).execute())
        if not res.data:
            logger.error(
                "vault_encrypt: RPC returned no data for %s — refusing to store plaintext",
                hint,
            )
            raise HTTPException(status_code=503, detail="Encryption service unavailable")
        return str(res.data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "vault_encrypt: RPC failed for %s — refusing to store plaintext",
            hint,
            exc_info=True,
        )
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
        logger.error(
            "vault_decrypt: RPC failed for %s — returning raw token",
            hint,
            exc_info=True,
        )
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
ARRIVE_FROM_STATES = (
    RideStatus.DRIVER_ASSIGNED,
    RideStatus.DRIVER_ACCEPTED,
    RideStatus.DRIVER_ARRIVED,
)
# verify-otp and start both move driver_arrived → in_progress.
# in_progress is idempotent for both (retry after network blip).
START_FROM_STATES = (RideStatus.DRIVER_ARRIVED, RideStatus.IN_PROGRESS)
COMPLETE_FROM_STATES = (RideStatus.IN_PROGRESS,)


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

    Uses Google Static Maps API for high-quality map tiles with the route
    polyline drawn server-side. Falls back to OSM/staticmap if the Google
    API key is unavailable.

    Requires a public ``ride-snapshots`` bucket in Supabase Storage.
    See backend/docs/STORAGE_BUCKETS.md for one-time setup.
    """
    if pickup_lat is None or pickup_lng is None or dropoff_lat is None or dropoff_lng is None:
        logger.warning(f"Snapshot skipped for ride {ride_id}: missing coordinates")
        return
    poly_len = len(route_polyline) if isinstance(route_polyline, list) else 0
    phase_keys = list((phase_polylines or {}).keys()) if phase_polylines else []
    logger.info(
        f"Snapshot pipeline start for ride {ride_id}: route_polyline={poly_len} pts, phase_polylines={phase_keys}"
    )
    try:
        try:
            from ..core.config import settings
            from ..settings_loader import get_app_settings
            from ..supabase_client import supabase  # type: ignore
            from ..utils.route_snapshot import render_ride_snapshot, render_ride_snapshot_google
        except ImportError:
            from core.config import settings  # type: ignore
            from settings_loader import get_app_settings  # type: ignore
            from supabase_client import supabase  # type: ignore
            from utils.route_snapshot import render_ride_snapshot, render_ride_snapshot_google  # type: ignore

        png_bytes = None
        loop = asyncio.get_event_loop()

        # Try Google Static Maps first (proper Google Maps tiles + polyline)
        try:
            app_settings = await get_app_settings() or {}
            gmap_key = app_settings.get("google_maps_api_key") or ""
            if gmap_key:
                png_bytes = await render_ride_snapshot_google(
                    api_key=gmap_key,
                    pickup_lat=float(pickup_lat),
                    pickup_lng=float(pickup_lng),
                    dropoff_lat=float(dropoff_lat),
                    dropoff_lng=float(dropoff_lng),
                    phase_polylines=phase_polylines,
                    route_polyline=route_polyline,
                )
                logger.info(
                    f"Google Static Maps for ride {ride_id}: "
                    f"{'success' if png_bytes else 'returned None'} "
                    f"({len(png_bytes) if png_bytes else 0} bytes)"
                )
            else:
                logger.warning(f"No Google Maps API key found for ride {ride_id} snapshot")
        except Exception as google_exc:
            logger.warning(f"Google Static Maps failed for ride {ride_id}, trying OSM fallback: {google_exc}")

        # Fallback to OSM/staticmap
        if not png_bytes:
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
                        # Cache effectively forever (1 year, the HTTP max
                        # per RFC 9111). Snapshots are immutable per ride;
                        # a regenerated snapshot may never be re-fetched by
                        # clients that already cached it, which is accepted.
                        "cache-control": "31536000",
                    },
                ),
            )
        except Exception as upload_exc:
            logger.error(
                f"Supabase Storage upload failed for ride {ride_id}: {upload_exc}",
                exc_info=True,
            )
            return

        base = (settings.SUPABASE_URL or "").rstrip("/")
        if not base:
            logger.error("SUPABASE_URL not configured; cannot build public snapshot URL")
            return
        # Cache-bust: the object is upserted to a STABLE filename with a 1-year
        # immutable cache-control, so a regenerated snapshot (e.g. the accurate
        # completion render replacing an earlier planned/buggy one) would never
        # be re-fetched by clients/CDNs that cached the old bytes — the stale
        # image (including a pre-fix duplicate-line render) would persist. A
        # content-hash query param changes only when the bytes change, forcing
        # a fresh fetch on regeneration while staying stable across identical
        # re-renders. Supabase Storage ignores the query string for object
        # resolution, so this resolves to the same upserted file.
        digest = hashlib.sha256(png_bytes).hexdigest()[:12]
        url = f"{base}/storage/v1/object/public/{bucket}/{storage_path}?v={digest}"

        # Persist the URL. Wrap in try/except so if migration 41 hasn't
        # landed yet the write fails gracefully instead of raising.
        try:
            await db_supabase.update_one("rides", {"id": ride_id}, {"route_snapshot_url": url})
        except Exception as exc:
            logger.error(
                f"route_snapshot_url write failed for ride {ride_id} (migration 41 missing?): {exc}",
                exc_info=True,
            )
    except Exception as exc:
        logger.error(f"Ride snapshot pipeline failed for {ride_id}: {exc}", exc_info=True)


async def _snap_pickup_leg_async(ride_id: str, breadcrumbs: list) -> None:
    """Background task: road-snap the Phase 2 (driver→pickup) leg for the admin map.

    Display-only — kept OFF the /complete hot path so a slow OSRM/Google Roads
    provider can never delay a driver finishing a ride. Best-effort: backfills
    ride_routes.road_polyline_pickup (idempotent single-column update) once the
    snap returns; a failure simply leaves it empty and the admin map falls back
    to the raw Phase 2 GPS breadcrumbs.
    """
    if not breadcrumbs:
        return
    try:
        try:
            from ..utils.route_distance import compute_road_route
        except ImportError:
            from utils.route_distance import compute_road_route  # type: ignore
        result = await compute_road_route(breadcrumbs, phase="navigating_to_pickup")
        poly = (result or {}).get("polyline") or []
        if poly:
            # update-only (no upsert): if the main settlement geometry write
            # failed and no ride_routes row exists, we must NOT insert a partial
            # row — that would default save_status and mask the failed settlement
            # in the admin detail merge. No row => this backfill is a no-op.
            await db_supabase.update_one(
                "ride_routes", {"ride_id": ride_id}, {"road_polyline_pickup": poly}, upsert=False
            )
    except Exception:
        logger.warning("[complete_ride] pickup-leg road-snap backfill failed for ride %s", ride_id, exc_info=True)


async def _validate_ride_route(ride_id: str, breadcrumbs: list, driver_id: str) -> None:
    """Background task: validate GPS trace against road network post-completion."""
    if not breadcrumbs or len(breadcrumbs) < 5:
        return
    try:
        try:
            from ..utils.route_validation import validate_trip_route
        except ImportError:
            from utils.route_validation import validate_trip_route  # type: ignore

        result = await validate_trip_route(breadcrumbs)
        if not result:
            return

        # Store validation result on ride
        try:
            await db_supabase.update_one("rides", {"id": ride_id}, {"route_validation": result})
        except Exception as db_exc:
            logger.error(f"[route_validation] failed to store results on ride {ride_id}: {db_exc}", exc_info=True)

        if result["verdict"] in ("suspicious", "likely_spoofed"):
            logger.warning(
                "[route_validation] ride=%s driver=%s verdict=%s deviation=%.1f%%",
                ride_id,
                driver_id,
                result["verdict"],
                result["deviation_pct"],
            )
    except Exception as exc:
        logger.error(f"[route_validation] failed for ride {ride_id}: {exc}", exc_info=True)


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

    # Admin-uploaded mp3/wav URL the driver-app plays as the ride-offer
    # ping. Null/empty → driver-app falls back to the bundled placeholder.
    ride_offer_sound_url = app_settings.get("ride_offer_sound_url") or None
    return {
        "ride_offer_timeout_seconds": _clamp(app_settings.get("ride_offer_timeout_seconds"), 5, 60, 15),
        "pickup_radius_meters": _clamp(app_settings.get("pickup_radius_meters"), 10, 1000, 100),
        "ride_offer_sound_url": ride_offer_sound_url,
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
    await db_supabase.update_one("drivers", {"id": driver["id"]}, await _encrypt_driver_pii(updates))
    # Append-only vehicle/identity change history (SGI/insurance audit). Uses
    # the pre-update `driver` row as the "before" snapshot.
    if changed_vehicle:
        try:
            from ..utils.vehicle_history import record_vehicle_changes
        except ImportError:
            from utils.vehicle_history import record_vehicle_changes  # type: ignore
        await record_vehicle_changes(
            driver["id"], driver, updates, changed_by_user_id=current_user["id"], role="driver"
        )
    # M-5: SGI insurance period audit — vehicle/document edits flip an
    # active driver to needs_review and force them offline. If they were
    # actually online before this update, that's a 1→0 transition.
    if changed_vehicle and driver.get("status") == "active" and driver.get("is_online"):
        await record_period_transition(driver["id"], 0)
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
        await db_supabase.update_one("drivers", {"id": existing["id"]}, await _encrypt_driver_pii(payload))
        driver = await db_supabase.get_driver_by_id(existing["id"])
        return serialize_doc(await _decrypt_driver_pii(driver))

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
    await db_supabase.insert_one("drivers", await _encrypt_driver_pii(new_driver))

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
            "destination_mode": False,
            "destination_address": None,
            "destination_lat": None,
            "destination_lng": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
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
                "status": RideStatus.COMPLETED,
            },
            limit=10000,
        )
        # Decimal-only money: float accumulation over many rows drifts cents,
        # and this feeds payable_balance which bounds the Stripe payout Transfer.
        total_earnings = sum(
            (
                _d(r.get("base_fare") or 0)
                + _d(r.get("distance_fare") or 0)
                + _d(r.get("time_fare") or 0)
                + _d(r.get("tip_amount") or 0)
                for r in rides
            ),
            Decimal("0"),
        )
        total_tips = sum((_d(r.get("tip_amount") or 0) for r in rides), Decimal("0"))
        total_rides = len(rides)

        # Deduct EVERY payout that represents money sent or in-flight — only
        # explicitly reversed/failed payouts (money returned or never left) are
        # excluded. The filter defaults to deducting, so an unknown/new status
        # still counts as money-out: worst case a driver is temporarily
        # under-paid (recoverable), NEVER a double-withdraw of platform money.
        # (Before, only status='pending' was deducted — a 'completed' /
        # 'transfer_completed' payout silently stopped reducing the balance, so
        # the driver could re-withdraw the same earnings.)
        payout_rows = await db_supabase.get_rows("payouts", {"driver_id": driver["id"]}, limit=5000)
        _not_money_out = {"reversed", "failed"}
        total_payouts = sum(
            (_d(p.get("amount") or 0) for p in payout_rows if str(p.get("status") or "").lower() not in _not_money_out),
            Decimal("0"),
        )
        # 'pending' = recorded but not yet transferred (shown as "Pending");
        # the rest of total_payouts is money already sent ("Paid Out").
        pending_payouts = sum(
            (_d(p.get("amount") or 0) for p in payout_rows if str(p.get("status") or "").lower() == "pending"),
            Decimal("0"),
        )
    except Exception as e:
        # A transient DB error here must NOT be masked as a $0 balance — a
        # driver seeing their earnings drop to zero looks like money vanished
        # and triggers false support/payout escalations. Surface 503 so the
        # client retries (per CLAUDE.md: never log-and-continue on a DB read).
        logger.error(f"Error fetching balance: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Balance temporarily unavailable") from e

    # Quest + driver-referral bonuses are payable earnings (driver_bonuses
    # ledger) — they fold into payable_balance and pay out via the normal Stripe
    # Transfer, like ride earnings. Fetched in a SEPARATE try so a driver_bonuses
    # error (e.g. migration not yet applied) never zeroes the driver's ride
    # earnings/balance.
    total_bonuses = Decimal("0")
    total_referral_bonuses = Decimal("0")
    try:
        bonus_rows = await db_supabase.get_rows("driver_bonuses", {"driver_id": driver["id"]}, limit=10000)
        total_bonuses = sum((_d(b.get("amount") or 0) for b in bonus_rows), Decimal("0"))
        # Referral-only slice so the activity/payout view shows it distinctly from
        # quest bonuses (both live in driver_bonuses; `kind` tells them apart).
        total_referral_bonuses = sum(
            (_d(b.get("amount") or 0) for b in bonus_rows if b.get("kind") == "referral"),
            Decimal("0"),
        )
    except Exception as e:
        logger.error(f"Error fetching driver bonuses for balance: {e}", exc_info=True)

    return {
        "total_earnings": _money_str(total_earnings + total_bonuses),
        # payable_balance = ride earnings + bonuses - ALL money-out payouts
        "payable_balance": _money_str(total_earnings + total_bonuses - total_payouts),
        "pending_payouts": _money_str(pending_payouts),
        "total_paid_out": _money_str(total_payouts - pending_payouts),
        "total_bonuses": _money_str(total_bonuses),
        "total_referral_bonuses": _money_str(total_referral_bonuses),
        "has_bank_account": bool(driver.get("bank_account")),
        "stripe_account_onboarded": bool(driver.get("stripe_account_onboarded", False)),
        "stripe_id_number_provided": bool(driver.get("stripe_id_number_provided", False)),
        "total_tips": _money_str(total_tips),
        "total_rides": total_rides,
    }


@api_router.get("/bonuses")
async def get_driver_bonuses(
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List the driver's bonus credits (quest + referral) — the payable-earnings
    line items behind the bonus portion of payable_balance, for the earnings /
    payout history view."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    try:
        rows = await db_supabase.get_rows(
            "driver_bonuses", {"driver_id": driver["id"]}, limit=limit, order="created_at", desc=True
        )
    except Exception as e:
        logger.error(f"Error fetching driver bonuses: {e}", exc_info=True)
        rows = []
    return {
        "bonuses": [
            {
                "id": b.get("id"),
                "amount": _money_str(b.get("amount") or 0),
                "kind": b.get("kind"),
                "description": b.get("description"),
                "created_at": b.get("created_at"),
            }
            for b in rows
        ],
        "total": _money_str(sum((_d(b.get("amount") or 0) for b in rows), Decimal("0"))),
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

    # Calculate date range using the driver's service area timezone so "today"
    # reflects the driver's local calendar day regardless of which province they
    # operate in.  Falls back to America/Regina if no service area is set.
    _tz_name = "America/Regina"
    if driver.get("service_area_id"):
        _sa_rows = await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
        if _sa_rows and _sa_rows[0].get("timezone"):
            _tz_name = _sa_rows[0]["timezone"]
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
        # Fallback: treat unknown period as 'week'
        start_date = now - timedelta(days=7)

    try:
        filters: Dict[str, Any] = {
            "driver_id": driver["id"],
            "status": RideStatus.COMPLETED,
        }
        if use_date_filter and start_date:
            filters["ride_completed_at"] = {"$gte": start_date.isoformat()}

        rides = await db_supabase.get_rows("rides", filters, limit=10000)

        # Fetch incentive claims for these rides
        _ride_ids = [r["id"] for r in rides if r.get("id")]
        _incentive_total = Decimal("0")
        if _ride_ids:
            try:
                _claims = (
                    db_supabase.supabase.table("ride_incentive_claims")
                    .select("bonus_amount")
                    .in_("ride_id", _ride_ids)
                    .execute()
                ).data or []
                _incentive_total = sum(Decimal(str(c.get("bonus_amount") or 0)) for c in _claims)
            except Exception:
                logger.debug("earnings: ride_incentive_claims lookup failed", exc_info=True)

        # Fetch cancellation/no-show fees earned by this driver
        _cancel_filters: Dict[str, Any] = {
            "driver_id": driver["id"],
            "status": RideStatus.CANCELLED,
        }
        if use_date_filter and start_date:
            _cancel_filters["cancelled_at"] = {"$gte": start_date.isoformat()}
        _cancelled_rides = await db_supabase.get_rows("rides", _cancel_filters, limit=10000)
        _cancel_fees_total = sum(Decimal(str(r.get("cancellation_fee_driver") or 0)) for r in _cancelled_rides)

        # Tax collected from riders — passed through to driver as their income
        _total_tax = Decimal("0")
        for r in rides:
            _t = Decimal(str(r.get("tax_amount") or 0))
            if _t == 0:
                _snap = r.get("fare_breakdown_snapshot") or {}
                for _ln in _snap.get("lines") or []:
                    if _ln.get("type") in ("tax", "gst", "pst"):
                        _t += Decimal(str(_ln.get("amount") or 0))
            _total_tax += _t

        stats = {
            # Driver INCOME = driver_earnings (canonical), fare-component fallback
            # for legacy rows. Matches the T4A summary and the trips view.
            "total_earnings": sum((_ride_income(r) for r in rides), Decimal("0")),
            "total_tips": sum(r.get("tip_amount", 0) or 0 for r in rides),
            "total_incentives": float(_incentive_total),
            "total_cancel_fees": float(_cancel_fees_total),
            "total_tax": float(_total_tax),
            "total_rides": len(rides),
            "total_distance_km": sum(r.get("distance_km", 0) or 0 for r in rides),
            "total_duration_minutes": sum(r.get("duration_minutes", 0) or 0 for r in rides),
        }
    except Exception as e:
        # Don't mask a DB failure as an all-zero earnings summary — surface 503
        # so the dashboard retries instead of telling the driver they earned
        # nothing this period.
        logger.error(f"Error fetching earnings: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Earnings temporarily unavailable") from e

    # Quest + referral bonuses earned in this period (driver_bonuses ledger).
    # Isolated so a bonus-fetch error never zeroes ride earnings. Distinct from
    # total_incentives (per-ride pickup/surge bonuses) — don't conflate them.
    _total_bonuses = Decimal("0")
    try:
        _bonus_filters: Dict[str, Any] = {"driver_id": driver["id"]}
        if use_date_filter and start_date:
            _bonus_filters["created_at"] = {"$gte": start_date.isoformat()}
        _bonus_rows = await db_supabase.get_rows("driver_bonuses", _bonus_filters, limit=10000)
        _total_bonuses = sum((_d(b.get("amount") or 0) for b in _bonus_rows), Decimal("0"))
    except Exception:
        logger.error("earnings: driver_bonuses lookup failed", exc_info=True)

    _total_with_extras = (
        Decimal(str(stats.get("total_earnings", 0)))
        + Decimal(str(stats.get("total_incentives", 0)))
        + Decimal(str(stats.get("total_cancel_fees", 0)))
        + Decimal(str(stats.get("total_tax", 0)))
        + _total_bonuses
    )
    return {
        "period": period,
        "total_earnings": _money_str(_total_with_extras),
        "total_tips": _money_str(stats.get("total_tips", 0)),
        "total_incentives": _money_str(stats.get("total_incentives", 0)),
        "total_bonuses": _money_str(_total_bonuses),
        "total_cancel_fees": _money_str(stats.get("total_cancel_fees", 0)),
        "total_tax": _money_str(stats.get("total_tax", 0)),
        "total_rides": stats.get("total_rides", 0),
        "total_distance_km": stats.get("total_distance_km", 0),
        "total_duration_minutes": stats.get("total_duration_minutes", 0),
        "average_per_ride": (
            _money_str(_total_with_extras / stats.get("total_rides", 1)) if stats.get("total_rides", 0) > 0 else "0.00"
        ),
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
                "status": RideStatus.COMPLETED,
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
                daily_data[date_str] = {
                    "earnings": 0,
                    "tips": 0,
                    "rides": 0,
                    "distance_km": 0,
                }
            daily_data[date_str]["earnings"] += (
                float(r.get("base_fare") or 0)
                + float(r.get("distance_fare") or 0)
                + float(r.get("time_fare") or 0)
                + float(r.get("tip_amount") or 0)
            )
            daily_data[date_str]["tips"] += r.get("tip_amount", 0) or 0
            daily_data[date_str]["rides"] += 1
            daily_data[date_str]["distance_km"] += r.get("distance_km", 0) or 0

        results = [{"date": date, **data} for date, data in sorted(daily_data.items())]
    except Exception as e:
        # An empty chart reads as "no rides this period" — surface the DB error
        # as 503 instead of fabricating an empty result.
        logger.error(f"Error fetching daily earnings: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Daily earnings temporarily unavailable") from e

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

    filters: Dict[str, Any] = {
        "driver_id": driver["id"],
        "status": RideStatus.COMPLETED,
    }
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
                "completed_at": (r.get("ride_completed_at") if r.get("ride_completed_at") else None),
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
            {
                "driver_id": driver["id"],
                "stat_date": {"$gte": start_date.strftime("%Y-%m-%d")},
            },
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
                "status": RideStatus.COMPLETED,
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
            weekly_data[week_key]["earnings"] += (
                float(r.get("base_fare") or 0)
                + float(r.get("distance_fare") or 0)
                + float(r.get("time_fare") or 0)
                + float(r.get("tip_amount") or 0)
            )
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
            {
                "driver_id": driver["id"],
                "stat_date": {"$gte": start_date.strftime("%Y-%m-%d")},
            },
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
                "status": RideStatus.COMPLETED,
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
            monthly_data[month_key]["earnings"] += (
                float(r.get("base_fare") or 0)
                + float(r.get("distance_fare") or 0)
                + float(r.get("time_fare") or 0)
                + float(r.get("tip_amount") or 0)
            )
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
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": current_start.isoformat()},
            },
            limit=5000,
        )
        # Previous period
        all_rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
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
            "earnings": sum(
                float(r.get("base_fare") or 0)
                + float(r.get("distance_fare") or 0)
                + float(r.get("time_fare") or 0)
                + float(r.get("tip_amount") or 0)
                for r in rides
            ),
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


@api_router.get("/earnings/forecast")
async def get_driver_earnings_forecast(current_user: dict = Depends(get_current_user)):
    """Weekly earnings projection for the driver home screen widget.

    Algorithm:
      1. Compute average daily earnings over the last 28 days of completed rides.
      2. Multiply by 7 to get the weekly baseline.
      3. Compute remaining days in the current week (Mon–Sun) and add
         the *this-week* earnings already locked in.

    The result is intentionally simple — it's a motivational nudge, not
    a financial guarantee.  Decimal precision is kept to 2 dp throughout.
    """
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    _zero = {
        "this_week_earnings": "0.00",
        "projected_weekly_total": "0.00",
        "daily_avg_last_28d": "0.00",
        "days_remaining_this_week": 6 - datetime.now(timezone.utc).weekday(),
        "this_week_trips": 0,
    }

    now = datetime.now(timezone.utc)
    # Rolling 28-day window for the daily average
    window_start = (now - timedelta(days=28)).isoformat()
    # Start of the current ISO week (Monday)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        recent_rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": window_start},
            },
            limit=5000,
        )
    except Exception as e:
        logger.error(
            f"[FORECAST] earnings fetch failed driver={driver['id']}: {e}",
            exc_info=True,
        )
        return _zero

    try:
        this_week_rides = [r for r in recent_rides if (r.get("ride_completed_at") or "") >= week_start.isoformat()]
        prev_28_rides = [r for r in recent_rides if (r.get("ride_completed_at") or "") < week_start.isoformat()]

        this_week_earnings = sum(
            Decimal(str(r.get("base_fare") or 0))
            + Decimal(str(r.get("distance_fare") or 0))
            + Decimal(str(r.get("time_fare") or 0))
            + Decimal(str(r.get("tip_amount") or 0))
            for r in this_week_rides
        )
        prev_28_earnings = sum(
            Decimal(str(r.get("base_fare") or 0))
            + Decimal(str(r.get("distance_fare") or 0))
            + Decimal(str(r.get("time_fare") or 0))
            + Decimal(str(r.get("tip_amount") or 0))
            for r in prev_28_rides
        )

        # Daily average over the 28-day window excluding the current week
        days_in_window = 28 - now.weekday()  # days before current week in window
        daily_avg = (prev_28_earnings / days_in_window) if days_in_window > 0 else Decimal("0")

        # Days remaining in current week (today = partially elapsed)
        days_remaining = 6 - now.weekday()  # Mon=0 … Sun=6
        projected_additional = daily_avg * days_remaining
        projected_total = (this_week_earnings + projected_additional).quantize(Decimal("0.01"))

        return {
            "this_week_earnings": _money_str(this_week_earnings),
            "projected_weekly_total": _money_str(projected_total),
            "daily_avg_last_28d": _money_str(daily_avg),
            "days_remaining_this_week": days_remaining,
            "this_week_trips": len(this_week_rides),
        }
    except Exception as e:
        logger.error(f"[FORECAST] computation failed driver={driver['id']}: {e}", exc_info=True)
        return _zero


@api_router.get("/nearby")
async def get_nearby_drivers_public(
    lat: float = Query(...),
    lng: float = Query(...),
    radius: float = Query(None),
    vehicle_type: str = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get nearby active drivers for riders. Filters by service area + vehicle type."""
    # Use admin-configured search_radius_km if caller didn't override
    if radius is None:
        try:
            from ..settings_loader import get_app_settings  # type: ignore
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        app_settings = await get_app_settings() or {}
        radius = float(app_settings.get("search_radius_km", 10.0))

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
    # the offer.
    #
    # Three cases, distinguished via present_driver_ids_checked():
    #   * reachable + non-empty → filter normally (ghost drivers removed).
    #   * reachable + empty     → every candidate is genuinely offline; hide
    #     them all (an empty map is correct here, not a bug).
    #   * NOT reachable (Redis configured but down) → presence is unknowable,
    #     so fall back to DB state rather than blanking the map during a
    #     failover. Dispatch still presence-filters, so a ghost booking that
    #     slips onto the map cannot actually complete an offer.
    try:
        driver_ids = [d["id"] for d in drivers if d.get("id")]
        if driver_ids:
            present, reachable = await present_driver_ids_checked(driver_ids)
            if reachable:
                drivers = [d for d in drivers if d["id"] in present]
            else:
                logger.warning(
                    "/drivers/nearby: presence store unreachable, showing DB "
                    "state (dispatch still presence-filters before any offer)"
                )
    except Exception as exc:
        logger.warning(f"/drivers/nearby presence filter failed, using DB state: {exc}")

    # Resolve vehicle type names + admin-configured marker variant once so
    # the rider app can pick the matching map marker (standard / XL /
    # premium) without an extra round trip.
    vt_name_by_id: dict = {}
    vt_marker_by_id: dict = {}
    if drivers:
        vehicle_types = await db_supabase.get_rows("vehicle_types", {}, limit=100)
        vt_name_by_id = {vt["id"]: vt.get("name") for vt in vehicle_types if vt.get("id")}
        vt_marker_by_id = {vt["id"]: vt.get("marker_variant") for vt in vehicle_types if vt.get("id")}

    # Manual filtering by distance
    nearby = []
    for d in drivers:
        # Exclude orphan/demo driver rows (no user_id → cannot be dispatched).
        if not d.get("user_id"):
            continue
        # Authoritative intent check using the went_online_at /
        # went_offline_at timestamps (migration 97). The DB pre-filter
        # already used `is_online=True`, but intent_online() also catches
        # the case where the column is stale and prefers the timestamp
        # when both are present. Falls back to is_online for unmigrated rows.
        if not intent_online(d):
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        # `is not None` (not truthy) so a driver legitimately at lat=0 or
        # lng=0 still matches. The literal (0, 0) is the registration
        # default and means "no GPS yet" — skip those so a freshly-online
        # driver doesn't surface as a ghost car at the origin.
        if d_lat is not None and d_lng is not None and (d_lat != 0 or d_lng != 0):
            dist = calculate_distance(lat, lng, d_lat, d_lng)
            if dist <= radius:
                # hide personal info for riders
                safe_driver = {
                    "id": d["id"],
                    "lat": d_lat,
                    "lng": d_lng,
                    "heading": d.get("heading"),
                    "vehicle_type_id": d.get("vehicle_type_id"),
                    "vehicle_type_name": vt_name_by_id.get(d.get("vehicle_type_id")),
                    "marker_variant": vt_marker_by_id.get(d.get("vehicle_type_id")),
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
    if lat is not None and lng is not None:
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

    row = driver.dict()
    row.setdefault("driver_code", generate_driver_code())
    await db_supabase.insert_one("drivers", row)
    return row


@api_router.post("/location-batch")
async def update_location_batch(batch: Union[List[dict], dict], current_user: dict = Depends(get_current_user)):
    """Update driver location in batch (from background tracking)."""
    try:
        from ..utils.location_integrity import check_location_integrity
    except ImportError:
        from utils.location_integrity import check_location_integrity  # type: ignore

    points = []
    if isinstance(batch, list):
        points = batch
    elif isinstance(batch, dict):
        points = batch.get("locations") or batch.get("points") or []

    # Simply take the last point and update current location
    if not points:
        return {"success": True}

    latest = points[-1]
    lat = latest.get("latitude") if latest.get("latitude") is not None else latest.get("lat")
    lng = latest.get("longitude") if latest.get("longitude") is not None else latest.get("lng")
    heading = latest.get("heading")

    if lat is not None and lng is not None:
        # GPS spoofing check
        driver_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        driver_id = driver_rows[0]["id"] if driver_rows else current_user["id"]
        trusted, _reason = await check_location_integrity(
            driver_id,
            lat,
            lng,
            speed=latest.get("speed"),
            accuracy=latest.get("accuracy"),
            mocked=latest.get("mocked"),
        )
        if not trusted:
            return {"success": False, "reason": "location_rejected"}
        # Update via Supabase wrapper which now handles casting. `heading`
        # column added in migration 113 — persist it so rider/admin map
        # markers can rotate the car icon to the real direction of travel
        # (and so two drivers at the same point don't render as one).
        update_data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc)}
        # Normalise to 0–359 and skip clearly-invalid values. We deliberately
        # only write heading when the device sent a usable number, so a
        # stationary fix with no bearing doesn't wipe the last good heading.
        if heading is not None:
            try:
                update_data["heading"] = float(heading) % 360
            except (TypeError, ValueError):
                pass

        await db_supabase.update_one("drivers", {"user_id": current_user["id"]}, update_data)
        # Also sync to generic lat/lng fields if they exist to support legacy queries
        # (Though update_one might not support setting multiple top-level fields easily if we rely on $set mapping)
        # Let's trust db.drivers.update_one to handle the schema or the wrapper.

        # Persist the FULL batch as breadcrumbs, not just the live marker above.
        # Until now this endpoint (background task + WS-down REST fallback) kept
        # only the last point, so any backgrounded stretch of a trip produced no
        # driver_location_history rows — settled distance and the per-insurance-
        # period SGI audit trail both undercounted. The helper is a no-op unless
        # the driver currently has an active ride, and derives ride_id + phase
        # server-side (so a point the client tagged "background" still lands in
        # trip_in_progress). Best-effort: never fail the marker update on it.
        try:
            from ..utils.breadcrumbs import persist_ride_breadcrumbs
        except ImportError:
            from utils.breadcrumbs import persist_ride_breadcrumbs  # type: ignore
        try:
            await persist_ride_breadcrumbs(driver_id, points)
        except Exception:
            logger.error("location-batch breadcrumb persist failed", exc_info=True)

        # Keep presence alive even when the driver's WebSocket briefly
        # drops but the REST location batch keeps flowing (e.g. phone on
        # cellular switching towers).
        driver_row = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        )
        if driver_row and driver_row.get("is_online"):
            await mark_present(driver_row["id"])

    return {"success": True}


@api_router.post("/attest-nonce")
async def attest_nonce(current_user: dict = Depends(get_current_user)):
    """Issue a single-use nonce for Play Integrity / App Attest verification.

    The client passes this nonce to the platform attestation API so the
    signed token can't be replayed from a different session.
    """
    import secrets

    nonce = secrets.token_hex(32)
    return {"nonce": nonce}


@api_router.post("/attest-device")
async def attest_device(device_info: dict, current_user: dict = Depends(get_current_user)):
    """Verify device integrity on go-online. Flags emulators and suspicious devices."""
    try:
        from ..utils.device_attestation import verify_device
    except ImportError:
        from utils.device_attestation import verify_device  # type: ignore

    driver_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    driver_id = driver_rows[0]["id"] if driver_rows else current_user["id"]

    result = await verify_device(current_user["id"], driver_id, device_info)
    return result


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
        le=Decimal("50000.00"),
        decimal_places=2,
        description="Payout must be between $10.00 and $50,000.00",
    )


class InstantPayoutRequest(BaseModel):
    # Lower floor than standard payouts because the fee floor below makes
    # micro-cashouts uneconomical on the platform side anyway; ceiling is
    # Stripe's documented Instant Payout cap of $5,000 USD/CAD per request.
    amount: Decimal = Field(
        ...,
        ge=Decimal("5.00"),
        le=Decimal("5000.00"),
        decimal_places=2,
        description="Instant payout must be between $5.00 and $5,000.00",
    )


# Instant payout fee model: 1.5% of gross, with a $0.50 floor and $15 ceiling.
# Matches Uber's Instant Pay and Lyft Express Pay fee structures within
# rounding. Standard scheduled payouts are still free (zero fee).
INSTANT_PAYOUT_FEE_PCT = Decimal("0.015")
INSTANT_PAYOUT_MIN_FEE = Decimal("0.50")
INSTANT_PAYOUT_MAX_FEE = Decimal("15.00")


def compute_instant_payout_fee(amount: Decimal) -> Decimal:
    """Fee charged on an instant payout. Caller subtracts to get net."""
    pct = (amount * INSTANT_PAYOUT_FEE_PCT).quantize(Decimal("0.01"))
    if pct < INSTANT_PAYOUT_MIN_FEE:
        return INSTANT_PAYOUT_MIN_FEE
    if pct > INSTANT_PAYOUT_MAX_FEE:
        return INSTANT_PAYOUT_MAX_FEE
    return pct


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
            "bank_account": {"bank_name": "Stripe Connect", "account_last4": "****"},
        }

    return {"has_bank_account": False, "bank_account": None}


async def _ensure_stripe_account(driver: dict, user: dict, stripe_secret: str) -> str:
    """Return the driver's Stripe Connect (Express) account id, creating it on
    first use. Shared by the hosted-link and embedded-onboarding flows so both
    converge on a single CA individual Express account per driver."""
    account_id = driver.get("stripe_account_id")
    if account_id:
        return account_id
    # Idempotency key keyed on the driver makes concurrent / rapid-retry creates
    # converge on ONE Express account instead of leaking duplicate orphans —
    # the embedded onboarding's fetchClientSecret can fire this twice before the
    # first write lands. It also lets a retry recover a create whose DB write
    # below failed (same key → same account within Stripe's 24h window).
    account = stripe.Account.create(
        type="express",
        country="CA",
        email=user.get("email"),
        capabilities={"transfers": {"requested": True}},
        business_type="individual",
        api_key=stripe_secret,
        idempotency_key=f"connect-acct-{driver['id']}",
    )
    try:
        await db_supabase.update_one("drivers", {"id": driver["id"]}, {"stripe_account_id": account.id})
    except Exception as e:
        # Never return an unpersisted account id — payouts read the DB column, so
        # a lost write would strand the driver on an account nothing points to.
        # Surface loudly; a retry re-creates with the same idempotency key and
        # converges on this same account, then persists it.
        logger.error("Failed to persist stripe_account_id", exc_info=True)
        raise HTTPException(status_code=502, detail="Could not start verification. Please try again.") from e
    return account.id


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
        account_id = await _ensure_stripe_account(driver, user, stripe_secret)

        # Build return/refresh URLs from the externally-routable API host
        # (Cloudflare CNAME), NOT the app_settings dict. The dict has no
        # "base_url" key, so the old code always fell back to localhost:8000 —
        # which is why drivers landed on http://localhost:8000/api/drivers/...
        # after finishing Stripe's hosted flow. An explicit app_settings
        # "base_url" still wins if an operator sets one.
        try:
            from ..core.config import settings as _config
        except ImportError:
            from core.config import settings as _config  # type: ignore
        api_base = (settings.get("base_url") or _config.PUBLIC_API_BASE_URL).rstrip("/")

        account_link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=f"{api_base}/api/v1/drivers/stripe-refresh",
            return_url=f"{api_base}/api/v1/drivers/stripe-return",
            type="account_onboarding",
            # Pull everything Stripe will *eventually* require into this session —
            # most importantly the SIN (individual.id_number), which for a CA
            # Express individual account is "eventually_due" and is otherwise
            # skipped at initial onboarding. future_requirements="include" also
            # pulls in threshold-gated requirements (Stripe often defers the full
            # SIN until a CAD payout volume threshold). Needed for T4A / CRA
            # platform reporting (Income Tax Act Part XX).
            collection_options={
                "fields": "eventually_due",
                "future_requirements": "include",
            },
            api_key=stripe_secret,
        )
        # The real onboarded gate is now stripe_details_submitted, set by
        # the account.updated webhook handler in services/stripe_kyc_sync.py.
        # We used to flip stripe_account_onboarded=True here optimistically,
        # which mis-classified every driver who abandoned Stripe's hosted
        # flow halfway through as fully onboarded. Removed.
        return {"url": account_link.url, "mock": False}
    except HTTPException:
        # Preserve the helper's 502 (e.g. failed stripe_account_id persist)
        # instead of masking it as a generic 500.
        raise
    except Exception as e:
        logger.error("Stripe onboarding error", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.") from e


@api_router.post("/stripe-sync")
async def stripe_sync_status(current_user: dict = Depends(get_current_user)) -> dict:
    """Pull the driver's LIVE Stripe Connect status (Account.retrieve) and
    mirror it onto the drivers row, then return the verification state.

    Called by the driver app immediately after returning from Stripe's hosted
    onboarding so the UI reflects acceptance right away — without waiting on the
    account.updated webhook, which can be delayed or, if the Connect webhook
    isn't configured, never arrive at all."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    try:
        from ..services.stripe_kyc_sync import refresh_driver_kyc
    except ImportError:
        from services.stripe_kyc_sync import refresh_driver_kyc  # type: ignore

    result = await refresh_driver_kyc(driver)
    status = result.get("status")
    if status == "no_stripe_account":
        # Driver hasn't started onboarding yet — not an error, just not set up.
        return {"synced": False, "onboarded": False, "payouts_enabled": False, "requirements_due": []}
    if status != "ok":
        # stripe_not_configured / stripe_error — surface it (don't silently
        # report "not onboarded") so the app can retry instead of masking it.
        raise HTTPException(status_code=502, detail="Could not sync verification status. Please try again.")

    updates = result.get("updates") or {}
    return {
        "synced": True,
        "onboarded": bool(updates.get("stripe_account_onboarded")),
        "details_submitted": bool(updates.get("stripe_details_submitted")),
        "payouts_enabled": bool(updates.get("stripe_payouts_enabled")),
        "id_number_provided": bool(updates.get("stripe_id_number_provided")),
        "requirements_due": list(updates.get("stripe_requirements_due") or []),
    }


# Driver-app deep link (scheme defined in driver-app/app.config.ts: SCHEME).
_STRIPE_RETURN_DEEP_LINK = "spinr-driver://driver/payout"


def _stripe_bounce_page(deep_link: str, heading: str, body: str) -> str:
    """HTML interstitial that bounces the system browser back into the Spinr
    Driver app. A raw 302 to a custom scheme is unreliable on iOS Safari, so we
    auto-attempt the deep link and also render a manual button as a fallback."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spinr Driver</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f0f10;color:#fff;
       display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;text-align:center}}
  .card{{padding:32px;max-width:360px}}
  h1{{font-size:20px;margin:0 0 8px}} p{{color:#bdbdbd;margin:0 0 24px}}
  a.btn{{display:inline-block;background:#16a34a;color:#fff;text-decoration:none;
         padding:14px 24px;border-radius:12px;font-weight:600}}
</style></head>
<body><div class="card">
  <h1>{heading}</h1><p>{body}</p>
  <a class="btn" href="{deep_link}">Return to Spinr Driver</a>
</div>
<script>setTimeout(function(){{window.location.replace("{deep_link}")}},150);</script>
</body></html>"""


@api_router.get("/stripe-return", include_in_schema=False)
async def stripe_return() -> HTMLResponse:
    """Stripe redirects the browser here when the driver finishes (or exits)
    hosted onboarding. Bounces back into the app, which re-reads KYC status on
    focus. The authoritative onboarding gate is the account.updated webhook
    (services/stripe_kyc_sync.py) — this endpoint is UX only, never a trust gate."""
    return HTMLResponse(
        _stripe_bounce_page(
            f"{_STRIPE_RETURN_DEEP_LINK}?stripe=return",
            "Verification submitted",
            "You can return to the Spinr Driver app now.",
        )
    )


@api_router.get("/stripe-refresh", include_in_schema=False)
async def stripe_refresh() -> HTMLResponse:
    """Stripe redirects here when an onboarding link expires or is reopened.
    Sends the driver back into the app, which restarts onboarding on demand."""
    return HTMLResponse(
        _stripe_bounce_page(
            f"{_STRIPE_RETURN_DEEP_LINK}?stripe=refresh",
            "Link expired",
            "Return to the Spinr Driver app and tap Connect again to continue.",
        )
    )


@api_router.post("/stripe-account-session")
async def stripe_account_session(current_user: dict = Depends(get_current_user)) -> dict:
    """Mint a single-use Stripe AccountSession client secret for the embedded
    account-onboarding component (Option B — fully in-app, no browser redirect).

    The connected account collects and *holds* the SIN itself; we never see the
    raw value, preserving the PIPEDA posture (only last-4 + status is mirrored
    back via the account.updated webhook). Called repeatedly by the WebView's
    fetchClientSecret callback, so it must stay cheap and idempotent."""
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
        # Dev/demo: Stripe not configured. 503 lets the WebView surface a clean
        # "not available yet" state instead of a blank embedded component.
        raise HTTPException(status_code=503, detail="Stripe is not configured")

    try:
        account_id = await _ensure_stripe_account(driver, user, stripe_secret)
        session = stripe.AccountSession.create(
            account=account_id,
            components={
                "account_onboarding": {
                    "enabled": True,
                    # Let the driver add their payout bank account inside the
                    # same embedded flow.
                    "features": {"external_account_collection": True},
                },
            },
            api_key=stripe_secret,
        )
        return {"client_secret": session.client_secret}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Stripe AccountSession error", exc_info=True)
        raise HTTPException(status_code=502, detail="Could not start verification. Please try again.") from e


# Plain template (NOT an f-string) so the embedded JS braces stay literal.
# __SPINR_PK__ is substituted with json.dumps(publishable_key) at render time.
_STRIPE_EMBEDDED_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<title>Spinr Driver — Verification</title>
<style>
  html,body{margin:0;height:100%;background:#0f0f10;
    font-family:-apple-system,Segoe UI,Roboto,sans-serif}
  #msg{color:#bdbdbd;padding:28px;text-align:center;font-size:15px}
  #dbg{color:#6b7280;padding:6px 12px;text-align:center;font-size:11px;
    font-family:ui-monospace,Menlo,Consolas,monospace;word-break:break-all}
  #container{padding:0 4px}
</style></head>
<body>
  <div id="msg">Loading secure verification…</div>
  <div id="container"></div>
  <div id="dbg"></div>
  <script>
    var PK = __SPINR_PK__;
    var mounted = false;
    function post(m){ if(window.ReactNativeWebView){ window.ReactNativeWebView.postMessage(m); } }
    // Surface progress to BOTH the RN host (debug strip) and on-page, so the
    // same URL opened directly in a browser shows exactly where it stalls.
    function stage(s){ post("stage:" + s); var d=document.getElementById("dbg"); if(d){ d.textContent="stage: "+s; } }
    function fail(c){ post("error:" + c); var d=document.getElementById("dbg"); if(d){ d.textContent="error: "+c; } }
    async function fetchClientSecret(){
      stage("fetch-start");
      var token = window.__SPINR_TOKEN || "";
      if(!token){ fail("no-token"); throw new Error("no token"); }
      var res;
      try{
        res = await fetch("/api/v1/drivers/stripe-account-session", {
          method: "POST",
          headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" }
        });
      }catch(e){ fail("fetch-network"); throw e; }  // network/CSP block — previously silent
      if(!res.ok){ fail(String(res.status)); throw new Error("account_session " + res.status); }
      var data = await res.json();
      stage("fetch-ok");
      return data.client_secret;
    }
    function mount(){
      stage("mounting");
      try{
        var instance = StripeConnect.init({
          publishableKey: PK,
          fetchClientSecret: fetchClientSecret,
          appearance: { variables: {
            colorBackground: "#0f0f10", colorText: "#ffffff",
            colorPrimary: "#16a34a", colorSecondaryText: "#bdbdbd" } }
        });
        stage("init-ok");
        var onboarding = instance.create("account-onboarding");
        // Mirror the hosted flow: collect eventually/future-due fields up front
        // so the SIN (individual.id_number) is requested during onboarding.
        onboarding.setCollectionOptions({ fields: "eventually_due", futureRequirements: "include" });
        onboarding.setOnExit(function(){ post("exit"); });
        document.getElementById("msg").style.display = "none";
        document.getElementById("container").appendChild(onboarding);
        mounted = true;
        stage("mounted");
      }catch(e){ fail("init"); }
    }
    if(!PK){ stage("no-pk"); document.getElementById("msg").textContent =
      "Payouts setup is not available yet. Please try again later."; }
    else {
      stage("script-init");
      window.StripeConnect = window.StripeConnect || {};
      // connect.js calls StripeConnect.onLoad once the script finishes loading.
      if(window.StripeConnect && typeof window.StripeConnect.init === "function"){ mount(); }
      else { stage("awaiting-connectjs"); window.StripeConnect.onLoad = mount; }
      // Watchdog: if connect.js never loads (CSP/network block), onLoad never
      // fires and the page would spin forever — surface it instead.
      setTimeout(function(){ if(!mounted){ fail("connectjs-timeout"); } }, 12000);
    }
  </script>
  <script src="https://connect-js.stripe.com/v1.0/connect.js" async onerror="fail('connectjs-load')"></script>
</body></html>"""


def _stripe_embedded_page(publishable_key: str) -> str:
    """HTML shell that mounts Stripe's embedded account-onboarding component.

    Served same-origin with the API so the in-page fetchClientSecret POST to
    /stripe-account-session needs no CORS. The page carries only the publishable
    key (public by design); the driver's bearer token is injected at runtime by
    the WebView via injectedJavaScriptBeforeContentLoaded (window.__SPINR_TOKEN),
    never placed in the URL or server logs. setCollectionOptions mirrors the
    hosted flow so the SIN (eventually/future-due) is collected up front."""
    # The publishable key comes from the admin-writable app_settings table, so
    # treat it as untrusted: plain json.dumps does NOT escape </script>, which
    # would let a tampered value break out of the inline <script> (stored XSS
    # that could read window.__SPINR_TOKEN). Escape the HTML-significant chars
    # to their \uXXXX forms — still a valid JS string literal, no breakout.
    safe_pk = json.dumps(publishable_key)
    for _ch, _esc in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),  # JS line separator — illegal raw in a string literal
        ("\u2029", "\\u2029"),  # JS paragraph separator
    ):
        safe_pk = safe_pk.replace(_ch, _esc)
    return _STRIPE_EMBEDDED_HTML.replace("__SPINR_PK__", safe_pk)


@api_router.get("/stripe-embedded", include_in_schema=False)
async def stripe_embedded() -> HTMLResponse:
    """Public HTML host for the embedded onboarding WebView. Contains no secret
    (publishable key only); all privileged work goes through the authenticated
    /stripe-account-session endpoint the page calls back into."""
    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    publishable_key = settings.get("stripe_publishable_key", "")
    return HTMLResponse(_stripe_embedded_page(publishable_key))


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

    account_data["account_last4"] = acc_num[-4:] if len(acc_num) >= 4 else acc_num
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


_GST_BN_RE = re.compile(r"^\d{9}(RT\d{4})?$")


def _gst_on_file(driver: dict) -> bool:
    """True when the driver has a CRA Business Number on file in a valid format
    (9-digit BN, optionally with the RTxxxx GST/HST program suffix).

    Rideshare drivers must register for GST/HST with the CRA from their first
    fare — the $30k small-supplier exemption does NOT apply to ride-sharing
    (Excise Tax Act "taxi business" definition). So a valid BN is a hard
    precondition for any payout, scheduled or instant."""
    bn = (driver.get("gst_bn") or "").replace(" ", "").upper()
    return bool(_GST_BN_RE.match(bn))


def _require_gst_for_payout(driver: dict) -> None:
    """Block payout until the driver's GST/HST Business Number is on file."""
    if not _gst_on_file(driver):
        raise HTTPException(
            status_code=422,
            detail=(
                "A valid GST/HST Business Number is required before you can be paid. "
                "Rideshare drivers must register for GST/HST with the CRA from their "
                "first fare. Add your 9-digit Business Number in Payouts to continue."
            ),
        )


def _require_sin_for_payout(driver: dict) -> None:
    """Block payout until the driver's SIN is on file with Stripe.

    Stripe collects and *holds* the SIN (individual.id_number); we only mirror
    the boolean stripe_id_number_provided (+ last4), never the number itself.
    CRA T4A / platform reporting (Income Tax Act Part XX) requires the SIN, so
    we treat it as a hard precondition for payout — stricter than Stripe, which
    leaves the full SIN "eventually due" until a payout-volume threshold. The
    driver supplies it by re-opening Stripe onboarding (Payouts -> Update); on
    an Express account it cannot be pushed via the platform API."""
    if not driver.get("stripe_id_number_provided"):
        raise HTTPException(
            status_code=422,
            detail=(
                "Your SIN must be on file before you can be paid. Open "
                "Payouts and tap Update to add it securely through Stripe "
                "(we never see or store it — Stripe holds it)."
            ),
        )


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

    # CRA: rideshare drivers must be GST/HST-registered from their first fare.
    _require_gst_for_payout(driver)
    # CRA T4A: the SIN (held by Stripe) must be on file before any payout.
    _require_sin_for_payout(driver)

    balance = await get_driver_balance(current_user)
    if req.amount > Decimal(balance.get("payable_balance", "0")):
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
    # Pre-allocate the payout id so the Stripe Transfer carries a stable
    # idempotency key — a retry of this same payout never double-transfers.
    # (The @idempotent_endpoint decorator dedupes at the HTTP layer, but a
    # Stripe-level key is defence in depth against any path that re-enters
    # this block, matching the money-safety contract of request_instant_payout.)
    payout_id = str(uuid.uuid4())

    if stripe_secret and stripe_account_id:
        try:
            transfer = stripe.Transfer.create(
                amount=dollars_to_cents(req.amount),
                currency="cad",
                destination=stripe_account_id,
                api_key=stripe_secret,
                idempotency_key=f"payout-transfer-{payout_id}",
            )
            status = RideStatus.COMPLETED
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
        "id": payout_id,
        "driver_id": driver["id"],
        "amount": req.amount,
        "status": status,
        "stripe_payout_id": stripe_payout_id,
        "bank_name": account.get("bank_name") if account else "Stripe Connect",
        "account_last4": account.get("account_last4") if account else "****",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("payouts", payout)
    return {"success": True, "payout": serialize_doc(payout)}


async def _attempt_transfer_reversal(transfer_id: str, stripe_secret: str, payout_id: str) -> bool:
    """Best-effort compensating reversal of a Stripe Transfer.

    Called when the payout step of an instant payout fails after the
    transfer step has already moved money to the connect account. Uses an
    idempotency key tied to the payout row so a retry of this same payout
    never issues a second reversal. Returns True iff Stripe accepted the
    reversal — the caller writes "reversed" vs "stranded" accordingly.
    """
    try:
        stripe.Transfer.create_reversal(
            transfer_id,
            api_key=stripe_secret,
            idempotency_key=f"instant-payout-reversal-{payout_id}",
        )
        return True
    except Exception:
        # logger.exception captures the underlying Stripe error (transfer
        # state, amount mismatch, etc.) without leaking the transfer id
        # back to the client. The payout row will be flagged for manual
        # review so ops can chase it down.
        logger.exception("Transfer reversal failed for stranded instant payout")
        return False


@api_router.post("/payouts/instant")
@idempotent_endpoint(scope="driver_instant_payout")
async def request_instant_payout(
    req: InstantPayoutRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Same-day cashout via Stripe Instant Payout (Uber-style Instant Pay).

    Money-safety contract:
      Instant payout is two Stripe calls — Transfer (platform → connect),
      then Payout(method=instant). If the second fails after the first
      succeeds, money is stranded in the connect account. To keep the
      books consistent:
        1. Generate the payout_id BEFORE the first Stripe call so every
           Stripe call carries a per-payout idempotency key — a retry on
           the same payout row never double-transfers or double-pays-out.
        2. INSERT the payout row immediately after the transfer succeeds
           with status='transfer_completed' and stripe_transfer_id set;
           a crash between transfer and payout still leaves a recoverable
           DB record.
        3. If the payout step fails, attempt Transfer.create_reversal().
           On reversal success → row status='reversed'. On reversal
           failure → status='stranded' and requires_manual_review=true so
           the ops dashboard surfaces it.

    Fee model is regulator-friendly: shown in the receipt, separate line
    item, never hidden. Standard scheduled payouts remain free (see
    request_payout).
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # CRA: rideshare drivers must be GST/HST-registered from their first fare.
    _require_gst_for_payout(driver)
    # CRA T4A: the SIN (held by Stripe) must be on file before any payout.
    _require_sin_for_payout(driver)

    fee = compute_instant_payout_fee(req.amount)
    net_amount = req.amount - fee
    if net_amount <= Decimal("0"):
        # Defence in depth: the request schema's ge=5.00 already prevents
        # this since the floor fee is 0.50, but guard anyway in case fee
        # config changes later.
        raise HTTPException(status_code=400, detail="Fee exceeds payout amount")

    balance = await get_driver_balance(current_user)
    if req.amount > Decimal(balance.get("payable_balance", "0")):
        raise HTTPException(status_code=400, detail="Insufficient funds")

    stripe_account_id = driver.get("stripe_account_id")
    account = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("bank_accounts", {"driver_id": driver["id"]}, limit=1)
    )

    # Instant Payout requires a Stripe Connect account with a debit-card
    # external_account on file — Stripe rejects bank-only setups with a
    # generic 400. Surface the eligibility check up-front so the driver
    # sees a clear message instead of a Stripe error.
    if not stripe_account_id:
        raise HTTPException(
            status_code=400,
            detail="Instant payout requires Stripe Connect onboarding. "
            "Please complete onboarding from the Payouts screen.",
        )

    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=503, detail="Payouts temporarily unavailable")

    # Pre-allocate the payout_id so every Stripe call carries a stable
    # per-payout idempotency key. A retry of the same row never causes a
    # second transfer or a second payout.
    payout_id = str(uuid.uuid4())

    # ── Step 1: Transfer platform → connect account ───────────────────
    try:
        transfer = stripe.Transfer.create(
            amount=dollars_to_cents(req.amount),
            currency="cad",
            destination=stripe_account_id,
            api_key=stripe_secret,
            idempotency_key=f"instant-payout-transfer-{payout_id}",
        )
        stripe_transfer_id = transfer.id
    except Exception as e:
        # Transfer never landed — nothing to reverse, nothing to persist.
        logger.exception("Stripe transfer step failed for instant payout")
        raise HTTPException(
            status_code=500,
            detail="Instant payout failed. Please try again or contact support.",
        ) from e

    # ── Persist the row IMMEDIATELY so a crash before the payout step
    #    still leaves a recoverable record of the in-flight transfer. ──
    now_iso = datetime.now(timezone.utc).isoformat()
    payout = {
        "id": payout_id,
        "driver_id": driver["id"],
        "amount": req.amount,
        "fee": fee,
        "net_amount": net_amount,
        "payout_type": "instant",
        "status": "transfer_completed",
        "stripe_transfer_id": stripe_transfer_id,
        "stripe_payout_id": None,
        "bank_name": account.get("bank_name") if account else "Stripe Connect",
        "account_last4": account.get("account_last4") if account else "****",
        "created_at": now_iso,
    }
    try:
        await db_supabase.insert_one("payouts", payout)
    except Exception as persist_exc:
        # Transfer succeeded but we couldn't record it. Reverse the transfer
        # so the books match, then fail loudly. If the reversal also fails
        # there's no DB row to flag — log + alert ops.
        logger.exception("Failed to persist instant payout row after transfer succeeded")
        reversal_ok = await _attempt_transfer_reversal(stripe_transfer_id, stripe_secret, payout_id)
        if not reversal_ok:
            logger.error(
                "STRANDED instant payout — DB persist failed AND reversal failed. payout_id=%s driver_id=%s amount=%s",
                payout_id,
                driver["id"],
                req.amount,
            )
        raise HTTPException(
            status_code=500,
            detail="Instant payout failed. Please try again or contact support.",
        ) from persist_exc

    # ── Step 2: Payout on connect account ─────────────────────────────
    try:
        # Stripe deducts its own ~1% fee from the platform side (separate
        # from the fee we charge the driver). Pass stripe_account so the
        # call runs in the connected account's context.
        payout_obj = stripe.Payout.create(
            amount=dollars_to_cents(net_amount),
            currency="cad",
            method="instant",
            api_key=stripe_secret,
            stripe_account=stripe_account_id,
            idempotency_key=f"instant-payout-{payout_id}",
        )
        stripe_payout_id = payout_obj.id
    except Exception as payout_exc:
        # Payout failed; reverse the transfer to keep funds on the platform
        # side. The row stays in DB either way — flagged for manual review
        # when reversal also fails so stranded money is visible to ops.
        logger.exception("Stripe payout step failed; attempting transfer reversal")
        reversal_ok = await _attempt_transfer_reversal(stripe_transfer_id, stripe_secret, payout_id)
        new_status = "reversed" if reversal_ok else "stranded"
        try:
            await db_supabase.update_one(
                "payouts",
                {"id": payout_id},
                {
                    "status": new_status,
                    "failure_reason": str(payout_exc)[:500],
                    "requires_manual_review": not reversal_ok,
                },
            )
        except Exception:
            # The row exists with status=transfer_completed; we couldn't
            # update it to reflect the failure. Log loudly — the partial
            # state is still recoverable from Stripe.
            logger.exception("Failed to flag instant payout row after payout failure")
        raise HTTPException(
            status_code=500,
            detail="Instant payout failed. Please try again or contact support.",
        ) from payout_exc

    # ── Step 3: Mark row as completed ─────────────────────────────────
    try:
        await db_supabase.update_one(
            "payouts",
            {"id": payout_id},
            {"status": RideStatus.COMPLETED, "stripe_payout_id": stripe_payout_id},
        )
    except Exception:
        # Money landed in the driver's bank but we couldn't flip the row
        # to "completed". The row stays as "transfer_completed" — a
        # follow-up reconciliation job will fix the status. Don't unwind
        # the payout (the driver has the money).
        logger.exception(
            "Failed to mark instant payout completed (money already disbursed)",
        )

    payout["status"] = RideStatus.COMPLETED
    payout["stripe_payout_id"] = stripe_payout_id
    return {"success": True, "payout": serialize_doc(payout)}


@api_router.get("/payouts/instant/quote")
async def get_instant_payout_quote(
    amount: Decimal = Query(..., ge=Decimal("5.00"), le=Decimal("5000.00")),
    current_user: dict = Depends(get_current_user),
):
    """Quote the fee + net for an instant payout before the driver confirms.

    Driver app shows: "Cash out $50.00 now — $0.75 fee, $49.25 to your bank"
    Reading the quote from the server (not computing it client-side) means
    a fee-schedule change rolls out without a mobile release.
    """
    fee = compute_instant_payout_fee(amount)
    net = amount - fee
    return {
        "amount": _money_str(amount),
        "fee": _money_str(fee),
        "net_amount": _money_str(net),
        "payout_type": "instant",
    }


@api_router.get("/payouts")
async def get_payout_history(
    limit: int = Query(20),
    offset: int = Query(0),
    current_user: dict = Depends(get_current_user),
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
        statuses=[RideStatus.COMPLETED],
        from_date=f"{year}-01-01",
        to_date=f"{year + 1}-01-01",
        limit=10000,
    )

    # T4A reports the driver's INCOME — sum driver_earnings (see _ride_income),
    # not the gross fare; that would misreport income to the CRA if they ever
    # diverge under a future fee model.
    total_earnings = _money_str(sum((_ride_income(r) for r in rides), Decimal("0")))

    driver_name = f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip() or None
    return {
        "year": year,
        "total_earnings": total_earnings,
        "total_trips": len(rides),
        "platform_fees": "0.00",
        "net_earnings": total_earnings,
        "gst_registered": driver.get("gst_registered", False),
        "gst_bn": driver.get("gst_bn") or "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_url": f"/api/v1/drivers/t4a/{year}/pdf",
        "driver_name": driver_name,
    }


@api_router.get("/t4a/{year}/pdf")
async def download_t4a_pdf(year: int, current_user: dict = Depends(get_current_user)):
    from fastapi.responses import Response as _Response

    summary = await get_t4a_summary(year, current_user)
    summary["driver_name"] = f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip() or None
    pdf_bytes = generate_t4a_pdf(summary)
    filename = f"T4A_{year}_{current_user['id'][:8]}.pdf"
    return _Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.get("/earnings/export")
async def export_earnings(year: int = Query(None), current_user: dict = Depends(get_current_user)):
    if not year:
        year = datetime.now(timezone.utc).year

    summary_data = await get_t4a_summary(year, current_user)

    # CRA T4A-compatible CSV. GST/BN columns are required for drivers who
    # earn above the T4A reporting threshold and hold a GST/HST account.
    csv_data = (
        "Year,Total Earnings,Total Trips,Net Earnings,GST Registered,GST/HST Business Number\n"
        f"{year},"
        f"{summary_data['total_earnings']},"
        f"{summary_data['total_trips']},"
        f"{summary_data['net_earnings']},"
        f"{'Yes' if summary_data['gst_registered'] else 'No'},"
        f"{summary_data['gst_bn']}"
    )
    filename = f"earnings_export_{year}.csv"

    return {"data": csv_data, "filename": filename}


# Account (users) fields omitted from a data export: credentials and internal
# session/auth state that are not "personal data about the subject".
#  - password_hash: credential
#  - fcm_token*: device push credentials (impersonation risk if intercepted)
#  - token_version / current_session_id / sessions_invalid_before: auth/session
#    revocation state, useless to the subject and a replay-window roadmap
#  - stripe_customer_id: operational Stripe identifier
_EXPORT_REDACT_ACCOUNT = frozenset(
    {
        "password_hash",
        "fcm_token",
        "fcm_token_rider",
        "fcm_token_driver",
        "token_version",
        "current_session_id",
        "sessions_invalid_before",
        "stripe_customer_id",
    }
)

# Per-ride fields omitted from a data export: these describe the RIDER, not the
# driver (the data subject). Raw pickup/dropoff coordinates, the route polyline,
# and rider_id are third-party PII (PIPEDA s.4.5). The human-readable
# pickup/dropoff addresses are kept — they are the driver's own trip record.
_EXPORT_REDACT_RIDE = frozenset(
    {
        "rider_id",
        "pickup_lat",
        "pickup_lng",
        "pickup_nav_lat",
        "pickup_nav_lng",
        "dropoff_lat",
        "dropoff_lng",
        "dropoff_nav_lat",
        "dropoff_nav_lng",
        "route_polyline",
        "phase_polylines",
        "polyline",
    }
)


# Driver-profile (drivers) fields omitted from a data export:
#  - password_hash / fcm_token: credentials
#  - stripe_account_id / bank_account: financial credentials (already excluded
#    from normal self-responses via _STRIP_FROM_SELF_RESPONSE)
#  - lat / lng / location_geog: transient last-known GPS, not stored profile data
_EXPORT_REDACT_DRIVER = frozenset(
    {
        "password_hash",
        "fcm_token",
        "stripe_account_id",
        "bank_account",
        "lat",
        "lng",
        "location_geog",
    }
)


def _csv_cell(value: Any) -> str:
    """Render a single CSV cell. Nested dict/list → compact JSON; None → ''."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def _rows_to_csv(rows: list) -> str:
    """Tabular CSV for a list of dict records (union of keys, first-seen order)."""
    if not rows:
        return "No records.\n"
    import csv  # noqa: PLC0415
    import io  # noqa: PLC0415

    fieldnames: list = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_cell(row.get(k)) for k in fieldnames})
    return buf.getvalue()


def _object_to_csv(obj: dict) -> str:
    """Two-column field,value CSV for a single record (account/profile)."""
    import csv  # noqa: PLC0415
    import io  # noqa: PLC0415

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["field", "value"])
    for key, value in (obj or {}).items():
        writer.writerow([key, _csv_cell(value)])
    return buf.getvalue()


def _build_export_readme(payload: dict, generated_on: str) -> str:
    """Human-readable index of what's in the export archive."""
    account = payload.get("account", {}) or {}
    raw_name = account.get("name") or account.get("full_name") or account.get("first_name") or "Driver"
    # Strip control characters so a name with newlines can't corrupt the README.
    name = " ".join(str(raw_name).split()) or "Driver"
    return (
        "Spinr — Personal Data Export\n"
        "============================\n\n"
        f"Generated: {generated_on}\n"
        f"Account: {name}\n\n"
        "This archive contains the personal data Spinr holds about you, provided\n"
        "in PIPEDA-compliant form. Files:\n\n"
        "  account.csv                  Your account record (field,value).\n"
        "  driver_profile.csv           Your driver profile (field,value).\n"
        "  rides.csv                    Your trip history (one row per ride).\n"
        "  payouts.csv                  Your payout history (one row per payout).\n"
        "  documents.csv                Records of documents you uploaded\n"
        "                               (the file contents themselves are not included).\n"
        "  notification_preferences.csv Your notification settings.\n"
        "  raw_data.json                The complete export in machine-readable JSON.\n\n"
        "Counts:\n"
        f"  Rides:     {len(payload.get('rides') or [])}\n"
        f"  Payouts:   {len(payload.get('payouts') or [])}\n"
        f"  Documents: {len(payload.get('documents') or [])}\n\n"
        "Questions or deletion requests: privacy@spinr.ca\n"
    )


def _build_export_zip(payload: dict, generated_on: str) -> bytes:
    """Bundle the export payload into a ZIP of CSV files + README + JSON."""
    import io  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    files = {
        "README.txt": _build_export_readme(payload, generated_on),
        "account.csv": _object_to_csv(payload.get("account", {})),
        "driver_profile.csv": _object_to_csv(payload.get("driver_profile", {})),
        "rides.csv": _rows_to_csv(payload.get("rides") or []),
        "payouts.csv": _rows_to_csv(payload.get("payouts") or []),
        "documents.csv": _rows_to_csv(payload.get("documents") or []),
        "notification_preferences.csv": _rows_to_csv(payload.get("notification_preferences") or []),
        "raw_data.json": json.dumps(payload, indent=2, default=str),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _build_export_email_html(filename: str) -> str:
    """Lyft-style 'your data is ready' HTML email body."""
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'max-width:520px;margin:0 auto;color:#1a1a1a;line-height:1.5">'
        '<h2 style="margin:0 0 12px">Your data export is ready</h2>'
        "<p>As requested, your personal data held by Spinr is attached as a ZIP archive "
        f"(<strong>{filename}</strong>).</p>"
        "<p>Inside you'll find spreadsheet (CSV) files you can open in Excel, Numbers, or "
        "Google Sheets:</p>"
        "<ul>"
        "<li><strong>README.txt</strong> — what each file contains</li>"
        "<li><strong>account.csv</strong>, <strong>driver_profile.csv</strong> — your profile</li>"
        "<li><strong>rides.csv</strong> — your trip history</li>"
        "<li><strong>payouts.csv</strong> — your payout history</li>"
        "<li><strong>documents.csv</strong> — your uploaded document records</li>"
        "<li><strong>notification_preferences.csv</strong> — your notification settings</li>"
        "<li><strong>raw_data.json</strong> — the complete machine-readable export</li>"
        "</ul>"
        '<p style="color:#555;font-size:13px">Questions about your data or want it deleted? '
        'Contact <a href="mailto:privacy@spinr.ca">privacy@spinr.ca</a>.</p>'
        '<p style="color:#888;font-size:12px">— The Spinr Team</p>'
        "</div>"
    )


# Signed download link lives for 7 days — long enough for the driver to grab it
# on their own schedule, short enough that a leaked link self-expires.
_EXPORT_LINK_TTL_SECONDS = 7 * 24 * 3600


async def _upload_export_zip(user_id: str, zip_bytes: bytes, expires_in_seconds: int) -> str:
    """Upload the export ZIP to the private ``data-exports`` bucket and return a
    time-limited signed download URL. Raises on failure so the caller can fall
    back to attaching the ZIP."""
    import asyncio  # noqa: PLC0415

    try:
        from ..supabase_client import supabase  # type: ignore
    except ImportError:
        from supabase_client import supabase  # type: ignore
    try:
        from ..documents import _extract_signed_url  # type: ignore
    except ImportError:
        from documents import _extract_signed_url  # type: ignore

    bucket = "data-exports"
    storage_path = f"exports/{user_id}/{uuid.uuid4()}.zip"
    loop = asyncio.get_running_loop()

    # Best-effort provisioning: a private bucket so objects are reachable only
    # via a signed URL. Swallow "already exists" and any supabase-py signature
    # drift — a genuinely missing bucket surfaces on the upload below.
    def _ensure_bucket() -> None:
        try:
            supabase.storage.create_bucket(bucket, options={"public": False})
        except Exception as exc:
            # Already exists (the common case) or a supabase-py signature
            # difference — debug-only; a real missing bucket surfaces on upload.
            logger.debug("data-exports bucket ensure skipped: %s", exc)

    await loop.run_in_executor(None, _ensure_bucket)
    await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(bucket).upload(
            path=storage_path,
            file=zip_bytes,
            file_options={"content-type": "application/zip", "upsert": "true"},
        ),
    )
    res = await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(bucket).create_signed_url(storage_path, expires_in_seconds),
    )
    return _extract_signed_url(res)


def _build_export_link_email_text(download_url: str, expires_human: str) -> str:
    """Plain-text 'your data is ready' email with a download link + expiry."""
    return (
        "Hi,\n\n"
        "As requested, your personal data held by Spinr is ready to download:\n\n"
        f"  {download_url}\n\n"
        f"This secure link expires on {expires_human}. If it expires before you "
        "download it, just request a new export from the app.\n\n"
        "The download is a ZIP archive containing:\n"
        "  • README.txt — what each file contains\n"
        "  • account.csv, driver_profile.csv — your profile\n"
        "  • rides.csv — your trip history\n"
        "  • payouts.csv — your payout history\n"
        "  • documents.csv — your uploaded document records\n"
        "  • notification_preferences.csv — your notification settings\n"
        "  • raw_data.json — the complete machine-readable export\n\n"
        "If you have any questions about your data or would like to request "
        "deletion, please contact privacy@spinr.ca.\n\n"
        "— The Spinr Team"
    )


def _build_export_link_email_html(download_url: str, expires_human: str) -> str:
    """Lyft-style 'your data is ready' HTML email with a download button."""
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'max-width:520px;margin:0 auto;color:#1a1a1a;line-height:1.5">'
        '<h2 style="margin:0 0 12px">Your data export is ready</h2>'
        "<p>As requested, your personal data held by Spinr is ready to download.</p>"
        f'<p style="margin:20px 0"><a href="{download_url}" '
        'style="background:#FF3B30;color:#fff;text-decoration:none;padding:12px 22px;'
        'border-radius:8px;display:inline-block;font-weight:600">Download my data (ZIP)</a></p>'
        f'<p style="color:#555;font-size:13px">This secure link expires on '
        f"<strong>{expires_human}</strong>. If it expires before you download it, just "
        "request a new export from the app.</p>"
        "<p>The download contains spreadsheet (CSV) files you can open in Excel, Numbers, "
        "or Google Sheets, plus a README and the complete machine-readable JSON.</p>"
        '<p style="color:#555;font-size:13px">Questions about your data or want it deleted? '
        'Contact <a href="mailto:privacy@spinr.ca">privacy@spinr.ca</a>.</p>'
        '<p style="color:#888;font-size:12px">— The Spinr Team</p>'
        "</div>"
    )


@api_router.post("/me/export-data")
@dsar_export_limit
async def export_driver_data(
    background_tasks: BackgroundTasks,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """GDPR/PIPEDA: export all personal data for the authenticated driver.

    Immediately returns a confirmation message. The actual data collection,
    JSON generation, and email delivery happen in a background task so the
    driver does not wait.

    Rate-limited (@dsar_export_limit, 3/hour) — each call fans out DB reads, a
    ZIP build, a Storage upload, and an email. SlowAPI needs a parameter named
    ``request`` typed as starlette Request; do not remove it.
    """
    user_id = current_user["id"]
    # Export is delivered by email only — require a real address. Falling back
    # to the phone number (the old behaviour) would pass a raw phone number to
    # the email provider, which both fails to send and risks logging the number.
    email = (current_user.get("email") or "").strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="No email address on file to send the export to.")

    background_tasks.add_task(_build_and_email_data_export, user_id, email)
    return {"message": "Your data export is being prepared. Check your email."}


async def _build_and_email_data_export(user_id: str, email: str) -> None:
    """Background task: collect all driver data and email a JSON export."""
    import asyncio  # noqa: PLC0415

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
                db_supabase.get_rows(
                    "rides",
                    {"driver_id": driver_id},
                    limit=500,
                    order="created_at",
                    desc=True,
                ),
                db_supabase.get_rows(
                    "driver_payouts",
                    {"driver_id": driver_id},
                    limit=200,
                    order="created_at",
                    desc=True,
                ),
                db_supabase.get_rows("driver_documents", {"driver_id": driver_id}, limit=50),
            )
            rides = rides or []
            payouts = payouts or []
            documents = documents or []

        export_payload = {
            "export_generated_at": datetime.now(timezone.utc).isoformat() + "Z",
            "account": {k: v for k, v in user.items() if k not in _EXPORT_REDACT_ACCOUNT},
            "driver_profile": {k: v for k, v in driver.items() if k not in _EXPORT_REDACT_DRIVER},
            "rides": [{k: v for k, v in r.items() if k not in _EXPORT_REDACT_RIDE} for r in rides],
            "payouts": payouts,
            "documents": [{k: v for k, v in doc.items() if k != "document_url"} for doc in documents],
            "notification_preferences": notification_prefs,
        }

        # Lyft-style bundle: human-readable CSV files (one per data category)
        # plus a README and the complete machine-readable JSON, zipped together.
        # Drivers get spreadsheets they can open, not a raw JSON blob.
        now = datetime.now(timezone.utc)
        generated_on = now.strftime("%Y-%m-%d")
        zip_bytes = _build_export_zip(export_payload, generated_on)
        subject = "Your Spinr data export is ready"

        # Primary delivery: a time-limited signed download link (like Lyft) —
        # keeps PII out of the email body and lets a leaked link self-expire.
        try:
            expires_human = (now + timedelta(seconds=_EXPORT_LINK_TTL_SECONDS)).strftime("%B %d, %Y")
            download_url = await _upload_export_zip(user_id, zip_bytes, _EXPORT_LINK_TTL_SECONDS)
            # The URL is interpolated into an HTML href — refuse anything that
            # isn't a plain https URL so a malformed value can't break out of
            # the attribute. (Triggers the attachment fallback below.)
            if not download_url.startswith("https://"):
                raise ValueError(f"unexpected signed URL scheme: {download_url[:30]!r}")
            await send_email(
                to=email,
                subject=subject,
                body=_build_export_link_email_text(download_url, expires_human),
                html=_build_export_link_email_html(download_url, expires_human),
                email_type="dsar",
                recipient_user_id=user_id,
                log_id="dsar",
            )
            logger.info("Data export link emailed for user %s (expires %s)", user_id, expires_human)
        except Exception as link_exc:
            # Storage/link generation failed — fall back to attaching the ZIP so
            # the PIPEDA access request is still fulfilled. Logged loudly so the
            # storage problem gets fixed rather than masked.
            logger.error(
                "Data export link generation failed for user %s; falling back to attachment: %s",
                user_id,
                link_exc,
                exc_info=True,
            )
            filename = f"spinr-data-export-{generated_on}.zip"
            await send_email(
                to=email,
                subject=subject,
                body=(
                    "Hi,\n\n"
                    "As requested, your personal data held by Spinr is attached as a ZIP "
                    f'archive ("{filename}").\n\n'
                    "If you have any questions about your data or would like to request "
                    "deletion, please contact privacy@spinr.ca.\n\n"
                    "— The Spinr Team"
                ),
                html=_build_export_email_html(filename),
                attachments=[{"filename": filename, "content": zip_bytes, "mime": "application/zip"}],
                email_type="dsar",
                recipient_user_id=user_id,
                log_id="dsar",
            )
            logger.info("Data export emailed as attachment (fallback) for user %s", user_id)
        logger.info(
            "dsar_export_completed",
            extra={
                "user_id": user_id,
                "domain": "privacy",
                "metric": "dsar_export_completed",
            },
        )

    except Exception as exc:
        # Surface the full traceback and, for DatabaseError, the original DB
        # error (str(exc) alone yields only "Database operation failed").
        original = exc.details.get("original") if hasattr(exc, "details") and isinstance(exc.details, dict) else None
        logger.error(
            "Data export failed for user %s: %s%s",
            user_id,
            exc,
            f" — {original}" if original else "",
            exc_info=True,
        )


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


@api_router.get("/rides/history")
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
        from .rides import _redact_driver_location_fields
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

    return {"total": total, "rides": [serialize_doc(r) for r in rides]}


@api_router.post("/rides/{ride_id}/accept")
async def accept_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    if driver.get("status") == "suspended":
        raise AccountDisabledException(
            message="Your account is suspended. Please renew your documents to continue driving.",
            message_key=ErrorKeys.AUTH_ACCOUNT_SUSPENDED,
            action_hint="Contact support",
        )

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # A driver-user must never accept a ride they themselves created — prevents
    # self-dispatch fraud on dual-role accounts.
    if ride.get("rider_id") == current_user["id"]:
        raise HTTPException(status_code=403, detail="Cannot accept your own ride")

    # Subscription guard: if the ride's service area requires a Spinr Pass,
    # verify the driver has an active subscription before allowing acceptance.
    # This is the last-resort gate — go-online and dispatch already block
    # unsubscribed drivers, but a driver whose subscription expired mid-shift
    # (or who was grandfathered online before the policy was enabled) could
    # still reach this point.
    if ride.get("service_area_id"):
        try:
            _ride_area = await db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
            # Finding F: child areas (airport sub-regions) inherit subscription_required
            # from their parent; check parent when child flag is False.
            _accept_sub_required = bool(_ride_area and _ride_area.get("subscription_required"))
            if not _accept_sub_required and _ride_area and _ride_area.get("parent_service_area_id"):
                _parent = await db_supabase.find_one("service_areas", {"id": _ride_area["parent_service_area_id"]})
                _accept_sub_required = bool(_parent and _parent.get("subscription_required"))
            if _accept_sub_required:
                _active_sub = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows(
                        "driver_subscriptions",
                        {"driver_id": driver["id"], "status": "active"},
                        limit=1,
                    )
                )
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
        from ..utils.spinr_pass import assert_quota_available
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

    guard = await db.update_one(
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
        diag_logger.info(
            f"[ACCEPT] claim rejected ride_id={ride_id} "
            f"current_status={ride.get('status')} current_driver_id={ride.get('driver_id')}"
        )
        raise SpinrException(
            message="Ride already accepted by another driver",
            error_code=ErrorCode.RESOURCE_CONFLICT,
            status_code=409,
            message_key=ErrorKeys.RIDE_TAKEN,
            action_hint="Pick another ride",
        )

    # Re-read the now-claimed ride so we can notify the rider with fresh data.
    ride = await db.find_one("rides", {"id": ride_id})
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
    await record_period_transition(driver["id"], 2, ride_id=ride_id)

    # ── Batch dispatch: resolve offers for this ride ──────────────
    try:
        from ..repositories.driver_repo import update_acceptance_rate
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
            for lid in loser_ids:
                await db_supabase.set_driver_available(lid, True)
                await record_period_transition(lid, 1)
                try:
                    loser_drv = await db_supabase.get_driver_by_id(lid)
                    loser_uid = (loser_drv or {}).get("user_id")
                    if loser_uid:
                        await manager.send_personal_message(
                            {"type": "ride_taken", "ride_id": ride_id},
                            f"driver_{loser_uid}",
                        )
                except Exception as e:
                    logger.warning(f"Failed to send ride_taken WS to loser driver {lid}: {e}")
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
            await db.update_one(
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
        await manager.send_personal_message(
            {"type": "driver_accepted", "ride_id": ride_id}, f"rider_{ride['rider_id']}"
        )
        await send_push_notification(
            ride["rider_id"],
            "Driver Assigned! 🚗",
            "Your driver has accepted the ride and is on the way.",
            data={"type": "driver_accepted", "ride_id": str(ride_id)},
        )
    await manager.broadcast_ride_status(ride_id, RideStatus.DRIVER_ACCEPTED, rider_id=(ride or {}).get("rider_id"))

    # Start the rider's live activity (no-op until the app registers its token).
    if ride:
        asyncio.create_task(send_live_activity_update(ride, EVENT_START))

    return {"success": True}


@api_router.post("/rides/{ride_id}/decline")
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
        from ..repositories.driver_repo import update_acceptance_rate
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
    await record_period_transition(driver["id"], 1)
    await reset_miss_streak(driver["id"])

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
                "actor_id": driver["id"],
                "details": {"driver_id": driver["id"]},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as _e:
        logger.error(f"Could not log ride decline to audit_logs: {_e}", exc_info=True)

    # Cooldown: skip this driver for 5 minutes on the next dispatch cycle
    try:
        from ..utils.redis_client import redis_set as _redis_set  # type: ignore
    except ImportError:
        from utils.redis_client import redis_set as _redis_set  # type: ignore
    try:
        await _redis_set(f"spinr:offer_skip:{ride_id}:{driver['id']}", "1", ttl=300)
    except Exception as _e:
        logger.error(f"Could not set offer cooldown key for ride {ride_id}: {_e}", exc_info=True)

    # Early resolution: if no pending offers remain and ride is still
    # searching, re-dispatch immediately instead of waiting for batch timeout.
    try:
        import asyncio

        try:
            from .rides import match_driver_to_ride
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
                asyncio.create_task(match_driver_to_ride(ride_id))
                logger.info(f"[DECLINE] all offers resolved for ride {ride_id} — re-dispatching")
            else:
                logger.info(f"[DECLINE] ride {ride_id} still has {len(remaining.data)} pending offer(s)")
    except Exception as e:
        logger.error(f"Could not check/trigger re-match for ride {ride_id}: {e}", exc_info=True)

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

    guard = await db.update_one(
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
        await manager.send_personal_message({"type": "driver_arrived", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        asyncio.create_task(
            send_push_notification(
                ride["rider_id"],
                "Driver Arrived! 📍",
                "Your driver has arrived at the pickup location.",
                data={"type": "driver_arrived", "ride_id": str(ride_id)},
            )
        )
    await manager.broadcast_ride_status(ride_id, RideStatus.DRIVER_ARRIVED, rider_id=ride.get("rider_id"))

    # Update the rider's live activity to "driver arrived".
    asyncio.create_task(send_live_activity_update({**ride, "status": RideStatus.DRIVER_ARRIVED}, EVENT_UPDATE))

    return {"success": True}


@api_router.post("/rides/{ride_id}/verify-otp")
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
    guard = await db.update_one(
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
    await record_period_transition(driver["id"], 3, ride_id=ride_id)

    if ride.get("rider_id"):
        await manager.send_personal_message({"type": "ride_started", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        asyncio.create_task(
            send_push_notification(
                ride["rider_id"],
                "Ride Started! ▶️",
                "Your ride has started. Have a safe trip!",
                data={"type": "ride_started", "ride_id": str(ride_id)},
            )
        )
    await manager.broadcast_ride_status(ride_id, RideStatus.IN_PROGRESS, rider_id=ride.get("rider_id"))

    # Update the rider's live activity to "trip in progress".
    asyncio.create_task(send_live_activity_update({**ride, "status": RideStatus.IN_PROGRESS}, EVENT_UPDATE))

    return {"success": True}


@api_router.post("/rides/{ride_id}/start")
async def start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Start ride without OTP — disabled in production.

    In production all trip starts must go through POST /rides/{id}/verify-otp
    so the rider's presence is confirmed before the meter starts. The no-OTP
    path exists only as a dev/staging fallback (e.g. automated E2E tests).
    """
    try:
        from ..core.config import settings as _settings
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

    guard = await db.update_one(
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
    await record_period_transition(driver["id"], 3, ride_id=ride_id)

    if ride.get("rider_id"):
        await manager.send_personal_message({"type": "ride_started", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        asyncio.create_task(
            send_push_notification(
                ride["rider_id"],
                "Ride Started! ▶️",
                "Your ride has started. Have a safe trip!",
                data={"type": "ride_started", "ride_id": str(ride_id)},
            )
        )
    await manager.broadcast_ride_status(ride_id, RideStatus.IN_PROGRESS, rider_id=ride.get("rider_id"))
    # Update the rider's live activity to "trip in progress" (dev/staging path).
    asyncio.create_task(send_live_activity_update({**ride, "status": RideStatus.IN_PROGRESS}, EVENT_UPDATE))
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

    # B3.3: drain this driver's WS breadcrumb buffer before aggregating —
    # otherwise the last ~10s of the trip would miss the settled distance
    # and the SGI trail read below. Only meaningful when the driver's WS is
    # on THIS replica (which it is for the driver calling this endpoint via
    # the same affinity-LB'd session); a failed flush must not block
    # completion — the buffer's disconnect flush still covers the points.
    try:
        await flush_driver_breadcrumbs(driver["id"])
    except Exception:
        logger.error("[complete_ride] breadcrumb flush failed for driver %s", driver["id"], exc_info=True)

    # ── Aggregate all GPS breadcrumbs for this ride ──
    # On completion we compute everything once and store it on the ride row.
    # After this the admin dashboard reads from the ride row directly — no
    # need to join against driver_location_history for historical rides.
    planned_distance = ride.get("planned_distance_km") or ride.get("distance_km", 0) or 0
    actual_distance_km = planned_distance
    actual_distance_km_haversine: Optional[float] = None
    actual_distance_km_road: Optional[float] = None
    phase_distances: Dict[str, float] = {}
    phase_durations: Dict[str, int] = {}
    phase_polylines: Dict[str, list] = {}
    pickup_to_driver_km = 0.0
    route_polyline = []
    road_polyline: list = []
    road_polyline_pickup: list = []
    gps_points_count = 0
    trip_points_count = 0
    rejected_segments = 0
    max_segment_gap_s = 0
    road_distance_provider = "haversine_filtered"
    route_quality: Dict[str, Any] = {"confidence": "low", "reason": "no_gps_breadcrumbs"}
    route_geometry_status = "pending"
    route_geometry_error: Optional[str] = None

    try:
        # Page through ALL breadcrumbs for the ride (time-ordered). The old
        # single limit=1000 read dropped the tail of long, densely-sampled
        # trips (>~67 min at the 4s background cadence), under-reporting
        # billable/phase distance and the SGI trail. Bounded by a hard ceiling
        # so a pathological trail can't blow up settlement memory; the per-phase
        # and route polylines are downsampled below regardless of input size.
        _PAGE = 1000
        _MAX_BREADCRUMBS = 10000  # ~11 h at 4s — beyond any real single trip
        all_breadcrumbs = []
        _offset = 0
        while True:
            _page = await db_supabase.get_rows(
                "driver_location_history",
                {"ride_id": ride_id},
                order="timestamp",
                limit=_PAGE,
                offset=_offset,
            )
            if not _page:
                break
            all_breadcrumbs.extend(_page)
            if len(_page) < _PAGE or len(all_breadcrumbs) >= _MAX_BREADCRUMBS:
                break
            _offset += _PAGE
        if len(all_breadcrumbs) >= _MAX_BREADCRUMBS:
            logger.warning(
                f"GPS breadcrumbs hit ceiling {_MAX_BREADCRUMBS} for ride {ride_id}; tail beyond this is not summed"
            )
        all_breadcrumbs = [b for b in all_breadcrumbs if b.get("lat") is not None and b.get("lng") is not None]
        all_breadcrumbs.sort(key=lambda b: str(b.get("timestamp", "")))
        gps_points_count = len(all_breadcrumbs)

        if gps_points_count >= 2:
            # Compute per-phase distances (attribute each segment to the
            # current point's phase) and per-phase durations from the
            # timestamp deltas. Phase 1 (online_idle) is not expected
            # against a ride_id but tolerated if it shows up.
            # GPS sanity caps — reject segments that are physically impossible
            # before summing. Without these, a single tower-handoff jump on a
            # 7 km trip could inflate actual_distance_km to 90+ km (the
            # ingestion-time filter in utils/location_integrity.py is not
            # retroactively re-applied to stored breadcrumbs).
            #   MAX_SEG_KM      — single ping-to-ping displacement. Even at
            #                     240 km/h with a 30 s gap that's 2 km; 5 km
            #                     is well above any realistic value.
            #   MAX_SEG_KMH     — sustained ground speed. Saskatchewan max
            #                     posted is 110 km/h; allow 150 for downhill
            #                     /overtake transients before rejecting.
            #   MAX_SEG_GAP_S   — long gaps (background, signal loss) make
            #                     straight-line distance unreliable; treat
            #                     same as the duration cap.
            MAX_SEG_KM = 5.0
            MAX_SEG_KMH = 150.0
            MAX_SEG_GAP_S = 300
            rejected_segments = 0
            max_segment_gap_s = 0
            phase_totals: Dict[str, float] = {}
            phase_secs: Dict[str, float] = {}
            for i in range(1, len(all_breadcrumbs)):
                prev = all_breadcrumbs[i - 1]
                curr = all_breadcrumbs[i]
                phase = curr.get("tracking_phase") or "unknown"
                seg_km = calculate_distance(prev["lat"], prev["lng"], curr["lat"], curr["lng"])

                t_prev = parse_iso_utc(prev.get("timestamp"))
                t_curr = parse_iso_utc(curr.get("timestamp"))
                delta = None
                if t_prev and t_curr:
                    delta = (t_curr - t_prev).total_seconds()
                    if delta is not None and delta > max_segment_gap_s:
                        max_segment_gap_s = int(round(delta))

                # Reject anomalous segments before adding to phase totals.
                if seg_km > MAX_SEG_KM:
                    rejected_segments += 1
                    continue
                if delta is not None and delta > MAX_SEG_GAP_S:
                    rejected_segments += 1
                    continue
                if delta is not None and delta > 0:
                    seg_kmh = seg_km / (delta / 3600.0)
                    if seg_kmh > MAX_SEG_KMH:
                        rejected_segments += 1
                        continue

                phase_totals[phase] = phase_totals.get(phase, 0.0) + seg_km
                # Duration: only count if the gap is reasonable (< 5 min)
                # to avoid one stale breadcrumb inflating a phase by hours.
                if delta is not None and 0 < delta <= 300:
                    phase_secs[phase] = phase_secs.get(phase, 0.0) + delta
            if rejected_segments:
                logger.info(
                    f"Ride {ride_id}: dropped {rejected_segments}/{len(all_breadcrumbs) - 1} "
                    "GPS segments as anomalous (speed/distance/gap caps)"
                )
            phase_distances = {k: round(v, 3) for k, v in phase_totals.items()}
            phase_durations = {k: int(round(v)) for k, v in phase_secs.items()}

            # Actual distance = trip_in_progress only (the paid portion).
            # Guard against sparse GPS: if fewer than 5 trip_in_progress
            # points were recorded the haversine sum is essentially just
            # a straight-line from pickup to dropoff (equivalent to the
            # booking-time haversine). In that case keep planned_distance
            # so the displayed km matches what the fare was calculated on,
            # rather than showing a misleadingly short GPS value.
            trip_points_count = sum(1 for b in all_breadcrumbs if b.get("tracking_phase") == "trip_in_progress")
            actual_distance_km = round(phase_distances.get("trip_in_progress", 0.0), 2)
            if trip_points_count < 5:
                logger.warning(
                    f"Ride {ride_id}: only {trip_points_count} trip_in_progress GPS points "
                    f"— GPS data too sparse for accurate distance; keeping planned={planned_distance}km"
                )
                actual_distance_km = planned_distance
            elif actual_distance_km == 0:
                # >= 5 points recorded but every segment was rejected by the
                # speed/distance/gap caps (e.g. GPS dead zone, spoofed trace).
                logger.warning(
                    f"Ride {ride_id}: {trip_points_count} trip_in_progress GPS points but all "
                    f"segments rejected by anomaly filter — keeping planned={planned_distance}km"
                )
                actual_distance_km = planned_distance

            pickup_to_driver_km = round(phase_distances.get("navigating_to_pickup", 0.0), 2)

            # Road-snapped recompute (P2): the haversine sum above is already
            # spike-protected by the speed/distance/gap caps, but it still
            # approximates straight-line distance between consecutive pings,
            # missing road curvature and turns. Roads API snapToRoads with
            # interpolate=true gives us the actual road-network distance and
            # respects the driver's chosen route (detours included), so it's
            # the structurally correct billable distance.
            #
            # When the recompute succeeds AND its value is within sanity range
            # of the haversine baseline (1/3× to 3×), it wins. Otherwise we
            # log the discrepancy and stick with the haversine value — Maps
            # outage or an empty response can't be allowed to corrupt billing.
            actual_distance_km_haversine = actual_distance_km
            actual_distance_km_road = None
            road_result = None
            try:
                try:
                    from ..utils.route_distance import compute_road_route
                except ImportError:
                    from utils.route_distance import compute_road_route  # type: ignore
                road_result = await compute_road_route(all_breadcrumbs)
            except Exception:
                logger.warning(
                    "[complete_ride] road-snap recompute raised; keeping haversine",
                    exc_info=True,
                )
            if road_result is not None:
                actual_distance_km_road = road_result["distance_km"]
                lo = max(0.1, actual_distance_km_haversine / 3.0)
                hi = max(0.1, actual_distance_km_haversine * 3.0)
                if lo <= actual_distance_km_road <= hi:
                    actual_distance_km = round(actual_distance_km_road, 2)
                    road_distance_provider = str(road_result.get("provider") or "road_snapped")
                    # Trusted match → persist the road-snapped geometry (saved to
                    # ride_routes below) for SGI / dispute map review.
                    road_polyline = road_result.get("polyline") or []
                else:
                    logger.warning(
                        f"Ride {ride_id}: road-snap distance {actual_distance_km_road}km "
                        f"out of sanity range [{lo:.2f}, {hi:.2f}] from haversine "
                        f"{actual_distance_km_haversine}km — keeping haversine value"
                    )

            # The PICKUP-leg road-snap (Phase 2, driver→pickup) is display-only
            # (admin map) and does NOT feed billing, so it must NOT add a second
            # provider round-trip to the /complete hot path. It is backgrounded
            # after settlement (see _snap_pickup_leg_async below); road_polyline_pickup
            # stays empty here and the column is backfilled best-effort.

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

            # Trip-leg polyline for the static map snapshot (route_snapshot_url)
            # ONLY — kept in-memory, not persisted to rides (geometry now lives in
            # ride_routes). [[lat, lng, phase], ...]. The navigating_to_pickup leg
            # is excluded: the receipt map shows the travelled pickup→dropoff
            # route only (drawing both legs read as two routes on the snapshot).
            trip_points = [b for b in all_breadcrumbs if b.get("tracking_phase") == "trip_in_progress"]
            if trip_points:
                MAX_POINTS = 200
                step = max(1, len(trip_points) // MAX_POINTS)
                sampled = trip_points[::step]
                if sampled and sampled[-1] is not trip_points[-1]:
                    sampled.append(trip_points[-1])
                route_polyline = [
                    [
                        round(p["lat"], 6),
                        round(p["lng"], 6),
                        p.get("tracking_phase", ""),
                    ]
                    for p in sampled
                ]

            rejected_ratio = rejected_segments / max(1, len(all_breadcrumbs) - 1)
            if trip_points_count >= 20 and rejected_ratio <= 0.1 and max_segment_gap_s <= 120:
                confidence = "high"
            elif trip_points_count >= 5 and rejected_ratio <= 0.25 and max_segment_gap_s <= 300:
                confidence = "medium"
            else:
                confidence = "low"
            route_quality = {
                "confidence": confidence,
                "gps_points_count": gps_points_count,
                "trip_points_count": trip_points_count,
                "rejected_segments": rejected_segments,
                "rejected_segment_ratio": round(rejected_ratio, 3),
                "max_segment_gap_seconds": max_segment_gap_s,
                "distance_provider": road_distance_provider,
                "actual_distance_km_haversine": (
                    round(float(actual_distance_km_haversine), 3) if actual_distance_km_haversine is not None else None
                ),
                "actual_distance_km_road_snapped": (
                    round(float(actual_distance_km_road), 3) if actual_distance_km_road is not None else None
                ),
                "road_snap_accepted": bool(road_polyline),
            }

    except Exception as e:
        logger.error(f"Could not aggregate GPS data for ride {ride_id}: {e}", exc_info=True)

    # Persist the heavy route geometry to the ride_routes side-table (1:1),
    # keeping it OFF the hot rides row — written once here, read on-demand by the
    # admin map modal. Best-effort: a settlement must not fail on the side write.
    route_payload = {
        "phase_distances": phase_distances,
        "phase_durations": phase_durations,
        "phase_polylines": phase_polylines,
        "road_polyline": road_polyline,
        "road_polyline_pickup": road_polyline_pickup,
        "gps_points_count": gps_points_count,
        "route_quality": route_quality,
        "save_status": "saved",
        "save_error": None,
        "computed_at": datetime.now(timezone.utc),
    }
    for attempt in range(1, 4):
        try:
            await db_supabase.update_one(
                "ride_routes",
                {"ride_id": ride_id},
                route_payload,
                upsert=True,
            )
            route_geometry_status = "saved"
            route_geometry_error = None
            break
        except Exception as exc:
            route_geometry_error = str(exc)[:500]
            route_geometry_status = "failed"
            logger.error(
                "Could not persist ride_routes for ride %s (attempt %s/3): %s",
                ride_id,
                attempt,
                route_geometry_error,
                exc_info=True,
            )
            if attempt < 3:
                await asyncio.sleep(0.2 * attempt)
    if route_geometry_status != "saved":
        try:
            await db_supabase.update_one(
                "rides",
                {"id": ride_id},
                {
                    "route_geometry_status": route_geometry_status,
                    "route_geometry_error": route_geometry_error,
                    "route_quality": route_quality,
                },
            )
        except Exception:
            logger.error("Could not record ride_routes persistence failure for ride %s", ride_id, exc_info=True)

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
        "status": RideStatus.COMPLETED,
        "ride_completed_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "planned_distance_km": planned_distance,
        "actual_distance_km": actual_distance_km,
        "pickup_to_driver_km": pickup_to_driver_km,
        "phase_distances": phase_distances,
        "phase_durations": phase_durations,
        "gps_points_count": gps_points_count,
        "route_quality": route_quality,
        "route_geometry_status": route_geometry_status,
        # Clear any previous failed-save message when a retry/re-completion saves
        # ride_routes successfully; otherwise admin list/detail can show a stale
        # error while the status is now "saved".
        "route_geometry_error": route_geometry_error,
    }

    # Check fare lock setting — when enabled, rider pays the booking-time
    # estimate regardless of actual distance/time (SK regulatory requirement).
    _fare_lock = False
    try:
        try:
            from ..settings_loader import get_app_settings as _gas
        except ImportError:
            from settings_loader import get_app_settings as _gas  # type: ignore
        _fl_settings = await _gas()
        _fare_lock = (_fl_settings or {}).get("fare_lock_enabled", False)
    except Exception:
        logger.debug("fare_lock_enabled check failed, defaulting to False", exc_info=True)

    if _fare_lock:
        update_fields["distance_km"] = actual_distance_km
        logger.info(
            f"Ride {ride_id}: fare_lock active — keeping booking-time fare. "
            f"planned={planned_distance}km actual={actual_distance_km}km"
        )
    elif actual_distance_km > 0 and abs(actual_distance_km - planned_distance) > 0.1:
        fare_adj = recalculate_fare_for_distance(ride, actual_distance_km)
        if fare_adj:
            update_fields.update(fare_adj)
            logger.info(
                f"Ride {ride_id}: fare recalculated on completion. "
                f"planned={planned_distance}km actual={actual_distance_km}km"
            )
    else:
        update_fields["distance_km"] = actual_distance_km

    # ── ride_metrics consolidated planned-vs-actual summary ──
    # Read cache assembled from existing data sources at completion:
    #   * Pickup-leg estimates: already written into ride.ride_metrics at acceptance.
    #   * Per-phase actual durations: derived from ride timestamps (the same
    #     transitions that drive driver_insurance_periods).
    #   * Distances: pickup leg from phase_distances/pickup_to_driver_km; trip
    #     leg from planned_distance_km / actual_distance_km.
    # driver_insurance_periods remains the regulatory system of record for
    # timing; this column is a read cache for the rider-app / admin detail UI.
    # Failure here must not block ride completion.
    try:

        def _minutes_between(start, end) -> int | None:
            start_dt = parse_iso_utc(start) if isinstance(start, str) else start
            end_dt = parse_iso_utc(end) if isinstance(end, str) else end
            if not start_dt or not end_dt:
                return None
            secs = (end_dt - start_dt).total_seconds()
            if secs <= 0:
                return None
            return max(1, int(round(secs / 60)))

        nav_minutes = _minutes_between(ride.get("driver_accepted_at"), ride.get("driver_arrived_at"))
        wait_minutes = _minutes_between(ride.get("driver_arrived_at"), ride.get("ride_started_at"))
        trip_minutes = _minutes_between(ride.get("ride_started_at"), update_fields["ride_completed_at"])

        existing_metrics = ride.get("ride_metrics") or {}
        nav_phase = dict((existing_metrics.get("phases") or {}).get("navigating_to_pickup") or {})
        # Actuals overwrite anything previously written; estimates are preserved.
        nav_phase["actual_distance_km"] = round(float(pickup_to_driver_km or 0), 3)
        if nav_minutes is not None:
            nav_phase["actual_duration_minutes"] = nav_minutes

        trip_phase: Dict[str, Any] = {
            "estimated_distance_km": round(float(planned_distance or 0), 3),
            "estimated_duration_minutes": int(ride.get("duration_minutes") or 0) or None,
            "actual_distance_km": round(float(actual_distance_km or 0), 3),
            # P2: ops visibility — both raw computations stored so a regression
            # in either path (haversine spike filter, or Roads API outage) can
            # be diagnosed from a single ride row. actual_distance_km above is
            # the canonical billed value.
            "actual_distance_km_haversine": (
                round(float(actual_distance_km_haversine), 3) if actual_distance_km_haversine is not None else None
            ),
            "actual_distance_km_road_snapped": (
                round(float(actual_distance_km_road), 3) if actual_distance_km_road is not None else None
            ),
        }
        if trip_minutes is not None:
            trip_phase["actual_duration_minutes"] = trip_minutes
        # Drop the estimate keys when we genuinely have no value, rather than
        # writing 0 / None that the UI would render as "0 km" / "0 min".
        trip_phase = {k: v for k, v in trip_phase.items() if v not in (None, 0, 0.0)}

        wait_phase: Dict[str, Any] = {}
        if wait_minutes is not None:
            wait_phase["actual_duration_minutes"] = wait_minutes

        phases: Dict[str, Any] = {"navigating_to_pickup": nav_phase, "trip_in_progress": trip_phase}
        if wait_phase:
            phases["arrived_at_pickup"] = wait_phase

        def _sum(field: str) -> float | int | None:
            vals = [p.get(field) for p in phases.values() if p.get(field) is not None]
            if not vals:
                return None
            if all(isinstance(v, int) for v in vals):
                return sum(vals)
            return round(sum(float(v) for v in vals), 3)

        totals = {
            "estimated_distance_km": _sum("estimated_distance_km"),
            "estimated_duration_minutes": _sum("estimated_duration_minutes"),
            "actual_distance_km": _sum("actual_distance_km"),
            "actual_duration_minutes": _sum("actual_duration_minutes"),
        }
        totals = {k: v for k, v in totals.items() if v is not None}

        update_fields["ride_metrics"] = {"phases": phases, "totals": totals}
    except Exception as metrics_err:
        logger.error(
            "complete_ride: ride_metrics assembly failed for ride %s: %s",
            ride_id,
            metrics_err,
            exc_info=True,
        )

    # Atomic completion guard: filter on status=in_progress so a concurrent
    # complete/cancel that won the race after the read above matches zero rows
    # instead of writing a second completion (same CAS pattern as ride
    # acceptance filtering on status='searching').
    _complete_filters = {"id": ride_id, "driver_id": driver["id"], "status": RideStatus.IN_PROGRESS}
    try:
        _updated_ride_row = await db_supabase.update_one("rides", _complete_filters, update_fields)
    except Exception as e:
        # Some columns may not exist yet in older deployments. Retry with only
        # the essential fields so ride completion never fails. run_sync wraps the
        # PostgREST error, so match the real text + structured code, not str(e)
        # (the generic "Database operation failed" sentinel never contains it).
        err_msg = db_error_text(e)
        if pg_error_code(e).upper() == "PGRST204" or "column" in err_msg or "pgrst204" in err_msg:
            logger.warning(f"Retrying ride update with minimal fields: {e}")
            safe_keys = {
                "status",
                "ride_completed_at",
                "payment_status",
                "updated_at",
                "distance_km",
            }
            safe_updates = {k: v for k, v in update_fields.items() if k in safe_keys}
            _updated_ride_row = await db_supabase.update_one("rides", _complete_filters, safe_updates)
        else:
            raise
    if _updated_ride_row is None:
        raise RideStateError(
            f"Ride {ride_id} is no longer in_progress — completion already processed by a concurrent request"
        )

    # ── Record incentive claims ──────────────────────────────────
    # Fetch active incentives matching this ride's service area / vehicle type
    # and insert claim records into ride_incentive_claims. driver_earnings in
    # the rides table is NOT updated here — it holds the fare-only amount
    # (base + distance + time). The bonus is summed from ride_incentive_claims
    # at read time by get_ride() and exposed as incentive_amount / total_earned.
    _total_bonus = Decimal("0")
    try:
        sa_id = ride.get("service_area_id")
        vt_id = ride.get("vehicle_type_id")
        iq = (
            db_supabase.supabase.table("ride_incentives")
            .select("id, bonus_amount, vehicle_type_id")
            .eq("is_active", True)
        )
        if sa_id:
            iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")
        else:
            iq = iq.is_("service_area_id", "null")
        inc_result = await db_supabase.run_sync(iq.execute)
        for inc in inc_result.data or []:
            if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
                continue
            ba = Decimal(str(inc.get("bonus_amount") or 0))
            if ba <= 0:
                continue
            await db_supabase.insert_one(
                "ride_incentive_claims",
                {
                    "id": str(uuid.uuid4()),
                    "ride_id": ride_id,
                    "driver_id": driver["id"],
                    "incentive_id": inc["id"],
                    "bonus_amount": float(ba.quantize(Decimal("0.01"))),
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            _total_bonus += ba
        if _total_bonus > 0:
            logger.info(
                "complete_ride: claimed %s incentive bonus for ride %s (driver %s)",
                float(_total_bonus.quantize(Decimal("0.01"))),
                ride_id,
                driver["id"],
            )
    except Exception as inc_err:
        logger.error(
            "complete_ride: incentive claim failed for ride %s: %s",
            ride_id,
            inc_err,
            exc_info=True,
        )

    # ── Driver earnings snapshot (JSONB) ───────────────────────
    # Freeze the full breakdown so totals never drift from recomputation.
    # _total_bonus comes from the incentive block above; default to 0 if
    # the variable wasn't set (incentive block errored before assignment).
    try:
        _fare_d = (
            to_decimal(update_fields.get("base_fare") or ride.get("base_fare") or 0)
            + to_decimal(update_fields.get("distance_fare") or ride.get("distance_fare") or 0)
            + to_decimal(update_fields.get("time_fare") or ride.get("time_fare") or 0)
        )
        _snapshot = build_earnings_snapshot(
            fare=_fare_d,
            tip=ride.get("tip_amount") or 0,
            incentive=_total_bonus,
            tax=ride.get("tax_amount") or 0,
            cancel_fee=ride.get("cancellation_fee_driver") or 0,
        )
        await db_supabase.update_one("rides", {"id": ride_id}, {"driver_earnings_snapshot": _snapshot})
    except Exception:
        logger.error("complete_ride: driver_earnings_snapshot failed for ride %s", ride_id, exc_info=True)

    # Post-ride receipt notification stub
    rider = await db_supabase.get_user_by_id(ride.get("rider_id"))
    if rider and rider.get("email"):
        logger.info(f"Sending email receipt for ride {ride_id} (rider_id={rider.get('id')})")

    # Fire-and-forget: render the route PNG from phase_polylines and
    # upload to Supabase Storage so the admin drawer + email receipt can
    # embed a permanent image URL. Only regenerate if we have enough GPS
    # data to produce a meaningful route — otherwise preserve the planned-
    # route snapshot from creation (which used the Google Directions polyline).
    # A road-snapped trip geometry (sanity-gated above) always qualifies; a raw
    # breadcrumb trail needs >= 10 trip_in_progress points, mirroring the
    # renderer's own sparsity guard. Below that the Static Maps API draws
    # straight chords between the few points — the "straight line P→D next to
    # the real path" snapshot artifact — and the planned-route image from
    # creation is more accurate than the GPS trace.
    trip_points_for_snapshot = sum(
        1
        for b in (all_breadcrumbs if "all_breadcrumbs" in locals() else [])
        if b.get("tracking_phase") == "trip_in_progress"
    )
    has_gps_trail = bool(road_polyline) or trip_points_for_snapshot >= 10
    if has_gps_trail:
        # Prefer the road-snapped trip polyline: it follows the road network
        # even where raw GPS was sparse. phase_polylines is dropped in that
        # case so the renderer can't pick the raw trail over it.
        asyncio.create_task(
            _generate_and_store_ride_snapshot(
                ride_id=ride_id,
                pickup_lat=ride.get("pickup_lat"),
                pickup_lng=ride.get("pickup_lng"),
                dropoff_lat=ride.get("dropoff_lat"),
                dropoff_lng=ride.get("dropoff_lng"),
                phase_polylines=None if road_polyline else phase_polylines,
                route_polyline=road_polyline or route_polyline,
            )
        )
    else:
        logger.info(
            f"Ride {ride_id}: skipping completion snapshot ({trip_points_for_snapshot} GPS points) "
            "— planned-route snapshot from creation is preserved."
        )

    # Fire-and-forget: validate GPS trace against road network.
    # Flags spoofed trips for admin review without blocking completion.
    _breadcrumbs_for_validation = all_breadcrumbs if "all_breadcrumbs" in locals() else []
    asyncio.create_task(_validate_ride_route(ride_id, _breadcrumbs_for_validation, driver["id"]))
    # Fire-and-forget: road-snap the pickup leg for the admin map (display-only;
    # must not block completion on a provider round-trip).
    asyncio.create_task(_snap_pickup_leg_async(ride_id, _breadcrumbs_for_validation))

    # Update driver stats. Setting is_available=True is safe here because the
    # ride has just transitioned to `completed`, and the driver's row already
    # has is_online=True (a driver cannot be on an active trip while offline).
    # See update_driver_status docstring for the is_online/is_available invariant.
    await db_supabase.set_driver_available(driver["id"], available=True, total_rides_inc=1)
    # M-5: SGI insurance period audit — ride completed, driver returns to
    # period 1 (still online, no ride). No ride_id on period 1.
    await record_period_transition(driver["id"], 1)

    # Daily Spinr Pass allowance: if this completion used the driver's last ride
    # for the day, flip them offline now (DB-level, so dispatch stops offering)
    # until the allowance resets at the next local midnight. The driver WS
    # notice is sent at the end, after the ride_completed events, so the app
    # doesn't reset its completion UI prematurely.
    try:
        from ..utils.spinr_pass import force_offline_if_exhausted
    except ImportError:
        from utils.spinr_pass import force_offline_if_exhausted  # type: ignore
    try:
        _quota_offline = await force_offline_if_exhausted(driver)
    except Exception:
        _quota_offline = None
        logger.error("complete_ride: quota offline check failed for driver=%s", driver["id"], exc_info=True)

    completed_ride = await db_supabase.get_ride(ride_id)

    if completed_ride and completed_ride.get("rider_id"):
        rider_bill = completed_ride.get("grand_total") or completed_ride.get("total_fare", ride.get("total_fare", 0))
        await manager.send_personal_message(
            {
                "type": "ride_completed",
                "ride_id": ride_id,
                "total_fare": completed_ride.get("total_fare", ride.get("total_fare", 0)),
                "grand_total": rider_bill,
            },
            f"rider_{completed_ride['rider_id']}",
        )
        await send_push_notification(
            completed_ride["rider_id"],
            "Ride Completed! ✅",
            f"Your ride has finished. Total fare: ${rider_bill}",
            data={"type": "ride_completed", "ride_id": str(ride_id)},
        )

    total_fare = (completed_ride or {}).get("total_fare", ride.get("total_fare", 0))
    await manager.broadcast_ride_status(
        ride_id,
        RideStatus.COMPLETED,
        rider_id=(completed_ride or {}).get("rider_id"),
        total_fare=total_fare,
    )
    # End the rider's live activity on trip completion.
    asyncio.create_task(
        send_live_activity_update(completed_ride or {"id": ride_id, "status": RideStatus.COMPLETED}, EVENT_END)
    )
    # Keep the specific ``ride_completed`` event on admin too for dashboards
    # that switch directly on the event name rather than status.
    try:
        await manager.broadcast_to_admins({"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"complete_ride: admin broadcast failed: {_exc}")

    # Advance any active driver quests this completion contributes to. Runs once
    # per ride because the atomic in_progress→completed guard above lets only the
    # winning completion path reach here. Scheduled as a background task so the
    # per-quest queries/updates never block the completion response (the ride is
    # already completed); the tracker swallows its own errors internally.
    try:
        try:
            from ..utils.quest_tracker import update_quest_progress_on_ride_complete
        except ImportError:
            from utils.quest_tracker import update_quest_progress_on_ride_complete
        asyncio.create_task(update_quest_progress_on_ride_complete(driver["id"], completed_ride or ride))
    except Exception:
        logger.error("complete_ride: scheduling quest progress update failed for ride %s", ride_id, exc_info=True)

    # Notify the driver (and admins) if this completion took them offline for
    # the day. Sent last so the app handles ride_completed first. Reuses the
    # existing 'auto_offline' client handler (stops offer sound, flips offline).
    if _quota_offline and driver.get("user_id"):
        _reset_h = round(_quota_offline.get("hours_until_reset") or 0)
        try:
            await manager.send_personal_message(
                {
                    "type": "auto_offline",
                    "reason": "quota_exhausted",
                    "message": (
                        f"You've used all {_quota_offline.get('rides_per_day')} Spinr Pass rides for "
                        f"today. You're now offline — your allowance resets in about {_reset_h}h."
                    ),
                    "quota_resets_at": _quota_offline.get("quota_resets_at"),
                },
                f"driver_{driver['user_id']}",
            )
            await manager.broadcast_to_admins(
                {"type": "driver_status_changed", "driver_id": driver["id"], "is_online": False}
            )
        except Exception:
            logger.warning("complete_ride: quota auto_offline notify failed for driver=%s", driver["id"])
        # Push so the driver sees it even with the app backgrounded.
        try:
            await send_push_notification(
                driver["user_id"],
                "Daily ride limit reached",
                (
                    f"You've used all {_quota_offline.get('rides_per_day')} Spinr Pass rides for today. "
                    f"You're now offline — your allowance resets in about {_reset_h}h."
                ),
                data={"type": "quota_exhausted", "driver_id": str(driver["id"])},
                target_app="driver",
            )
        except Exception:
            logger.warning("complete_ride: quota push failed for driver=%s", driver["id"])

    return serialize_doc(completed_ride)


@api_router.post("/rides/{ride_id}/cancel")
async def cancel_ride(
    ride_id: str,
    reason: str = Query(""),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    # Prefer the reason from the JSON body so a free-text note never rides in the
    # URL (proxy/access logs leak query strings). Fall back to legacy ?reason=.
    _body_reason = None
    if request is not None:
        try:
            _b = await request.json()
            if isinstance(_b, dict):
                _body_reason = _b.get("reason")
        except Exception:
            _body_reason = None
    reason = (str(_body_reason).strip() if _body_reason else "") or reason

    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("status") in (RideStatus.IN_PROGRESS, RideStatus.COMPLETED):
        raise RideStateError(f"Cannot cancel a ride in state '{ride.get('status')}'")

    now = datetime.now(timezone.utc)
    base_update = {
        "status": RideStatus.CANCELLED,
        "cancelled_at": now,
        "updated_at": now,
    }

    # C2: atomic, status-guarded cancel. The in-memory check above can race —
    # between get_ride and the write the rider/driver can call verify-otp/start
    # and flip the ride to in_progress. An unguarded update_ride (filters on id
    # only) would then overwrite in_progress -> cancelled, violating "never cancel
    # after trip start" (Period 3 left open, fare settlement skipped, regulatory
    # trip log corrupted). Filtering on the pre-trip states matches zero rows once
    # the ride has started/ended -> 409, nothing mutated. Mirrors the rider cancel
    # path in routes/rides.py.
    _cancel_filter = {
        "id": ride_id,
        "status": {
            "$in": [
                "requested",
                RideStatus.SEARCHING,
                RideStatus.DRIVER_ASSIGNED,
                RideStatus.DRIVER_ACCEPTED,
                "en_route",
                RideStatus.DRIVER_ARRIVED,
            ]
        },
    }

    # Try to persist cancelled_by / cancellation_reason for audit. These
    # columns may not exist in older Supabase schemas — PGRST204 on an
    # unknown column would crash the whole cancel. Fall back to the
    # minimal (still status-guarded) update so the cancellation still succeeds.
    try:
        _claim = await db_supabase.update_one(
            "rides",
            _cancel_filter,
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
        _claim = await db_supabase.update_one("rides", _cancel_filter, base_update)

    if _claim is None:
        raise HTTPException(
            status_code=409,
            detail="Ride can no longer be cancelled (it has started or already ended)",
        )

    # Make driver available again
    await db_supabase.set_driver_available(driver["id"], True)
    # M-5: SGI insurance period audit — driver-side cancel after the
    # driver was assigned/accepted/arrived returns them to period 1.
    # If the ride was still in searching the driver was never in period
    # 2; skip to avoid a phantom 1→1 transition.
    if ride.get("status") in (
        RideStatus.DRIVER_ASSIGNED,
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
    ):
        await record_period_transition(driver["id"], 1)

    ride = await db_supabase.get_ride(ride_id)
    if ride and ride.get("rider_id"):
        await manager.send_personal_message(
            {"type": "ride_cancelled", "ride_id": ride_id, "reason": reason},
            f"rider_{ride['rider_id']}",
        )
        await send_push_notification(
            ride["rider_id"],
            "Ride Cancelled ❌",
            "Your driver has cancelled the ride.",
            data={"type": "ride_cancelled", "ride_id": str(ride_id)},
        )
    await manager.broadcast_ride_status(
        ride_id,
        RideStatus.CANCELLED,
        rider_id=(ride or {}).get("rider_id"),
        reason="driver_cancelled",
    )
    # End the rider's live activity on driver cancellation.
    asyncio.create_task(send_live_activity_update(ride or {"id": ride_id, "status": RideStatus.CANCELLED}, EVENT_END))
    # Keep the specific ``ride_cancelled`` event on admin for dashboards
    # that switch on event name.
    try:
        await manager.broadcast_to_admins({"type": "ride_cancelled", "ride_id": ride_id, "reason": "driver_cancelled"})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"driver cancel admin broadcast failed: {_exc}")

    return {"success": True}


@api_router.post("/rides/{ride_id}/noshow")
async def mark_rider_noshow(
    ride_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Driver marks rider as no-show after waiting at pickup.

    Requires: ride is in driver_arrived state, driver has waited at least
    noshow_wait_seconds (default 300 = 5 min) since arriving. Charges the
    rider a no-show fee and pays the driver.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=403, detail="Not your assigned ride")
    if ride.get("status") != RideStatus.DRIVER_ARRIVED:
        raise RideStateError("No-show can only be marked when driver has arrived")

    driver_arrived_at = ride.get("driver_arrived_at")
    if not driver_arrived_at:
        raise HTTPException(status_code=400, detail="Arrival time not recorded")

    if isinstance(driver_arrived_at, str):
        arrived_dt = datetime.fromisoformat(driver_arrived_at.replace("Z", "+00:00"))
    else:
        arrived_dt = driver_arrived_at
    if arrived_dt.tzinfo is None:
        arrived_dt = arrived_dt.replace(tzinfo=timezone.utc)

    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings() or {}
    area = None
    if ride.get("service_area_id"):
        area = await db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
    if area and area.get("noshow_wait_seconds") is not None:
        noshow_wait_seconds = int(area["noshow_wait_seconds"])
    else:
        noshow_wait_seconds = int(settings.get("noshow_wait_seconds", 300))

    waited = (datetime.now(timezone.utc) - arrived_dt).total_seconds()
    if waited < noshow_wait_seconds:
        remaining = int(noshow_wait_seconds - waited)
        raise HTTPException(
            status_code=400,
            detail=f"Must wait {remaining} more seconds before marking no-show",
        )

    # C2: atomically claim the no-show cancel (driver_arrived -> cancelled) BEFORE
    # charging anyone. No-show charges the rider and pays the driver; if the status
    # write were deferred until after the charge (and filtered on id only), a ride
    # that started in the race window would be BOTH charged a no-show fee AND
    # overwritten cancelled. The status-guarded claim matches zero rows once the
    # ride leaves driver_arrived -> 409, nothing charged. It also makes a duplicate
    # no-show call idempotent (no double charge).
    _noshow_now = datetime.now(timezone.utc)
    _noshow_claim = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "status": RideStatus.DRIVER_ARRIVED},
        {"status": RideStatus.CANCELLED, "cancelled_at": _noshow_now, "updated_at": _noshow_now},
    )
    if _noshow_claim is None:
        raise HTTPException(
            status_code=409,
            detail="Ride can no longer be marked no-show (it has started or already ended)",
        )

    try:
        from ..services.cancellation_service import (
            calculate_noshow_fee,
            pay_driver_cancellation_fee,
        )
    except ImportError:
        from services.cancellation_service import calculate_noshow_fee, pay_driver_cancellation_fee  # type: ignore

    fee_admin, fee_driver = calculate_noshow_fee(ride, settings, area)
    total_fee = fee_admin + fee_driver

    # Charge rider
    if total_fee > 0:
        payment_method = (ride.get("payment_method") or "card").lower()
        if payment_method == "wallet":
            rider_id = ride.get("rider_id")
            if rider_id:
                rider_wallet = await db_supabase.find_one("wallets", {"user_id": rider_id})
                if rider_wallet:
                    old_balance = Decimal(str(rider_wallet.get("balance", 0)))
                    new_balance = max(old_balance - total_fee, Decimal("0"))
                    actual_charge = old_balance - new_balance
                    if actual_charge > 0:
                        await db_supabase.update_one(
                            "wallets",
                            {"id": rider_wallet["id"]},
                            {
                                "balance": float(new_balance.quantize(Decimal("0.01"))),
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        await db_supabase.insert_one(
                            "wallet_transactions",
                            {
                                "id": str(uuid.uuid4()),
                                "wallet_id": rider_wallet["id"],
                                "user_id": rider_id,
                                "type": "noshow_fee",
                                "amount": -float(actual_charge.quantize(Decimal("0.01"))),
                                "balance_after": float(new_balance.quantize(Decimal("0.01"))),
                                "reference_id": ride_id,
                                "description": f"No-show fee for ride {ride_id[:8]}",
                                "metadata": {"ride_id": ride_id},
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )

    # Pay driver
    if fee_driver > 0:
        await pay_driver_cancellation_fee(
            ride_id=ride_id,
            driver_id=driver["id"],
            fee=fee_driver,
            actor_user_id=current_user["id"],
            ride_status_at_cancel="noshow",
        )

    # Status was already flipped to cancelled by the atomic claim above; here we
    # only persist the fee attribution. Writing these columns onto an already-
    # terminal cancelled ride is safe (cancelled cannot transition away), so an
    # id-only update is fine. Optional columns may not exist on older schemas.
    _fee_now = datetime.now(timezone.utc)
    try:
        await db_supabase.update_ride(
            ride_id,
            {
                "cancelled_by": "driver",
                "cancellation_type": "noshow",
                "cancellation_fee_admin": float(fee_admin.quantize(Decimal("0.01"))),
                "cancellation_fee_driver": float(fee_driver.quantize(Decimal("0.01"))),
                "updated_at": _fee_now,
            },
        )
    except Exception as exc:
        logger.warning(f"[NOSHOW] extended fields write failed; retrying minimal: {exc}")
        await db_supabase.update_ride(ride_id, {"updated_at": _fee_now})

    await db_supabase.set_driver_available(driver["id"], True)
    await record_period_transition(driver["id"], 1)

    rider_id = ride.get("rider_id")
    if rider_id:
        await manager.send_personal_message(
            {
                "type": "ride_cancelled",
                "ride_id": ride_id,
                "reason": "noshow",
                "noshow_fee": float(total_fee.quantize(Decimal("0.01"))),
            },
            f"rider_{rider_id}",
        )
        await send_push_notification(
            rider_id,
            "Ride Cancelled",
            f"Your driver waited but you didn't show up. A ${float(total_fee.quantize(Decimal('0.01'))):.2f} no-show fee has been charged.",
            data={"type": "ride_noshow", "ride_id": str(ride_id)},
        )

    await manager.broadcast_ride_status(
        ride_id,
        RideStatus.CANCELLED,
        rider_id=rider_id,
        reason="noshow",
    )
    try:
        await manager.broadcast_to_admins(
            {
                "type": "ride_noshow",
                "ride_id": ride_id,
                "fee": float(total_fee.quantize(Decimal("0.01"))),
            }
        )
    except Exception as _exc:
        logger.warning(f"noshow admin broadcast failed: {_exc}")

    return {
        "success": True,
        "noshow_fee_total": float(total_fee.quantize(Decimal("0.01"))),
        "noshow_fee_driver": float(fee_driver.quantize(Decimal("0.01"))),
    }


@api_router.post("/rides/{ride_id}/rate-rider")
async def rate_rider(
    ride_id: str,
    rating_data: RideRatingRequest,
    current_user: dict = Depends(get_current_user),
):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Authorization: a driver may only rate the rider on a ride they actually
    # drove. Without this guard any credentialed driver could overwrite the
    # rider_rating/rider_comment on any ride by supplying an arbitrary ride_id.
    # Return the SAME 404 for a ride owned by another driver as for a missing
    # ride, so a leaked/guessed ride_id can't be used to distinguish real rides
    # from nonexistent ones (matches the chat-status guard).
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=404, detail="Ride not found")

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


# Referral reward terms — single source of truth so the earnings calc, the
# per-referee progress, and the displayed terms can never drift apart.
REFERRAL_RIDES_REQUIRED = 10
REFERRAL_REWARD_AMOUNT = 10  # CAD, paid per referee who reaches the ride target
# Days the referee has to reach REFERRAL_RIDES_REQUIRED (from referral_applied_at)
# before the referral expires unpaid. 0 = no deadline. Per-area override lives in
# service_areas.driver_referral_window_days (migration 189).
REFERRAL_WINDOW_DAYS = 30


def _fmt_money(v) -> str:
    """Format a money amount for display copy: '10' for 10.00, '10.50' otherwise."""
    d = Decimal(str(v))
    return str(int(d)) if d == d.to_integral_value() else f"{d:.2f}"


def _driver_referral_codes(driver: dict) -> list:
    """Every code this driver may have been shared under — the current
    driver_code plus the legacy referral_code / DRIVER<id8> defaults — so
    referees who signed up with an older code still count in the summary."""
    out: list = []
    for c in (driver.get("driver_code"), driver.get("referral_code"), f"DRIVER{driver['id'][:8].upper()}"):
        if c and c not in out:
            out.append(c)
    return out


@api_router.get("/referral")
async def get_driver_referral_info(current_user: dict = Depends(get_current_user)):
    """Get driver's referral code and earnings from referrals."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # The shareable referral code IS the human-readable driver_code (DRV-XXXXXX)
    # when present — it's designed to be spoken/typed. Fall back to a stored
    # custom referral_code, then to the id-derived default for legacy rows that
    # predate driver_code (migration 156).
    codes = _driver_referral_codes(driver)
    referral_code = codes[0]  # primary code shown/shared (driver_code when present)

    # Find users who used ANY of this driver's codes (incl. legacy ones) so a
    # referrer doesn't lose progress for referees who applied an older code.
    # Only the id is used below (to look up each referred user's driver row),
    # so project it and keep base64 profile_image out of the read.
    referred_users = await db_supabase.get_rows(
        "users", {"referral_code_used": {"$in": codes}}, columns="id", limit=100
    )

    # Reward terms follow this driver's assigned service area (global default
    # when unassigned). The ride threshold is per-area, so resolve before the
    # qualification loop.
    terms = await resolve_referral_terms(driver.get("service_area_id"), "driver")
    rides_required = terms["rides"]
    reward_amount = terms["referrer"]

    # A referral pays out once the referred driver completes rides_required
    # rides; until then it's "in progress". Earnings are the sum of qualified ones.
    total_referrals = len(referred_users)
    qualified_referrals = 0

    for user in referred_users:
        # Check if user became a driver and completed rides
        referred_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
        )
        if referred_driver:
            completed_rides = await db_supabase.count_documents(
                "rides",
                {"driver_id": referred_driver["id"], "status": RideStatus.COMPLETED},
            )
            if completed_rides >= rides_required:
                qualified_referrals += 1

    # Earned total: prefer the snapshotted sum of PAID payouts so it never changes
    # retroactively when area terms or the driver's area change; fall back to the
    # estimate (reward × qualified) until the payout loop has actually paid this
    # driver (the loop runs every 5 min, so the snapshot wins shortly after a
    # referee qualifies).
    paid = await paid_referral_earnings(current_user["id"], "driver")
    referral_earnings = paid if paid is not None else (reward_amount * qualified_referrals)

    # Who referred THIS driver (inbound). users.referred_by holds the referrer's
    # DRIVER id for driver referrals; resolve to a name + code. None if this
    # driver wasn't referred (or was referred via a rider code → not a driver row).
    me = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("users", {"id": current_user["id"]}, columns="referred_by", limit=1)
    )
    referred_by = None
    ref_drv_id = (me or {}).get("referred_by")
    if ref_drv_id:
        ref_drv = await db_supabase.get_driver_by_id(ref_drv_id)
        if ref_drv:
            ref_user = await db_supabase.get_user_by_id(ref_drv.get("user_id")) if ref_drv.get("user_id") else None
            referred_by = {
                "name": f"{(ref_user or {}).get('first_name', '')} {(ref_user or {}).get('last_name', '')}".strip()
                or ref_drv.get("name")
                or "Driver",
                "code": _driver_referral_codes(ref_drv)[0],
            }

    return {
        "referral_code": referral_code,
        "referral_link": f"https://spinr.app/join/{referral_code}",
        "total_referrals": total_referrals,
        "qualified_referrals": qualified_referrals,
        "pending_referrals": total_referrals - qualified_referrals,
        # Money serialised as 2-dp strings (house convention; clients parseFloat).
        "referral_earnings": _money_str(referral_earnings),
        "reward_amount": _money_str(reward_amount),
        # Both sides' reward amounts so the app shows who earns what. referee=0 for
        # drivers (no signup bonus) → app renders "$0 = that party earns nothing".
        "referrer_reward": _money_str(reward_amount),
        "referee_reward": _money_str(terms["referee"]),
        "referred_by": referred_by,
        "rides_required": rides_required,
        # Admin-authored per-area T&C wins; otherwise generate the default
        # sentence from this area's reward numbers.
        "terms": terms.get("terms")
        or (
            f"Earn ${_fmt_money(reward_amount)} for each driver who signs up with your code "
            f"and completes {rides_required} rides."
        ),
    }


@api_router.post("/referral/apply")
async def apply_referral_code(req: ApplyReferralCodeRequest, current_user: dict = Depends(get_current_user)):
    """Apply a referral code during driver onboarding."""
    code = req.referral_code.strip().upper()

    # Check if user already has a referral code applied
    user = await db_supabase.get_user_by_id(current_user["id"])
    if user and user.get("referral_code_used"):
        raise HTTPException(status_code=400, detail="Referral code already applied")

    # Resolve the referrer. The primary shareable code is the human-readable
    # driver_code (DRV-XXXXXX) shown in the profile / referral screen; also
    # accept a stored custom referral_code for backward compatibility.
    ref_driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"driver_code": code}, limit=1)
    )
    if not ref_driver:
        ref_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"referral_code": code}, limit=1)
        )
    if not ref_driver:
        # Fallback for the auto-generated default code, which is
        # "DRIVER" + the first 8 chars of the driver id, upper-cased
        # (see get_driver_referral_info). Resolve it back to the driver.
        #
        # NOTE: _apply_filters maps {"$regex": x} onto a SQL LIKE/ILIKE
        # pattern (%x%), NOT a real regex — so the previous ".*<id>.*"
        # value was matched *literally* (looking for ".*" inside the id)
        # and never hit. Pass the bare 8-char token and set $options:"i"
        # for a case-insensitive contains match (the code upper-cases the
        # lower-case hex id, so a case-sensitive match would also miss).
        potential_id = code.replace("DRIVER", "")
        if len(potential_id) == 8 and potential_id.isalnum():
            try:
                ref_driver = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows(
                        "drivers",
                        {"id": {"$regex": potential_id, "$options": "i"}},
                        limit=1,
                    )
                )
            except Exception as e:
                logger.warning(f"Referral default-code fallback lookup failed: {e}")

    if not ref_driver:
        raise HTTPException(status_code=404, detail="Invalid referral code")

    # Block self-referral — a driver can't refer themselves (now that the code
    # can be entered at signup, this is an easy thing to try).
    if ref_driver.get("user_id") == current_user["id"]:
        raise HTTPException(status_code=400, detail="You can't use your own referral code")

    # Apply referral code to user
    await db_supabase.update_one(
        "users",
        {"id": current_user["id"]},
        {
            "referral_code_used": code,
            "referred_by": ref_driver["id"],
            # Recorded so the payout loop only rewards rides completed AFTER this.
            "referral_applied_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"success": True, "referral_code": code}


@api_router.get("/referrals")
async def get_referred_drivers(
    limit: int = Query(50),
    offset: int = Query(0),
    current_user: dict = Depends(get_current_user),
):
    """Get list of drivers referred by current driver."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Match every code this driver may have been shared under (incl. legacy)
    # so referees who applied an older code still appear in the list.
    codes = _driver_referral_codes(driver)

    # Use the viewing driver's area terms so the per-referee progress cards agree
    # with the summary endpoint (both follow the referrer's area). Actual payout
    # still uses each referee's own area; this list is an estimate.
    terms = await resolve_referral_terms(driver.get("service_area_id"), "driver")
    rides_required = terms["rides"]
    reward_amount = terms["referrer"]

    # Each referred user contributes name + email + signup date to the response
    # — project those columns and keep base64 profile_image out of the read.
    referred_users = await db_supabase.get_rows(
        "users", {"referral_code_used": {"$in": codes}}, columns="id,first_name,last_name,email,created_at", limit=100
    )

    referred_drivers = []
    for user in referred_users:
        referred_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
        )
        if referred_driver:
            # Get completed rides count
            completed_rides = await db_supabase.count_documents(
                "rides",
                {"driver_id": referred_driver["id"], "status": RideStatus.COMPLETED},
            )
            # Progress toward the reward: qualified once they hit the ride target.
            qualified = completed_rides >= rides_required
            rides_remaining = max(0, rides_required - completed_rides)
            referred_drivers.append(
                {
                    "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or "Driver",
                    "email": user.get("email", ""),
                    "referred_at": user.get("created_at", ""),
                    "total_trips": completed_rides,
                    # Reward-progress detail surfaced to the referrer.
                    "rides_required": rides_required,
                    "rides_remaining": rides_remaining,
                    "reward_amount": _money_str(reward_amount),
                    "qualified": qualified,
                    # "earned"  → reward unlocked (>= target rides)
                    # "in_progress" → started but not yet at target
                    "status": "earned" if qualified else "in_progress",
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
                {"driver_id": d_id, "status": RideStatus.COMPLETED},
                limit=1000,
            )
            period_rides = [
                r for r in rides if isinstance(r.get("created_at", ""), str) and r.get("created_at", "") >= start
            ]
        except Exception:
            period_rides = []

        total_rides = len(period_rides)
        total_earnings = sum(
            Decimal(str(r.get("base_fare") or 0))
            + Decimal(str(r.get("distance_fare") or 0))
            + Decimal(str(r.get("time_fare") or 0))
            + Decimal(str(r.get("tip_amount") or 0))
            for r in period_rides
        )
        total_tips = sum(Decimal(str(r.get("tip_amount") or 0)) for r in period_rides)

        user = await db.find_one("users", {"id": d.get("user_id")})
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Driver"

        rankings.append(
            {
                "driver_id": d_id,
                "name": name,
                "rides": total_rides,
                "earnings": _money_str(total_earnings),
                "tips": _money_str(total_tips),
                "rating": d.get("rating", 0),
                "is_current_user": d_id == driver["id"],
            }
        )

    # Sort by rides (primary), then earnings (secondary)
    rankings.sort(key=lambda x: (x["rides"], Decimal(x["earnings"])), reverse=True)

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

    requester_role = current_user.get("role", "")
    is_admin = requester_role in {"admin", "super_admin", "operations", "support"}
    is_self = driver.get("user_id") == current_user["id"]

    if is_admin or is_self:
        return serialize_doc(await _decrypt_driver_pii(driver))

    # Rider with an active ride assigned to this driver gets a safe public projection.
    active_ride = None
    try:
        rides = await db_supabase.get_rows(
            "rides",
            {
                "driver_id": driver_id,
                "rider_id": current_user["id"],
                "status": {"$in": list(RideStatus.active_statuses())},
            },
            limit=1,
        )
        active_ride = rides[0] if rides else None
    except Exception:
        logger.error("get_driver: active-ride check failed driver=%s", driver_id, exc_info=True)

    if not active_ride:
        raise HTTPException(status_code=403, detail="Not authorized")

    return {
        "id": driver["id"],
        "name": driver.get("name"),
        "rating": driver.get("rating"),
        "vehicle_make": driver.get("vehicle_make"),
        "vehicle_model": driver.get("vehicle_model"),
        "vehicle_color": driver.get("vehicle_color"),
        "license_plate": driver.get("license_plate"),
    }


@api_router.put("/{driver_id}/status")
async def update_driver_status(
    driver_id: str,
    # `embed=True` so FastAPI accepts `{"is_online": true}` as the JSON body
    # instead of interpreting a bare primitive as the whole body. Without the
    # explicit Body() wrapper, FastAPI treats non-Pydantic primitives as
    # query parameters, and the mobile client (which posts it as body) got
    # a 422 and surfaced it as "Request failed".
    is_online: bool = Body(..., embed=True),
    # Optional GPS coords sent by the driver app on Go Online. Closes the
    # window between the status flip and the first /drivers/location-batch
    # POST during which the driver would otherwise sit at the registration
    # default (0, 0) and be invisible to riders/admins.
    lat: Optional[float] = Body(None, embed=True),
    lng: Optional[float] = Body(None, embed=True),
    current_user: dict = Depends(get_current_user),
):
    """Toggle the driver's online flag (driver-facing "Go online" / "Go offline").

    Driver online/available flag contract
    -------------------------------------
    ``is_online``
        Driver-toggled. ``True`` when the driver has tapped "Go online".
        Independent of ride state; remains ``True`` while the driver is on
        an active trip.
    ``is_available``
        System-computed. ``True`` only when (``is_online`` AND not currently
        on an active ride AND not in offer-pending state). Used by dispatch
        to decide who to offer the next ride to.

    Invariant: ``is_available == True`` implies ``is_online == True``. The
    inverse does NOT hold — an online driver mid-trip is ``is_online=True,
    is_available=False``. Dispatch reads ``is_available``; admin filters
    typically read ``is_online``. Never set ``is_available = True`` without
    ``is_online = True``.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    if driver.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Ban check: prevent banned drivers from going online
    if is_online and driver.get("status") == "banned":
        raise AccountDisabledException(
            message="Your account has been permanently suspended due to policy violations.",
            message_key=ErrorKeys.AUTH_ACCOUNT_SUSPENDED,
            action_hint="Contact support",
        )
    if is_online and driver.get("status") == "suspended":
        raise AccountDisabledException(
            message="Your account is currently suspended. Please contact support.",
            message_key=ErrorKeys.AUTH_ACCOUNT_SUSPENDED,
            action_hint="Contact support",
        )
    if is_online and driver.get("status") == "needs_review":
        raise SpinrException(
            message="Your account is under review. Please wait for admin approval before going online.",
            error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
            status_code=400,
            message_key=ErrorKeys.DRIVER_DOCUMENTS_PENDING,
            action_hint="Wait for verification",
        )
    if is_online and driver.get("status") not in ("active",):
        # Pending, rejected, or any unknown status
        if not driver.get("is_verified", False) and driver.get("status") != "active":
            raise SpinrException(
                message="Your driver profile has not been verified yet. Please wait for admin approval.",
                error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
                status_code=400,
                message_key=ErrorKeys.DRIVER_DOCUMENTS_PENDING,
                action_hint="Wait for verification",
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
                    "status": {
                        "$in": [
                            RideStatus.DRIVER_ACCEPTED,
                            RideStatus.DRIVER_ARRIVED,
                            RideStatus.IN_PROGRESS,
                        ]
                    },
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
                raise SpinrException(
                    message=f"{req_name} has expired. Please update your documents before going online.",
                    error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
                    status_code=400,
                    message_key=ErrorKeys.DRIVER_DOCUMENTS_EXPIRED,
                    action_hint="Renew documents in Profile",
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
                    raise SpinrException(
                        message=f"{label} has expired ({field}). Please update your documents before going online.",
                        error_code=ErrorCode.DRIVER_DOCUMENTS_PENDING,
                        status_code=400,
                        message_key=ErrorKeys.DRIVER_DOCUMENTS_EXPIRED,
                        action_hint="Renew documents in Profile",
                    )

        # is_verified check removed — status field is the single source of truth now.
        # Only status='active' drivers reach this point (blocked above).

        # Check active Spinr Pass subscription.
        # Enforcement triggers when EITHER:
        #   a) the global app setting `require_driver_subscription` is True, OR
        #   b) the driver's service area has `subscription_required=True`
        # When neither is set (default) we skip the DB query entirely — the
        # driver_subscriptions table may not exist yet pre-launch.
        try:
            from ..settings_loader import get_app_settings  # type: ignore
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        app_settings = await get_app_settings()
        require_sub = bool(app_settings.get("require_driver_subscription", False))

        if not require_sub and driver.get("service_area_id"):
            _driver_area = await db_supabase.find_one("service_areas", {"id": driver["service_area_id"]})
            if _driver_area and _driver_area.get("subscription_required"):
                require_sub = True
            # Finding F: inherit subscription_required from parent area (airport sub-regions
            # should require the same pass as the parent city area).
            elif _driver_area and _driver_area.get("parent_service_area_id"):
                _parent_area = await db_supabase.find_one(
                    "service_areas", {"id": _driver_area["parent_service_area_id"]}
                )
                if _parent_area and _parent_area.get("subscription_required"):
                    require_sub = True

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
                raise SpinrException(
                    message="You need an active Spinr Pass subscription to go online. Subscribe from your dashboard.",
                    error_code=ErrorCode.PAYMENT_FAILED,
                    status_code=402,
                    message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                    action_hint="Activate Spinr Pass",
                )

            # Check expiry on the active subscription row. parse_iso_utc
            # returns None on malformed values — we let those through rather
            # than blocking a driver from going online because of a data bug.
            if sub.get("expires_at"):
                exp = parse_iso_utc(sub["expires_at"])
                if exp is not None and exp < datetime.now(timezone.utc):
                    await db_supabase.update_one("driver_subscriptions", {"id": sub["id"]}, {"status": "expired"})
                    raise SpinrException(
                        message="Your Spinr Pass has expired. Please renew to go online.",
                        error_code=ErrorCode.PAYMENT_FAILED,
                        status_code=402,
                        message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                        action_hint="Activate Spinr Pass",
                    )

            # Plan scope enforcement: check vehicle_types and service_areas allowlists.
            # A null/empty list means all types/areas allowed.
            if sub.get("plan_id"):
                try:
                    plan = await db_supabase.find_one("subscription_plans", {"id": sub["plan_id"]})
                    allowed_vt = plan.get("vehicle_types") if plan else None
                    if allowed_vt:
                        driver_vt = driver.get("vehicle_type_id")
                        if driver_vt not in allowed_vt:
                            raise SpinrException(
                                message=(
                                    "Your vehicle type is not covered by your active Spinr Pass plan. "
                                    "Please switch to a compatible plan or update your vehicle."
                                ),
                                error_code=ErrorCode.PAYMENT_FAILED,
                                status_code=402,
                                message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                                action_hint="Update Spinr Pass",
                            )
                    # Finding C: service-area scope — a pass for another area must
                    # not satisfy enforcement in this driver's area.
                    # Also accept plans covering the parent area (child areas inherit).
                    allowed_areas = plan.get("service_areas") if plan else None
                    if allowed_areas and driver.get("service_area_id") not in allowed_areas:
                        if driver.get("service_area_id"):
                            _sa_row = await db_supabase.find_one("service_areas", {"id": driver["service_area_id"]})
                            _go_parent_id = (_sa_row or {}).get("parent_service_area_id")
                        else:
                            _go_parent_id = None
                        if not (_go_parent_id and _go_parent_id in allowed_areas):
                            raise SpinrException(
                                message=(
                                    "Your Spinr Pass plan does not cover this service area. "
                                    "Please subscribe to a plan for your area."
                                ),
                                error_code=ErrorCode.PAYMENT_FAILED,
                                status_code=402,
                                message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                                action_hint="Subscribe to Spinr Pass",
                            )
                except SpinrException:
                    raise
                except Exception as e:
                    logger.error(
                        "plan scope check failed for driver=%s plan=%s: %s",
                        driver_id,
                        sub.get("plan_id"),
                        e,
                        exc_info=True,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="Could not verify subscription plan restrictions. Please try again.",
                    ) from e

        # Daily ride-allowance gate — applies to ANY driver holding a finite
        # Spinr Pass, not only in pass-required areas. Once the pass's rides for
        # the local calendar day are used up the driver stays offline until the
        # next midnight, so they can't toggle back online and pull more offers.
        # assert_quota_available fetches the active pass itself, is a no-op for
        # unlimited / no-pass / rides-remaining, and fails open so a missing
        # table (pre-launch) never wrongly blocks a driver in a non-gated area.
        try:
            from ..utils.spinr_pass import assert_quota_available
        except ImportError:
            from utils.spinr_pass import assert_quota_available  # type: ignore
        # Anchor the quota day on the driver's service-area timezone (Regina
        # fallback) so the reset matches their local calendar day under DST.
        await assert_quota_available(driver_id, area_id=driver.get("service_area_id"))

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
    # If the driver app supplied current GPS on Go Online, persist it in the
    # same write so the rider/admin queries see a real location immediately
    # instead of the registration default (0, 0). Guarded on is_online so the
    # Go Offline path doesn't overwrite a perfectly good last-known location.
    if is_online and lat is not None and lng is not None and (lat != 0 or lng != 0):
        _base["lat"] = lat
        _base["lng"] = lng
    # Invariant guardrail: is_available => is_online (see handler docstring).
    # This is a sanity check on the payload we are about to write — never
    # gate user behaviour on the assert; if it ever trips, the bug is in the
    # logic that built _base, not in the driver's request.
    assert not (_base["is_available"] and not _base["is_online"]), "is_available implies is_online"
    status_flipped = bool(driver.get("is_online")) != bool(is_online)
    # Durable intent timestamps (migration 97). Only written on an actual
    # flip — re-tapping Go Online while already online does NOT bump
    # went_online_at; that timestamp is "when this intent began", not "last
    # refresh time". Readers compose these with the Redis presence key to
    # derive effective_online, replacing the retired presence_sweeper.
    _intent_payload = {}
    if status_flipped:
        _intent_payload["last_status_changed_at"] = _now_iso
        if is_online:
            _intent_payload["went_online_at"] = _now_iso
        else:
            _intent_payload["went_offline_at"] = _now_iso
    _payload = {**_base, **_intent_payload}
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
        _missing_intent = any(
            col in _combined for col in ("last_status_changed_at", "went_online_at", "went_offline_at", "pgrst204")
        )
        if _missing_intent:
            logger.warning(
                f"[GO-ONLINE] intent timestamp column(s) missing; retrying minimal. original={_detail or _col_exc}"
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

    # M-5: SGI insurance period audit — only on actual flips. Idempotent
    # toggles (driver re-asserts the same state) shouldn't open a new
    # period row; the helper's no-op branch would absorb it but we save
    # the round-trip by gating on status_flipped.
    if status_flipped:
        await record_period_transition(driver_id, 1 if is_online else 0)

    # Presence: Go Online is the strongest possible liveness signal — the
    # driver is actively using the app. Refresh the TTL now so dispatch can
    # see them immediately without waiting for the next WS heartbeat. Go
    # Offline clears the key so admin monitoring and dispatch drop them
    # from the pool without waiting for the 90 s TTL to expire.
    if is_online:
        await mark_present(driver_id)
        await reset_miss_streak(driver_id)
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

    # Filter by driver's vehicle type — only show plans that cover this vehicle.
    # A null/empty vehicle_types list means the plan accepts all types.
    if driver:
        driver_vt = driver.get("vehicle_type_id")
        plans = [p for p in plans if not p.get("vehicle_types") or driver_vt in p["vehicle_types"]]

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

    # Check if expired. parse_iso_utc always returns a UTC-aware datetime (or
    # None if unparseable), so the comparison below is aware-vs-aware. The old
    # code stripped tzinfo off `exp` and then compared it against an aware
    # `datetime.now(timezone.utc)`, which raised TypeError for EVERY pass (the
    # Postgres timestamptz always carries an offset). That exception was caught
    # and logged as a warning, so this whole expired-flip branch was dead code
    # and a lapsed-but-still-`active` row displayed as active with a quota.
    if sub.get("expires_at"):
        try:
            from ..utils.datetime_utils import parse_iso_utc  # type: ignore
        except ImportError:
            from utils.datetime_utils import parse_iso_utc  # type: ignore

        exp = parse_iso_utc(sub["expires_at"])
        if exp is None:
            # Unparseable expiry is a data-integrity problem, not something to
            # paper over by showing the pass as active — surface it loudly. The
            # go-online gate and the expiry sweeper still enforce independently.
            logger.error(
                "get_current_subscription: unparseable expires_at on active pass",
                extra={"subscription_id": sub.get("id")},
            )
            raise HTTPException(status_code=503, detail="Subscription state unavailable")
        if exp < datetime.now(timezone.utc):
            await db_supabase.update_one("driver_subscriptions", {"id": sub["id"]}, {"status": "expired"})
            return {
                "has_subscription": False,
                "subscription": None,
                "expired": True,
            }

    # Quota state for the current calendar day (America/Regina). The same
    # window powers go-online / dispatch / accept enforcement, so the numbers
    # the driver sees here match the ones enforced on the rides.
    try:
        from ..utils.spinr_pass import area_timezone, completed_today, compute_quota, hours_until
    except ImportError:
        from utils.spinr_pass import area_timezone, completed_today, compute_quota, hours_until  # type: ignore

    # Same service-area timezone the enforcement gates use, so the countdown the
    # driver sees matches when their rides actually reset. This is display only,
    # so a transient lookup error degrades to the Regina default rather than 500
    # the screen (enforcement, which must be exact, lets the error propagate).
    try:
        _tz = await area_timezone(driver.get("service_area_id"))
    except Exception:
        logger.warning("get_current_subscription: area timezone lookup failed; using default", exc_info=True)
        _tz = None
    today_rides = await completed_today(driver["id"], tz=_tz)
    quota = compute_quota(sub.get("rides_per_day", -1), today_rides, tz=_tz)

    return {
        "has_subscription": True,
        "subscription": sub,
        "today_rides": today_rides,
        "rides_remaining": quota["rides_remaining"],
        "can_accept_rides": quota["can_accept_rides"],
        # Quota refills (rides reset) at the next local midnight.
        "quota_resets_at": quota["quota_resets_at"],
        "hours_until_reset": quota["hours_until_reset"],
        # Pass itself ends (must renew) at expires_at.
        "hours_until_expiry": hours_until(sub.get("expires_at")),
    }


async def _cancel_stripe_subscription(stripe_subscription_id: str | None, *, raise_on_error: bool = False) -> None:
    """Cancel a Stripe Subscription so a recurring plan stops billing.

    No-op when there's no Stripe subscription (one-off / dev-mode rows) or
    Stripe isn't configured.

    ``raise_on_error`` controls failure handling:
      - False (default): best-effort. Used on plan-switch activation, where a
        paid replacement already exists and the reconcile loop /
        customer.subscription.* webhooks backstop a lingering old subscription;
        a failure must not block activation.
      - True: re-raise on failure. Used by the driver-initiated cancel so the
        caller can refuse to mark the row cancelled — otherwise the app would
        show "cancelled" while Stripe keeps billing (and a later invoice.paid
        would silently re-activate the row).
    """
    if not stripe_subscription_id:
        return
    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        # There IS a Stripe subscription to cancel (guarded above) but no key —
        # e.g. key removed mid-rotation. With raise_on_error the caller must NOT
        # mark the row cancelled while Stripe keeps billing, so fail loudly.
        if raise_on_error:
            raise RuntimeError("stripe_secret_key not configured — cannot cancel Stripe subscription")
        return
    try:
        stripe.Subscription.delete(stripe_subscription_id, api_key=stripe_secret)
        logger.info(f"[SUBSCRIBE] Stripe subscription {stripe_subscription_id} cancelled")
    except Exception:
        logger.exception(f"[SUBSCRIBE] Failed to cancel Stripe subscription {stripe_subscription_id}")
        if raise_on_error:
            raise


async def _compute_subscription_tax(driver_id: str, plan_price: Decimal) -> dict:
    """Return pre-tax subtotal plus per-component tax amounts for a subscription charge.

    Reads the driver's service area's subscription_tax_config (migration 185).
    Raises on DB error — callers at checkout time must propagate the failure
    so we never issue a Stripe session with silently-zeroed tax.
    """
    _ZERO = Decimal("0")
    _q2 = lambda v: v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # noqa: E731
    subtotal = _q2(plan_price)
    gst = pst = hst = _ZERO
    province = "SK"
    _drv = await db_supabase.find_one("drivers", {"id": driver_id})
    _area_id = (_drv or {}).get("service_area_id")
    if _area_id:
        _area = await db_supabase.find_one("service_areas", {"id": _area_id})
        _cfg = (_area or {}).get("subscription_tax_config") or {}
        if _cfg.get("enabled", True):
            province = str(_cfg.get("province", "SK") or "SK")
            gst = _q2(subtotal * Decimal(str(_cfg.get("gst_rate", 5) or 0)) / Decimal("100"))
            pst = _q2(subtotal * Decimal(str(_cfg.get("pst_rate", 6) or 0)) / Decimal("100"))
            hst = _q2(subtotal * Decimal(str(_cfg.get("hst_rate", 0) or 0)) / Decimal("100"))
    tax_total = gst + pst + hst
    return {
        "subtotal": subtotal,
        "gst_amount": gst,
        "pst_amount": pst,
        "hst_amount": hst,
        "tax_total": tax_total,
        "total": subtotal + tax_total,
        "province": province,
    }


@api_router.get("/subscription/checkout-return")
async def subscription_checkout_return(session_id: str = "", to: str = ""):
    """PUBLIC https bounce for the Stripe Checkout return → driver app.

    Stripe Checkout requires an https success/cancel URL (it rejects custom app
    schemes), but the driver app returns via a custom scheme that
    WebBrowser.openAuthSessionAsync intercepts. Stripe redirects here over https;
    this page immediately redirects the in-app browser to the app's custom scheme
    (e.g. spinr-driver://subscription/success?session_id=...), handing control
    back to the app. No auth — the OS browser opening this carries no JWT, and it
    exposes nothing sensitive (the session_id is already the user's own).

    `to` is strictly allowlisted to our app schemes so this can never be abused
    as an open redirect.

    Returns a server-side **302** to the app scheme, NOT an HTML meta/JS redirect.
    WebBrowser.openAuthSessionAsync (ASWebAuthenticationSession on iOS, Chrome
    Custom Tab on Android) reliably intercepts a server redirect to the callback
    scheme; a JS/meta redirect to a custom scheme is BLOCKED by Chrome Custom Tabs
    without a user gesture, which left the driver stranded in the browser after
    paying (they had to tap the manual fallback link). See the openAuthSessionAsync
    call in driver-app/app/driver/subscription.tsx.
    """
    import re as _re

    from fastapi.responses import RedirectResponse

    _allowed_prefixes = ("spinr-driver://", "exp://")
    _safe = bool(
        to and any(to.startswith(p) for p in _allowed_prefixes) and _re.fullmatch(r"[A-Za-z0-9:/._%@!$&()*+,;=~-]+", to)
    )
    _target = to if _safe else "spinr-driver://subscription/success"
    _sid = session_id if _re.fullmatch(r"[A-Za-z0-9_]+", session_id or "") else ""
    _sep = "&" if "?" in _target else "?"
    _dest = f"{_target}{_sep}session_id={_sid}" if _sid else _target
    # Observability: confirms Stripe actually reached the bounce (if this never
    # logs after a payment, the problem is upstream — PUBLIC_API_BASE_URL / Stripe
    # can't reach this host). No PII: scheme + flags only, not the session id.
    logger.info(
        "subscription checkout-return bounce hit -> 302",
        extra={
            "scheme": _target.split("://", 1)[0],
            "allowlisted_to": _safe,
            "has_session_id": bool(_sid),
        },
    )
    return RedirectResponse(url=_dest, status_code=302)


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
            raise HTTPException(
                status_code=403,
                detail="Spinr Pass is not available in your service area",
            )

    plan = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("subscription_plans", {"id": plan_id, "is_active": True}, limit=1)
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found or inactive")

    # Reject checkout before Stripe if this plan's vehicle_types don't cover
    # the driver. Mirrors the go-online gate so a driver can't subscribe to an
    # incompatible plan and then be silently blocked at go-online time.
    allowed_vt = plan.get("vehicle_types")
    if allowed_vt:
        driver_vt = driver.get("vehicle_type_id")
        if driver_vt not in allowed_vt:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Your vehicle type is not compatible with this plan. "
                    "Please choose a plan that supports your vehicle."
                ),
            )

    # Reject checkout if this plan's service_areas don't cover the driver's area.
    # Also accepts plans covering the parent area (child areas inherit parent scope).
    allowed_plan_areas = plan.get("service_areas")
    if allowed_plan_areas and driver.get("service_area_id") not in allowed_plan_areas:
        if driver.get("service_area_id"):
            _checkout_area = await db_supabase.find_one("service_areas", {"id": driver["service_area_id"]})
            _checkout_parent_id = (_checkout_area or {}).get("parent_service_area_id")
        else:
            _checkout_parent_id = None
        if not (_checkout_parent_id and _checkout_parent_id in allowed_plan_areas):
            raise HTTPException(
                status_code=422,
                detail=(
                    "This plan is not available for your service area. Please choose a plan that covers your area."
                ),
            )

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
        # Do NOT cancel `existing` here. For the paid Checkout path the driver
        # must keep their current pass until the new payment is confirmed —
        # _activate_subscription (called by the checkout webhook / verify-session)
        # cancels the prior active subscription and bumps the subscriber count.
        # Dev/immediate-activation mode handles `existing` at the end of this fn.
        pass

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=plan.get("duration_days", 30))
    plan_price = Decimal(str(plan.get("price", 0) or 0))
    subscription_id = str(uuid.uuid4())

    # Try Stripe Checkout if configured; otherwise dev-mode instant activation.
    checkout_url = None
    stripe_session_id = None
    payment_status = "pending"

    # Dual-import (package vs top-level run modes) — must be OUTSIDE the broad
    # Stripe try below, else an ImportError in top-level mode is swallowed as a
    # 502 before we can reach the no-Stripe/free-plan activation path.
    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings

    try:
        _settings = await get_app_settings()
        _stripe_secret = _settings.get("stripe_secret_key", "")
        _price_id = plan.get("stripe_price_id")
        _metadata = {
            "driver_id": driver["id"],
            "subscription_id": subscription_id,
            "plan_id": plan_id,
            # scope lets utils/stripe_reconcile.py recognise a subscription
            # PaymentIntent and not flag it as a STRIPE_ORPHAN ride.
            "scope": "driver_subscription",
        }
        # The client supplies its app-scheme return URL (spinr-driver:// in
        # production builds, exp://... in Expo Go) that the in-app browser
        # (openAuthSessionAsync) intercepts. Stripe Checkout, however, REQUIRES an
        # https success/cancel URL and rejects custom app schemes — so we point
        # Stripe at our https /subscription/checkout-return bounce, which then
        # redirects the in-app browser back to the app scheme. This needs no
        # AASA/assetlinks (the final hop is the custom scheme, handled by
        # openAuthSessionAsync), only a valid https URL for Stripe.
        _RETURN_PREFIXES = ("spinr-driver://", "exp://")
        _client_return: str = data.get("success_url", "")
        if _client_return and any(_client_return.startswith(p) for p in _RETURN_PREFIXES):
            _app_success = _client_return
        else:
            _app_success = "spinr-driver://subscription/success"
        _app_cancel = _app_success.replace("/success", "/cancel")

        try:
            from ..core.config import settings as _config
        except ImportError:
            from core.config import settings as _config  # type: ignore
        from urllib.parse import quote as _quote

        _bounce = f"{_config.PUBLIC_API_BASE_URL.rstrip('/')}/api/v1/drivers/subscription/checkout-return"
        # {CHECKOUT_SESSION_ID} is a Stripe template placeholder substituted by
        # Stripe at redirect time (success only).
        _success_url = f"{_bounce}?to={_quote(_app_success, safe='')}&session_id={{CHECKOUT_SESSION_ID}}"
        _cancel_url = f"{_bounce}?to={_quote(_app_cancel, safe='')}"

        if _stripe_secret and _price_id:
            # Guard against a Stripe Price that disagrees with the DB price the
            # driver was shown (admin edited plan.price after attaching a Price,
            # or pasted the wrong Price id). Retrieve it and refuse on an
            # amount/currency mismatch so we never bill a surprise amount.
            try:
                _price_obj = stripe.Price.retrieve(_price_id, api_key=_stripe_secret)
            except Exception as _price_err:
                logger.exception(f"[SUBSCRIBE] Could not retrieve Stripe Price {_price_id} for plan {plan_id}")
                raise HTTPException(
                    status_code=502,
                    detail="Payment service unavailable. Please try again later.",
                ) from _price_err
            _expected_cents = dollars_to_cents(plan_price)
            _price_cents = _price_obj.get("unit_amount")
            _price_currency = (_price_obj.get("currency") or "").lower()
            if _price_cents != _expected_cents or _price_currency != "cad":
                logger.error(
                    "[SUBSCRIBE] Stripe Price %s mismatch for plan %s: stripe=%s/%s plan=%s/cad — refusing to charge",
                    _price_id,
                    plan_id,
                    _price_cents,
                    _price_currency,
                    _expected_cents,
                    extra={"domain": "payments"},
                )
                raise HTTPException(
                    status_code=409,
                    detail="This subscription plan is misconfigured. Please contact support.",
                )

            # Also validate the billing cadence: a same-price monthly Price on a
            # weekly plan (or vice versa) would bill on a different period than
            # the driver is shown. Compare the Price's recurring interval (in
            # days) to the plan's duration_days with a small tolerance for the
            # calendar-month/year approximations.
            _INTERVAL_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
            _recurring = _price_obj.get("recurring") or {}
            _price_days = _INTERVAL_DAYS.get(_recurring.get("interval"), 0) * (_recurring.get("interval_count") or 1)
            _plan_days = plan.get("duration_days") or 0
            if not _recurring.get("interval") or abs(_price_days - _plan_days) > 3:
                logger.error(
                    "[SUBSCRIBE] Stripe Price %s interval mismatch for plan %s: "
                    "stripe=%sx%s (~%sd) plan=%sd — refusing to charge",
                    _price_id,
                    plan_id,
                    _recurring.get("interval"),
                    _recurring.get("interval_count"),
                    _price_days,
                    _plan_days,
                    extra={"domain": "payments"},
                )
                raise HTTPException(
                    status_code=409,
                    detail="This subscription plan is misconfigured. Please contact support.",
                )

            # Recurring billing: Stripe Subscription mode. Stripe auto-renews
            # the driver's card each period and fires invoice.paid (renewal)
            # / invoice.payment_failed (dunning) / customer.subscription.*
            # which routes/webhooks.py reconciles. Requires a recurring Price
            # created in the Stripe dashboard and stored on the plan.
            _session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": _price_id, "quantity": 1}],
                metadata=_metadata,
                # Mirror our ids onto the Stripe Subscription so renewal
                # invoices and subscription events are self-identifying.
                subscription_data={"metadata": _metadata},
                success_url=_success_url,
                cancel_url=_cancel_url,
                api_key=_stripe_secret,
            )
            checkout_url = _session.url
            stripe_session_id = _session.id
            payment_status = "pending"
            logger.info(
                f"[SUBSCRIBE] Recurring checkout session created: session={stripe_session_id} "
                f"subscription={subscription_id} driver={driver['id']} price={_price_id}"
            )
        elif _stripe_secret and plan_price > 0:
            # One-off Checkout (no recurring Price on this plan). The webhook
            # (checkout.session.completed) activates the subscription; renewal
            # is expiry-driven (driver re-subscribes each period).
            # Compute province tax and charge the inclusive total to Stripe.
            _tax = await _compute_subscription_tax(driver["id"], plan_price)
            _amount_cents = dollars_to_cents(_tax["total"])
            _session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                mode="payment",
                customer_creation="always",
                line_items=[
                    {
                        "price_data": {
                            "currency": "cad",
                            "product_data": {
                                "name": plan.get("name", "Spinr Pass"),
                                **({} if not plan.get("description") else {"description": plan["description"]}),
                            },
                            "unit_amount": _amount_cents,
                        },
                        "quantity": 1,
                    }
                ],
                metadata=_metadata,
                # Propagate our ids onto the underlying PaymentIntent so the
                # nightly reconciler (stripe_reconcile.py) can identify a
                # subscription charge instead of flagging it STRIPE_ORPHAN if
                # checkout.session.completed is ever missed.
                payment_intent_data={"metadata": _metadata},
                success_url=_success_url,
                cancel_url=_cancel_url,
                api_key=_stripe_secret,
            )
            checkout_url = _session.url
            stripe_session_id = _session.id
            payment_status = "pending"
            logger.info(
                f"[SUBSCRIBE] One-off checkout session created: session={stripe_session_id} "
                f"subscription={subscription_id} driver={driver['id']}"
            )
        else:
            # Dev/demo mode: no Stripe or free plan — activate immediately.
            payment_status = "paid"
            logger.info(
                f"[SUBSCRIBE] Dev mode (no Stripe/free plan): subscription {subscription_id} for driver {driver['id']}"
            )
    except HTTPException:
        # Intentional rejections (e.g. Price mismatch 409) must propagate with
        # their own status, not be masked as a generic 502.
        raise
    except Exception as _stripe_err:
        logger.exception(f"[SUBSCRIBE] Stripe Checkout creation failed for driver {driver['id']}")
        raise HTTPException(
            status_code=502,
            detail="Payment service unavailable. Please try again later.",
        ) from _stripe_err

    # Supersede any older pending checkout rows for this driver before
    # inserting the new one. Without this, a double-tap / retry / plan-change
    # leaves multiple valid Checkout URLs, and a stale session completing late
    # could activate over a newer choice (_activate_subscription only acts on
    # rows still in "pending", so superseding neutralises the old ones).
    if checkout_url:
        stale_pending = (
            await db_supabase.get_rows(
                "driver_subscriptions",
                {"driver_id": driver["id"], "status": "pending"},
                columns="id,stripe_session_id",
                limit=20,
            )
            or []
        )
        for row in stale_pending:
            # Expire the stale Stripe Checkout Session first so it can never be
            # paid — otherwise a superseded one-off session that completes later
            # charges the driver's card for a pass they won't receive (one-off
            # sessions carry no subscription to cancel). Best-effort: a session
            # that's already completed/expired raises, which we ignore.
            _stale_session = row.get("stripe_session_id")
            if _stale_session and _stripe_secret:
                try:
                    stripe.checkout.Session.expire(_stale_session, api_key=_stripe_secret)
                except Exception:
                    logger.info(
                        f"[SUBSCRIBE] Could not expire superseded checkout session {_stale_session} "
                        "(already completed/expired?) — continuing",
                    )
            await db_supabase.update_one(
                "driver_subscriptions",
                {"id": row["id"]},
                {"status": "superseded"},
            )

    # Create the subscription row (pending payment if Checkout, or paid in dev mode)
    subscription = {
        "id": subscription_id,
        "driver_id": driver["id"],
        "plan_id": plan_id,
        "plan_name": plan.get("name"),
        "price": plan.get("price"),
        "rides_per_day": plan.get("rides_per_day", -1),
        "status": "active" if payment_status == "paid" else "pending",
        "started_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "payment_status": payment_status,
        "stripe_session_id": stripe_session_id,
        "expiry_warned": False,
        "created_at": now.isoformat(),
    }

    try:
        await db_supabase.insert_one("driver_subscriptions", subscription)
    except Exception as _insert_err:
        # Distinguish the expected concurrency conflict from a real DB failure:
        # the partial unique index (migration 153) allows at most one pending
        # checkout per driver, so if a pending row already exists this was a
        # concurrent subscribe → 409. Otherwise the insert genuinely failed
        # (schema/network/RLS) → surface 503, not a misleading "checkout in
        # progress". Either way, expire the Stripe session we just created so it
        # can't be paid, and log at error (payment-path failure).
        if checkout_url and stripe_session_id and _stripe_secret:
            try:
                stripe.checkout.Session.expire(stripe_session_id, api_key=_stripe_secret)
            except Exception:
                logger.info(f"[SUBSCRIBE] Could not expire race-loser session {stripe_session_id}")
        existing_pending = (
            await db_supabase.get_rows(
                "driver_subscriptions",
                {"driver_id": driver["id"], "status": "pending"},
                columns="id",
                limit=1,
            )
            or []
        )
        if existing_pending:
            logger.warning(
                f"[SUBSCRIBE] Concurrent pending checkout for driver {driver['id']} — returning 409",
            )
            raise HTTPException(
                status_code=409,
                detail="A checkout is already in progress. Please finish or cancel it first.",
            ) from _insert_err
        logger.error(
            f"[SUBSCRIBE] Subscription insert failed for driver {driver['id']}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not start checkout. Please try again.",
        ) from _insert_err

    if checkout_url:
        # Payment pending — defer cancelling the driver's existing pass and
        # bumping the subscriber count to _activate_subscription (runs on the
        # checkout webhook / verify-session once payment is confirmed). This
        # keeps their current pass valid if they abandon Checkout, and avoids
        # double-counting (abandoned sessions would otherwise inflate the count
        # and successful payments would count twice).
        return {
            "success": True,
            "checkout_url": checkout_url,
            "subscription_id": subscription_id,
            # The driver app reads `session_id` to poll verify-session if the
            # deep-link return is delayed/missed after openURL().
            "session_id": stripe_session_id,
        }

    # Dev/immediate-activation mode: no payment step, so the activation path
    # never runs — cancel the prior active subscription and bump the count here.
    if existing:
        await _cancel_stripe_subscription(existing.get("stripe_subscription_id"))
        await db_supabase.update_one(
            "driver_subscriptions",
            {"id": existing["id"]},
            {
                "status": RideStatus.CANCELLED,
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    await db_supabase.update_one(
        "subscription_plans",
        {"id": plan_id},
        {"subscriber_count": (plan.get("subscriber_count", 0) or 0) + 1},
    )
    # Dev/immediate activation realizes revenue without a Stripe invoice —
    # record it in the ledger so admin stats see it.
    _dev_tax = await _compute_subscription_tax(driver["id"], plan_price)
    await _record_subscription_payment(
        driver_id=driver["id"],
        subscription_id=subscription_id,
        plan_id=plan_id,
        plan_name=plan.get("name"),
        amount=_dev_tax["total"],
        billing_reason="dev",
        subtotal=_dev_tax["subtotal"],
        gst_amount=_dev_tax["gst_amount"],
        pst_amount=_dev_tax["pst_amount"],
        hst_amount=_dev_tax["hst_amount"],
        tax_total=_dev_tax["tax_total"],
        province=_dev_tax["province"],
    )
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

    # Authorize: the session must belong to the calling driver. Session ids can
    # leak via redirect URLs, browser history, or support logs, so a non-owner
    # gets the same 404 as a non-existent session (no existence oracle).
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver or sub.get("driver_id") != driver["id"]:
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
        logger.error(f"[SUBSCRIBE] verify-session Stripe error: {e}", exc_info=True)
        return {"status": "pending"}

    if session.payment_status == "paid":
        # Activate first (idempotent; a no-op if the row was superseded by a
        # newer checkout), then re-read to decide whether to link. Pass the
        # session's actual mode so ledger recording matches what was created.
        await _activate_subscription(sub["id"], sub.get("plan_id"), session.get("mode"))
        sub = await db.find_one("driver_subscriptions", {"id": sub["id"]})
        stripe_subscription_id = session.get("subscription")

        if sub and sub.get("status") == "active":
            # Persist the Stripe Subscription id (recurring plans) so renewal /
            # dunning / cancellation webhooks can match this row even if the
            # checkout webhook was delayed or missed. Only on an active row —
            # never link a superseded one.
            if stripe_subscription_id and not sub.get("stripe_subscription_id"):
                await db.update_one(
                    "driver_subscriptions",
                    {"id": sub["id"]},
                    {"stripe_subscription_id": stripe_subscription_id},
                )
            return {"status": "active", "subscription": sub}

        # Superseded by a newer checkout — don't link/activate; cancel the
        # orphaned Stripe subscription so the driver isn't billed for it.
        if stripe_subscription_id:
            await _cancel_stripe_subscription(stripe_subscription_id)
        return {"status": "superseded"}

    return {"status": "pending"}


async def _record_subscription_payment(
    *,
    driver_id: str,
    subscription_id: str | None,
    plan_id: str | None,
    plan_name: str | None,
    amount,
    billing_reason: str,
    subtotal: Decimal | None = None,
    gst_amount: Decimal | None = None,
    pst_amount: Decimal | None = None,
    hst_amount: Decimal | None = None,
    tax_total: Decimal | None = None,
    province: str | None = None,
    stripe_invoice_id: str | None = None,
    stripe_session_id: str | None = None,
    stripe_payment_intent_id: str | None = None,
    stripe_invoice_url: str | None = None,
) -> str | None:
    """Append a realized-payment row to the subscription_payments ledger.

    Returns the new row id (used by resend-invoice), or None on failure/skip.
    The ledger (migration 151) is the source of truth for admin subscription
    revenue/transaction stats — driver_subscriptions tracks current STATE, this
    tracks money MOVED, so recurring renewals are captured (not just the first
    charge). Recurring inserts dedupe on the unique stripe_invoice_id index, so
    a replay is benign. Never raises — a ledger failure must not block the
    activation/webhook flow that already moved the money.
    """
    try:
        amt = Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amt <= 0:
            return None  # nothing realized — don't clutter the ledger
        row_id = str(uuid.uuid4())
        row: dict = {
            "id": row_id,
            "driver_id": driver_id,
            "subscription_id": subscription_id,
            "plan_id": plan_id,
            "plan_name": plan_name,
            "amount": str(amt),
            "currency": "cad",
            "billing_reason": billing_reason,
            "stripe_invoice_id": stripe_invoice_id,
            "stripe_session_id": stripe_session_id,
            "stripe_payment_intent_id": stripe_payment_intent_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Tax columns (migration 186) — stored when computed at checkout.
        def _q2(v):
            return str(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if v is not None else None

        if subtotal is not None:
            row["subtotal"] = _q2(subtotal)
        if gst_amount is not None:
            row["gst_amount"] = _q2(gst_amount)
        if pst_amount is not None:
            row["pst_amount"] = _q2(pst_amount)
        if hst_amount is not None:
            row["hst_amount"] = _q2(hst_amount)
        if tax_total is not None:
            row["tax_total"] = _q2(tax_total)
        if province:
            row["province"] = province
        # Stripe receipt link (migration 188) — only present on recurring charges;
        # written conditionally so dev/one-off rows omit the column cleanly.
        if stripe_invoice_url:
            row["stripe_invoice_url"] = stripe_invoice_url
        await db_supabase.insert_one("subscription_payments", row)
        return row_id
    except Exception as _ledger_err:
        # Distinguish a benign duplicate (unique stripe_invoice_id replay) from a
        # real write failure. A duplicate means the row already exists — nothing
        # lost — so log at debug. Any other error means the ledger row that drives
        # admin revenue/stats is now missing, which must be surfaced at error so
        # ops/reconciliation can repair it. Never raise — the money already moved.
        _msg = str(_ledger_err).lower()
        _is_duplicate = "duplicate" in _msg or "unique" in _msg or "23505" in _msg
        if _is_duplicate:
            logger.debug(
                "subscription_payments duplicate ignored driver=%s invoice=%s",
                driver_id,
                stripe_invoice_id,
            )
        else:
            logger.error(
                "subscription_payments insert FAILED (revenue row lost — repair needed) driver=%s reason=%s invoice=%s",
                driver_id,
                billing_reason,
                stripe_invoice_id,
                exc_info=True,
            )


async def _send_subscription_invoice_email(
    *,
    driver_id: str,
    plan_name: str,
    duration_label: str,
    subtotal: Decimal,
    gst_amount: Decimal,
    pst_amount: Decimal,
    hst_amount: Decimal,
    tax_total: Decimal,
    total: Decimal,
    province: str = "SK",
    billing_reason: str,
    payment_date: str,
    invoice_number: str | None = None,
    stripe_invoice_url: str | None = None,
) -> bool:
    """Email the driver a rich HTML invoice (+ PDF attachment) for their Spinr Pass charge.

    Returns True on success, False on any failure.  Never raises — the caller
    decides whether to surface the failure (resend endpoint raises 502; activation
    flow logs and continues).
    """
    try:
        driver = await db.find_one("drivers", {"id": driver_id})
        if not driver:
            return False
        user = await db.find_one("users", {"id": driver.get("user_id")})
        email = (user or {}).get("email", "")
        if not email:
            return False

        driver_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Driver"
        billing_label = "Auto-renewal" if billing_reason == "subscription_cycle" else "One-time purchase"
        inv_number = invoice_number or f"SPX-{driver_id[:6].upper()}-{payment_date.replace(' ', '').replace(',', '')}"

        # ── Tax line rows for HTML ────────────────────────────────────────────
        def _row(label: str, amount: Decimal, bold: bool = False, color: str = "#666") -> str:
            style = f"color:{color};font-weight:{'700' if bold else '400'}"
            return (
                f"<tr>"
                f"<td style='padding:6px 0;font-size:14px;{style}'>{label}</td>"
                f"<td style='padding:6px 0;font-size:14px;text-align:right;{style}'>${amount:.2f} CAD</td>"
                f"</tr>"
            )

        def _pct_label(amt: Decimal, base: Decimal) -> str:
            if base <= 0:
                return ""
            from decimal import ROUND_HALF_UP as _RHU

            pct = (amt / base * 100).quantize(Decimal("0.01"), rounding=_RHU).normalize()
            return f" ({pct}%)"

        tax_rows_html = ""
        if gst_amount > 0:
            tax_rows_html += _row(f"GST{_pct_label(gst_amount, subtotal)} — Federal", gst_amount)
        if pst_amount > 0:
            tax_rows_html += _row(f"PST{_pct_label(pst_amount, subtotal)} — {province}", pst_amount)
        if hst_amount > 0:
            tax_rows_html += _row(f"HST{_pct_label(hst_amount, subtotal)} — {province}", hst_amount)

        stripe_link_html = (
            f"<p style='margin:8px 0 0;font-size:13px;'>"
            f"<a href='{stripe_invoice_url}' style='color:#ee2b2b;'>View Stripe receipt →</a></p>"
            if stripe_invoice_url
            else ""
        )

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:24px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,0.08);">
  <!-- Header -->
  <tr><td style="background:#ee2b2b;padding:28px 32px;">
    <h1 style="color:#fff;margin:0;font-size:28px;font-weight:800;letter-spacing:-0.5px;">Spinr</h1>
    <p style="color:rgba(255,255,255,0.8);margin:4px 0 0;font-size:13px;">Subscription Invoice</p>
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:28px 32px 0;">
    <p style="color:#1a1a1a;font-size:16px;margin:0;">Hi {driver_name},</p>
    <p style="color:#888;font-size:14px;margin:6px 0 0;">
      Thank you for your Spinr Pass subscription. Your invoice is attached as a PDF.
    </p>
  </td></tr>

  <!-- Amount callout -->
  <tr><td style="padding:20px 32px;">
    <div style="background:#fef2f2;border-radius:12px;padding:20px;text-align:center;">
      <p style="color:#ee2b2b;font-size:40px;font-weight:800;margin:0;">${total:.2f} CAD</p>
      <p style="color:#999;font-size:12px;margin:6px 0 0;">{payment_date} · {billing_label}</p>
      <p style="color:#999;font-size:11px;margin:4px 0 0;letter-spacing:0.4px;">Invoice <strong style="color:#1a1a1a">{inv_number}</strong></p>
    </div>
  </td></tr>

  <!-- Line items -->
  <tr><td style="padding:0 32px 24px;">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr style="border-bottom:2px solid #f0f0f0;">
        <td style="padding:8px 0;font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:0.6px;">Description</td>
        <td style="padding:8px 0;font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:0.6px;text-align:right;">Amount (CAD)</td>
      </tr>
      {_row(f"Spinr Pass {plan_name} — {duration_label}", subtotal, color="#1a1a1a")}
      <tr><td colspan="2" style="padding:4px 0;border-top:1px solid #f0f0f0;"></td></tr>
      {tax_rows_html}
      <tr><td colspan="2" style="padding:4px 0;border-top:2px solid #1a1a1a;"></td></tr>
      {_row("Total", total, bold=True, color="#1a1a1a")}
    </table>
  </td></tr>

  <!-- Payment note -->
  <tr><td style="padding:0 32px 28px;">
    <div style="background:#f9f9f9;border-radius:10px;padding:16px;">
      <p style="margin:0;font-size:13px;color:#666;">Payment successfully charged to your card on file.</p>
      {stripe_link_html}
    </div>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 32px 24px;border-top:1px solid #f0f0f0;text-align:center;">
    <p style="color:#bbb;font-size:11px;margin:0;">Spinr Technologies Inc. · Saskatoon, SK, Canada</p>
    <p style="color:#bbb;font-size:11px;margin:4px 0 0;">support@spinr.ca · www.spinr.ca</p>
  </td></tr>
</table>
</body></html>"""

        # ── PDF attachment ────────────────────────────────────────────────────
        attachments = None
        try:
            try:
                from ..utils.subscription_invoice_pdf import generate_subscription_invoice_pdf
            except ImportError:
                from utils.subscription_invoice_pdf import generate_subscription_invoice_pdf  # type: ignore

            pdf_bytes = generate_subscription_invoice_pdf(
                invoice_number=inv_number,
                payment_date=payment_date,
                driver_name=driver_name,
                driver_email=email,
                plan_name=plan_name,
                duration_label=duration_label,
                billing_reason=billing_reason,
                subtotal=subtotal,
                gst_amount=gst_amount,
                pst_amount=pst_amount,
                hst_amount=hst_amount,
                tax_total=tax_total,
                total=total,
                province=province,
                stripe_invoice_url=stripe_invoice_url,
            )
            safe_plan = plan_name.replace(" ", "-")
            attachments = [
                {
                    "filename": f"Spinr-Pass-Invoice-{safe_plan}-{payment_date.replace(' ', '-').replace(',', '')}.pdf",
                    "content": pdf_bytes,
                    "mime": "application/pdf",
                }
            ]
        except Exception:
            logger.error("[SUBSCRIBE] Invoice PDF generation failed — sending HTML only", exc_info=True)

        try:
            from ..utils.email_provider import send_transactional_email
        except ImportError:
            from utils.email_provider import send_transactional_email  # type: ignore

        _ok = await send_transactional_email(
            to=email,
            subject=f"Your Spinr Pass Invoice — {plan_name} ({payment_date})",
            html=html,
            default_from="invoices@spinr.ca",
            log_id=driver_id,
            email_type="subscription_invoice",
            attachments=attachments,
        )
        if _ok:
            logger.info(
                "[SUBSCRIBE] Invoice email sent driver=%s plan=%s total=%s",
                driver_id,
                plan_name,
                total,
                extra={"domain": "payments", "driver_id": driver_id},
            )
        else:
            logger.error(
                "[SUBSCRIBE] Invoice email delivery failed driver=%s plan=%s",
                driver_id,
                plan_name,
                extra={"domain": "payments", "driver_id": driver_id},
            )
        return bool(_ok)
    except Exception as _mail_err:
        logger.error(
            "[SUBSCRIBE] Invoice email failed driver=%s: %s",
            driver_id,
            _mail_err,
            exc_info=True,
            extra={"domain": "payments", "driver_id": driver_id},
        )
        return False


async def _activate_subscription(subscription_id: str, plan_id: str | None = None, checkout_mode: str | None = None):
    """Activate a pending subscription after payment confirmation.

    Called by both the webhook handler and the verify-session endpoint
    (whichever runs first). Idempotent — skips if already active.

    ``checkout_mode`` is the Stripe Checkout Session's actual mode
    ("payment" for one-off, "subscription" for recurring). It decides whether
    to write the one-off ledger row here — based on what was actually created,
    NOT the plan's current stripe_price_id, which an admin could flip between
    checkout start and payment completion.
    """
    sub = await db.find_one("driver_subscriptions", {"id": subscription_id})
    # Only a still-pending checkout row activates. A row that is already
    # active (idempotent replay), cancelled, or superseded by a newer checkout
    # is terminal — never (re)activate it, so a stale session completing late
    # can't replace the driver's current plan.
    if not sub or sub.get("status") != "pending":
        return

    driver_id = sub.get("driver_id")

    # Recompute the period from activation time so a driver who completes
    # Checkout hours after creating it (Stripe sessions live up to 24h) doesn't
    # lose paid access — matters most on daily/weekly one-off plans. (Recurring
    # plans get expires_at overwritten by invoice.paid's authoritative period
    # end on the next event.)
    plan = await db.find_one("subscription_plans", {"id": plan_id}) if plan_id else None
    now = datetime.now(timezone.utc)
    activate_updates = {
        "status": "active",
        "payment_status": "paid",
        "started_at": now.isoformat(),
    }
    if plan and plan.get("duration_days"):
        activate_updates["expires_at"] = (now + timedelta(days=plan["duration_days"])).isoformat()

    # Atomic claim: flip pending→active filtering on status='pending'. Only the
    # caller that wins (webhook vs verify-session can race) gets a row back and
    # runs the one-time side effects below; the loser matches 0 rows and returns,
    # so cancel-prior / count-increment / push never run twice.
    claimed = await db.update_one(
        "driver_subscriptions",
        {"id": subscription_id, "status": "pending"},
        {"$set": activate_updates},
    )
    if not claimed:
        return

    # Cancel any prior active subscription for this driver (plan switch).
    # NOTE: the atomic claim above already flipped THIS row to active, so an
    # unordered limit-1 lookup could return it. Fetch all active rows and pick
    # one that isn't the row we just activated, so the prior pass is always
    # retired.
    _active_rows = (
        await db.get_rows("driver_subscriptions", {"driver_id": driver_id, "status": "active"}, limit=10) or []
    )
    existing = next((r for r in _active_rows if r.get("id") != subscription_id), None)
    if existing:
        try:
            # Durable cancel: raise on failure so we don't claim the old pass is
            # cancelled while Stripe keeps billing it.
            await _cancel_stripe_subscription(existing.get("stripe_subscription_id"), raise_on_error=True)
            await db.update_one(
                "driver_subscriptions",
                {"id": existing["id"]},
                {"$set": {"status": RideStatus.CANCELLED, "cancelled_at": now.isoformat()}},
            )
        except Exception:
            # The new pass is already active (driver paid), so we can't block,
            # but the old Stripe subscription may still bill. Mark the old row
            # cancel_pending (terminal — won't be reactivated) and log loudly so
            # ops can cancel it in Stripe. (No auto-retry yet — tracked follow-up.)
            logger.error(
                "[SUBSCRIBE] Prior Stripe subscription cancel failed on plan switch — "
                "old row marked cancel_pending; manual Stripe cancel needed. driver=%s old_row=%s",
                driver_id,
                existing["id"],
                exc_info=True,
                extra={"domain": "payments", "driver_id": driver_id},
            )
            await db.update_one(
                "driver_subscriptions",
                {"id": existing["id"]},
                {"$set": {"status": "cancel_pending", "cancelled_at": now.isoformat()}},
            )

    # Increment subscriber count (reuse the plan already fetched above).
    if plan:
        await db.update_one(
            "subscription_plans",
            {"id": plan_id},
            {"$set": {"subscriber_count": (plan.get("subscriber_count", 0) or 0) + 1}},
        )

    # Record one-off Checkout revenue in the ledger. Recurring (mode=subscription)
    # checkouts are NOT recorded here — their invoice.paid webhook writes the
    # ledger, so recording here too would double-count the first charge.
    # Decide from the ACTUAL session mode when known (not the plan's current
    # stripe_price_id, which an admin could flip mid-checkout); fall back to the
    # plan flag only when the mode wasn't passed.
    if checkout_mode is not None:
        _is_one_off = checkout_mode == "payment"
    else:
        _is_one_off = bool(plan) and not plan.get("stripe_price_id")
    if plan and _is_one_off:
        _one_off_tax = await _compute_subscription_tax(driver_id, Decimal(str(plan.get("price") or 0)))
        await _record_subscription_payment(
            driver_id=driver_id,
            subscription_id=subscription_id,
            plan_id=plan_id,
            plan_name=sub.get("plan_name") or plan.get("name"),
            amount=_one_off_tax["total"],
            billing_reason="one_off",
            subtotal=_one_off_tax["subtotal"],
            gst_amount=_one_off_tax["gst_amount"],
            pst_amount=_one_off_tax["pst_amount"],
            hst_amount=_one_off_tax["hst_amount"],
            tax_total=_one_off_tax["tax_total"],
            province=_one_off_tax["province"],
            stripe_session_id=sub.get("stripe_session_id"),
        )

    logger.info(f"[SUBSCRIBE] Subscription {subscription_id} activated for driver {driver_id}")

    # Push notification + invoice email to driver.
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

        # Invoice email — one-off and dev plans only. Recurring plans receive
        # their invoice email from the invoice.paid webhook (which has the
        # authoritative Stripe invoice URL and amount for every charge).
        if plan and _is_one_off:
            _days = plan.get("duration_days", 30)
            _dur_map = {1: "Daily", 7: "Weekly", 30: "Monthly", 365: "Annual"}
            _dur_label = _dur_map.get(_days, f"{_days}-day")
            await _send_subscription_invoice_email(
                driver_id=driver_id,
                plan_name=plan.get("name", "Spinr Pass"),
                duration_label=_dur_label,
                subtotal=_one_off_tax["subtotal"],
                gst_amount=_one_off_tax["gst_amount"],
                pst_amount=_one_off_tax["pst_amount"],
                hst_amount=_one_off_tax["hst_amount"],
                tax_total=_one_off_tax["tax_total"],
                total=_one_off_tax["total"],
                province=_one_off_tax["province"],
                billing_reason="one_off",
                payment_date=now.strftime("%B %d, %Y"),
            )


@api_router.get("/subscription/payments")
async def get_subscription_payment_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Return paginated Spinr Pass payment history for the authenticated driver.

    Only returns rows for the calling driver's own account — driver_id is
    resolved from the authenticated user's token, never from a query param.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    payments = (
        await db_supabase.get_rows(
            "subscription_payments",
            {"driver_id": driver["id"]},
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
        or []
    )

    total = await db_supabase.count_documents("subscription_payments", {"driver_id": driver["id"]})

    def _payment_row(p: dict) -> dict:
        """Serialize a subscription_payments row with tax breakdown.

        Prefers stored tax columns (migration 186). Falls back to back-computing
        GST+PST from the total for legacy rows written before migration 186.
        """

        def _q2(v):
            return Decimal(str(v)).quantize(Decimal("0.01"))

        total_d = _q2(p.get("amount") or 0)
        if p.get("subtotal") is not None:
            subtotal_d = _q2(p["subtotal"])
            gst_d = _q2(p.get("gst_amount") or 0)
            pst_d = _q2(p.get("pst_amount") or 0)
            hst_d = _q2(p.get("hst_amount") or 0)
        else:
            # Legacy rows written before migration 186 — return zero tax rather
            # than fabricating amounts that were never collected.
            subtotal_d = total_d
            gst_d = pst_d = hst_d = Decimal("0")
        return {
            "id": p["id"],
            "plan_id": p.get("plan_id"),
            "plan_name": p.get("plan_name"),
            "amount": str(total_d),
            "subtotal": str(subtotal_d),
            "gst_amount": str(gst_d),
            "pst_amount": str(pst_d),
            "hst_amount": str(hst_d),
            "province": p.get("province") or "SK",
            "currency": (p.get("currency") or "cad").upper(),
            "billing_reason": p.get("billing_reason"),
            "stripe_invoice_id": p.get("stripe_invoice_id"),
            "created_at": p.get("created_at"),
        }

    return {
        "payments": [_payment_row(p) for p in payments],
        "total": total,
        "has_more": (offset + len(payments)) < total,
        "limit": limit,
        "offset": offset,
    }


@api_router.post("/subscription/payments/{payment_id}/resend-invoice")
async def resend_subscription_invoice(
    payment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Re-send the invoice email for a specific Spinr Pass payment.

    Drivers can trigger a resend from the payment history screen.
    Only the owning driver may request resend — payment_id is verified
    against their driver account, never taken on trust.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    payment = await db_supabase.find_one("subscription_payments", {"id": payment_id})
    if not payment or payment.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=404, detail="Payment not found")

    plan = None
    if payment.get("plan_id"):
        plan = await db_supabase.find_one("subscription_plans", {"id": payment["plan_id"]})
    _days = (plan or {}).get("duration_days", 30)
    _dur_map = {1: "Daily", 7: "Weekly", 30: "Monthly", 365: "Annual"}
    _dur_label = _dur_map.get(_days, f"{_days}-day")

    def _q2(v):
        return Decimal(str(v)).quantize(Decimal("0.01"))

    total_d = _q2(payment.get("amount") or 0)
    if payment.get("subtotal") is not None:
        subtotal_d = _q2(payment["subtotal"])
        gst_d = _q2(payment.get("gst_amount") or 0)
        pst_d = _q2(payment.get("pst_amount") or 0)
        hst_d = _q2(payment.get("hst_amount") or 0)
        tax_total_d = _q2(payment.get("tax_total") or 0)
        province = payment.get("province") or "SK"
    else:
        # Legacy row (before migration 186) — zero tax rather than fabricating amounts.
        subtotal_d = total_d
        gst_d = pst_d = hst_d = tax_total_d = Decimal("0")
        province = "SK"

    from datetime import datetime as _dt

    _raw_date = payment.get("created_at") or ""
    try:
        _payment_date = _dt.fromisoformat(_raw_date.replace("Z", "+00:00")).strftime("%B %d, %Y")
    except Exception:
        _payment_date = _dt.now(timezone.utc).strftime("%B %d, %Y")

    _sent = await _send_subscription_invoice_email(
        driver_id=driver["id"],
        plan_name=payment.get("plan_name") or "Spinr Pass",
        duration_label=_dur_label,
        subtotal=subtotal_d,
        gst_amount=gst_d,
        pst_amount=pst_d,
        hst_amount=hst_d,
        tax_total=tax_total_d,
        total=total_d,
        province=province,
        billing_reason=payment.get("billing_reason") or "one_off",
        payment_date=_payment_date,
        invoice_number=f"SPX-{payment['id'][:8].upper()}",
    )
    if not _sent:
        raise HTTPException(status_code=502, detail="Invoice email could not be delivered")
    return {"success": True}


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

    # Stop Stripe billing for recurring plans first. If Stripe cancellation
    # fails we must NOT mark the row cancelled — otherwise the app shows
    # "cancelled" while Stripe keeps billing (and a later invoice.paid would
    # silently re-activate it). Surface a retryable error instead.
    try:
        await _cancel_stripe_subscription(sub.get("stripe_subscription_id"), raise_on_error=True)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail="Could not cancel your subscription with the payment provider. Please try again.",
        ) from e

    await db_supabase.update_one(
        "driver_subscriptions",
        {"id": sub["id"]},
        {
            "status": RideStatus.CANCELLED,
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"success": True}


# ── G5: Subscription expiry warning background task ──────────────


async def check_expiring_subscriptions():
    """Background task: handles Spinr Pass expiry end-to-end.

    Two jobs, both run every 6 hours from the FastAPI lifespan:

    1. **Warn** drivers whose active subscription expires within 72 h (3 days)
       via ``expiry_warned_3d``, then again within 24 h via ``expiry_warned``.
       Both are claim-flags so only one push fires per window per subscription.
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

    try:
        from ..utils.redis_client import redis_set_nx as _redis_set_nx  # type: ignore
    except ImportError:
        try:
            from utils.redis_client import redis_set_nx as _redis_set_nx  # type: ignore
        except ImportError:
            _redis_set_nx = None  # type: ignore

    while True:
        # Single-replica enforcement: only the pod that wins the lock runs
        # the expiry check per 6-hour window. Prevents N offline-kick push
        # notifications being sent to the same driver on multi-replica deploys.
        if _redis_set_nx is not None:
            lock_acquired = await _redis_set_nx(
                "spinr:subscription:expiry:lock",
                f"{socket.gethostname()}:{os.getpid()}",
                6 * 3600 + 300,  # 6h + 5 min grace
            )
        else:
            lock_acquired = True  # no Redis in dev → run on all replicas
        if not lock_acquired:
            await asyncio.sleep(6 * 3600)
            continue
        try:
            now = datetime.now(timezone.utc)
            window_24h = now + timedelta(hours=24)
            window_3d = now + timedelta(hours=72)

            # Retry subscriptions stuck in cancel_pending — a plan-switch Stripe
            # cancel that failed transiently. On success, finalize as cancelled;
            # a row still failing stays cancel_pending and is logged for ops.
            try:
                stuck_cancels = await db.get_rows("driver_subscriptions", {"status": "cancel_pending"}, limit=200) or []
                for pc in stuck_cancels:
                    try:
                        await _cancel_stripe_subscription(pc.get("stripe_subscription_id"), raise_on_error=True)
                        await db.update_one(
                            "driver_subscriptions",
                            {"id": pc["id"]},
                            {"$set": {"status": "cancelled"}},
                        )
                        logger.info(
                            "[SUB-EXPIRY] cancel_pending resolved row=%s",
                            pc["id"],
                            extra={"domain": "payments"},
                        )
                    except Exception:
                        logger.error(
                            "[SUB-EXPIRY] cancel_pending retry still failing row=%s — manual Stripe cancel needed",
                            pc["id"],
                            exc_info=True,
                            extra={"domain": "payments"},
                        )
            except Exception:
                logger.error("[SUB-EXPIRY] cancel_pending sweep query failed", exc_info=True)

            active_subs = await db.get_rows("driver_subscriptions", {"status": "active"}, limit=500)

            # Only enforce the offline flip when the admin has turned on
            # the subscription gate — otherwise expiry is purely advisory.
            try:
                app_settings = await get_app_settings()
                require_sub = bool(app_settings.get("require_driver_subscription", False))
            except Exception as e:
                logger.error(
                    f"[SUB-EXPIRY] get_app_settings failed, skipping enforcement this tick: {e}",
                    exc_info=True,
                )
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
                        logger.error(
                            f"[SUB-EXPIRY] Failed to mark sub {sub['id']} expired: {e}",
                            exc_info=True,
                        )
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
                    # M-5: SGI insurance period audit — driver was online
                    # (period 1) and we just forced them offline (period 0).
                    await record_period_transition(driver["id"], 0)

                    try:
                        await clear_presence(driver["id"])
                    except Exception as e:
                        logger.error(
                            f"[SUB-EXPIRY] clear_presence failed for {driver['id']}: {e}",
                            exc_info=True,
                        )

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
                        logger.error(
                            f"[SUB-EXPIRY] activity log insert failed for {driver['id']}: {e}",
                            exc_info=True,
                        )

                    if driver.get("user_id"):
                        try:
                            await send_push_notification(
                                driver["user_id"],
                                "Spinr Pass expired",
                                "Your Spinr Pass has expired and you've been set offline. Renew from your dashboard to keep driving.",
                                {
                                    "type": "subscription_expired",
                                    "driver_id": driver["id"],
                                },
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
                        logger.warning(
                            "check_expiring_subscriptions: admin broadcast failed for driver %s",
                            driver["id"],
                            exc_info=True,
                        )

                    enforced_count += 1
                    continue

                # ── Warning branch: 24-hour final notice ─────────────────
                if sub.get("expiry_warned"):
                    continue

                if now < expires_dt <= window_24h:
                    driver = await db.find_one("drivers", {"id": sub["driver_id"]})
                    if driver and driver.get("user_id"):
                        hours_left = max(1, int((expires_dt - now).total_seconds() / 3600))
                        plan_name = sub.get("plan_name", "Spinr Pass")
                        try:
                            await send_push_notification(
                                driver["user_id"],
                                "Spinr Pass Expiring Soon",
                                f"Your {plan_name} plan expires in ~{hours_left} hours. Renew now to keep driving!",
                                {
                                    "type": "subscription_expiring",
                                    "hours_left": str(hours_left),
                                },
                            )
                            warned_count += 1
                        except Exception as e:
                            logger.warning(f"[SUB-EXPIRY] Push failed for driver {sub['driver_id']}: {e}")

                    await db.update_one(
                        "driver_subscriptions",
                        {"id": sub["id"]},
                        {"$set": {"expiry_warned": True}},
                    )

            # ── 3-day advance warning: dedicated targeted query ──────────
            # Query only the subs in the (24h, 72h] expiry window that
            # haven't been warned yet, bypassing the 500-row active_subs cap
            # so no expiring row is silently skipped on a busy platform.
            subs_3d = (
                await db.get_rows(
                    "driver_subscriptions",
                    {
                        "$and": [
                            {"status": "active"},
                            {"expiry_warned_3d": False},
                            {"expires_at": {"$gt": window_24h.isoformat()}},
                            {"expires_at": {"$lte": window_3d.isoformat()}},
                        ]
                    },
                    limit=500,
                )
                or []
            )
            warned_3d_count = 0
            for sub_3d in subs_3d:
                try:
                    _exp_str = sub_3d.get("expires_at")
                    if not _exp_str:
                        continue
                    expires_3d = datetime.fromisoformat(str(_exp_str).replace("Z", "+00:00"))
                    if expires_3d.tzinfo is None:
                        expires_3d = expires_3d.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                # Claim atomically: also recheck status and the expiry window
                # so a renewal or cancellation between the query and this write
                # does not mark a stale or renewed row as warned.
                claimed = await db.update_one(
                    "driver_subscriptions",
                    {
                        "id": sub_3d["id"],
                        "expiry_warned_3d": False,
                        "status": "active",
                        "$and": [
                            {"expires_at": {"$gt": window_24h.isoformat()}},
                            {"expires_at": {"$lte": window_3d.isoformat()}},
                        ],
                    },
                    {"$set": {"expiry_warned_3d": True}},
                )
                if claimed is None:
                    continue
                drv_3d = await db.find_one("drivers", {"id": sub_3d["driver_id"]})
                if drv_3d and drv_3d.get("user_id"):
                    days_left = max(1, int((expires_3d - now).total_seconds() / 86400))
                    plan_name = sub_3d.get("plan_name", "Spinr Pass")
                    try:
                        await send_push_notification(
                            drv_3d["user_id"],
                            "Spinr Pass Expiring in 3 Days",
                            f"Your {plan_name} plan expires in ~{days_left} day{'s' if days_left != 1 else ''}. Renew now to keep driving!",
                            {"type": "subscription_expiring_3d", "days_left": str(days_left)},
                        )
                        warned_3d_count += 1
                    except Exception as e:
                        logger.warning(
                            "[SUB-EXPIRY] 3d push failed for driver %s: %s",
                            sub_3d["driver_id"],
                            e,
                        )

            logger.info(
                "[SUB-EXPIRY] Check complete. %d scanned, %d 24h warned, %d 3d warned, %d enforced offline.",
                len(active_subs),
                warned_count,
                warned_3d_count,
                enforced_count,
            )

        except Exception as e:
            logger.error(f"[SUB-EXPIRY] Background check error: {e}", exc_info=True)

        try:
            from utils.loop_monitor import record_heartbeat as _lm_hb

            _lm_hb("subscription_expiry (6h)")
        except ImportError:
            pass

        await asyncio.sleep(6 * 3600)
