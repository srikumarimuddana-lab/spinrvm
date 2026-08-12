from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile  # type: ignore
from pydantic import BaseModel, Field  # type: ignore

try:
    from .. import db_supabase  # type: ignore
    from ..dependencies import OTP_EXPIRY_MINUTES, generate_otp, get_current_user  # type: ignore
    from ..routes.auth import (  # type: ignore
        _check_otp_lockout,
        _clear_otp_failures,
        _enforce_otp_send_cap,
        _record_otp_failure,
    )
    from ..schemas import CreateProfileRequest, UserProfile  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..utils.audit_logger import log_admin_action, log_user_action  # type: ignore
    from ..utils.background import spawn  # type: ignore
    from ..utils.crypto import hash_otp, verify_otp_hash  # type: ignore
    from ..utils.error_handling import ErrorCode, SpinrException  # type: ignore
    from ..utils.error_keys import ErrorKeys  # type: ignore
    from ..utils.insurance_periods import record_period_transition  # type: ignore
    from ..utils.rate_limiter import dsar_export_limit, rider_email_verify_request_limit  # type: ignore
    from ..utils.redis_client import redis_delete  # type: ignore
    from ..utils.referral_terms import (  # type: ignore
        area_id_for_rider,
        paid_referee_earnings,
        paid_referral_earnings,
        resolve_referral_terms,
    )
    from ..utils.refresh_tokens import revoke_all_for_user  # type: ignore
    from ..utils.rider_emails import (  # type: ignore
        send_account_deletion_notice,
        send_email_changed_notice,
        send_email_verification_code,
        send_welcome_email,
    )
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import OTP_EXPIRY_MINUTES, generate_otp, get_current_user  # type: ignore  # noqa: F811
    from routes.auth import (  # type: ignore  # noqa: F811
        _check_otp_lockout,
        _clear_otp_failures,
        _enforce_otp_send_cap,
        _record_otp_failure,
    )
    from schemas import CreateProfileRequest, UserProfile  # type: ignore
    from settings_loader import get_app_settings  # type: ignore  # noqa: F811
    from utils.audit_logger import log_admin_action, log_user_action  # type: ignore  # noqa: F811
    from utils.background import spawn  # type: ignore  # noqa: F811
    from utils.crypto import hash_otp, verify_otp_hash  # type: ignore  # noqa: F811
    from utils.error_handling import ErrorCode, SpinrException  # type: ignore  # noqa: F811
    from utils.error_keys import ErrorKeys  # type: ignore  # noqa: F811
    from utils.insurance_periods import record_period_transition  # type: ignore  # noqa: F811
    from utils.rate_limiter import dsar_export_limit, rider_email_verify_request_limit  # type: ignore  # noqa: F811
    from utils.redis_client import redis_delete  # type: ignore  # noqa: F811
    from utils.referral_terms import (  # type: ignore  # noqa: F811
        area_id_for_rider,
        paid_referee_earnings,
        paid_referral_earnings,
        resolve_referral_terms,
    )
    from utils.refresh_tokens import revoke_all_for_user  # type: ignore  # noqa: F811
    from utils.rider_emails import (  # type: ignore  # noqa: F811
        send_account_deletion_notice,
        send_email_changed_notice,
        send_email_verification_code,
        send_welcome_email,
    )
import asyncio
import base64
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

_RIDER_EMAIL_OTP_TABLE = "rider_email_verification_otp"

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/users", tags=["Users"])


@api_router.get("/profile", response_model=UserProfile)
async def get_profile(current_user: dict = Depends(get_current_user)):
    """Get the current user's profile."""
    user = await db_supabase.get_user_by_id(current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User profile not found")
    return UserProfile(**user)


@api_router.post("/profile", response_model=UserProfile)
async def create_profile(request: CreateProfileRequest, current_user: dict = Depends(get_current_user)):
    valid_genders = ["Male", "Female", "Other"]
    if request.gender not in valid_genders:
        raise HTTPException(status_code=400, detail=f"Gender must be one of: {', '.join(valid_genders)}")

    # Uber-style single-identity model: one person = one account holding both
    # rider and driver roles (is_rider/is_driver), keyed on a unique phone AND a
    # unique email. If this email already belongs to another account, the user
    # already has an account — point them to log in / recover it rather than
    # creating a duplicate. We deliberately do NOT disclose the other account's
    # phone number (PII / account-enumeration).
    email_lower = request.email.strip().lower()
    existing_email_user = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("users", {"email": email_lower, "id": {"$ne": current_user["id"]}}, limit=1)
    )
    if existing_email_user:
        # Structured error so mobile clients derive a field-specific toast
        # title ("Email Already In Use") from `error.code` + `details.field`
        # instead of regex-sniffing the message. Message kept ≤140 chars so it
        # fits a toast whole (see shared/utils/toastMessage.ts TOAST_MESSAGE_MAX).
        raise SpinrException(
            message=(
                "This email is already linked to an existing Spinr account. "
                "Please log in to that account, or contact support if you can't access it."
            ),
            error_code=ErrorCode.RESOURCE_ALREADY_EXISTS,
            status_code=400,
            details={"field": "email"},
            message_key=ErrorKeys.PROFILE_EMAIL_IN_USE,
        )

    old_email = (current_user.get("email") or "").lower()
    email_changed = email_lower != old_email
    # This endpoint serves both first-time setup and later profile edits, and
    # they warrant different mail: a welcome once, a security notice thereafter.
    first_time_setup = not bool(current_user.get("profile_complete"))

    update_data = {
        "first_name": request.first_name.strip(),
        "last_name": request.last_name.strip(),
        "email": email_lower,
        "gender": request.gender,
        "profile_complete": True,
    }
    if email_changed:
        update_data["email_verified"] = False
        update_data["email_verified_at"] = None
    # Allow driver app to hint the role so onboarding status is computed.
    # We write the flag (is_driver / is_rider) rather than the role column so
    # that a user who starts driver onboarding keeps is_rider=true (dual-role).
    if request.role == "driver":
        update_data["role"] = "driver"
        update_data["is_driver"] = True
        # is_rider intentionally left unchanged
    elif request.role == "rider":
        update_data["is_rider"] = True

    await db_supabase.update_one("users", {"id": current_user["id"]}, update_data)
    updated_user = await db_supabase.get_user_by_id(current_user["id"])

    if not updated_user:
        raise HTTPException(
            status_code=500,
            detail="Database error: Could not retrieve updated user profile. Check server logs for DB connection issues.",
        )

    # Backgrounded: profile setup is a user-facing request and neither of these
    # is worth adding provider latency to it. Both senders swallow their own
    # failures — the profile write above is already committed.
    if first_time_setup:
        # Nothing in the rider flow ever confirmed the address we hold actually
        # works; this is the first and only check.
        spawn(send_welcome_email(updated_user))
    elif email_changed and old_email:
        # Security notice to the address that was just replaced. If someone else
        # made this change, that address is the only way to reach the person who
        # owned the account.
        spawn(send_email_changed_notice(updated_user, old_email))

    return UserProfile(**updated_user)


