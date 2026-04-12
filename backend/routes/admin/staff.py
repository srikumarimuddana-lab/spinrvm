import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

try:
    from ...db import db
    from ...utils.password import hash_password
except ImportError:
    from db import db
    from utils.password import hash_password

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


@router.get("/staff")
async def list_staff(authorization: Optional[str] = Header(None)):
    """List all staff members."""
    staff = await db.get_rows("admin_staff", limit=100)
    # Remove passwords from response
    for s in staff:
        s.pop("password_hash", None)
        s.pop("password", None)
    return staff


@router.post("/staff")
async def create_staff(req: StaffCreateRequest, authorization: Optional[str] = Header(None)):
    """Create a new staff member with role-based module access."""
    # Basic password policy: short passwords defeat bcrypt's cost factor
    # because the keyspace is too small. 12 chars is the floor; operators
    # should pick much longer in practice.
    if not req.password or len(req.password) < 12:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 12 characters long.",
        )

    # Check if email already exists
    existing = await db.admin_staff.find_one({"email": req.email.lower()})
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
        "created_at": datetime.utcnow().isoformat(),
        "last_login": None,
    }

    await db.admin_staff.insert_one(staff)
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
    s = await db.admin_staff.find_one({"id": staff_id})
    if not s:
        raise HTTPException(status_code=404, detail="Staff member not found")
    s.pop("password_hash", None)
    s.pop("password", None)
    return s


@router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, req: StaffUpdateRequest):
    """Update staff member role/modules/status."""
    s = await db.admin_staff.find_one({"id": staff_id})
    if not s:
        raise HTTPException(status_code=404, detail="Staff member not found")

    updates = {}
    if req.first_name is not None:
        updates["first_name"] = req.first_name
    if req.last_name is not None:
        updates["last_name"] = req.last_name
    if req.is_active is not None:
        updates["is_active"] = req.is_active
    if req.role is not None:
        updates["role"] = req.role
        if req.role in ROLE_PRESETS:
            updates["modules"] = ROLE_PRESETS[req.role]
    if req.modules is not None:
        updates["modules"] = [m for m in req.modules if m in AVAILABLE_MODULES]

    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.admin_staff.update_one({"id": staff_id}, {"$set": updates})

    return {"success": True}


@router.delete("/staff/{staff_id}")
async def delete_staff(staff_id: str):
    """Delete a staff member."""
    await db.admin_staff.delete_many({"id": staff_id})
    return {"success": True}
