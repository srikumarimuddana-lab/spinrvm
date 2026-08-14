import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
import pyotp
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...core.config import settings
    from ...dependencies import JWT_AUD_ADMIN, get_admin_user
    from ...utils.audit_logger import log_admin_action
    from ...utils.password import hash_password, verify_password
    from ...utils.rate_limiter import default_limiter as limiter
    from ...utils.rate_limiter import get_real_client_ip
    from ...utils.redis_client import (
        redis_delete,
        redis_expire,
        redis_get,
        redis_incr,
        redis_set,
    )
    from ...utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )
except ImportError:
    import db_supabase
    from core.config import settings
    from dependencies import JWT_AUD_ADMIN, get_admin_user
    from utils.audit_logger import log_admin_action
    from utils.password import hash_password, verify_password
    from utils.rate_limiter import default_limiter as limiter
    from utils.rate_limiter import get_real_client_ip
    from utils.redis_client import (
        redis_delete,
        redis_expire,
        redis_get,
        redis_incr,
        redis_set,
    )
    from utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

# Admin auth shares the distributed async limiter. Its /auth/ scope fails
# closed if Redis is unavailable, rather than weakening brute-force controls.

# Per-account lockout (A-P3-2) — 5 failures within the sliding window
# triggers a 24-hour lockout regardless of IP (defends against distributed
# attacks that rotate IPs to bypass the per-IP SlowAPI limit above). Stored
# in Redis with TTL; falls back to in-process dict when Redis is unavailable.
_LOGIN_MAX_FAILURES = 5
# 24h lockout in production; 2 minutes in dev so a mistyped password doesn't
# lock you out of a local environment for the rest of the day.
_LOGIN_LOCKOUT_TTL_SECONDS = 2 * 60 if settings.ENV.lower() != "production" else 24 * 60 * 60

# Per-account TOTP failure lockout — prevents brute-forcing 6-digit codes via
# IP rotation (the per-IP SlowAPI limit alone is insufficient: 10 req/min × N
# IPs = fast exhaustion of the 10^6 TOTP space within the 30-second window).
_TOTP_MAX_FAILURES = 5
_TOTP_LOCKOUT_TTL_SECONDS = 15 * 60  # 15 min; codes rotate every 30 s anyway


def _totp_lockout_key(user_id: str) -> str:
    return f"admin:totp_failures:{user_id}"


async def _is_totp_locked(user_id: str) -> bool:
    try:
        val = await redis_get(_totp_lockout_key(user_id))
        return val is not None and int(val) >= _TOTP_MAX_FAILURES
    except HTTPException:
        raise
    except Exception as e:
        # Fail CLOSED, same as _is_account_locked: the lockout is the only
        # per-account defence against TOTP brute force (the per-IP SlowAPI
        # limit is rotation-bypassable), so a degraded Redis must not
        # silently restore that attack vector.
        logger.error("[REDIS] _is_totp_locked check failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=503, detail="ERR_AUTH_UNAVAILABLE") from None


async def _record_totp_failure(user_id: str) -> None:
    try:
        key = _totp_lockout_key(user_id)
        await redis_incr(key)
        # Always refresh TTL — not just on first failure — so a partial write
        # where incr succeeded but expire failed is healed on the next attempt,
        # preventing an indefinitely-persisted lockout key.
        await redis_expire(key, _TOTP_LOCKOUT_TTL_SECONDS)
    except Exception as e:
        logger.error("[REDIS] _record_totp_failure failed for user %s: %s", user_id, e)


async def _clear_totp_failures(user_id: str) -> None:
    try:
        await redis_delete(_totp_lockout_key(user_id))
    except Exception as e:
        logger.error("[REDIS] _clear_totp_failures failed for user %s: %s", user_id, e)


def _lockout_key(email: str) -> str:
    return f"admin:login_failures:{email.lower().strip()}"


def _log_safe_email(email: str) -> str:
    """Stable non-PII identifier for an email in log lines.

    PIPEDA discipline: raw email addresses must never appear in logs.
    Same input → same digest, so lockout-related log lines for one
    account remain correlatable without exposing the address.
    """
    digest = hashlib.sha256(email.lower().strip().encode()).hexdigest()[:12]
    return f"email_sha256:{digest}"


async def _is_account_locked(email: str) -> bool:
    try:
        val = await redis_get(_lockout_key(email))
        return val is not None and int(val) >= _LOGIN_MAX_FAILURES
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[REDIS] _is_account_locked check failed for admin login ({_log_safe_email(email)}): {e}")
        raise HTTPException(status_code=503, detail="ERR_AUTH_UNAVAILABLE") from None


async def _record_login_failure(email: str) -> None:
    try:
        key = _lockout_key(email)
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, _LOGIN_LOCKOUT_TTL_SECONDS)
    except Exception as e:
        logger.error(f"[REDIS] _record_login_failure could not persist failure count ({_log_safe_email(email)}): {e}")


