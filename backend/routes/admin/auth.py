import logging
from datetime import datetime
from typing import Any, Dict, Optional

import jwt
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

try:
    from ...core.config import settings
    from ...db import db
except ImportError:
    from core.config import settings
    from db import db

logger = logging.getLogger(__name__)

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
async def admin_login(request: LoginRequest):
    """Admin login — supports super admin + staff members with module access."""
    import hashlib

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
    if request.email == settings.ADMIN_EMAIL and request.password == settings.ADMIN_PASSWORD:
        token = jwt.encode(
            {
                "user_id": "admin-001",
                "email": request.email,
                "role": "super_admin",
                "modules": ALL_MODULES,
                "phone": request.email,
            },
            settings.JWT_SECRET,
            algorithm=settings.ALGORITHM,
        )
        return {
            "user": {
                "id": "admin-001",
                "email": request.email,
                "role": "super_admin",
                "first_name": "Super",
                "last_name": "Admin",
                "modules": ALL_MODULES,
            },
            "token": token,
        }

    # 2. Staff member
    staff = await db.admin_staff.find_one({"email": request.email.lower()})
    if staff:
        pw_hash = hashlib.sha256(request.password.encode()).hexdigest()
        if staff.get("password_hash") == pw_hash:
            if not staff.get("is_active", True):
                raise HTTPException(status_code=403, detail="Account is deactivated")
            await db.admin_staff.update_one(
                {"id": staff["id"]},
                {"$set": {"last_login": datetime.utcnow().isoformat()}},
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
