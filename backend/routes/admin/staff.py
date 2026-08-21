import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.password import hash_password, verify_password
    from ...utils.password_policy import validate_admin_password
    from ...utils.pii import redact_email as _redact_email
    from ...utils.rate_limiter import admin_staff_delete_limit
    from ...utils.refresh_tokens import revoke_all_for_user
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from utils.password import hash_password, verify_password
    from utils.password_policy import validate_admin_password
    from utils.pii import redact_email as _redact_email
    from utils.rate_limiter import admin_staff_delete_limit
    from utils.refresh_tokens import revoke_all_for_user


def require_role(role: str):
    """Dependency factory: rejects requests where the actor's role != `role`."""

    async def _dep(admin: dict = Depends(get_admin_user)) -> dict:
        if admin.get("role") != role:
            raise HTTPException(status_code=403, detail=f"role_required:{role}")
        return admin

    return _dep


db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()

# ============================================================
# Staff Management — Multi-admin with role-based module access
# ============================================================

AVAILABLE_MODULES = [
    "dashboard",
    "users",
    "drivers",
    "rides",
    "earnings",
    "promotions",
    "service_areas",
    "vehicle_types",
    "support",
    "disputes",
    "notifications",
    "settings",
    "corporate_accounts",
    "documents",
    "staff",  # Only super_admin can access this
    "audit",
    "support_tickets",
]

# NOTE — "heatmap" was removed from this list (and from ALL_MODULES in
# routes/admin/auth.py) deliberately. It gated nothing on the backend: the
# admin Heat Map page's data comes from /rides/heatmap-data (rides),
# /surge/status (service_areas), /analytics/demand-forecast (dashboard) and
# /settings/heatmap (settings), each already gated by its own router's
# require_module(). The string's only effect was showing or hiding the sidebar
# link, so granting it implied a protection it did not provide and denying it
# implied a restriction it did not enforce — an admin holding "heatmap" alone
# still saw the link and then a page whose every request 403'd. The sidebar now
# gates that entry on "rides", the module behind the map itself. Same reasoning
# as the "bulk_operations" removal documented in routes/admin/__init__.py.
#
# Existing admin_staff rows may still carry "heatmap" in their modules array.
# That is inert (nothing reads it) and self-cleaning: the create/update paths
# below filter submitted modules against AVAILABLE_MODULES, so the next edit
# drops it. No migration needed.

# NOTE — "surge" and "pricing" were removed from this list (and from
# ALL_MODULES in routes/admin/auth.py) for the same reason as "heatmap"
# above. Both were grantable but gated no backend route: the real surge/
# pricing admin capability (PUT /service-areas/{area_id}/surge, GET
# /surge/status, and the general service-area PUT that carries surge
# fields) is entirely gated by require_module("service_areas")
# (routes/admin/__init__.py). An admin granted "surge"/"pricing" without
# "service_areas" believed they had surge control (checkbox on) but every
# call 403'd; an admin denied "surge"/"pricing" but holding "service_areas"
# had full surge/pricing control anyway. Decision-log item 3,
# docs/audit/2026-08-19-decision-writeups.md, recommendation A.
#
# Existing admin_staff rows may still carry "surge"/"pricing" in their
# modules array. That is inert (nothing reads it) and self-cleaning, same
# as the "heatmap" note above. No migration needed.

ROLE_PRESETS = {
    "super_admin": AVAILABLE_MODULES,
    "operations": [
        "dashboard",
        "rides",
        "drivers",
        "service_areas",
        "vehicle_types",
    ],
    "support": ["dashboard", "support", "support_tickets", "disputes", "notifications", "users"],
    "finance": ["dashboard", "earnings", "promotions", "corporate_accounts", "audit"],
}


class StaffCreateRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    role: str = "custom"  # super_admin, operations, support, finance, custom
    modules: Optional[List[str]] = None  # Only used if role=custom


class StaffUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[str] = None
    modules: Optional[List[str]] = None
    is_active: Optional[bool] = None
    # A-P3-6: required when promoting a staff member to super_admin
    password_confirmation: Optional[str] = None