async def _clear_login_failures(email: str) -> None:
    try:
        await redis_delete(_lockout_key(email))
    except Exception as e:
        logger.error(f"[REDIS] _clear_login_failures could not clear failure count ({_log_safe_email(email)}): {e}")


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
    "staff",
    "audit",
    "support_tickets",
]
# "heatmap" was removed here and from AVAILABLE_MODULES (routes/admin/staff.py)
# — it gated no backend route; see the note at that list for the full reasoning.

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
    token_version: int,
) -> tuple[str, datetime]:
    """Mint an admin access token with a bounded TTL and a token_version
    claim so the revocation gate in dependencies.py can reject stale
    tokens after an admin force-logout-all. Historically admin tokens
    were minted WITHOUT an ``exp`` claim, so a single captured token
    granted permanent access — the primary P0-S3 fix is this function.

    PIPEDA: the ``phone`` claim is always empty — admin staff have no phone
    on file and the old behavior of duplicating the email into it doubled
    the PII payload of every minted token for no consumer.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.ADMIN_ACCESS_TOKEN_TTL_HOURS)
    token = jwt.encode(
        {
            "user_id": user_id,
            "email": email,
            "role": role,
            "modules": modules,
            "phone": "",
            "aud": JWT_AUD_ADMIN,
            "token_version": int(token_version or 0),
            "jti": secrets.token_hex(16),
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

    # Verify the JWT token. ``audience=JWT_AUD_ADMIN`` makes PyJWT
    # reject any token whose ``aud`` claim is missing or wrong, so a
    # rider/driver token cannot be presented here for an admin
    # session even if it was signed with the same JWT_SECRET.
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.ALGORITHM],
            audience=JWT_AUD_ADMIN,
        )
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


def _admin_login_rate_limit() -> str:
    # Strict in production (brute-force defence); permissive in dev/staging
    # so a developer doesn't get locked out after a few mistyped passwords.
    return "3/30minutes" if settings.ENV.lower() == "production" else "20/minute"


@admin_auth_router.post("/login")
@limiter.limit(_admin_login_rate_limit)
async def admin_login(request: Request, response: Response, body: LoginRequest):
    """Admin login — supports super admin + staff members with module access.

    Rate-limited to 5 attempts per minute per IP (see `limiter` above)
    to make password brute-force impractical. slowapi requires the
    FastAPI ``Request`` parameter to be named ``request`` so it can
    extract the client address; the Pydantic body has been renamed
    from ``request`` to ``body`` to free up the name.
    """
    user_agent = request.headers.get("user-agent", "")
    client_ip = get_real_client_ip(request)

    # Per-account lockout (F-21): reject before touching credentials so that
    # timing differences cannot reveal whether an account exists.
    if await _is_account_locked(body.email):
        raise HTTPException(
            status_code=423,
            detail="Account locked due to too many failed login attempts. Try again in 24 hours.",
        )

    # 1. Super admin from env. Extra truthy-checks so an empty/whitespace
    # env var cannot match an empty body.password (A-P3-1: bcrypt comparison).
    if (
        settings.admin_password_hash
        and body.email == settings.ADMIN_EMAIL
        and verify_password(body.password, settings.admin_password_hash)[0]
    ):
        # admin-001 has no DB row, so token_version stays at 0. We still
        # emit the claim + an exp so a captured super-admin token dies
        # after ADMIN_ACCESS_TOKEN_TTL_HOURS and can't live forever.
        token, access_expires_at = _mint_admin_access_token(
            user_id="admin-001",
            email=body.email,
            role="super_admin",
            modules=ALL_MODULES,
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
            _now_iso = datetime.now(timezone.utc).isoformat()
            updates: dict = {"last_login": _now_iso}
            if needs_upgrade:
                updates["password_hash"] = hash_password(body.password)
                logger.info(
                    "admin auth: upgraded legacy password hash for staff %s",
                    staff["id"],
                )
            await db_supabase.update_one("admin_staff", {"id": staff["id"]}, updates)
            # Reset the idle-session clock on every fresh authentication. Without
            # this a staff member who idles past the 30-minute timeout is locked
            # out forever: _verify_admin_payload raises ERR_IDLE_TIMEOUT before it
            # reaches the line that refreshes last_activity_at, and nothing else
            # writes the column — so the stale timestamp 401s every request
            # (login → 401 → refresh → 401 → logout). This covers all three
            # session paths (direct login, MFA challenge, forced enrollment):
            # they all pass through here before a session is minted or gated.
            #
            # Kept as a separate best-effort write, NOT folded into the atomic
            # `updates` above: if last_activity_at hasn't been migrated yet
            # (233), folding it in would 500 every staff login. And that failure
            # mode is benign — a missing column means _verify_admin_payload reads
            # None and skips the idle check entirely, so there is no lockout to
            # recover from. The missing column is already surfaced loudly in
            # _verify_admin_payload's per-request log, so nothing is masked here.
            try:
                await db_supabase.update_one("admin_staff", {"id": staff["id"]}, {"last_activity_at": _now_iso})
            except Exception as _idle_err:
                logger.warning(
                    "Could not reset last_activity_at on login for staff %s: %s",
                    staff["id"],
                    _idle_err,
                )
            if staff.get("mfa_enabled"):
                mfa_token = _mint_mfa_challenge_token(staff["id"], token_version=int(staff.get("token_version") or 0))
                await _clear_login_failures(body.email)
                return {"mfa_required": True, "mfa_token": mfa_token}
            if settings.ADMIN_MFA_ENFORCED:
                # MFA is mandatory for every staff account: a correct password
                # buys an enrollment-scoped token, never a session. The
                # dashboard routes this into the QR enrollment flow;
                # /mfa/confirm issues the real tokens once the first code
                # verifies.
                enroll_token = _mint_mfa_enroll_token(staff["id"], token_version=int(staff.get("token_version") or 0))
                await _clear_login_failures(body.email)
                return {"mfa_enrollment_required": True, "mfa_token": enroll_token}
            modules = staff.get("modules", ["dashboard"])
            token, access_expires_at = _mint_admin_access_token(
                user_id=staff["id"],
                email=staff["email"],
                role=staff.get("role", "custom"),
                modules=modules,
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
        # NOTE: this literal already drifts from ALL_MODULES (it omits "audit"
        # and "support_tickets"), so refresh returns a narrower list than login
        # does for the same account. Inert today — admin-001 is super_admin, and
        # both require_module() and the sidebar bypass the modules array for that
        # role — so it is left alone here rather than widened as a side effect of
        # removing "heatmap".
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
            "staff",
        ]
        token_version = 0
    else:
        staff = await db.find_one("admin_staff", {"id": user_id})
        if not staff or not staff.get("is_active", True):
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        # MFA enforcement must hold on the refresh path too. A staff account
        # holding a pre-enforcement (or pre-reset) refresh token would
        # otherwise keep silently minting full admin sessions without ever
        # enrolling, bypassing ADMIN_MFA_ENFORCED until the 30-day token
        # expires. 401 forces them back through /login → enrollment.
        if settings.ADMIN_MFA_ENFORCED and not staff.get("mfa_enabled"):
            raise HTTPException(status_code=401, detail="MFA enrollment required")
        email = staff["email"]
        role = staff.get("role", "custom")
        modules = staff.get("modules", ["dashboard"])
        token_version = int(staff.get("token_version") or 0)

    user_agent = request.headers.get("user-agent", "")
    client_ip = get_real_client_ip(request)

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
async def admin_logout(
    request: Request,
    body: LogoutRequest,
    authorization: Optional[str] = Header(None),
):
    """Admin logout — revokes the refresh token and blacklists the access token JTI.

    Blacklisting the JTI means the access token stops working immediately
    rather than coasting until its exp claim. The Redis key is set with a TTL
    equal to the token's remaining lifetime so the blacklist is self-pruning.

    The ``request`` parameter is required by slowapi's rate limiter
    (>=0.1.9 validates the signature at decoration time) even though we
    don't reference it in the body — removing it raises at import and
    takes the whole admin router (and therefore server boot) down.
    """
    if body.refresh_token:
        await revoke_refresh_token(body.refresh_token)

    if authorization:
        try:
            scheme, access_token = authorization.split()
            if scheme.lower() == "bearer":
                payload = jwt.decode(
                    access_token,
                    settings.JWT_SECRET,
                    algorithms=[settings.ALGORITHM],
                    options={"verify_exp": False},
                )
                jti = payload.get("jti")
                exp = payload.get("exp")
                if jti and exp:
                    remaining = int(exp - datetime.now(timezone.utc).timestamp())
                    if remaining > 0:
                        await redis_set(f"admin:revoked:{jti}", "1", ttl=remaining)
        except Exception:  # noqa: S110
            pass  # malformed / already-expired token — nothing to blacklist

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
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.ALGORITHM],
            audience=JWT_AUD_ADMIN,
        )
    except (ValueError, jwt.InvalidTokenError) as e:
        # B-P3-leak-cleanup: JWT library error strings carry hints
        # about token shape (algorithm, kid, exp, audience). Don't
        # ship them to the client — log server-side and surface a
        # generic "Invalid token" so the auth path can't be
        # fingerprinted by sending malformed tokens and reading the
        # rejection reasons.
        logger.error(
            "Admin auth rejected malformed token",
            exc_info=True,
            extra={"domain": "admin"},
        )
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
            user_id,
            client_types=["admin"],
            reason="logout_all",
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
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    authorization: Optional[str] = Header(None),
):
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
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.ALGORITHM],
            audience=JWT_AUD_ADMIN,
        )
    except (ValueError, jwt.InvalidTokenError) as e:
        # B-P3-leak-cleanup: JWT library error strings carry hints
        # about token shape (algorithm, kid, exp, audience). Don't
        # ship them to the client — log server-side and surface a
        # generic "Invalid token" so the auth path can't be
        # fingerprinted by sending malformed tokens and reading the
        # rejection reasons.
        logger.error(
            "Admin auth rejected malformed token",
            exc_info=True,
            extra={"domain": "admin"},
        )
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


# Dedicated audience for the short-lived MFA challenge token. Distinct from
# JWT_AUD_ADMIN on purpose: a challenge token is proof of password only, not
# an admin session, and must never be accepted where either is expected.
JWT_AUD_MFA_CHALLENGE = "spinr:admin:mfa-challenge"

# Enrollment-scoped audience: handed out at login when ADMIN_MFA_ENFORCED is
# on and the staff member hasn't enrolled yet. Accepted ONLY by /mfa/enroll
# and /mfa/confirm — it is proof of password, not a session, and must never
# unlock any other admin surface.
JWT_AUD_MFA_ENROLL = "spinr:admin:mfa-enroll"


def _mint_mfa_challenge_token(user_id: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "type": "mfa_challenge",
            "aud": JWT_AUD_MFA_CHALLENGE,
            "user_id": user_id,
            # Bound to the staff row's token_version at mint time (same
            # rationale as the enrollment token): a logout-all / MFA reset
            # during the 5-minute challenge window must invalidate the
            # in-flight challenge, not let it exchange for a fresh session
            # stamped with the new version.
            "token_version": int(token_version or 0),
            "exp": now + timedelta(minutes=5),
        },
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )


def _mint_mfa_enroll_token(user_id: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "type": "mfa_enroll",
            "aud": JWT_AUD_MFA_ENROLL,
            "user_id": user_id,
            # Bound to the staff row's token_version at mint time so a
            # force-logout-all or MFA reset (both bump token_version)
            # invalidates an in-flight enrollment token — it can no longer
            # be exchanged for a session via /mfa/confirm.
            "token_version": int(token_version or 0),
            # 15 min: enough to install an authenticator app, scan the QR and
            # type the first code; short enough that an intercepted token is
            # near-useless (it still can't read or mutate anything).
            "exp": now + timedelta(minutes=15),
        },
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


async def _require_staff_from_token(
    authorization: str | None,
    *,
    allow_enroll_token: bool = False,
    return_payload: bool = False,
) -> dict:
    """Resolve the admin_staff row from a Bearer token.

    Accepts a full admin access token. With ``allow_enroll_token=True``
    (only the /mfa/enroll and /mfa/confirm endpoints) it additionally
    accepts the enrollment-scoped token minted at login under
    ADMIN_MFA_ENFORCED — that token must never unlock anything else.

    With ``return_payload=True`` returns ``(staff, payload)`` so the caller
    can tell which audience authenticated (e.g. /mfa/confirm only mints a
    new session for the enroll-token / forced-login flow, not for a
    Settings-page re-enrollment that already holds a session).
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    audiences = [JWT_AUD_ADMIN] + ([JWT_AUD_MFA_ENROLL] if allow_enroll_token else [])
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid auth scheme")
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.ALGORITHM],
            audience=audiences,
        )
    except (ValueError, jwt.InvalidTokenError) as e:
        # Static detail — PyJWT error strings reveal which exact claim
        # failed (alg, aud, exp), letting an attacker fingerprint the
        # token validation by probing with crafted tokens.
        logger.error(
            "Admin auth rejected malformed token",
            exc_info=True,
            extra={"domain": "admin"},
        )
        raise HTTPException(status_code=401, detail="Invalid token") from e
    if payload.get("aud") == JWT_AUD_MFA_ENROLL and payload.get("type") != "mfa_enroll":
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("user_id")
    if not user_id or user_id == "admin-001":
        raise HTTPException(
            status_code=400,
            detail="MFA is not available for the super admin env account. Use a staff account.",
        )
    # Mirror _verify_admin_payload's per-JTI denylist: /admin/auth/logout
    # revokes a single access token by writing admin:revoked:{jti} WITHOUT
    # bumping token_version, so the version gate below doesn't catch it. A
    # logged-out (or stolen-then-revoked) admin token must not be able to
    # start/confirm MFA enrollment or disable MFA. Fail OPEN on Redis
    # outage for the same reason as _verify_admin_payload — the
    # authoritative logout-all/token_version control still runs below.
    jti = payload.get("jti")
    if jti:
        try:
            _jti_revoked = await redis_get(f"admin:revoked:{jti}")
        except Exception as _revoke_err:
            logger.error(
                "[auth] admin revocation denylist unreachable (Redis down) — "
                f"failing OPEN for jti={jti} on MFA helper; token_version still enforced: {_revoke_err}"
            )
            _jti_revoked = None
        if _jti_revoked:
            raise HTTPException(status_code=401, detail="Invalid token")
    staff = await db.find_one("admin_staff", {"id": user_id})
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if not staff.get("is_active", True):
        raise HTTPException(status_code=401, detail="Account not found or inactive")
    # Revocation gate. Both audiences carry token_version (admin access tokens
    # from _mint_admin_access_token, enrollment tokens from
    # _mint_mfa_enroll_token). A force-logout-all or MFA reset bumps
    # admin_staff.token_version; any token minted before that is stale and must
    # not be exchangeable for a fresh session via /mfa/confirm — otherwise the
    # forced logout is silently undone.
    if int(payload.get("token_version") or 0) < int(staff.get("token_version") or 0):
        raise HTTPException(status_code=401, detail="Invalid token")
    # An enrollment token is single-purpose: get a not-yet-enrolled account
    # through first enrollment. Once mfa_enabled is set it must die —
    # otherwise an intercepted enroll token could be replayed within its
    # 15-minute life to re-run /mfa/enroll, overwrite the freshly bound
    # secret, and mint another session, replacing the victim's MFA.
    if payload.get("aud") == JWT_AUD_MFA_ENROLL and staff.get("mfa_enabled"):
        raise HTTPException(status_code=401, detail="Invalid token")
    if return_payload:
        return staff, payload
    return staff


