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
    "surge",
    "service_areas",
    "vehicle_types",
    "pricing",
    "support",
    "disputes",
    "notifications",
    "settings",
    "corporate_accounts",
    "documents",
    "heatmap",
    "staff",  # Only super_admin can access this
]

ROLE_PRESETS = {
    "super_admin": AVAILABLE_MODULES,
    "operations": [
        "dashboard",
        "rides",
        "drivers",
        "surge",
        "service_areas",
        "vehicle_types",
        "heatmap",
    ],
    "support": ["dashboard", "support", "disputes", "notifications", "users"],
    "finance": ["dashboard", "earnings", "promotions", "corporate_accounts", "pricing"],
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
    # Remove passwords from response
    for s in staff:
        s.pop("password_hash", None)
        s.pop("password", None)
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
            "resource": "staff",
            "resource_id": staff["id"],
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
    s.pop("password_hash", None)
    s.pop("password", None)
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
            raise HTTPException(status_code=422, detail="password_confirmation required for super_admin promotion")
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
                "resource": "staff",
                "resource_id": staff_id,
                "details": {k: v for k, v in updates.items() if k != "updated_at"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

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
            "resource": "staff",
            "resource_id": staff_id,
            "details": {
                "email_masked": _redact_email(s.get("email")) if s else None,
                "role": s.get("role") if s else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True}