@router.get("/staff")
async def list_staff(
    response: Response,
    admin: dict = Depends(get_admin_user),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List staff members with offset/limit pagination.

    Returns a flat array (backwards compatible). Total row count and the
    applied limit are exposed via the ``X-Total-Count`` and ``X-Limit``
    response headers so the dashboard can opt into paging without a
    response-shape change. Default limit is 500 to preserve the legacy
    "return everything" behaviour for the current admin dashboard, which
    does not yet paginate this endpoint.
    """
    staff = await db_supabase.get_rows("admin_staff", limit=limit, offset=offset)
    total = await db_supabase.count_documents("admin_staff")
    # Strip credentials from the response. The TOTP secret is as sensitive as
    # a password — anyone holding it can mint valid 6-digit codes — and this
    # endpoint is readable by every staff role, not just super_admin.
    for s in staff:
        for _cred in ("password_hash", "password", "mfa_secret", "mfa_secret_pending", "mfa_backup_codes"):
            s.pop(_cred, None)
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(limit)
    return staff


@router.post("/staff")
async def create_staff(req: StaffCreateRequest, admin: dict = Depends(require_role("super_admin"))):
    """Create a new staff member with role-based module access.

    Only super_admin can create new staff members.
    """
    if not req.password:
        raise HTTPException(status_code=400, detail="Password is required.")
    # A-P4-3: 20-char minimum, complexity, common-password blacklist.
    validate_admin_password(req.password)

    # Check if email already exists
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("admin_staff", {"email": req.email.lower()}, limit=1)
    )
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered as staff")

    # Determine modules based on role
    if req.role in ROLE_PRESETS:
        modules = ROLE_PRESETS[req.role]
    elif req.role == "custom" and req.modules:
        modules = [m for m in req.modules if m in AVAILABLE_MODULES]
    else:
        modules = ["dashboard"]

    staff = {
        "id": str(uuid.uuid4()),
        "email": req.email.lower(),
        # bcrypt, not sha256. See utils/password.py for the rationale
        # + the legacy SHA256 auto-upgrade path on login.
        "password_hash": hash_password(req.password),
        "first_name": req.first_name,
        "last_name": req.last_name,
        "role": req.role,
        "modules": modules,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login": None,
    }

    await db_supabase.insert_one("admin_staff", staff)
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "staff_created",
            "entity_type": "staff",
            "entity_id": staff["id"],
            "details": {
                "email_masked": _redact_email(staff["email"]),
                "role": staff["role"],
                "modules": staff["modules"],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    staff.pop("password_hash")
    return staff


@router.get("/staff/modules/list")
async def list_modules():
    """List available modules and role presets."""
    return {
        "modules": AVAILABLE_MODULES,
        "role_presets": {k: v for k, v in ROLE_PRESETS.items()},
    }


@router.get("/staff/{staff_id}")
async def get_staff(staff_id: str):
    """Get a single staff member."""
    s = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("admin_staff", {"id": staff_id}, limit=1))
    if not s:
        raise HTTPException(status_code=404, detail="Staff member not found")
    # Same credential-stripping as list_staff — see comment there.
    for _cred in ("password_hash", "password", "mfa_secret", "mfa_secret_pending", "mfa_backup_codes"):
        s.pop(_cred, None)
    return s


@router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, req: StaffUpdateRequest, admin: dict = Depends(get_admin_user)):
    """Update staff member role/modules/status. Only super_admin may call this (A-P3-5)."""
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can update staff members")
    s = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("admin_staff", {"id": staff_id}, limit=1))
    if not s:
        raise HTTPException(status_code=404, detail="Staff member not found")

    # A-P3-6: promotion to super_admin requires password re-entry.
    if req.role == "super_admin" and s.get("role") != "super_admin":
        if not req.password_confirmation:
            raise HTTPException(
                status_code=422,
                detail="password_confirmation required for super_admin promotion",
            )
        actor_id = admin.get("id")
        if actor_id and actor_id != "admin-001":
            actor_row = (lambda _r: _r[0] if _r else None)(
                await db_supabase.get_rows("admin_staff", {"id": actor_id}, limit=1)
            )
            if not actor_row:
                raise HTTPException(status_code=401, detail="Actor not found")
            ok, _ = verify_password(req.password_confirmation, actor_row.get("password_hash", ""))
            if not ok:
                raise HTTPException(status_code=401, detail="Incorrect password — promotion denied")
        logger.info(f"super_admin promotion: target={staff_id} actor={actor_id}")

    if req.role is not None and req.role != "super_admin" and s.get("role") == "super_admin":
        count = await db_supabase.count_documents("admin_staff", {"role": "super_admin", "is_active": True})
        if count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last active super admin")

    updates = {}
    if req.first_name is not None:
        updates["first_name"] = req.first_name
    if req.last_name is not None:
        updates["last_name"] = req.last_name
    if req.is_active is not None:
        updates["is_active"] = req.is_active
        if req.is_active is False:
            # Bump token_version so the dependency gate rejects all existing
            # access tokens for this staff member immediately (audit [03-2]).
            updates["token_version"] = int(s.get("token_version") or 0) + 1
            await revoke_all_for_user(staff_id)
    if req.role is not None:
        updates["role"] = req.role
        if req.role in ROLE_PRESETS:
            updates["modules"] = ROLE_PRESETS[req.role]
    if req.modules is not None:
        updates["modules"] = [m for m in req.modules if m in AVAILABLE_MODULES]

    # Admin access tokens carry role/modules as trusted JWT claims (see
    # dependencies.get_admin_user — unlike rider/driver, they are NOT
    # re-read from the DB on every request). Without a token_version bump
    # here, a demoted-or-reduced admin's already-issued access token keeps
    # granting the OLD role/modules for up to its full 1hr TTL, and the
    # matching refresh token would silently mint more tokens carrying the
    # stale claims. Force re-auth on any actual role/modules change so the
    # new claims take effect within this same request cycle instead of
    # persisting for up to an hour (H6).
    _role_changed = req.role is not None and req.role != s.get("role")
    _modules_changed = "modules" in updates and updates["modules"] != (s.get("modules") or [])
    if (_role_changed or _modules_changed) and "token_version" not in updates:
        updates["token_version"] = int(s.get("token_version") or 0) + 1
        await revoke_all_for_user(staff_id)

    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("admin_staff", {"id": staff_id}, updates)
        await db_supabase.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "actor_id": admin["id"],
                "actor_role": admin.get("role"),
                "action": "staff_updated",
                "entity_type": "staff",
                "entity_id": staff_id,
                "details": {k: v for k, v in updates.items() if k != "updated_at"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    return {"success": True}


@router.post("/staff/{staff_id}/mfa-reset")
async def reset_staff_mfa(staff_id: str, admin: dict = Depends(require_role("super_admin"))):
    """Clear a staff member's MFA so they can re-enroll (lost phone / forgotten authenticator).

    Super-admin only, and deliberately NOT for your own account: self-service
    removal goes through Settings → Disable MFA (password + TOTP) or a backup
    code at login — otherwise a hijacked super-admin session could silently
    weaken its own account. The target's sessions and refresh tokens are
    revoked because the account just lost a factor; they must log in again
    (email + password only) and re-enroll from Settings.
    """
    if staff_id == admin.get("id"):
        raise HTTPException(
            status_code=400,
            detail="Use Settings → Disable MFA (or a backup code at login) for your own account",
        )
    s = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("admin_staff", {"id": staff_id}, limit=1))
    if not s:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if not (s.get("mfa_enabled") or s.get("mfa_secret_pending")):
        raise HTTPException(status_code=400, detail="MFA is not enabled for this staff member")

    await db_supabase.update_one(
        "admin_staff",
        {"id": staff_id},
        {
            "mfa_enabled": False,
            "mfa_secret": None,
            "mfa_secret_pending": None,
            "mfa_backup_codes": None,
            # Bump token_version so the dependency gate rejects all existing
            # access tokens for this staff member immediately (same pattern
            # as deactivation above — the account just lost a factor).
            "token_version": int(s.get("token_version") or 0) + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    await revoke_all_for_user(staff_id)
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "staff_mfa_reset",
            "entity_type": "staff",
            "entity_id": staff_id,
            "details": {
                "email_masked": _redact_email(s.get("email")),
                "had_pending_enrollment": bool(s.get("mfa_secret_pending")),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info(f"MFA reset for staff {staff_id} by super_admin {admin.get('id')}")
    return {"success": True}


@router.delete("/staff/{staff_id}")
@admin_staff_delete_limit
async def delete_staff(request: Request, staff_id: str, admin: dict = Depends(require_role("super_admin"))):
    """Delete a staff member. Requires super_admin (A-P3-5)."""
    if staff_id == admin.get("id"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    s = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("admin_staff", {"id": staff_id}, limit=1))
    # Revoke all refresh tokens before the row is gone so any in-flight
    # session cannot exchange a refresh token after deletion (audit [03-3]).
    await revoke_all_for_user(staff_id)
    await db_supabase.delete_many("admin_staff", {"id": staff_id})
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "staff_deleted",
            "entity_type": "staff",
            "entity_id": staff_id,
            "details": {
                "email_masked": _redact_email(s.get("email")) if s else None,
                "role": s.get("role") if s else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True}