async def _fulfill_rider_data_export(user_id: str, email: str, request_id: str) -> None:
    """Background task: actually build and email the DSAR export, then reflect
    the real outcome on the queued `data_export_requests` row.

    Reuses `_build_and_email_data_export` from the driver module rather than
    duplicating it (N1, ACTION_ITEMS.md). That function now includes a
    rider-shaped `rides_as_rider` + `saved_addresses` read alongside its
    original driver-shaped `rides`/`payouts`/`documents` (gated on having a
    `drivers` row) — an earlier version of this fix reused it before that
    branch existed and shipped an export containing only account +
    notification-preferences for a rider-only account, which is not a real
    answer to a PIPEDA access request from someone whose relationship with
    Spinr is as a rider. The export itself never raises out (its own
    try/except logs the full traceback at error level on failure).
    """
    try:
        from .drivers.tax_exports import _build_and_email_data_export  # type: ignore
    except ImportError:
        from routes.drivers.tax_exports import _build_and_email_data_export  # type: ignore

    succeeded = await _build_and_email_data_export(user_id, email)
    try:
        await db_supabase.update_one(
            "data_export_requests",
            {"id": request_id},
            {
                "status": "completed" if succeeded else "pending",
                "completed_at": datetime.now(timezone.utc).isoformat() if succeeded else None,
            },
        )
    except Exception:
        # The export itself already succeeded or failed and was logged by
        # _build_and_email_data_export above — a failure here only means the
        # DSAR queue row's status didn't update, not that the export was
        # lost. Still surfaced loudly: an admin relying on the queue's status
        # to track SLA compliance needs to know it may be stale.
        logger.error(
            "Failed to update data_export_requests status for request %s (user %s)",
            request_id,
            user_id,
            exc_info=True,
        )


@api_router.post("/data-export")
@dsar_export_limit
async def request_data_export(request: Request = None, current_user: dict = Depends(get_current_user)):
    """R-P1-6 / DV-17 / N1 PIPEDA DSAR: queue a data-export request with 30-day
    SLA tracking, and actually fulfil it.

    Previously this only inserted the `data_export_requests` row and stopped —
    nothing built the export or emailed it, so a rider's access request sat
    unfulfilled until an admin noticed it (there is no admin-side automation
    either; ACTION_ITEMS.md N1). Now it also spawns the same build-and-email
    flow the driver-side `/drivers/me/export-data` endpoint already uses,
    self-swallowing per the shared `spawn()` helper's contract so a background
    failure can't affect this response.

    Rate-limited (@dsar_export_limit, 3/hour) — this now fans out the same
    DB-reads + ZIP-build + Storage-upload + email pipeline the driver export
    does, not just a single insert, so it needs the same abuse cap (storage
    fill / SES exhaustion). SlowAPI needs a parameter named ``request`` typed
    as starlette Request; do not remove it (mirrors
    routes/drivers/tax_exports.py::export_driver_data).

    PIPEDA s.9 requires a response within 30 days of receipt — the queued row
    with its SLA deadline is recorded either way, so even if no email is on
    file (fulfilment is skipped, logged, and the row stays 'pending' for an
    admin to handle manually) the request itself is never lost.
    """
    user_id = current_user["id"]
    request_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    response_due_at = (now + timedelta(days=30)).isoformat()
    logger.info(f"DSAR submitted for user {user_id} request_id={request_id} due={response_due_at}")
    try:
        export_record = {
            "id": request_id,
            "user_id": user_id,
            "status": "pending",
            "requested_at": now.isoformat(),
            "response_due_at": response_due_at,
        }
        await db_supabase.insert_one("data_export_requests", export_record)
    except Exception as e:
        logger.error(
            f"Could not record data export request for user {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="data_export_request_failed") from e

    email = (current_user.get("email") or "").strip()
    if "@" in email:
        spawn(_fulfill_rider_data_export(user_id, email, request_id))
    else:
        # Matches the driver endpoint's own requirement (a phone-number
        # fallback would leak a raw phone number to the email provider and
        # fail to send anyway) — the request row stays 'pending' for an
        # admin to fulfil manually via the existing DSAR admin queue.
        logger.warning(
            "DSAR request %s for user %s has no email on file — request recorded but not auto-fulfilled",
            request_id,
            user_id,
        )

    return {
        "success": True,
        "request_id": request_id,
        "response_due_at": response_due_at,
        "message": "Data export requested. We will respond within 30 days as required by PIPEDA.",
    }


# NOTE: ride anonymization on deletion was intentionally removed — rides stay
# fully attributable to the rider for the 7-year retention window and are then
# hard-deleted by purge_pii_retention() (Uber/Lyft model). GPS coords still drop
# at the separate 3-year ceiling inside that same purge.


async def _assert_deletable(user_id: str) -> None:
    """Refuse deletion while the account still has something outstanding.

    The driver app already told drivers this was enforced ("Deletion rejections
    are actionable (active ride, unsettled balance, pending payout)" — see
    driver-app/app/driver/settings.tsx) but nothing on the backend checked, so
    deletion would tombstone a driver mid-ride and lock them out of a positive
    earnings balance they can no longer withdraw.

    Raises 409 with an actionable reason. Deliberately NOT 403/400: the request
    is well-formed and the caller is authorised, the account is just not in a
    deletable state yet, and the client shows the detail verbatim.
    """
    try:
        from ..models.ride_status import RideStatus  # type: ignore
    except ImportError:
        from models.ride_status import RideStatus  # type: ignore

    active = [s.value for s in RideStatus.active_statuses()]
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": user_id, "deleted_at": None}, limit=1)
    )

    # A ride in flight, on either side of it. Tombstoning mid-ride would strand
    # the counterparty: the rider loses their driver, or the driver loses the
    # trip they are being paid for.
    ride_filters = [{"rider_id": user_id, "status": {"$in": active}}]
    if driver:
        ride_filters.append({"driver_id": driver["id"], "status": {"$in": active}})
    for _filter in ride_filters:
        if await db_supabase.get_rows("rides", _filter, limit=1):
            raise HTTPException(
                status_code=409,
                detail="You have a ride in progress. Please finish or cancel it before deleting your account.",
            )

    if not driver:
        return

    # A payout mid-flight settles asynchronously through Stripe; deleting now
    # would leave money moving toward an account nobody can reconcile against.
    if await db_supabase.get_rows("payouts", {"driver_id": driver["id"], "status": "pending"}, limit=1):
        raise HTTPException(
            status_code=409,
            detail="You have a payout still processing. Please wait for it to complete before deleting your account.",
        )

    # Unwithdrawn earnings. Deletion revokes every token, so a driver who
    # deletes with a positive balance can no longer reach the payout screen to
    # claim it. Reuses the earnings module's balance rather than recomputing —
    # that function is the single source of truth for what Spinr owes a driver
    # (rides + bonuses - all money-out payouts), and a second implementation
    # here would drift from the number the driver sees in the app.
    try:
        from .drivers import earnings as _earnings  # type: ignore
    except ImportError:
        from routes.drivers import earnings as _earnings  # type: ignore

    balance = await _earnings.get_driver_balance({"id": user_id})
    payable = Decimal(str(balance.get("payable_balance") or "0"))
    if payable > 0:
        raise HTTPException(
            status_code=409,
            detail=(f"You have ${payable} in unpaid earnings. Please withdraw them before deleting your account."),
        )


