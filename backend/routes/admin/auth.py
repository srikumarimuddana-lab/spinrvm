import logging
from datetime import datetime, timedelta, timezone
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
    from ...utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )
except ImportError:
    from core.config import settings
    from db import db
    from utils.password import hash_password, verify_password
    from utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )

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


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


def _mint_admin_access_token(
    user_id: str,
    email: str,
    role: str,
    modules: list,
    phone: str,
    token_version: int,
) -> tuple[str, datetime]:
    """Mint an admin access token with a bounded TTL and a token_version
    claim so the revocation gate in dependencies.py can reject stale
    tokens after an admin force-logout-all. Historically admin tokens
    were minted WITHOUT an ``exp`` claim, so a single captured token
    granted permanent access — the primary P0-S3 fix is this function.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.ADMIN_ACCESS_TOKEN_TTL_HOURS)
    token = jwt.encode(
        {
            "user_id": user_id,
            "email": email,
            "role": role,
            "modules": modules,
            "phone": phone,
            "token_version": int(token_version or 0),
            "iat": now,
            "exp": expires_at,
        },
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )
    return token, expires_at


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

    user_agent = request.headers.get("user-agent", "")
    client_ip = get_remote_address(request)

    # 1. Super admin from env
    if body.email == settings.ADMIN_EMAIL and body.password == settings.ADMIN_PASSWORD:
        # admin-001 has no DB row, so token_version stays at 0. We still
        # emit the claim + an exp so a captured super-admin token dies
        # after ADMIN_ACCESS_TOKEN_TTL_HOURS and can't live forever.
        token, access_expires_at = _mint_admin_access_token(
            user_id="admin-001",
            email=body.email,
            role="super_admin",
            modules=ALL_MODULES,
            phone=body.email,
            token_version=0,
        )
        refresh_raw, _, refresh_expires_at = await issue_refresh_token(
            "admin-001", audience="admin", user_agent=user_agent, ip=client_ip
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
            "refresh_token": refresh_raw,
            "access_expires_at": access_expires_at.isoformat(),
            "refresh_expires_at": refresh_expires_at.isoformat(),
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
            token, access_expires_at = _mint_admin_access_token(
                user_id=staff["id"],
                email=staff["email"],
                role=staff.get("role", "custom"),
                modules=modules,
                phone=staff["email"],
                token_version=int(staff.get("token_version") or 0),
            )
            refresh_raw, _, refresh_expires_at = await issue_refresh_token(
                staff["id"], audience="admin", user_agent=user_agent, ip=client_ip
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
                "refresh_token": refresh_raw,
                "access_expires_at": access_expires_at.isoformat(),
                "refresh_expires_at": refresh_expires_at.isoformat(),
            }

    raise HTTPException(status_code=401, detail="Invalid credentials")


@admin_auth_router.post("/refresh")
@limiter.limit("20/minute")
async def admin_refresh(request: Request, body: RefreshRequest):
    """Exchange an admin refresh token for a new admin access token.

    Scoped to ``audience='admin'`` — a rider refresh token cannot be
    exchanged here even if it's structurally valid. This is the
    privilege-escalation guard.
    """
    row = await lookup_refresh_token(body.refresh_token)
    if not row or row.get("audience") != "admin":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = row.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # admin-001 has no DB row. Staff rows must still be active.
    if user_id == "admin-001":
        email = settings.ADMIN_EMAIL
        role = "super_admin"
        modules = [
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
        token_version = 0
    else:
        staff = await db.admin_staff.find_one({"id": user_id})
        if not staff or not staff.get("is_active", True):
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        email = staff["email"]
        role = staff.get("role", "custom")
        modules = staff.get("modules", ["dashboard"])
        token_version = int(staff.get("token_version") or 0)

    user_agent = request.headers.get("user-agent", "")
    client_ip = get_remote_address(request)

    new_raw, _, refresh_expires_at = await issue_refresh_token(
        user_id,
        audience="admin",
        user_agent=user_agent,
        ip=client_ip,
        replaces=row.get("id"),
    )

    token, access_expires_at = _mint_admin_access_token(
        user_id=user_id,
        email=email,
        role=role,
        modules=modules,
        phone=email,
        token_version=token_version,
    )
    return {
        "token": token,
        "refresh_token": new_raw,
        "access_expires_at": access_expires_at.isoformat(),
        "refresh_expires_at": refresh_expires_at.isoformat(),
    }


@admin_auth_router.post("/logout")
@limiter.limit("10/minute")
async def admin_logout(body: LogoutRequest):
    """Admin logout — revokes the presented refresh token.

    Previously returned a canned success message with zero DB side
    effects. Now actually stamps revoked_at so the refresh token can
    never be exchanged again. The current access token keeps working
    until exp; use /admin/auth/logout-all for immediate kill.
    """
    if body.refresh_token:
        await revoke_refresh_token(body.refresh_token)
    return {"success": True}


@admin_auth_router.post("/logout-all")
@limiter.limit("5/minute")
async def admin_logout_all(authorization: Optional[str] = Header(None)):
    """Force-invalidate every admin session for the caller.

    Only valid for staff (admin-001 uses env-var creds and has no
    persisted token_version; rotate ADMIN_PASSWORD to kill the super-
    admin globally). Bumps admin_staff.token_version and revokes every
    active refresh token for that staff row.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid auth scheme")
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM])
    except (ValueError, jwt.InvalidTokenError) as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}") from e

    user_id = payload.get("user_id")
    if not user_id or user_id == "admin-001":
        raise HTTPException(
            status_code=400,
            detail="Super admin cannot force-logout here. Rotate ADMIN_PASSWORD in the environment to kill all super-admin sessions.",
        )

    staff = await db.admin_staff.find_one({"id": user_id})
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found")

    new_version = int(staff.get("token_version") or 0) + 1
    await db.admin_staff.update_one({"id": user_id}, {"$set": {"token_version": new_version}})
    revoked = await revoke_all_for_user(user_id)
    logger.info(f"admin logout-all: user={user_id} token_version→{new_version} revoked_refresh={revoked}")
    return {"success": True, "revoked_refresh_tokens": revoked}


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
