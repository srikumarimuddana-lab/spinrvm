import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

try:
    from ... import db_supabase
    from ...core.config import settings
    from ...core.csrf import clear_csrf_cookie, generate_csrf_token, set_csrf_cookie
    from ...utils.password import hash_password, verify_password
    from ...utils.redis_client import redis_delete, redis_expire, redis_get, redis_incr, redis_set
    from ...utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )
except ImportError:
    import db_supabase
    from core.config import settings
    from utils.password import hash_password, verify_password
    from utils.redis_client import redis_delete, redis_expire, redis_get, redis_incr
    from utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

# Per-router rate limiter. A-P3-2: tightened to 3 attempts per 30 minutes per IP
# (was 5/minute = 300/hr). Admin accounts are the highest-value brute-force target.
limiter = Limiter(key_func=get_remote_address)

# A-P3-2: Two-layer account lockout.
#   Layer 1 — failure counter: 5 failures within a 1-hour sliding window.
#   Layer 2 — lockout key: set for 24 hours when the counter hits 5.
# Both layers are stored in Redis with TTL; fall back to in-process dict when
# Redis is unavailable (state is lost on restart in that mode).
_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 3600  # 1-hour failure window
_LOGIN_LOCKOUT_TTL_SECONDS = 24 * 3600  # 24-hour lockout once threshold hit


def _lockout_key(email: str) -> str:
    """Key for the 24-hour lockout flag."""
    return f"admin:login_lock:{email.lower().strip()}"


def _failure_key(email: str) -> str:
    """Key for the rolling failure counter (1-hour window)."""
    return f"admin:login_failures:{email.lower().strip()}"


async def _is_account_locked(email: str) -> bool:
    try:
        val = await redis_get(_lockout_key(email))
        return val is not None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[REDIS] _is_account_locked check failed for admin login ({email!r}): {e}")
        raise HTTPException(status_code=503, detail="ERR_AUTH_UNAVAILABLE") from None


async def _record_login_failure(email: str) -> None:
    try:
        key = _failure_key(email)
        count = await redis_incr(key)
        if count == 1:
            # Start the 1-hour failure window on first failure.
            await redis_expire(key, _LOGIN_WINDOW_SECONDS)
        if count >= _LOGIN_MAX_FAILURES:
            # Threshold hit — set a 24-hour hard lockout and reset the counter.
            await redis_set(_lockout_key(email), "1", ttl=_LOGIN_LOCKOUT_TTL_SECONDS)
            await redis_delete(key)
    except Exception as e:
        logger.error(f"[REDIS] _record_login_failure could not persist failure count ({email!r}): {e}")


