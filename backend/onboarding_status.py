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

    user_id = user.get("id")

    # Step 1: profile fields
    if not _has_profile(user):
        return _result("profile_incomplete")

    # Step 2: driver row + vehicle fields
    driver = None
    try:
        driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1)
        )
    except Exception:
        driver = None

    # If the user has no driver flag and no driver row, skip onboarding status.
    if not user.get("is_driver") and not driver:
        return None, None, None

    if not _has_vehicle(driver):
        return _result("vehicle_required")

    # Step 3: suspension shortcut
    # `drivers.is_suspended` is a dead column — no code path ever sets it True
    # (confirmed via grep: only this read exists, no writer). The real signal
    # is `drivers.status`, which admin_driver_action() actually writes to on
    # suspend/ban. Checking the dead boolean meant a suspended/banned driver
    # fell through to the documents/verified checks below and could still be
    # routed to /driver as if nothing were wrong.
    if driver.get("status") in ("suspended", "banned"):
        return _result("suspended")

    # Step 4: documents
    # Requirements come from the driver's service-area `required_documents`
    # (slug-keyed, same source the driver app's upload UI uses). The global
    # `document_requirements` table is legacy — matching against it misses
    # slug-based uploads (requirement_id column holds NULL for non-UUID keys;
    # the slug lives in requirement_key when migration 28 is applied).
    try:
        documents = await db_supabase.get_rows("driver_documents", {"driver_id": driver["id"]}, limit=200)
    except Exception:
        documents = []
    # Superseded docs are historical — don't let stale approved-then-expired
    # rows trigger documents_expired after a re-upload.
    documents = [d for d in (documents or []) if d.get("status") != "superseded"]

    area_requirements: list = []
    if driver.get("service_area_id"):
        try:
            area = (lambda _r: _r[0] if _r else None)(
                await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
            )
            if area:
                area_requirements = area.get("required_documents") or []
        except Exception:
            area_requirements = []

    mandatory_reqs = [r for r in area_requirements if r.get("required", True)]

    def _docs_for_req(req: Dict[str, Any]) -> list:
        """Match docs to a service-area requirement using the same strategies
        as the admin panel: requirement_key (slug) → requirement_id (UUID or
        legacy slug) → document_type vs label/key."""
        req_key = (req.get("key") or "").lower()
        req_label = (req.get("label") or "").lower()
        req_id = req.get("id")
        out = []
        for d in documents:
            dkey = (d.get("requirement_key") or "").lower()
            if dkey and dkey == req_key:
                out.append(d)
                continue
            drid = d.get("requirement_id")
            if drid and (drid == req_id or (isinstance(drid, str) and drid.lower() == req_key)):
                out.append(d)
                continue
            dt = (d.get("document_type") or "").lower()
            if dt and (dt == req_label or dt == req_key.replace("_", " ")):
                out.append(d)
                continue
            if dt and req_key and req_key.replace("_", "") in dt.replace(" ", "").replace("_", ""):
                out.append(d)
        return out

    # Check: are any mandatory requirements missing entirely?
    missing_any = False
    for req in mandatory_reqs:
        if not _docs_for_req(req):
            missing_any = True
            break
    if missing_any:
        return _result("documents_required")

    # Check: are any docs rejected?
    has_rejected = any((d.get("status") == "rejected") for d in documents)
    if has_rejected:
        return _result("documents_rejected")

    # Check: are any approved mandatory docs expired?
    now = datetime.now(timezone.utc)
    has_expired = False
    for req in mandatory_reqs:
        for d in _docs_for_req(req):
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