async def _tombstone_driver_row(user_id: str, now: str) -> Optional[dict]:
    """Soft-delete the caller's driver row and take it out of service.

    Setting `deleted_at` alone was not enough. `drivers.status` stays whatever
    it was (normally 'active') and there is no 'deleted' value in the status
    set, so the row stayed indistinguishable from a working driver on every
    bulk `get_rows` read — including the dispatch candidate query, which does
    not join the users row and so never saw `status='pending_deletion'`.

    Two things therefore have to happen here, not just the `deleted_at` stamp:

    1. Clear the intent flags. `is_online`/`is_available` are what dispatch
       actually filters on. The `stale_intent` background loop would flip them
       eventually (after `stale_intent_offline_hours`, default 4h), but that is
       a reconciler for unreachable apps, not the correct primary path for a
       driver who explicitly left — and its presence-based safety net is
       fail-open, so a Redis outage inside that window could still route a ride
       to a deleted driver.
    2. Close the open insurance period. `driver_insurance_periods` is the
       append-only 7-year SGI/insurance audit trail; a driver who deletes while
       online (Period 1) otherwise leaves a period row that never ends. Mirrors
       what the go-offline toggle does (`routes/drivers/status.py`).

    Returns the driver row (pre-update) or None when the caller is not a driver
    (or was already tombstoned — the lookup filters `deleted_at IS NULL`, so a
    repeat call is a no-op rather than a double-write that would append another
    row to the insurance-period audit table).
    """
    rows = await db_supabase.get_rows("drivers", {"user_id": user_id, "deleted_at": None}, limit=1)
    driver = rows[0] if rows else None
    if not driver:
        return None

    updates = {
        "deleted_at": now,
        "is_online": False,
        "is_available": False,
        "went_offline_at": now,
    }

    # Spinr stops dispatching immediately, but the regulator (SGI in SK) still
    # lists this driver as an active passenger-for-hire driver until we file the
    # D00032/D00033 "remove" rows. Nothing triggered that filing, so it depended
    # on someone remembering. Queue it here, stamped with the date the driver
    # actually stopped — SGI cares about that date, not the date an admin got
    # round to generating the form.
    #
    # Only for drivers who were actually filed with the regulator: an applicant
    # who never got approved was never added, so there is nothing to remove and
    # queueing them would bury the real backlog in noise.
    if driver.get("is_verified") or driver.get("regulatory_authority_approved"):
        updates["regulator_removal_required"] = True
        updates["regulator_removal_effective_date"] = now[:10]

    await db_supabase.update_one("drivers", {"id": driver["id"]}, updates)
    # `_pre_invalidate_for_table` only evicts the cache keys present in the
    # update's filter dict, so keying the write by `id` leaves the separate
    # by-user_id entry (30s TTL, read by get_current_user on every request)
    # serving the pre-deletion row. Evict both explicitly.
    await db_supabase.invalidate_driver_cache(driver_id=driver["id"], user_id=user_id)
    # Period 0 = out of the app entirely, personal auto insurance only.
    # Non-raising by contract (see utils/insurance_periods.py) — a missed
    # transition must not block the deletion the user asked for.
    await record_period_transition(driver["id"], 0)
    return driver


