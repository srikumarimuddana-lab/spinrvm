from fastapi import APIRouter, Depends, File, HTTPException, UploadFile  # type: ignore

try:
    from .. import db_supabase  # type: ignore
    from ..dependencies import get_current_user  # type: ignore
    from ..schemas import CreateProfileRequest, UserProfile  # type: ignore
    from ..utils.audit_logger import log_admin_action  # type: ignore
    from ..utils.error_handling import ErrorCode, SpinrException  # type: ignore
    from ..utils.error_keys import ErrorKeys  # type: ignore
    from ..utils.redis_client import redis_delete  # type: ignore
    from ..utils.referral_terms import (  # type: ignore
        area_id_for_rider,
        paid_referee_earnings,
        paid_referral_earnings,
        resolve_referral_terms,
    )
    from ..utils.refresh_tokens import revoke_all_for_user  # type: ignore
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_current_user  # type: ignore
    from schemas import CreateProfileRequest, UserProfile  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore  # noqa: F811
    from utils.error_handling import ErrorCode, SpinrException  # type: ignore  # noqa: F811
    from utils.error_keys import ErrorKeys  # type: ignore  # noqa: F811
    from utils.redis_client import redis_delete  # type: ignore  # noqa: F811
    from utils.referral_terms import (  # type: ignore  # noqa: F811
        area_id_for_rider,
        paid_referee_earnings,
        paid_referral_earnings,
        resolve_referral_terms,
    )
    from utils.refresh_tokens import revoke_all_for_user  # type: ignore  # noqa: F811
import asyncio
import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

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

    return UserProfile(**updated_user)


@api_router.post("/data-export")
async def request_data_export(current_user: dict = Depends(get_current_user)):
    """R-P1-6 / DV-17 PIPEDA DSAR: Queue a data-export request with 30-day SLA tracking.
    PIPEDA s.9 requires a response within 30 days of receipt.
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
        await db_supabase.update_one("drivers", {"user_id": user_id}, {"deleted_at": now})
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

    now = datetime.now(timezone.utc).isoformat()
    try:
        # Soft-delete driver record (preserves audit trail)
        await db_supabase.update_one("drivers", {"user_id": user_id}, {"deleted_at": now})
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


from pydantic import BaseModel  # type: ignore  # noqa: E402


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
        contacts = []
    return {"contacts": contacts}


@api_router.post("/emergency-contacts")
async def add_emergency_contact(contact: EmergencyContactCreate, current_user: dict = Depends(get_current_user)):
    """Add an emergency contact (max 3 contacts per user, matching Uber/Lyft)."""
    try:
        existing = await db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=100)
    except Exception:
        existing = []

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