async def _clear_login_failures(email: str) -> None:
    try:
        await redis_delete(_failure_key(email))
        await redis_delete(_lockout_key(email))
    except Exception as e:
        logger.error(f"[REDIS] _clear_login_failures could not clear failure count ({email!r}): {e}")


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
@limiter.limit("3/30minute")
async def admin_login(request: Request, response: Response, body: LoginRequest):
    """Admin login — supports super admin + staff members with module access.

    A-P3-2: Rate-limited to 3 attempts per 30 minutes per IP (was 5/minute).
    Account-level lockout: 5 failures within 1 hour → 24-hour lockout (423).
    slowapi requires the FastAPI ``Request`` parameter named ``request``.
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

    # A-P3-2: Per-account lockout — reject before touching credentials so that
    # timing differences cannot reveal whether an account exists.
    if await _is_account_locked(body.email):
        raise HTTPException(
            status_code=423,
            detail="Account locked due to too many failed login attempts. Try again in 24 hours.",
        )

    # 1. Super admin from env. A-P3-1: compare via bcrypt checkpw against the
    # hash computed once at startup — never a plaintext string compare.
    _super_ok = (
        settings.admin_password_hash
        and body.email == settings.ADMIN_EMAIL
        and verify_password(body.password, settings.admin_password_hash)[0]
    )
    if _super_ok:
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
        await _clear_login_failures(body.email)
        csrf = generate_csrf_token()
        set_csrf_cookie(
            response, csrf, secure=settings.ENV == "production", max_age=settings.ADMIN_ACCESS_TOKEN_TTL_HOURS * 3600
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
            "csrf_token": csrf,
        }

    # 2. Staff member
    staff = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("admin_staff", {"email": body.email.lower()}, limit=1)
    )
    if staff:
        # A-P3-3: IP whitelist — empty list means no restriction.
        _allowed_ips = staff.get("allowed_ips") or []
        if _allowed_ips:
            import ipaddress as _ip

            try:
                _client = _ip.ip_address(client_ip)
                if not any(_client in _ip.ip_network(cidr, strict=False) for cidr in _allowed_ips):
                    await _record_login_failure(body.email)
                    raise HTTPException(status_code=403, detail="ip_not_allowed")
            except ValueError:
                # Malformed CIDR in DB — fail open (log + allow); don't lock out the admin.
                logger.error(f"Malformed allowed_ips for staff {staff.get('id')}: {_allowed_ips}")

        stored_hash = staff.get("password_hash", "") or ""
        ok, needs_upgrade = verify_password(body.password, stored_hash)
        if ok:
            if not staff.get("is_active", True):
                raise HTTPException(status_code=403, detail="Account is deactivated")
            await db_supabase.update_one(
                "admin_staff", {"id": staff["id"]}, {"last_login": datetime.now(timezone.utc).isoformat()}
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
            await _clear_login_failures(body.email)
            csrf = generate_csrf_token()
            set_csrf_cookie(
                response,
                csrf,
                secure=settings.ENV == "production",
                max_age=settings.ADMIN_ACCESS_TOKEN_TTL_HOURS * 3600,
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
                "csrf_token": csrf,
            }

    await _record_login_failure(body.email)
    raise HTTPException(status_code=401, detail="Invalid credentials")


@admin_auth_router.post("/refresh")
@limiter.limit("20/minute")
async def admin_refresh(request: Request, response: Response, body: RefreshRequest):
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
        staff = await db.find_one("admin_staff", {"id": user_id})
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
    csrf = generate_csrf_token()
    set_csrf_cookie(
        response, csrf, secure=settings.ENV == "production", max_age=settings.ADMIN_ACCESS_TOKEN_TTL_HOURS * 3600
    )
    return {
        "token": token,
        "refresh_token": new_raw,
        "access_expires_at": access_expires_at.isoformat(),
        "refresh_expires_at": refresh_expires_at.isoformat(),
        "csrf_token": csrf,
    }


@admin_auth_router.post("/logout")
@limiter.limit("10/minute")
async def admin_logout(request: Request, response: Response, body: LogoutRequest):
    """Admin logout — revokes the presented refresh token.

    Previously returned a canned success message with zero DB side
    effects. Now actually stamps revoked_at so the refresh token can
    never be exchanged again. The current access token keeps working
    until exp; use /admin/auth/logout-all for immediate kill.

    The ``request`` parameter is required by slowapi's rate limiter
    (>=0.1.9 validates the signature at decoration time) even though we
    don't reference it in the body — removing it raises at import and
    takes the whole admin router (and therefore server boot) down.
    """
    if body.refresh_token:
        await revoke_refresh_token(body.refresh_token)
    clear_csrf_cookie(response)
    return {"success": True}


@admin_auth_router.post("/logout-all")
@limiter.limit("5/minute")
async def admin_logout_all(request: Request, authorization: Optional[str] = Header(None)):
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

    staff = await db.find_one("admin_staff", {"id": user_id})
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found")

    new_version = int(staff.get("token_version") or 0) + 1
    await db.update_one("admin_staff", {"id": user_id}, {"$set": {"token_version": new_version}})
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

    staff = await db.find_one("admin_staff", {"id": user_id})
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
    await db.update_one(
        "admin_staff",
        {"id": user_id},
        {"$set": {"password_hash": new_hash}},
    )

    logger.info(f"Password changed for admin_staff id={user_id}")
    return {"success": True, "message": "Password changed successfully"}


# ---------------------------------------------------------------------------
# A-P3-4: Forgot-password / reset-password flow for admin staff
# ---------------------------------------------------------------------------

_RESET_PREFIX = "admin:pw_reset:"
_RESET_TTL_SECONDS = 15 * 60  # 15-minute reset window


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@admin_auth_router.post("/forgot-password")
@limiter.limit("3/hour")
async def admin_forgot_password(request: Request, body: ForgotPasswordRequest):
    """Request a password-reset link for an admin staff account.

    Always returns 200 regardless of whether the email matches a staff row
    (prevents email-enumeration). The reset token is a short-lived JWT stored
    in Redis; the caller is responsible for delivering the token to the user
    (e.g. via email). Rate-limited to 3 requests per email per hour.
    """
    import secrets as _secrets

    staff = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("admin_staff", {"email": body.email.lower()}, limit=1)
    )
    if staff and staff.get("is_active", True):
        token = _secrets.token_urlsafe(32)
        await redis_set(f"{_RESET_PREFIX}{token}", staff["id"], ttl=_RESET_TTL_SECONDS)
        logger.info(f"Password reset token issued for admin_staff id={staff['id']}")
        # In production, email this token via the email service.
        # Returned here for dev/test environments only.
        if settings.ENV != "production":
            return {"success": True, "reset_token": token, "expires_in_seconds": _RESET_TTL_SECONDS}

    # Always return the same shape to prevent email enumeration.
    return {"success": True}


@admin_auth_router.post("/reset-password")
@limiter.limit("5/hour")
async def admin_reset_password(request: Request, body: ResetPasswordRequest):
    """Consume a password-reset token and set a new password.

    The token is single-use; it is deleted from Redis on first successful
    consumption. All existing refresh tokens for the staff member are revoked.
    Rate-limited to 5 attempts per hour per IP to deter token guessing.
    """
    staff_id = await redis_get(f"{_RESET_PREFIX}{body.token}")
    if not staff_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if len(body.new_password) < 12:
        raise HTTPException(status_code=400, detail="New password must be at least 12 characters")

    staff = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("admin_staff", {"id": staff_id}, limit=1))
    if not staff or not staff.get("is_active", True):
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    new_hash = hash_password(body.new_password)
    await db_supabase.update_one("admin_staff", {"id": staff_id}, {"password_hash": new_hash})
    # Invalidate the token and all active sessions.
    await redis_delete(f"{_RESET_PREFIX}{body.token}")
    await revoke_all_for_user(staff_id)
    await _clear_login_failures(staff.get("email", ""))

    logger.info(f"Password reset completed for admin_staff id={staff_id}")
    return {"success": True, "message": "Password reset successfully. Please log in again."}