@api_router.delete("/account")
async def delete_account_pipeda(current_user: dict = Depends(get_current_user)):
    """Soft-delete / tombstone the account (Uber/Lyft model).

    The account is LOCKED immediately (tokens revoked, status pending_deletion)
    but records stay FULLY ATTRIBUTABLE — no anonymization — for the 7-year SK
    Transportation Act / tax retention window. The daily purge hard-deletes the
    account + its footprint once that window elapses; the rider can reactivate
    by OTP login any time before then. PIPEDA erasure is satisfied via that
    lawful-retention carve-out, not immediate deletion.
    """
    user_id = current_user["id"]
    logger.info(f"Account deletion (soft/tombstone) requested for user {user_id}")

    # Refuse while a ride, payout, or unwithdrawn balance is outstanding.
    # Raises 409 before anything is written, so a blocked deletion leaves the
    # account exactly as it was.
    await _assert_deletable(user_id)

    # 7-year retention ceiling (2557 days ≈ 7y). The purge compares against this.
    grace_period_end = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2557)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    try:
        # Bump token_version so every already-issued access token (and WebSocket
        # session, whose handshake checks token_version) is rejected immediately —
        # not just on the per-request status guard. Without this a deletion-requested
        # account could keep a live realtime channel until its token's TTL lapsed.
        next_token_version = int(current_user.get("token_version") or 0) + 1
        await db_supabase.update_one(
            "users",
            {"id": user_id},
            {
                "deletion_requested_at": now,
                "deletion_scheduled_at": grace_period_end,
                "status": "pending_deletion",
                "token_version": next_token_version,
            },
        )
        await _tombstone_driver_row(user_id, now)
        # Kill credentials at the root: revoke every refresh token (so /auth/refresh
        # can't rotate a deleting account back to life) and drop the Redis session
        # mirror. Best-effort — the deletion is already recorded above, and the daily
        # purge + the per-request guard remain the durable enforcement.
        try:
            await revoke_all_for_user(user_id)
            await redis_delete(f"session:{user_id}")
        except Exception:
            logger.error("Account deletion: session/refresh-token revocation failed (non-fatal)", exc_info=True)
        # Rides are deliberately NOT anonymized — they stay linked to this rider
        # (attributable) for the full 7-year retention, then are hard-deleted by
        # the purge. Only the GPS coords drop at the separate 3-year ceiling.
        await log_admin_action(
            {"id": user_id, "role": "user"},
            action="dsar_deletion_requested",
            resource="users",
            resource_id=user_id,
            details={"grace_period_end": grace_period_end, "pipeda": True},
        )
        logger.info(f"Account deletion scheduled for user {user_id} (grace period until {grace_period_end})")
        # Confirm in writing what was deleted, what is kept and why, and when it
        # finally goes. The in-app message says this too, but the account is now
        # locked — the rider cannot go back and re-read it. Backgrounded and
        # self-swallowing; the tombstone above is already committed.
        spawn(send_account_deletion_notice(current_user, grace_period_end))
        return {
            "success": True,
            "message": (
                "Your account has been deactivated. Your ride records are kept to meet "
                "regulatory requirements and are then permanently deleted — sign in again "
                "anytime to reactivate."
            ),
        }
    except Exception as e:
        logger.error(f"Account deletion failed for user {user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to schedule account deletion. Please contact support.",
        ) from e


@api_router.delete("/profile")
async def delete_account(current_user: dict = Depends(get_current_user)):
    """Permanently delete the current user's account and all associated data."""
    user_id = current_user["id"]
    logger.info(f"Account deletion requested for user {user_id}")

    # Same outstanding-work gate as the soft-delete path. This endpoint is
    # documented as internal-admin tooling, but it takes a normal user token,
    # so leaving it ungated would be a way around the guard — and its deletion
    # is irreversible, which makes stranding a balance worse here, not better.
    await _assert_deletable(user_id)

    now = datetime.now(timezone.utc).isoformat()
    try:
        # Soft-delete driver record (preserves audit trail), clear the intent
        # flags dispatch reads, and close any open insurance period.
        await _tombstone_driver_row(user_id, now)
        # Hard-delete non-sensitive ancillary data (no soft-delete column)
        await db_supabase.delete_many("driver_documents", {"driver_id": user_id})
        await db_supabase.delete_many("emergency_contacts", {"user_id": user_id})
        await db_supabase.delete_many("saved_addresses", {"user_id": user_id})
        # Rides are kept attributable (no anonymization) — retained and then
        # hard-deleted by the 7-year retention purge like every other account.
        # Soft-delete the user record
        await db_supabase.update_one("users", {"id": user_id}, {"deleted_at": now})
        await log_admin_action(
            {"id": user_id, "role": "user"},
            action="dsar_deletion_executed",
            resource="users",
            resource_id=user_id,
            details={"immediate": True, "pipeda": True},
        )
        logger.info(f"Account deleted successfully for user {user_id}")
        return {"success": True, "message": "Account permanently deleted"}
    except Exception as e:
        logger.error(f"Account deletion failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete account. Please contact support.") from e


class UpdatePhoneRequest(BaseModel):
    phone: str


@api_router.patch("/profile/phone", response_model=UserProfile)
async def update_phone(request: UpdatePhoneRequest, current_user: dict = Depends(get_current_user)):
    """Update the current user's phone number."""
    phone = request.phone.strip()
    if len(phone) < 10:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    # Check if phone is already in use by another user
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("users", {"phone": phone, "id": {"$ne": current_user["id"]}}, limit=1)
    )
    if existing:
        raise HTTPException(status_code=400, detail="Phone number already in use")

    await db_supabase.update_one("users", {"id": current_user["id"]}, {"phone": phone})
    updated_user = await db_supabase.get_user_by_id(current_user["id"])

    if not updated_user:
        raise HTTPException(
            status_code=500,
            detail="Database error: Could not retrieve updated user profile.",
        )

    return UserProfile(**updated_user)


