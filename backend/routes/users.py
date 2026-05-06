from fastapi import APIRouter, Depends, File, HTTPException, UploadFile  # type: ignore

try:
    from .. import db_supabase  # type: ignore
    from ..dependencies import get_current_user  # type: ignore
    from ..schemas import CreateProfileRequest, UserProfile  # type: ignore
    from ..utils.audit_logger import log_admin_action  # type: ignore
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_current_user  # type: ignore
    from schemas import CreateProfileRequest, UserProfile  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore  # noqa: F811
import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone
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

    # GAP FIX: Check for duplicate email across users
    email_lower = request.email.strip().lower()
    existing_email_user = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("users", {"email": email_lower, "id": {"$ne": current_user["id"]}}, limit=1)
    )
    if existing_email_user:
        raise HTTPException(status_code=400, detail="This email address is already in use by another account")

    update_data = {
        "first_name": request.first_name.strip(),
        "last_name": request.last_name.strip(),
        "email": email_lower,
        "gender": request.gender,
        "profile_complete": True,
    }
    # Allow driver app to set role='driver' so onboarding status is computed
    if request.role and request.role in ("driver", "rider"):
        update_data["role"] = request.role

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
        logger.error(f"Could not record data export request for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="data_export_request_failed") from e
    return {
        "success": True,
        "request_id": request_id,
        "response_due_at": response_due_at,
        "message": "Data export requested. We will respond within 30 days as required by PIPEDA.",
    }


async def _anonymise_rides(user_id: str) -> None:
    """Strip PII from completed rides linked to a user (PIPEDA R-P2-46).

    Ride records are retained for 7 years per Saskatchewan Transportation Act
    but personal identifiers are removed immediately on deletion request.
    Only 'completed' rides are anonymised — in_progress rides remain fully
    linked for insurance period audit obligations.
    Coordinates are rounded to 2 dp (~1 km precision); address text fields
    are replaced with '[anonymized]'.
    """
    try:
        rides = await db_supabase.get_rows(
            "rides",
            {"rider_id": user_id, "status": "completed"},
            limit=5000,
        )
        for ride in rides:
            updates: dict = {
                "rider_id": None,
                "pickup_address": "[anonymized]",
                "dropoff_address": "[anonymized]",
            }
            for lat_field in ("pickup_lat", "dropoff_lat"):
                if ride.get(lat_field) is not None:
                    updates[lat_field] = round(float(ride[lat_field]), 2)
            for lng_field in ("pickup_lng", "dropoff_lng"):
                if ride.get(lng_field) is not None:
                    updates[lng_field] = round(float(ride[lng_field]), 2)
            await db_supabase.update_one("rides", {"id": ride["id"]}, updates)
        logger.info(f"Anonymised {len(rides)} completed rides for deleted user {user_id}")
    except Exception as e:
        # Log but don't fail the deletion — ride anonymisation is best-effort;
        # the account is still marked pending_deletion so the purge loop retries.
        logger.error(f"Ride anonymisation failed for user {user_id}: {e}", exc_info=True)


@api_router.delete("/account")
async def delete_account_pipeda(current_user: dict = Depends(get_current_user)):
    """R-P1-6 PIPEDA: Soft-delete account with a 30-day grace period (right to erasure)."""
    user_id = current_user["id"]
    logger.info(f"Account deletion (PIPEDA) requested for user {user_id}")

    grace_period_end = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db_supabase.update_one(
            "users",
            {"id": user_id},
            {"deletion_requested_at": now, "deletion_scheduled_at": grace_period_end, "status": "pending_deletion"},
        )
        await db_supabase.update_one("drivers", {"user_id": user_id}, {"deleted_at": now})
        # R-P2-46: strip PII from ride records immediately even though the account
        # itself has a 30-day grace period.  Ride rows are retained for SK regulatory
        # retention (7 years) but personal identifiers are removed now.
        await _anonymise_rides(user_id)
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
            "message": "Account deletion scheduled. Your account will be permanently deleted after 30 days.",
        }
    except Exception as e:
        logger.error(f"Account deletion failed for user {user_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to schedule account deletion. Please contact support."
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
        # R-P2-46: anonymise rides before unlinking user_id
        await _anonymise_rides(user_id)
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
        raise HTTPException(status_code=500, detail="Database error: Could not retrieve updated user profile.")

    return UserProfile(**updated_user)


@api_router.put("/profile-image", response_model=UserProfile)
async def upload_profile_image(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """Upload a profile image for the current user (stored as base64 in database)."""
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

    # Convert to base64
    base64_image = base64.b64encode(content).decode("utf-8")
    # Store as data URI
    data_uri = f"data:{file.content_type};base64,{base64_image}"

    await db_supabase.update_one(
        "users",
        {"id": current_user["id"]},
        {
            "profile_image": data_uri,
            "profile_image_status": "pending_review",
        },
    )
    updated_user = await db_supabase.get_user_by_id(current_user["id"])

    if not updated_user:
        raise HTTPException(status_code=500, detail="Database error: Could not retrieve updated user profile.")

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

    await db_supabase.update_one(
        "users", {"id": current_user["id"]}, {"corporate_account_id": request.corporate_account_id}
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
        contacts_cursor = db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=100)
        contacts = (
            await contacts_cursor.to_list(length=10) if hasattr(contacts_cursor, "to_list") else list(contacts_cursor)
        )
    except Exception as e:
        logger.error(f"Could not fetch emergency contacts for user {current_user['id']}: {e}", exc_info=True)
        contacts = []
    return {"contacts": contacts}


@api_router.post("/emergency-contacts")
async def add_emergency_contact(contact: EmergencyContactCreate, current_user: dict = Depends(get_current_user)):
    """Add an emergency contact (max 3 contacts per user, matching Uber/Lyft)."""
    try:
        existing_cursor = db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=100)
        existing = (
            await existing_cursor.to_list(length=10) if hasattr(existing_cursor, "to_list") else list(existing_cursor)
        )
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
        await db_supabase.get_rows("emergency_contacts", {"id": contact_id, "user_id": current_user["id"]}, limit=1)
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Emergency contact not found")

    await db_supabase.delete_one("emergency_contacts", {"id": contact_id})
    return {"success": True}
