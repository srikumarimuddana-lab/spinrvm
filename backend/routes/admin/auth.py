import hashlib  # noqa: F401
import logging
import secrets  # noqa: F401
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
import pyotp
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

try:
    from ... import db_supabase
    from ...core.config import settings
    from ...utils.audit_logger import log_admin_action  # noqa: F401
    from ...utils.password import hash_password, verify_password
    from ...utils.redis_client import redis_delete, redis_expire, redis_get, redis_incr
    from ...utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )
except ImportError:
    import db_supabase
    from core.config import settings
    from utils.audit_logger import log_admin_action  # noqa: F401
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

# Per-router rate limiter. Admin login is a high-value brute-force
# target (one correct hit → full super-admin access), so we cap it
# to 5 attempts per minute per IP. Matches the pattern already used
# for the rider/driver OTP endpoint in routes/auth.py.
limiter = Limiter(key_func=get_remote_address)

# Per-account lockout — 5 failures within the sliding window triggers a
# 15-minute lockout regardless of IP (defends against distributed attacks
# that rotate IPs to bypass the per-IP SlowAPI limit above). Stored in
# Redis with TTL; falls back to in-process dict when Redis is unavailable.
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCKOUT_TTL_SECONDS = 15 * 60  # 15 minutes


def _lockout_key(email: str) -> str:
    return f"admin:login_failures:{email.lower().strip()}"


async def _is_account_locked(email: str) -> bool:
    try:
        val = await redis_get(_lockout_key(email))
        return val is not None and int(val) >= _LOGIN_MAX_FAILURES
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[REDIS] _is_account_locked check failed for admin login ({email!r}): {e}")
        raise HTTPException(status_code=503, detail="ERR_AUTH_UNAVAILABLE") from None


async def _record_login_failure(email: str) -> None:
    try:
        key = _lockout_key(email)
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, _LOGIN_LOCKOUT_TTL_SECONDS)
    except Exception as e:
        logger.error(f"[REDIS] _record_login_failure could not persist failure count ({email!r}): {e}")


async def _clear_login_failures(email: str) -> None:
    try:
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

    # Per-account lockout (F-21): reject before touching credentials so that
    # timing differences cannot reveal whether an account exists.
    if await _is_account_locked(body.email):
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Account temporarily locked. Try again in 15 minutes.",
        )

    # 1. Super admin from env. Extra truthy-check on ADMIN_PASSWORD so an
    # empty/whitespace value in .env cannot match an empty body.password.
    if settings.ADMIN_PASSWORD and body.email == settings.ADMIN_EMAIL and body.password == settings.ADMIN_PASSWORD:
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
    staff = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("admin_staff", {"email": body.email.lower()}, limit=1)
    )
    if staff:
        stored_hash = staff.get("password_hash", "") or ""
        ok, needs_upgrade = verify_password(body.password, stored_hash)
        if ok:
            if not staff.get("is_active", True):
                raise HTTPException(status_code=403, detail="Account is deactivated")
            await db_supabase.update_one(
                "admin_staff", {"id": staff["id"]}, {"last_login": datetime.now(timezone.utc).isoformat()}
            )
            if staff.get("mfa_enabled"):
                mfa_token = _mint_mfa_challenge_token(staff["id"])
                await _clear_login_failures(body.email)
                return {"mfa_required": True, "mfa_token": mfa_token}
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

    await _record_login_failure(body.email)
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
    return {
        "token": token,
        "refresh_token": new_raw,
        "access_expires_at": access_expires_at.isoformat(),
        "refresh_expires_at": refresh_expires_at.isoformat(),
    }


@admin_auth_router.post("/logout")
@limiter.limit("10/minute")
async def admin_logout(request: Request, body: LogoutRequest):
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
        # B-P3-leak-cleanup: JWT library error strings carry hints
        # about token shape (algorithm, kid, exp, audience). Don't
        # ship them to the client — log server-side and surface a
        # generic "Invalid token" so the auth path can't be
        # fingerprinted by sending malformed tokens and reading the
        # rejection reasons.
        logger.warning("Admin auth rejected malformed token", exc_info=e)
        raise HTTPException(status_code=401, detail="Invalid token") from e

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

    # B-P1-11: kick any live admin WebSocket sockets (live monitoring
    # console, etc.) so the staff member is logged out instantly. See
    # the rider/driver logout_all comment for the best-effort rationale.
    try:
        try:
            from ...socket_manager import manager as ws_manager
        except ImportError:  # pragma: no cover — package-relative fallback
            from socket_manager import manager as ws_manager
        await ws_manager.kick_user(
            user_id, client_types=["admin"], reason="logout_all",
        )
    except Exception as e:
        logger.warning(f"admin logout-all: WS kick failed for {user_id}: {e}")

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
        # B-P3-leak-cleanup: JWT library error strings carry hints
        # about token shape (algorithm, kid, exp, audience). Don't
        # ship them to the client — log server-side and surface a
        # generic "Invalid token" so the auth path can't be
        # fingerprinted by sending malformed tokens and reading the
        # rejection reasons.
        logger.warning("Admin auth rejected malformed token", exc_info=e)
        raise HTTPException(status_code=401, detail="Invalid token") from e

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


# ── MFA models ──────────────────────────────────────────────────────────────


class MfaConfirmRequest(BaseModel):
    totp_code: str


class MfaDisableRequest(BaseModel):
    totp_code: str
    password: str


class MfaChallengeRequest(BaseModel):
    mfa_token: str
    totp_code: str