def _compress_profile_image(content: bytes, content_type: str) -> "tuple[bytes, str]":
    """Resize + recompress a profile photo to a small, load-fast JPEG.

    Profile photos render at avatar size, so a 512px square JPEG is ample and
    keeps the stored file — and therefore every avatar/profile load and every
    API response that carries the URL/base64 — tiny. The rider app already crops
    to 1:1 before upload; this bounds the longest edge and drops EXIF/metadata
    regardless of client. Returns (bytes, mime); on any processing error it
    falls back to the original bytes so an upload never hard-fails on an odd
    image.
    """
    try:
        from io import BytesIO

        from PIL import Image, ImageOps

        img = Image.open(BytesIO(content))
        img = ImageOps.exif_transpose(img)  # honour phone-camera rotation
        # Flatten transparency onto white — JPEG has no alpha channel.
        if img.mode in ("RGBA", "LA", "P"):
            base = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            base.paste(rgba, mask=rgba.split()[-1])
            img = base
        else:
            img = img.convert("RGB")
        img.thumbnail((512, 512), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=82, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:
        logger.warning("[profile-image] compression failed — storing original bytes", exc_info=True)
        return content, content_type


async def store_profile_image(user_id: str, content: bytes, content_type: str) -> str:
    """Compress + store a profile photo for ``user_id`` and return the stored
    value: a public Supabase Storage URL, or a base64 data-URI fallback when
    storage is unavailable/unconfigured so uploads never hard-fail.

    Shared by the self-serve endpoint below and the admin upload-on-behalf
    endpoint (routes/admin/drivers.py). CPU-bound compression and the blocking
    storage call both run off the event loop.
    """
    content, content_type = await asyncio.to_thread(_compress_profile_image, content, content_type)
    _ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(content_type, "jpg")
    object_path = f"{user_id}/{uuid.uuid4()}.{_ext}"
    profile_value: Optional[str] = None
    try:
        sb = getattr(db_supabase, "supabase", None)
        if sb:

            def _upload() -> Optional[str]:
                sb.storage.from_("profile-photos").upload(
                    file=content,
                    path=object_path,
                    file_options={"content-type": content_type, "upsert": "true"},
                )
                res = sb.storage.from_("profile-photos").get_public_url(object_path)
                return res if isinstance(res, str) else getattr(res, "public_url", None)

            profile_value = await asyncio.to_thread(_upload)
    except Exception:
        logger.warning("[profile-image] storage upload failed — falling back to base64", exc_info=True)
        profile_value = None

    if not profile_value:
        profile_value = f"data:{content_type};base64,{base64.b64encode(content).decode('utf-8')}"
    return profile_value


@api_router.put("/profile-image", response_model=UserProfile)
async def upload_profile_image(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """Upload a profile image for the current user (resized to a small JPEG)."""
    # Validate file type
    allowed_types = ["image/jpeg", "image/png", "image/webp", "image/gif"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="File must be an image (JPEG, PNG, WebP, or GIF)")

    # Validate file size (max 5MB)
    content = await file.read()
    if not isinstance(content, bytes):
        content = bytes(content) if hasattr(content, "__bytes__") else str(content).encode("utf-8")

    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image must be smaller than 5MB")

    # Compress + store (public URL, or base64 fallback). Shared with the admin
    # upload-on-behalf endpoint. `file.content_type` is validated above.
    profile_value = await store_profile_image(current_user["id"], content, file.content_type)

    # Riders' profile photos are visible immediately; only driver photos
    # go to the admin review queue (identity/safety check before going online).
    image_status = "pending_review" if current_user.get("role") == "driver" else "approved"

    await db_supabase.update_one(
        "users",
        {"id": current_user["id"]},
        {
            "profile_image": profile_value,
            "profile_image_status": image_status,
        },
    )
    updated_user = await db_supabase.get_user_by_id(current_user["id"])

    if not updated_user:
        raise HTTPException(
            status_code=500,
            detail="Database error: Could not retrieve updated user profile.",
        )

    return UserProfile(**updated_user)


class LinkCorporateRequest(BaseModel):
    corporate_account_id: Optional[str] = None


@api_router.patch("/profile/corporate", response_model=UserProfile)
async def link_corporate_account(request: LinkCorporateRequest, current_user: dict = Depends(get_current_user)):
    """Link or unlink a corporate account to the user profile."""
    if request.corporate_account_id:
        account = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("corporate_accounts", {"id": request.corporate_account_id}, limit=1)
        )
        if not account:
            raise HTTPException(status_code=404, detail="Corporate account not found")

        # WS-18: verify the user has an active membership in this corporate
        # account. Without this, any authenticated user could link themselves
        # to any company and gain access to corporate-paid rides.
        membership = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows(
                "corporate_members",
                {
                    "user_id": current_user["id"],
                    "company_id": request.corporate_account_id,
                    "status": "active",
                },
                limit=1,
            )
        )
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this corporate account")

    await db_supabase.update_one(
        "users",
        {"id": current_user["id"]},
        {"corporate_account_id": request.corporate_account_id},
    )

    updated_user = await db_supabase.get_user_by_id(current_user["id"])
    if not updated_user:
        raise HTTPException(status_code=500, detail="Could not retrieve updated profile.")

    return UserProfile(**updated_user)


# ============================================================
# GAP FIX: Emergency Contacts (Uber/Lyft/Grab standard feature)
# ============================================================


class EmergencyContactCreate(BaseModel):
    name: str
    phone: str
    relationship: str = "Friend"  # Friend, Family, Spouse, Other


class EmergencyContactResponse(BaseModel):
    id: str
    user_id: str
    name: str
    phone: str
    relationship: str


@api_router.get("/emergency-contacts")
async def get_emergency_contacts(current_user: dict = Depends(get_current_user)):
    """Get the user's emergency contacts."""
    try:
        contacts = await db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=100)
    except Exception as e:
        logger.error(
            f"Could not fetch emergency contacts for user {current_user['id']}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not load emergency contacts. Please try again.",
        ) from e
    return {"contacts": contacts}


