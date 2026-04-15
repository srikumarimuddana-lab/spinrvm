"""
Driver onboarding state machine.

Derives a single enum value from the user + driver + documents rows so the
mobile app can route to the correct screen without duplicating business logic
on the client. This is the authoritative source of truth for "where is this
driver in onboarding".

States (ordered by flow progression):

    profile_incomplete  — user row missing first_name/last_name/email
    vehicle_required    — no drivers row, or missing mandatory vehicle fields
    documents_required  — drivers row exists but mandatory docs not uploaded
    documents_rejected  — admin rejected at least one required doc
    documents_expired   — at least one approved required doc past expiry
    pending_review      — all docs uploaded, awaiting admin verification
    verified            — fully verified, can go online
    suspended           — admin suspended this driver

Always call via `derive_driver_onboarding_status(user)` — it pulls the related
driver + documents rows itself so callers don't have to assemble them.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
    from . import db_supabase  # type: ignore
except ImportError:
    import db_supabase  # type: ignore


# Ordered list of states for logging / validation.
STATES = (
    "profile_incomplete",
    "vehicle_required",
    "documents_required",
    "documents_rejected",
    "documents_expired",
    "pending_review",
    "verified",
    "suspended",
)

# Map each state to the mobile route the driver app should navigate to.
# The app is free to interpret these, but these are the defaults.
NEXT_SCREEN = {
    "profile_incomplete": "/profile-setup",
    "vehicle_required": "/become-driver",
    "documents_required": "/documents",
    "documents_rejected": "/documents",
    "documents_expired": "/documents",
    "pending_review": "/driver",
    "verified": "/driver",
    "suspended": "/driver",
}

# Human-readable explanations for banners in the app.
DETAIL = {
    "profile_incomplete": "Complete your personal details to continue.",
    "vehicle_required": "Add your vehicle information to continue.",
    "documents_required": "Upload the required documents to complete verification.",
    "documents_rejected": "One or more documents were rejected. Please re-upload.",
    "documents_expired": "One or more documents have expired. Please re-upload.",
    "pending_review": "Your profile is under review. We will notify you once approved.",
    "verified": "You are verified and ready to drive.",
    "suspended": "Your account is suspended. Contact support for help.",
}


def _has_profile(user: Dict[str, Any]) -> bool:
    return bool(
        (user.get("first_name") or "").strip()
        and (user.get("last_name") or "").strip()
        and (user.get("email") or "").strip()
    )


def _has_vehicle(driver: Optional[Dict[str, Any]]) -> bool:
    if not driver:
        return False
    return bool(
        driver.get("vehicle_make")
        and driver.get("vehicle_model")
        and driver.get("license_plate")
        and driver.get("vehicle_type_id")
    )


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


async def derive_driver_onboarding_status(
    user: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (status, detail, next_screen). Tuple is (None, None, None) for
    users whose role is not driver.

    The caller should attach these to the UserProfile response so the client
    can route without a second request.
    """
    if not user:
        return None, None, None

    # Only compute for users who are drivers or on the driver app flow.
    # The driver app sets role='driver' on registration; if role isn't set
    # yet we still compute because the user is about to become a driver.
    role = (user.get("role") or "").lower()
    user_id = user.get("id")

    # Step 1: profile fields
    if not _has_profile(user):
        return _result("profile_incomplete")

    # Step 2: driver row + vehicle fields
    driver = None
    try:
        driver = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1))
    except Exception:
        driver = None

    # If we're on the rider app (role=rider, no driver row, is_driver=False),
    # don't emit a driver onboarding status at all — this is a rider.
    if role != "driver" and not driver:
        return None, None, None

    if not _has_vehicle(driver):
        return _result("vehicle_required")

    # Step 3: suspension shortcut
    if driver.get("is_suspended"):
        return _result("suspended")

    # Step 4: documents
    # Fetch requirements + driver's submitted docs.
    try:
        requirements = await db_supabase.get_rows("driver_requirements", {}, limit=100)
    except Exception:
        requirements = []
    try:
        documents = await db_supabase.get_rows("driver_documents", {"driver_id": driver["id"]}, limit=200)
    except Exception:
        documents = []

    mandatory_reqs = [r for r in (requirements or []) if r.get("is_mandatory")]

    # Index docs by requirement_id for quick lookup. Prefer the most recent
    # per requirement if multiple (front/back sides share the requirement_id).
    docs_by_req: Dict[str, list] = {}
    for d in documents or []:
        docs_by_req.setdefault(d.get("requirement_id"), []).append(d)

    # Check: are any mandatory requirements missing entirely?
    missing_any = False
    for req in mandatory_reqs:
        if not docs_by_req.get(req.get("id")):
            missing_any = True
            break
    if missing_any:
        return _result("documents_required")

    # Check: are any docs rejected?
    has_rejected = any((d.get("status") == "rejected") for d in documents or [])
    if has_rejected:
        return _result("documents_rejected")

    # Check: are any approved mandatory docs expired?
    now = datetime.now(timezone.utc)
    has_expired = False
    for req in mandatory_reqs:
        reqdocs = docs_by_req.get(req.get("id"), [])
        for d in reqdocs:
            if d.get("status") != "approved":
                continue
            exp = _parse_date(d.get("expiry_date") or d.get("expires_at"))
            if exp and exp < now:
                has_expired = True
                break
        if has_expired:
            break

    # Also honour the legacy top-level expiry fields on the drivers row,
    # which older code still writes during become-driver.
    if not has_expired:
        for key in (
            "license_expiry_date",
            "insurance_expiry_date",
            "vehicle_inspection_expiry_date",
            "background_check_expiry_date",
            "work_eligibility_expiry_date",
        ):
            exp = _parse_date(driver.get(key))
            if exp and exp < now:
                has_expired = True
                break

    if has_expired:
        return _result("documents_expired")

    # Step 5: verified vs pending review
    # is_verified=true means admin signed off. Otherwise still pending.
    if driver.get("is_verified"):
        return _result("verified")

    return _result("pending_review")


def _result(state: str) -> Tuple[str, str, str]:
    return state, DETAIL[state], NEXT_SCREEN[state]
