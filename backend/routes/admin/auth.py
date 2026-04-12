import logging
from datetime import datetime
from typing import Any, Dict, Optional

import jwt
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

try:
    from ...core.config import settings
    from ...db import db
    from ...utils.password import hash_password, verify_password
except ImportError:
    from core.config import settings
    from db import db
    from utils.password import hash_password, verify_password

logger = logging.getLogger(__name__)

# Per-router rate limiter. Admin login is a high-value brute-force
# target (one correct hit → full super-admin access), so we cap it
# to 5 attempts per minute per IP. Matches the pattern already used
# for the rider/driver OTP endpoint in routes/auth.py.
limiter = Limiter(key_func=get_remote_address)

# Auth sub-router — mounted at /admin/auth by server.py directly
admin_auth_router = APIRouter(prefix="/admin/auth", tags=["Admin Auth"])

# Also expose a plain router so __init__.py can include it into admin_router
# (the auth routes themselves live on admin_auth_router, but we export `router`
#  as an empty placeholder so the include_router calls stay uniform)
router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class SessionResponse(BaseModel):
    user: Optional[Dict[str, Any]] = None
    authenticated: bool = False


@admin_auth_router.get("/session", response_model=SessionResponse)
async def get_session(authorization: Optional[str] = Header(None)):
    """Get current admin session - returns user if authenticated"""
    if not authorization:
        return SessionResponse(user=None, authenticated=False)

    # Extract token from "Bearer <token>" format
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            return SessionResponse(user=None, authenticated=False)
    except ValueError:
        return SessionResponse(user=None, authenticated=False)

    # Verify the JWT token
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM])
        user_id = payload.get("user_id")
        role = payload.get("role")
        email = payload.get("email")
        phone = payload.get("phone")

        if not user_id:
            return SessionResponse(user=None, authenticated=False)

        # Return authenticated user info
        modules = payload.get("modules", [])
        return SessionResponse(
            user={
                "id": user_id,
                "email": email,
                "phone": phone,
                "role": role or "admin",
                "modules": modules,
            },
            authenticated=True,
        )
    except jwt.ExpiredSignatureError:
        return SessionResponse(user=None, authenticated=False)
    except jwt.InvalidTokenError:
        return SessionResponse(user=None, authenticated=False)


@admin_auth_router.post("/login")
@limiter.limit("5/minute")
async def admin_login(request: Request, body: LoginRequest):
    """Admin login — supports super admin + staff members with module access.

    Rate-limited to 5 attempts per minute per IP (see `limiter` above)
    to make password brute-force impractical. slowapi requires the
    FastAPI ``Request`` parameter to be named ``request`` so it can
    extract the client address; the Pydantic body has been renamed
    from ``request`` to ``body`` to free up the name.
    """
    ALL_MODULES = [
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
        "staff",
    ]

    # 1. Super admin from env
    if body.email == settings.ADMIN_EMAIL and body.password == settings.ADMIN_PASSWORD:
        token = jwt.encode(
            {
                "user_id": "admin-001",
                "email": body.email,
                "role": "super_admin",
                "modules": ALL_MODULES,
                "phone": body.email,
            },
            settings.JWT_SECRET,
            algorithm=settings.ALGORITHM,
        )
        return {
            "user": {
                "id": "admin-001",
                "email": body.email,
                "role": "super_admin",
                "first_name": "Super",
                "last_name": "Admin",
                "modules": ALL_MODULES,
            },
            "token": token,
        }

    # 2. Staff member
    staff = await db.admin_staff.find_one({"email": body.email.lower()})
    if staff:
        stored_hash = staff.get("password_hash", "") or ""
        ok, needs_upgrade = verify_password(body.password, stored_hash)
        if ok:
            if not staff.get("is_active", True):
                raise HTTPException(status_code=403, detail="Account is deactivated")

            update_payload: Dict[str, Any] = {"last_login": datetime.utcnow().isoformat()}
            # Transparent upgrade: legacy SHA256 rows (and any bcrypt
            # hashes at a lower cost factor than the current target)
            # get re-hashed to the current bcrypt cost on successful
            # login. The next login will find a modern hash and skip
            # the upgrade. No operator action required.
            if needs_upgrade:
                try:
                    update_payload["password_hash"] = hash_password(body.password)
                    logger.info(f"Upgraded password hash for admin_staff id={staff.get('id')}")
                except Exception as e:
                    # Never fail a login because the upgrade path hit
                    # a bcrypt hiccup; just leave the legacy hash in
                    # place and try again next time.
                    logger.warning(f"Password upgrade failed for staff id={staff.get('id')}: {e}")

            await db.admin_staff.update_one(
                {"id": staff["id"]},
                {"$set": update_payload},
            )
            modules = staff.get("modules", ["dashboard"])
            token = jwt.encode(
                {
                    "user_id": staff["id"],
                    "email": staff["email"],
                    "role": staff.get("role", "custom"),
                    "modules": modules,
                    "phone": staff["email"],
                },
                settings.JWT_SECRET,
                algorithm=settings.ALGORITHM,
            )
            return {
                "user": {
                    "id": staff["id"],
                    "email": staff["email"],
                    "role": staff.get("role", "custom"),
                    "first_name": staff.get("first_name", ""),
                    "last_name": staff.get("last_name", ""),
                    "modules": modules,
                },
                "token": token,
            }

    raise HTTPException(status_code=401, detail="Invalid credentials")


@admin_auth_router.post("/logout")
async def admin_logout():
    """Admin logout endpoint"""
    return {"message": "Logged out successfully"}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@admin_auth_router.post("/change-password")
@limiter.limit("3/minute")
async def change_password(request: Request, body: ChangePasswordRequest, authorization: Optional[str] = Header(None)):
    """Change the authenticated staff member's own password.

    Requires the current password for verification (prevents session
    hijacking from escalating to a permanent credential change). The
    new password must be at least 12 characters — same policy enforced
    by the staff-creation endpoint in routes/admin/staff.py.

    The super-admin account (credentials in env vars) cannot change
    their password via this endpoint — that's a config change, not a
    DB write.

    Rate-limited to 3 attempts per minute per IP.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Decode the JWT to find the staff member.
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid auth scheme")
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM])
    except (ValueError, jwt.InvalidTokenError) as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}") from e

    user_id = payload.get("user_id")
    if not user_id or user_id == "admin-001":
        # admin-001 is the super-admin; their password lives in env vars.
        raise HTTPException(
            status_code=400,
            detail="Super admin password cannot be changed here. Update ADMIN_PASSWORD in the environment.",
        )

    staff = await db.admin_staff.find_one({"id": user_id})
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found")

    # Verify current password (supports both bcrypt and legacy SHA256).
    ok, _ = verify_password(body.current_password, staff.get("password_hash", ""))
    if not ok:
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Enforce minimum length on the new password.
    if len(body.new_password) < 12:
        raise HTTPException(status_code=400, detail="New password must be at least 12 characters")

    # Hash + store.
    new_hash = hash_password(body.new_password)
    await db.admin_staff.update_one(
        {"id": user_id},
        {"$set": {"password_hash": new_hash}},
    )

    logger.info(f"Password changed for admin_staff id={user_id}")
    return {"success": True, "message": "Password changed successfully"}
