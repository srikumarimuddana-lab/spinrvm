import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin.auth import (
    CertificateFetchError,
    ExpiredIdTokenError,
    InvalidIdTokenError,
    RevokedIdTokenError,
    UserDisabledError,
)
from loguru import logger

try:
    from . import db_supabase
    from .core.config import settings
    from .utils.error_handling import DatabaseError, ServiceUnavailableException
    from .utils.redis_client import redis_get
except ImportError:
    import db_supabase
    from core.config import settings
    from utils.error_handling import DatabaseError, ServiceUnavailableException
    from utils.redis_client import redis_get

db = db_supabase  # legacy alias

# Security Configuration
# JWT signing secret is the single `settings.JWT_SECRET` defined in
# core/config.py (loaded from the `JWT_SECRET` environment variable).
# Previously this module read its own env var with a separate hardcoded
# fallback, which meant regular-user tokens and admin tokens were signed
# with DIFFERENT secrets — a silent auth hazard. Unified here so both
# `routes/admin/auth.py` and this module share the same source of truth.
JWT_ALGORITHM = "HS256"
OTP_EXPIRY_MINUTES = 5
# Product decision: 4-digit OTP across the whole app (login + ride pickup).
# Trade-off: 1/10,000 guess odds per attempt vs 1/1,000,000 for 6 digits.
# Mitigated by rate limiting + short expiry (OTP_EXPIRY_MINUTES).
OTP_LENGTH = 4
PICKUP_OTP_LENGTH = 4

security = HTTPBearer(auto_error=False)


# Helper Functions
def generate_otp() -> str:
    """Generate a cryptographically secure numeric OTP.

    Uses `secrets.choice` (not `random.choices`) so the OTP can't be
    predicted from wall-clock time / PID state — which matters because
    a predictable OTP lets anyone take over an account they can SMS.
    """
    return "".join(secrets.choice(string.digits) for _ in range(OTP_LENGTH))


def generate_pickup_otp() -> str:
    """Generate a 4-digit OTP for ride pickup verification."""
    return "".join(secrets.choice(string.digits) for _ in range(PICKUP_OTP_LENGTH))


def hash_token(raw: str) -> str:
    """SHA-256 hash of a raw token — used to store refresh tokens safely."""
    return hashlib.sha256(raw.encode()).hexdigest()


def create_refresh_token() -> str:
    """Generate a cryptographically random opaque refresh token."""
    return secrets.token_urlsafe(32)


def create_jwt_token(
    user_id: str,
    phone: str,
    session_id: Optional[str] = None,
    *,
    token_version: int = 0,
) -> str:
    """Mint a rider/driver access token.

    ``token_version`` is written into the payload so the middleware can
    compare it against ``users.token_version`` and reject tokens issued
    before a force-logout-all. TTL comes from
    ``settings.ACCESS_TOKEN_EXPIRE_MINUTES`` (default 15m); admin tokens are
    minted in ``routes/admin/auth.py`` directly because they carry a different
    claim set (role, modules, email).
    """
    now = datetime.now(timezone.utc)
    # P0-S3: Short-lived access tokens (15 minutes).
    ttl = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict = {
        "user_id": user_id,
        "phone": phone,
        "iat": now,
        "exp": now + ttl,
        "token_version": int(token_version or 0),
    }
    if session_id:
        payload["session_id"] = session_id

    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token") from None