@api_router.post("/emergency-contacts")
async def add_emergency_contact(contact: EmergencyContactCreate, current_user: dict = Depends(get_current_user)):
    """Add an emergency contact (max 3 contacts per user, matching Uber/Lyft)."""
    try:
        existing = await db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=100)
    except Exception as e:
        logger.error(
            f"Could not check emergency contact count for user {current_user['id']}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not verify contact limit. Please try again.",
        ) from e

    MAX_EMERGENCY_CONTACTS = 3
    if len(existing) >= MAX_EMERGENCY_CONTACTS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_EMERGENCY_CONTACTS} emergency contacts allowed. Remove one before adding another.",
        )

    phone = contact.phone.strip()
    if len(phone) < 10:
        raise HTTPException(status_code=400, detail="Invalid phone number for emergency contact")

    contact_doc = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "name": contact.name.strip(),
        "phone": phone,
        "relationship": contact.relationship,
    }

    await db_supabase.insert_one("emergency_contacts", contact_doc)
    return {"success": True, "contact": contact_doc}


@api_router.delete("/emergency-contacts/{contact_id}")
async def delete_emergency_contact(contact_id: str, current_user: dict = Depends(get_current_user)):
    """Remove an emergency contact."""
    contact = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "emergency_contacts",
            {"id": contact_id, "user_id": current_user["id"]},
            limit=1,
        )
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Emergency contact not found")

    await db_supabase.delete_one("emergency_contacts", {"id": contact_id})
    return {"success": True}


# ── Rider Referral Program ───────────────────────────────────────────
# Riders refer riders. "First-ride bonus, both sides": the referee gets a
# first-ride credit and the referrer earns a credit once the referee completes
# their first paid ride. Reward terms live here as the single source of truth.
# NOTE: like the driver referral, the figures below are TRACKED/DISPLAYED only —
# the actual wallet crediting is a separate money task (idempotent payout +
# migration + money-auditor review) and is intentionally not done here.
RIDER_REFERRAL_RIDES_REQUIRED = 1
RIDER_REFERRER_REWARD = 5  # CAD — referrer credit once referee takes first ride
RIDER_REFEREE_REWARD = 5  # CAD — new rider's first-ride credit
# Days the referee has to reach RIDER_REFERRAL_RIDES_REQUIRED (from
# referral_applied_at) before the referral expires unpaid. 0 = no deadline.
# Per-area override lives in service_areas.rider_referral_window_days (migration 189).
RIDER_REFERRAL_WINDOW_DAYS = 30


def _rider_referral_code(user: dict) -> str:
    """Rider's shareable code — a stored custom code, else derived from the id."""
    return user.get("referral_code") or f"RIDE{user['id'][:8].upper()}"


def _fmt_money(v) -> str:
    """Format a money amount for display copy: '5' for 5.00, '5.50' otherwise."""
    d = Decimal(str(v))
    return str(int(d)) if d == d.to_integral_value() else f"{d:.2f}"


async def _rider_referral_summary(user: dict, *, include_referees: bool) -> dict:
    code = _rider_referral_code(user)

    # The shown reward follows the viewing rider's current service area (derived
    # from their most recent ride); a brand-new rider with no area-resolved rides
    # → global default. Referee progress/earnings are an estimate against the
    # viewer's area threshold — actual payouts use each referee's own area.
    area_id = await area_id_for_rider(user["id"])
    terms = await resolve_referral_terms(area_id, "rider")
    rides_required = terms["rides"]
    referrer_reward = terms["referrer"]
    referee_reward = terms["referee"]

    referred = await db_supabase.get_rows(
        "users",
        {"referral_code_used": code},
        columns="id,first_name,last_name,created_at",
        limit=200,
    )
    referees: list = []
    qualified = 0
    for u in referred:
        completed = await db_supabase.count_documents("rides", {"rider_id": u["id"], "status": "completed"})
        is_qualified = completed >= rides_required
        if is_qualified:
            qualified += 1
        if include_referees:
            referees.append(
                {
                    "name": f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Rider",
                    "referred_at": u.get("created_at", ""),
                    "completed_rides": completed,
                    "rides_required": rides_required,
                    "qualified": is_qualified,
                    "status": "earned" if is_qualified else "in_progress",
                }
            )
    total = len(referred)
    # Earned total: prefer the snapshotted sum of PAID payouts so it never changes
    # retroactively when area terms or the rider's area change; fall back to the
    # estimate (current reward × qualified) until the payout loop has actually
    # paid this referrer (the loop runs every 5 min, so the snapshot wins shortly
    # after a referee qualifies).
    paid = await paid_referral_earnings(user["id"], "rider")
    earnings = paid if paid is not None else (referrer_reward * qualified)
    # The viewer's OWN signup bonus — what they earned as a REFEREE (referred by
    # someone). Actual paid amount only (a user is referred at most once; there's
    # no meaningful pre-payout estimate for a past signup), 0 when not referred /
    # not yet paid. Surfaced so the "Refer & Earn" screen shows the referee's $5,
    # which previously only appeared as a raw wallet transaction.
    referee_earned = await paid_referee_earnings(user["id"], "rider") or Decimal("0")
    summary = {
        "referral_code": code,
        "referral_link": f"https://spinr.app/r/{code}",
        "total_referrals": total,
        "qualified_referrals": qualified,
        "pending_referrals": total - qualified,
        # Money serialised as 2-dp strings (house convention; clients parseFloat).
        "referral_earnings": str(earnings),
        "referee_earnings": str(referee_earned),
        "referrer_reward": str(referrer_reward),
        "referee_reward": str(referee_reward),
        "rides_required": rides_required,
        # Admin-authored per-area T&C wins; otherwise generate the default
        # sentence from this area's reward numbers.
        "terms": terms.get("terms")
        or (
            f"Give ${_fmt_money(referee_reward)}, get ${_fmt_money(referrer_reward)} when your friend "
            f"{_ride_phrase(rides_required)}."
        ),
    }
    if include_referees:
        summary["referees"] = referees
    return summary