# ── MFA endpoints ─────────────────────────────────────────────────────────


@admin_auth_router.get("/mfa/status")
async def admin_mfa_status(authorization: Optional[str] = Header(None)):
    """Return MFA enrollment status for the authenticated staff member.

    Super admin (admin-001 env account) has no admin_staff row and cannot
    enroll in TOTP MFA — return available=false instead of a 400 so the
    frontend can hide the MFA panel gracefully.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid auth scheme")
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.ALGORITHM],
            audience=JWT_AUD_ADMIN,
        )
    except (ValueError, jwt.InvalidTokenError) as e:
        # Static detail — PyJWT error strings reveal which exact claim
        # failed (alg, aud, exp), letting an attacker fingerprint the
        # token validation by probing with crafted tokens.
        logger.error(
            "Admin auth rejected malformed token",
            exc_info=True,
            extra={"domain": "admin"},
        )
        raise HTTPException(status_code=401, detail="Invalid token") from e
    user_id = payload.get("user_id")
    if not user_id or user_id == "admin-001":
        return {"mfa_enabled": False, "available": False, "enforced": settings.ADMIN_MFA_ENFORCED}
    staff = await db.find_one("admin_staff", {"id": user_id})
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found")
    # `enforced` lets the Settings UI hide the Disable button (the backend
    # rejects disable with 403 under enforcement regardless).
    return {"mfa_enabled": bool(staff.get("mfa_enabled")), "available": True, "enforced": settings.ADMIN_MFA_ENFORCED}


@admin_auth_router.post("/mfa/enroll")
@limiter.limit("5/minute")
async def admin_mfa_enroll(request: Request, authorization: Optional[str] = Header(None)):
    """Begin TOTP enrollment. Returns secret + otpauth URI; confirm with /mfa/confirm."""
    staff = await _require_staff_from_token(authorization, allow_enroll_token=True)
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=staff["email"], issuer_name="Spinr Admin")
    await db_supabase.update_one("admin_staff", {"id": staff["id"]}, {"mfa_secret_pending": secret})
    return {"secret": secret, "otpauth_uri": uri}


@admin_auth_router.post("/mfa/confirm")
@limiter.limit("5/minute")
async def admin_mfa_confirm(
    request: Request,
    body: MfaConfirmRequest,
    authorization: Optional[str] = Header(None),
):
    """Confirm TOTP enrollment by verifying the first code. Activates MFA and issues backup codes.

    For the forced-enrollment flow (caller holds an enrollment-scoped token,
    no session yet) this also returns full session tokens: confirming the
    first TOTP code is at least as strong a proof as /mfa/challenge —
    password (at login) + possession of the freshly bound authenticator.
    Settings-page re-enrollment (caller already holds a session) gets
    backup_codes only, so its existing session and CSRF cookies are
    untouched.
    """
    staff, payload = await _require_staff_from_token(authorization, allow_enroll_token=True, return_payload=True)
    pending = staff.get("mfa_secret_pending")
    if not pending:
        raise HTTPException(status_code=400, detail="No pending MFA enrollment. Call /mfa/enroll first.")
    if not pyotp.TOTP(pending).verify(body.totp_code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    plaintext_codes, hashed_records = _generate_backup_codes()
    await db_supabase.update_one(
        "admin_staff",
        {"id": staff["id"]},
        {
            "mfa_enabled": True,
            "mfa_secret": pending,
            "mfa_secret_pending": None,
            "mfa_backup_codes": hashed_records,
        },
    )
    await log_admin_action(staff, "mfa_enabled", "admin_staff", staff["id"], {})

    # Only the forced-login flow (authenticated via the enrollment token) gets
    # a fresh session here. A Settings re-enrollment already holds one; minting
    # a new refresh token would silently rotate it out from under the live
    # session and desync the CSRF cookie.
    if payload.get("aud") != JWT_AUD_MFA_ENROLL:
        return {"backup_codes": plaintext_codes}

    user_agent = request.headers.get("user-agent", "")
    client_ip = get_real_client_ip(request)
    modules = staff.get("modules", ["dashboard"])
    token, access_expires_at = _mint_admin_access_token(
        user_id=staff["id"],
        email=staff["email"],
        role=staff.get("role", "custom"),
        modules=modules,
        token_version=int(staff.get("token_version") or 0),
    )
    refresh_raw, _, refresh_expires_at = await issue_refresh_token(
        staff["id"], audience="admin", user_agent=user_agent, ip=client_ip
    )
    return {
        "backup_codes": plaintext_codes,
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


@admin_auth_router.post("/mfa/disable")
@limiter.limit("3/minute")
async def admin_mfa_disable(
    request: Request,
    body: MfaDisableRequest,
    authorization: Optional[str] = Header(None),
):
    """Disable MFA. Requires current password + valid TOTP code.

    Blocked entirely while ADMIN_MFA_ENFORCED is on: self-service disable
    would let any staff member opt out of mandatory MFA and keep their
    1-hour session. Lost-authenticator recovery goes through the
    super-admin reset (which revokes all sessions and forces re-enrollment
    at next login), not through disable.
    """
    staff = await _require_staff_from_token(authorization)
    if settings.ADMIN_MFA_ENFORCED:
        raise HTTPException(
            status_code=403,
            detail="MFA is mandatory for all staff accounts. Ask a super admin "
            "to reset your MFA if you lost your authenticator.",
        )
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
        # audience pin: only tokens minted by _mint_mfa_challenge_token pass.
        # Rollout note: challenge tokens live 5 minutes, so requiring the aud
        # here in the same deploy that adds it at mint is safe — a pre-deploy
        # token at worst forces one fresh login.
        payload = jwt.decode(
            body.mfa_token,
            settings.JWT_SECRET,
            algorithms=[settings.ALGORITHM],
            audience=JWT_AUD_MFA_CHALLENGE,
        )
    except jwt.InvalidTokenError as e:
        # Static detail — see _require_staff_from_token; PyJWT messages
        # fingerprint which claim failed.
        logger.error(
            "MFA challenge rejected malformed token",
            exc_info=True,
            extra={"domain": "admin"},
        )
        raise HTTPException(status_code=401, detail="Invalid MFA token") from e
    if payload.get("type") != "mfa_challenge":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid MFA token")
    staff = await db.find_one("admin_staff", {"id": user_id})
    if not staff or not staff.get("is_active", True):
        raise HTTPException(status_code=401, detail="Account not found or inactive")
    # Revocation gate — a challenge token minted before a logout-all /
    # MFA reset (token_version bump) must not exchange for a session.
    if int(payload.get("token_version") or 0) < int(staff.get("token_version") or 0):
        raise HTTPException(status_code=401, detail="Invalid MFA token")
    if not staff.get("mfa_enabled") or not staff.get("mfa_secret"):
        raise HTTPException(status_code=400, detail="MFA not configured for this account")
    # Per-account TOTP lockout — the per-IP SlowAPI limit alone is bypassable
    # via IP rotation; 6-digit codes are brute-forceable without this gate.
    if await _is_totp_locked(user_id):
        logger.error(
            "MFA challenge: account locked out after repeated TOTP failures",
            extra={"domain": "admin", "user_id": user_id},
        )
        raise HTTPException(status_code=429, detail="Too many failed codes — try again later")
    totp_valid = pyotp.TOTP(staff["mfa_secret"]).verify(body.totp_code, valid_window=1)
    if not totp_valid:
        backup_codes = staff.get("mfa_backup_codes") or []
        matched, updated_codes = _consume_backup_code(body.totp_code, backup_codes)
        if not matched:
            await _record_totp_failure(user_id)
            raise HTTPException(status_code=401, detail="Invalid TOTP code or backup code")
        await db_supabase.update_one("admin_staff", {"id": user_id}, {"mfa_backup_codes": updated_codes})
    await _clear_totp_failures(user_id)
    user_agent = request.headers.get("user-agent", "")
    client_ip = get_real_client_ip(request)
    modules = staff.get("modules", ["dashboard"])
    token, access_expires_at = _mint_admin_access_token(
        user_id=staff["id"],
        email=staff["email"],
        role=staff.get("role", "custom"),
        modules=modules,
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


# ── Break-glass emergency access ─────────────────────────────────────────────

_BG_RATE_KEY = "spinr:admin:break_glass:rate"
_BG_MAX_USES_PER_DAY = 5
_BG_TOKEN_TTL_HOURS = 1


class BreakGlassRequest(BaseModel):
    token: str
    justification: str


@admin_auth_router.post("/break-glass")
@limiter.limit("5/hour")
async def break_glass_access(request: Request, body: BreakGlassRequest):
    """Emergency access endpoint — issues a 1-hour super_admin JWT when a
    valid pre-shared break-glass token is presented with a justification.

    Every use is logged to audit_logs at ERROR level (reaches Sentry) so
    operators are alerted immediately.  The endpoint is disabled unless
    BREAK_GLASS_TOKEN_HASH is set in the environment.

    Rate-limited to 5 uses per 24h (Redis); each attempt is logged
    regardless of outcome so brute-force attempts are visible in Sentry.

    Token management:
      Generate a token + its SHA-256 hash with:
        python3 -c "import hashlib, secrets; t=secrets.token_hex(32);
          print('TOKEN:', t); print('HASH:', hashlib.sha256(t.encode()).hexdigest())"
      Store BREAK_GLASS_TOKEN_HASH=<hash> in the environment.
      Keep the raw token in an offline vault (1Password, Vault, etc.).
    """
    client_ip = get_real_client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")

    # 1. Feature-gate: disabled unless hash is configured
    if not settings.BREAK_GLASS_TOKEN_HASH:
        # Security signal — capture every attempt at a disabled break-glass endpoint
        # so Sentry surfaces it for the security on-call.
        logger.error(
            "break_glass: attempt from %s ua=%s — endpoint not configured",
            client_ip,
            user_agent,
        )
        raise HTTPException(status_code=404, detail="Not found")

    # 2. Justification required (non-empty, min 10 chars)
    justification = (body.justification or "").strip()
    if len(justification) < 10:
        logger.error(
            "break_glass: rejected from %s — justification too short (%d chars)",
            client_ip,
            len(justification),
        )
        raise HTTPException(status_code=400, detail="justification must be at least 10 characters")

    # 3. Daily rate limit (5 attempts total, including failed token checks)
    try:
        from utils.redis_client import (
            redis_expire,
            redis_get,
            redis_incr,
            redis_set,
        )  # noqa: PLC0415
    except ImportError:
        from ..utils.redis_client import (  # type: ignore[no-redef]
            redis_expire,
            redis_get,
            redis_incr,
            redis_set,
        )

    # Fail CLOSED: an unreadable counter on a super-admin-minting endpoint
    # must block, not allow — an attacker who can degrade Redis must not gain
    # unlimited brute-force attempts. The 1h-window SlowAPI decorator above is
    # an additional per-IP backstop, but it shares the same Redis dependency.
    try:
        count_raw = await redis_get(_BG_RATE_KEY)
        count = int(count_raw or 0)
    except Exception as e:
        logger.error("break_glass: rate-limit counter unreadable (Redis down) — failing closed: %s", e)
        raise HTTPException(status_code=503, detail="Break-glass temporarily unavailable") from e

    if count >= _BG_MAX_USES_PER_DAY:
        logger.error(
            "break_glass: RATE LIMIT EXCEEDED from %s — %d attempts today",
            client_ip,
            count,
        )
        raise HTTPException(status_code=429, detail="Break-glass rate limit exceeded for today")

    # Increment before token validation so brute-force attempts consume quota
    try:
        daily_use_count = await redis_incr(_BG_RATE_KEY)
        if daily_use_count == 1:
            # First use today — set expiry to 24h
            await redis_expire(_BG_RATE_KEY, 86400)
    except Exception as e:
        logger.error("break_glass: rate-limit counter increment failed — failing closed: %s", e)
        raise HTTPException(status_code=503, detail="Break-glass temporarily unavailable") from e

    # 4. Constant-time token comparison
    provided_hash = hashlib.sha256(body.token.encode()).hexdigest()
    if not secrets.compare_digest(provided_hash, settings.BREAK_GLASS_TOKEN_HASH):
        logger.error(
            "break_glass: INVALID TOKEN from %s (ua=%s) justification=%r",
            client_ip,
            user_agent,
            justification[:100],
        )
        raise HTTPException(status_code=401, detail="Invalid break-glass token")

    # 5. Mint a short-lived super_admin token (1 hour, no refresh)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=_BG_TOKEN_TTL_HOURS)
    bg_jti = secrets.token_hex(16)
    bg_token = jwt.encode(
        {
            "user_id": "break-glass",
            "email": "break-glass@spinr.ca",
            "role": "super_admin",
            "modules": ALL_MODULES,
            "phone": "",
            "aud": JWT_AUD_ADMIN,
            "token_version": 0,
            "jti": bg_jti,
            "iat": now,
            "exp": expires_at,
            "break_glass": True,
        },
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )

    # 5b. Register the JTI in a Redis ALLOWLIST so the token is revocable (C7).
    # _verify_admin_payload requires admin:breakglass:{jti} to be present on every
    # request and FAILS CLOSED if it is missing, so a leaked token is killed by
    # deleting this key (or via /admin/auth/logout, which denylists the jti). The
    # TTL matches the token lifetime so the key self-prunes. Fail closed here: if
    # we cannot register, the token would never authenticate, so surface 503
    # rather than handing back a dead token (matches the rate-limit posture above).
    bg_ttl_seconds = int(_BG_TOKEN_TTL_HOURS * 3600)
    try:
        await redis_set(f"admin:breakglass:{bg_jti}", "1", ttl=bg_ttl_seconds)
    except Exception as e:
        logger.error("break_glass: allowlist registration failed (Redis down) — failing closed: %s", e)
        raise HTTPException(status_code=503, detail="Break-glass temporarily unavailable") from e

    # 6. Mandatory audit log — this MUST land; if it fails, still return the token
    #    (operator is in an emergency) but log loudly so the gap is visible.
    audit_payload = {
        "actor": "break-glass",
        "client_ip": client_ip,
        "user_agent": user_agent,
        "justification": justification,
        "token_expires_at": expires_at.isoformat(),
        "daily_use_count": daily_use_count,
        # JTI recorded so an operator can revoke this exact token
        # (DEL admin:breakglass:{jti}) without rotating JWT_SECRET.
        "jti": bg_jti,
    }
    try:
        await db_supabase.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "action": "break_glass_access",
                "entity_type": "system",
                "entity_id": "break_glass",
                "details": audit_payload,
                "created_at": now.isoformat(),
            },
        )
    except Exception as exc:
        logger.error("break_glass: AUDIT LOG WRITE FAILED — %s", exc, exc_info=True)

    # 7. Sentry-visible error-level log so on-call is paged immediately
    logger.error(
        "BREAK GLASS ACCESSED from ip=%s ua=%s justification=%r — 1h super_admin token issued; investigate urgently",
        client_ip,
        user_agent,
        justification[:200],
    )

    return {
        "token": bg_token,
        "role": "super_admin",
        "expires_at": expires_at.isoformat(),
        "ttl_hours": _BG_TOKEN_TTL_HOURS,
        # Surfaced so an operator can revoke this exact session
        # (DEL admin:breakglass:{jti}) without rotating JWT_SECRET.
        "jti": bg_jti,
        "warning": "This token is time-limited and every use is audited. Use only in genuine emergencies.",
    }


# ----------------------------------------------------------------------
# Admin unlock (L-12)
# ----------------------------------------------------------------------
# When an admin trips the per-account lockout (5 failed logins → 24h ban),
# they have no self-service path back in. If a typo storm freezes the only
# super_admin, ops is stuck. This endpoint lets a *different* super_admin
# clear another admin's lockout. Every call is audit-logged.


class UnlockRequest(BaseModel):
    email: str


@admin_auth_router.post("/unlock")
async def admin_unlock(
    request: Request,
    body: UnlockRequest,
    actor: dict = Depends(get_admin_user),
):
    """Clear the 24h login lockout on another admin account.

    Caller must be a super_admin. Body: {"email": "<target>"}.
    - 404 if the target admin doesn't exist.
    - 200 {"unlocked": false, "reason": "not_locked"} if not currently locked
      (idempotent — safe to call repeatedly without false positives).
    - 200 {"unlocked": true} on success.
    """
    if actor.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="role_required:super_admin")

    target_email = (body.email or "").strip().lower()
    if not target_email:
        raise HTTPException(status_code=422, detail="email required")

    # Verify the target exists in admin_staff (don't leak which emails are
    # registered to non-super_admins, but super_admin already has staff list).
    rows = await db_supabase.get_rows("admin_staff", {"email": target_email}, limit=1)
    target = rows[0] if rows else None
    if not target:
        raise HTTPException(status_code=404, detail="admin not found")

    # Check current lock state. Redis key holds failure count; lock is in
    # effect when count >= _LOGIN_MAX_FAILURES (see _is_account_locked).
    try:
        raw = await redis_get(_lockout_key(target_email))
    except Exception as e:
        logger.error(
            "[REDIS] admin_unlock could not read lockout state for %s: %s",
            _log_safe_email(target_email),
            e,
        )
        raise HTTPException(status_code=503, detail="ERR_AUTH_UNAVAILABLE") from None

    failure_count = int(raw) if raw is not None else 0
    was_locked = failure_count >= _LOGIN_MAX_FAILURES

    if not was_locked:
        # Idempotent: nothing to clear. Still audit the no-op so we have a
        # record that someone tried (helps detect coordination issues).
        await log_admin_action(
            actor,
            "admin_unlock_noop",
            "admin_staff",
            target["id"],
            {"target_email": target_email, "failure_count": failure_count},
        )
        return {"unlocked": False, "reason": "not_locked"}

    # Clear the failure counter (deletes the Redis key with TTL).
    await _clear_login_failures(target_email)

    await log_admin_action(
        actor,
        "admin_unlocked",
        "admin_staff",
        target["id"],
        {"target_email": target_email, "prior_failure_count": failure_count},
    )

    logger.info(
        "admin unlock: actor=%s target=%s prior_failures=%s",
        actor.get("id"),
        target["id"],
        failure_count,
    )

    return {"unlocked": True}