def _token_version_mismatch(payload: dict, user_row: dict) -> bool:
    """Return True if the access-token's token_version is stale.

    Tokens minted before this migration land do not carry a
    token_version claim; we treat a missing claim as 0. ``user_row`` is
    whatever came back from the users / admin_staff table — the check
    is symmetric: default 0 on both sides.
    """
    claim = int(payload.get("token_version") or 0)
    stored = int(user_row.get("token_version") or 0)
    return claim < stored


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Resolve the current user using Firebase ID token (preferred) or fallback to legacy JWT."""
    if not credentials:
        raise HTTPException(status_code=401, detail="No authorization token provided")
    token = credentials.credentials

    # First, try Firebase ID token
    try:
        try:
            payload = firebase_auth.verify_id_token(token)
        except ExpiredIdTokenError:
            raise HTTPException(status_code=401, detail="Firebase token has expired") from None
        except (InvalidIdTokenError, RevokedIdTokenError, UserDisabledError, CertificateFetchError) as e:
            logger.debug(f"Firebase token verification failed, falling through to JWT: {type(e).__name__}")
            payload = None
        except ValueError:
            # Token doesn't look like a Firebase token at all — fall through to JWT
            payload = None

        if payload:
            # R-P1-12: Enforce rider app audience — reject tokens minted for the
            # driver app (which would otherwise create/access rider accounts).
            rider_app_id = getattr(settings, "FIREBASE_RIDER_APP_ID", None)
            if rider_app_id and payload.get("aud") != rider_app_id:
                raise HTTPException(status_code=401, detail="ERR_TOKEN_AUDIENCE")

            uid = payload.get("uid") or payload.get("user_id")
            # Try to find user by Firebase UID
            user = await db_supabase.get_user_by_id(uid)
            if not user:
                # Fallback: try to match by phone number
                phone = payload.get("phone_number")
                if phone:
                    user = await db_supabase.get_user_by_phone(phone)
                # If still not found, create a new user record tied to Firebase UID
                if not user:
                    new_user = {
                        "id": uid,
                        "phone": phone or "",
                        "role": "rider",  # Always default — never trust token claims
                        "created_at": datetime.now(timezone.utc),
                        "profile_complete": False,
                    }
                    await db_supabase.create_user(new_user)
                    user = new_user

            # R-P1-13: Apply same revocation checks as the JWT path so that
            # /auth/logout-all also invalidates Firebase-authenticated sessions.
            if user:
                if _token_version_mismatch({}, user):
                    raise HTTPException(status_code=401, detail="ERR_SESSION_REVOKED")
                token_session = payload.get("session_id")
                db_session = user.get("current_session_id")
                if db_session and token_session and token_session != db_session:
                    raise HTTPException(status_code=401, detail="ERR_SESSION_EXPIRED")
                # Cached (30s) — get_current_user runs on every
                # authenticated request so this lookup used to dominate
                # the Supabase read load.
                driver = await db_supabase.get_driver_by_user_id_cached(user["id"])
                user["is_driver"] = True if driver else False
            return user
    except HTTPException:
        raise

    # Fallback: existing JWT behavior
    try:
        payload = verify_jwt_token(token)
    except Exception as e:
        # Never log the signing secret, even partially — it's a credential.
        logger.error(f"JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}") from e

    # Admin tokens are minted by routes/admin/auth.py and carry `role` +
    # `email` + `modules` claims that regular rider/driver tokens do not
    # have. admin-001 (super admin from env) has no DB row — trust JWT claims
    # directly. All other staff are validated against admin_staff to enforce
    # is_active and token_version revocation (fixes audit [03-2/03-3]).
    _admin_roles = {"admin", "super_admin", "operations", "support", "finance", "custom"}
    if payload.get("role") in _admin_roles and payload.get("email"):
        user_id = payload["user_id"]
        if user_id != "admin-001":
            staff_rows = await db_supabase.get_rows("admin_staff", {"id": user_id}, limit=1)
            staff = staff_rows[0] if staff_rows else None
            if not staff or not staff.get("is_active", True):
                raise HTTPException(status_code=401, detail="ERR_ACCOUNT_INACTIVE")
            if _token_version_mismatch(payload, staff):
                raise HTTPException(status_code=401, detail="ERR_SESSION_REVOKED")
        return {
            "id": user_id,
            "email": payload.get("email"),
            "phone": payload.get("phone", ""),
            "role": payload["role"],
            "modules": payload.get("modules", []),
            "token_version": int(payload.get("token_version") or 0),
            "profile_complete": True,
            "is_driver": False,
        }

    # Look up the user row. A transient Supabase failure here MUST surface
    # as a 503 so the client retries — not be silently swallowed, which
    # previously cascaded into the "create new user" path below and
    # produced phantom duplicates (see CLAUDE.md: "Never logger.warning
    # and continue on a DB/auth error").
    try:
        user = await db_supabase.get_user_by_id(payload["user_id"])
    except (DatabaseError, ServiceUnavailableException):
        # run_sync already retried the transient error — it's genuinely
        # unreachable. Let the DB error propagate to the global handler
        # which returns a clean 503.
        raise
    except Exception as e:
        logger.error(f"Unexpected error looking up user from DB: {e}", exc_info=True)
        raise DatabaseError(details={"original": str(e)}) from e

    if user:
        token_session = payload.get("session_id")
        # Fast-path Redis check: login writes session:{user_id} → session_id with
        # the access-token TTL. A mismatch here means the user logged in from
        # another device and this token is stale — reject immediately without
        # the Postgres read latency. Falls back to the DB comparison when the
        # key has expired or Redis is unavailable (redis_get returns None).
        if token_session:
            redis_session = await redis_get(f"session:{user['id']}")
            if redis_session is not None and redis_session != token_session:
                raise HTTPException(status_code=401, detail="ERR_SESSION_EXPIRED")
        # Enforce single-device login: check if the session_id matches the one in DB
        db_session = user.get("current_session_id")
        if db_session and token_session != db_session:
            raise HTTPException(status_code=401, detail="ERR_SESSION_EXPIRED")
        # Revocation gate — if the user's token_version has been bumped
        # (admin force-logout-all, password reset, suspected compromise)
        # every access token issued before the bump must be rejected.
        # Tokens pre-dating migration 25 carry no claim → treated as 0,
        # which matches the default DB value, so the upgrade is
        # backwards-compatible until someone calls /auth/logout-all.
        if _token_version_mismatch(payload, user):
            raise HTTPException(
                status_code=401,
                detail="ERR_SESSION_REVOKED",
            )
        # Role is always determined by the DB — never trust JWT role claims.
        # A forged JWT with "role": "super_admin" must not grant escalated access.

    if not user:
        # User not in DB yet — create with default rider role.
        # Never trust the JWT role claim for auto-created users.
        user = {
            "id": payload["user_id"],
            "phone": payload.get("phone", ""),
            "role": "rider",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_complete": False,
        }
        try:
            await db_supabase.create_user(user)
            logger.info(f"Created new user {user['id']} from JWT")
        except (DatabaseError, ServiceUnavailableException):
            # Same rule as the lookup above — surface DB outages as a
            # clean 503 rather than returning a user dict whose backing
            # row doesn't exist. That used to cause cascading 401/500s
            # on every subsequent authenticated call in the session.
            raise
        except Exception as e:
            logger.error(f"Unexpected error inserting user into DB: {e}", exc_info=True)
            raise DatabaseError(details={"original": str(e)}) from e
        user["is_driver"] = False
        return user

    try:
        # Cached driver-by-user lookup (30s). Same reason as the Firebase
        # path above — this is the JWT hot path for every API call.
        driver = await db_supabase.get_driver_by_user_id_cached(user["id"])
        user["is_driver"] = True if driver else False
    except (DatabaseError, ServiceUnavailableException):
        # Treat the drivers lookup the same as the users lookup — if the
        # DB is flaking, 503 so the client retries. Silently defaulting
        # is_driver=False caused drivers to see the rider UI mid-outage.
        raise
    except Exception as e:
        logger.error(f"Unexpected error looking up driver row: {e}", exc_info=True)
        raise DatabaseError(details={"original": str(e)}) from e
    return user


async def get_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    """Require the caller to be an authenticated admin."""
    role = current_user.get("role", "")
    if role not in ("admin", "super_admin", "operations", "support", "finance", "custom"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def require_module(module: str):
    """Return a FastAPI dependency that enforces module-level RBAC.

    Usage::

        @router.post("/wallet/credit")
        async def credit(admin: dict = Depends(require_module("earnings"))):
            ...

    Or at include_router time::

        admin_router.include_router(wallet_router, dependencies=[Depends(require_module("earnings"))])

    super_admin always passes regardless of the modules claim.
    """

    async def _check(current_user: dict = Depends(get_admin_user)) -> dict:
        if current_user.get("role") == "super_admin":
            return current_user
        modules: list = current_user.get("modules") or []
        if module not in modules:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied — module '{module}' not in your role permissions",
            )
        return current_user

    return _check


# Alias for backward compatibility
get_current_admin = get_admin_user