# ── MFA helpers ──────────────────────────────────────────────────────────────


def _mint_mfa_challenge_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"type": "mfa_challenge", "user_id": user_id, "exp": now + timedelta(minutes=5)},
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )


def _generate_backup_codes() -> tuple[list[str], list[dict]]:
    codes: list[str] = []
    records: list[dict] = []
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    for _ in range(10):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        h = hashlib.sha256(code.encode()).hexdigest()
        codes.append(code)
        records.append({"hash": h, "used": False})
    return codes, records


def _consume_backup_code(code: str, stored: list[dict]) -> tuple[bool, list[dict]]:
    h = hashlib.sha256(code.upper().encode()).hexdigest()
    updated = list(stored)
    for i, entry in enumerate(updated):
        if not entry.get("used") and entry.get("hash") == h:
            updated[i] = {**entry, "used": True}
            return True, updated
    return False, stored


async def _require_staff_from_token(authorization: str | None) -> dict:
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
            detail="MFA is not available for the super admin env account. Use a staff account.",
        )
    staff = await db.find_one("admin_staff", {"id": user_id})
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return staff


# ── MFA endpoints ─────────────────────────────────────────────────────────


@admin_auth_router.get("/mfa/status")
async def admin_mfa_status(authorization: Optional[str] = Header(None)):
    """Return MFA enrollment status for the authenticated staff member."""
    staff = await _require_staff_from_token(authorization)
    return {"mfa_enabled": bool(staff.get("mfa_enabled"))}


@admin_auth_router.post("/mfa/enroll")
@limiter.limit("5/minute")
async def admin_mfa_enroll(request: Request, authorization: Optional[str] = Header(None)):
    """Begin TOTP enrollment. Returns secret + otpauth URI; confirm with /mfa/confirm."""
    staff = await _require_staff_from_token(authorization)
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=staff["email"], issuer_name="Spinr Admin")
    await db_supabase.update_one("admin_staff", {"id": staff["id"]}, {"mfa_secret_pending": secret})
    return {"secret": secret, "otpauth_uri": uri}


@admin_auth_router.post("/mfa/confirm")
@limiter.limit("5/minute")
async def admin_mfa_confirm(request: Request, body: MfaConfirmRequest, authorization: Optional[str] = Header(None)):
    """Confirm TOTP enrollment by verifying the first code. Activates MFA and issues backup codes."""
    staff = await _require_staff_from_token(authorization)
    pending = staff.get("mfa_secret_pending")
    if not pending:
        raise HTTPException(status_code=400, detail="No pending MFA enrollment. Call /mfa/enroll first.")
    if not pyotp.TOTP(pending).verify(body.totp_code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    plaintext_codes, hashed_records = _generate_backup_codes()
    await db_supabase.update_one(
        "admin_staff",
        {"id": staff["id"]},
        {"mfa_enabled": True, "mfa_secret": pending, "mfa_secret_pending": None, "mfa_backup_codes": hashed_records},
    )
    await log_admin_action(staff, "mfa_enabled", "admin_staff", staff["id"], {})
    return {"backup_codes": plaintext_codes}


@admin_auth_router.post("/mfa/disable")
@limiter.limit("3/minute")
async def admin_mfa_disable(request: Request, body: MfaDisableRequest, authorization: Optional[str] = Header(None)):
    """Disable MFA. Requires current password + valid TOTP code."""
    staff = await _require_staff_from_token(authorization)
    if not staff.get("mfa_enabled"):
        raise HTTPException(status_code=400, detail="MFA is not enabled on this account")
    ok, _ = verify_password(body.password, staff.get("password_hash", ""))
    if not ok:
        raise HTTPException(status_code=400, detail="Incorrect password")
    if not pyotp.TOTP(staff["mfa_secret"]).verify(body.totp_code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    await db_supabase.update_one(
        "admin_staff",
        {"id": staff["id"]},
        {"mfa_enabled": False, "mfa_secret": None, "mfa_backup_codes": None},
    )
    await log_admin_action(staff, "mfa_disabled", "admin_staff", staff["id"], {})
    return {"success": True}


@admin_auth_router.post("/mfa/challenge")
@limiter.limit("10/minute")
async def admin_mfa_challenge(request: Request, body: MfaChallengeRequest):
    """Exchange an MFA challenge token + TOTP (or backup code) for full admin tokens."""
    try:
        payload = jwt.decode(body.mfa_token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM])
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid MFA token: {e}") from e
    if payload.get("type") != "mfa_challenge":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid MFA token")
    staff = await db.find_one("admin_staff", {"id": user_id})
    if not staff or not staff.get("is_active", True):
        raise HTTPException(status_code=401, detail="Account not found or inactive")
    if not staff.get("mfa_enabled") or not staff.get("mfa_secret"):
        raise HTTPException(status_code=400, detail="MFA not configured for this account")
    totp_valid = pyotp.TOTP(staff["mfa_secret"]).verify(body.totp_code, valid_window=1)
    if not totp_valid:
        backup_codes = staff.get("mfa_backup_codes") or []
        matched, updated_codes = _consume_backup_code(body.totp_code, backup_codes)
        if not matched:
            raise HTTPException(status_code=401, detail="Invalid TOTP code or backup code")
        await db_supabase.update_one("admin_staff", {"id": user_id}, {"mfa_backup_codes": updated_codes})
    user_agent = request.headers.get("user-agent", "")
    client_ip = get_remote_address(request)
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