def _ride_phrase(rides_required: int) -> str:
    """Human phrase for the referral ride threshold (per-area configurable)."""
    return "takes their first ride" if rides_required == 1 else f"completes {rides_required} rides"


class ApplyRiderReferralRequest(BaseModel):
    referral_code: str


@api_router.get("/referral")
async def get_rider_referral_info(current_user: dict = Depends(get_current_user)):
    """The rider's own referral code, link, and progress stats."""
    user = await db_supabase.get_user_by_id(current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await _rider_referral_summary(user, include_referees=False)


@api_router.get("/referrals")
async def get_rider_referrals(current_user: dict = Depends(get_current_user)):
    """The riders this user has referred, with first-ride progress."""
    user = await db_supabase.get_user_by_id(current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return await _rider_referral_summary(user, include_referees=True)


@api_router.post("/referral/apply")
async def apply_rider_referral(req: ApplyRiderReferralRequest, current_user: dict = Depends(get_current_user)):
    """Apply a referral code (at signup or later). Links the referee to the referrer."""
    code = req.referral_code.strip().upper()

    user = await db_supabase.get_user_by_id(current_user["id"])
    if user and user.get("referral_code_used"):
        raise HTTPException(status_code=400, detail="Referral code already applied")

    # Resolve the referrer. Codes are "RIDE" + first 8 chars of the user id
    # (case-insensitive contains match — _apply_filters maps $regex to ILIKE),
    # or a stored custom referral_code.
    ref_user = None
    if code.startswith("RIDE"):
        suffix = code[4:]
        if len(suffix) == 8 and suffix.isalnum():
            try:
                ref_user = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows(
                        "users", {"id": {"$regex": suffix, "$options": "i"}}, columns="id", limit=1
                    )
                )
            except Exception as e:
                logger.warning(f"Rider referral code lookup failed: {e}")
    if not ref_user:
        ref_user = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("users", {"referral_code": code}, columns="id", limit=1)
        )

    if not ref_user:
        raise HTTPException(status_code=404, detail="Invalid referral code")
    if ref_user["id"] == current_user["id"]:
        raise HTTPException(status_code=400, detail="You can't use your own referral code")

    await db_supabase.update_one(
        "users",
        {"id": current_user["id"]},
        {
            "referral_code_used": code,
            "referred_by": ref_user["id"],
            # Recorded so the payout loop only rewards rides completed AFTER this.
            "referral_applied_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True, "referral_code": code}


# ── Rider email verification (N14, ACTION_ITEMS.md) ─────────────────────────
#
# Reuses the SAME OTP mechanics as the corporate portal's
# `POST /auth/verify-email-otp` (routes/auth.py:744) — SHA-256-hashed code at
# rest (utils/crypto.hash_otp/verify_otp_hash), the shared brute-force lockout
# (_check_otp_lockout/_record_otp_failure/_clear_otp_failures) and per-
# destination send cap (_enforce_otp_send_cap), and the same dev-bypass
# ("1234" only when ENV != production, refused outright in production when no
# email provider is configured). This is a NEW, separate flow rather than a
# call into the corporate endpoints, because the two differ in what they are
# proving: the corporate flow authenticates an inbox to *log in as* (and will
# create a user row if none exists); this flow proves the rider who is
# ALREADY authenticated owns the email already on their account, and only
# ever flips `email_verified` on that one existing row. Correspondingly this
# keys its lockout/send-cap on `user_id`, not on the email address, so the
# codes for "log in as this email" (corporate) and "prove I read this inbox"
# (rider) never share a bucket even if the same address is used both ways.
#
# Scope boundary (deliberate, see ACTION_ITEMS.md N14): these two endpoints
# are additive only. Nothing calls them automatically and nothing gates on
# `email_verified` — no rider-app UI exists yet to call them, and whether to
# require verification before booking/payouts/etc. is a product decision this
# backend-only change does not make.


class RiderEmailVerifyConfirmRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$")


def _rider_email_verify_lockout_key(user_id: str) -> str:
    """Synthetic lockout-key namespace, mirroring auth.py's
    `_synthetic_phone_for_company_email` pattern but keyed on user_id (see
    module docstring above for why this flow must NOT share a bucket with the
    corporate email-OTP flow)."""
    return f"rider_email_verify:{user_id}"


def _email_log_id(email: str) -> str:
    """Log-safe identifier for an email address (PIPEDA: never log the
    address itself). Mirrors auth.py's `_email_log_id`."""
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:16]
    return f"email_sha256:{digest}"


async def _latest_rider_email_otp(user_id: str) -> Optional[Dict[str, Any]]:
    rows = await db_supabase.get_rows(
        _RIDER_EMAIL_OTP_TABLE,
        {"user_id": user_id, "verified": False},
        order="created_at",
        desc=True,
        limit=1,
    )
    return rows[0] if rows else None


@api_router.post("/verify-email/request")
@rider_email_verify_request_limit
async def request_rider_email_verification(request: Request, current_user: dict = Depends(get_current_user)):
    """Issue a verification code to the email already on the rider's account.

    Does not accept an email in the request body — it verifies whatever is
    already on file, exactly like the corporate flow's "email you're logging
    in as" model but scoped to the signed-in user's own row, so there is no
    way to use this endpoint to probe or verify an address you do not already
    own on this account.
    """
    email = (current_user.get("email") or "").strip().lower()
    if not email:
        raise SpinrException(
            message="Add an email to your profile before verifying it",
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            message_key=ErrorKeys.PROFILE_EMAIL_MISSING,
        )
    if current_user.get("email_verified"):
        return {"success": True, "already_verified": True, "message": "Your email is already verified"}

    user_id = current_user["id"]
    lockout_key = _rider_email_verify_lockout_key(user_id)
    await _enforce_otp_send_cap(lockout_key)

    # Same email-provider bypass as routes/auth.py's send_company_email_otp:
    #  - SES or Resend configured  → real random OTP delivered via email.
    #  - Neither configured + non-production → fixed code "1234" (dev/test).
    #  - Neither configured + production      → refuse (no static-code bypass).
    app_settings = None
    try:
        app_settings = await get_app_settings()
    except Exception as e:
        logger.error(f"Could not read app_settings from DB: {e}", exc_info=True)

    try:
        from ..core.config import settings as _settings  # type: ignore
    except ImportError:
        from core.config import settings as _settings  # type: ignore

    email_provider_configured = bool(
        app_settings
        and (
            (app_settings.get("aws_ses_access_key_id") and app_settings.get("aws_ses_secret_access_key"))
            or app_settings.get("resend_api_key")
        )
    )
    is_production = _settings.ENV.lower() == "production"
    deliver_via_email = True
    if email_provider_configured:
        otp_code = generate_otp()
    elif not is_production:
        otp_code = "1234"
        deliver_via_email = False
        logger.info(
            "Email provider not configured — rider email-verify OTP bypass active (code=1234) for %s (ENV=%s)",
            _email_log_id(email),
            _settings.ENV,
        )
    else:
        logger.error(
            "Email provider not configured in production — refusing to issue rider email-verify OTP "
            "(static-code bypass is disabled in production)"
        )
        raise SpinrException(
            message="Verification is temporarily unavailable, please try again later",
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_SERVICE_UNAVAILABLE,
        )

    otp_row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "email": email,
        "code_hash": hash_otp(otp_code),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat(),
        "verified": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        await db_supabase.delete_many(_RIDER_EMAIL_OTP_TABLE, {"user_id": user_id})
        await db_supabase.insert_one(_RIDER_EMAIL_OTP_TABLE, otp_row)
    except Exception as e:
        logger.error("rider email verify: OTP persist failed for %s", _email_log_id(email), exc_info=True)
        raise SpinrException(
            message="Could not store verification code, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    if deliver_via_email:
        sent = await send_email_verification_code(current_user, otp_code, OTP_EXPIRY_MINUTES)
        if not sent:
            try:
                await db_supabase.delete_many(_RIDER_EMAIL_OTP_TABLE, {"id": otp_row["id"]})
            except Exception:
                logger.error(
                    "rider email verify: failed-code cleanup failed for %s", _email_log_id(email), exc_info=True
                )
            raise HTTPException(status_code=502, detail="Could not send verification code")

    return {"success": True, "message": "Verification code sent"}


@api_router.post("/verify-email/confirm")
async def confirm_rider_email_verification(
    body: RiderEmailVerifyConfirmRequest,
    current_user: dict = Depends(get_current_user),
):
    """Verify the code and flip `email_verified` on the caller's own row.

    Never touches any user other than the authenticated caller — the row to
    update is `current_user["id"]`, not anything derived from the request
    body.
    """
    user_id = current_user["id"]
    email = (current_user.get("email") or "").strip().lower()
    lockout_key = _rider_email_verify_lockout_key(user_id)
    code = body.code.strip()

    await _check_otp_lockout(lockout_key)

    try:
        otp_record = await _latest_rider_email_otp(user_id)
    except Exception as e:
        logger.error("rider email verify: OTP lookup failed for %s", _email_log_id(email), exc_info=True)
        raise SpinrException(
            message="Service temporarily unavailable, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    if not otp_record or not verify_otp_hash(str(otp_record.get("code_hash", "")), code):
        await _record_otp_failure(lockout_key)
        raise SpinrException(
            message="ERR_OTP_INVALID",
            error_code=ErrorCode.AUTH_OTP_INVALID,
            status_code=400,
            message_key=ErrorKeys.AUTH_OTP_INVALID,
            action_hint="Re-enter the verification code",
        )

    expires_at = otp_record.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            logger.error("rider email verify: invalid OTP expires_at for %s", _email_log_id(email), exc_info=True)
            raise SpinrException(
                message="Internal error processing verification code",
                error_code=ErrorCode.INTERNAL_ERROR,
                status_code=500,
                message_key=ErrorKeys.SYSTEM_INTERNAL,
            ) from None
    if not expires_at:
        raise SpinrException(
            message="Internal error processing verification code",
            error_code=ErrorCode.INTERNAL_ERROR,
            status_code=500,
            message_key=ErrorKeys.SYSTEM_INTERNAL,
        )
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise SpinrException(
            message="ERR_OTP_EXPIRED",
            error_code=ErrorCode.AUTH_OTP_EXPIRED,
            status_code=400,
            message_key=ErrorKeys.AUTH_OTP_EXPIRED,
            action_hint="Request a new code",
        )

    # Guard against a mid-flow email change: the code was minted for whatever
    # address was on the account at request time. If the rider's current email
    # has since changed (another tab, a support edit), flipping email_verified
    # now would wrongly verify the NEW address using a code sent to the OLD
    # one.
    otp_email = (otp_record.get("email") or "").strip().lower()
    if otp_email and email and otp_email != email:
        raise SpinrException(
            message="Your email address has changed since this code was sent — request a new code",
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=409,
            message_key=ErrorKeys.PROFILE_EMAIL_VERIFICATION_STALE,
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await db_supabase.update_one(_RIDER_EMAIL_OTP_TABLE, {"id": otp_record["id"]}, {"verified": True})
        await db_supabase.update_one(
            "users",
            {"id": user_id},
            {"email_verified": True, "email_verified_at": now_iso},
        )
    except Exception as e:
        logger.error("rider email verify: flag update failed for user_id=%s", user_id, exc_info=True)
        raise SpinrException(
            message="Service temporarily unavailable, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    await _clear_otp_failures(lockout_key)

    try:
        await log_user_action(current_user, "email_verified", "users", user_id, {"email": _email_log_id(email)})
    except Exception:
        logger.debug("audit_log write failed for rider email_verified event", exc_info=True)

    return {"success": True, "message": "Email verified", "email_verified": True}
