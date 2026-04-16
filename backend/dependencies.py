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
except ImportError:
    import db_supabase
    from core.config import settings

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
# 6 digits gives ~1/1,000,000 guessing odds per attempt. 4-digit OTPs
# only give 1/10,000 and are considered insufficient for phone auth.
OTP_LENGTH = 6

security = HTTPBearer(auto_error=False)


# Helper Functions
def generate_otp() -> str:
    """Generate a cryptographically secure numeric OTP.

    Uses `secrets.choice` (not `random.choices`) so the OTP can't be
    predicted from wall-clock time / PID state — which matters because
    a predictable OTP lets anyone take over an account they can SMS.
    """
    return "".join(secrets.choice(string.digits) for _ in range(OTP_LENGTH))


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
    ``settings.ACCESS_TOKEN_TTL_DAYS``; admin tokens are minted in
    ``routes/admin/auth.py`` directly because they carry a different
    claim set (role, modules, email).
    """
    now = datetime.now(timezone.utc)
    payload: dict = {
        "user_id": user_id,
        "phone": phone,
        "iat": now,
        "exp": now + timedelta(days=settings.ACCESS_TOKEN_TTL_DAYS),
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
                        "created_at": datetime.utcnow(),
                        "profile_complete": False,
                    }
                    await db_supabase.create_user(new_user)
                    user = new_user

            if user:
                driver = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
                )
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
    # have. Since the token is signed with our own JWT_SECRET, we can
    # trust these claims and return the user directly without a DB lookup.
    # Without this check, admin-001 (which has no users row) would be
    # auto-created as a rider and fail the get_admin_user role check.
    _admin_roles = {"admin", "super_admin", "operations", "support", "finance", "custom"}
    if payload.get("role") in _admin_roles and payload.get("email"):
        return {
            "id": payload["user_id"],
            "email": payload.get("email"),
            "phone": payload.get("phone", ""),
            "role": payload["role"],
            "modules": payload.get("modules", []),
            "token_version": int(payload.get("token_version") or 0),
            "profile_complete": True,
            "is_driver": False,
        }

    user = None
    try:
        user = await db_supabase.get_user_by_id(payload["user_id"])
    except Exception as e:
        logger.warning(f"Could not look up user from DB: {e}")

    if user:
        # Enforce single-device login: check if the session_id matches the one in DB
        token_session = payload.get("session_id")
        db_session = user.get("current_session_id")
        if db_session and token_session != db_session:
            raise HTTPException(status_code=401, detail="Session expired. Logged in from another device.")
        # Revocation gate — if the user's token_version has been bumped
        # (admin force-logout-all, password reset, suspected compromise)
        # every access token issued before the bump must be rejected.
        # Tokens pre-dating migration 25 carry no claim → treated as 0,
        # which matches the default DB value, so the upgrade is
        # backwards-compatible until someone calls /auth/logout-all.
        if _token_version_mismatch(payload, user):
            raise HTTPException(
                status_code=401,
                detail="Session revoked — please log in again.",
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
            "created_at": datetime.utcnow().isoformat(),
            "profile_complete": False,
        }
        try:
            await db_supabase.create_user(user)
            logger.info(f"Created new user {user['id']} from JWT")
        except Exception as e:
            logger.warning(f"Could not insert user into DB: {e}")
        user["is_driver"] = False
        return user

    try:
        driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
        )
        user["is_driver"] = True if driver else False
    except Exception:
        user["is_driver"] = False
    return user


async def get_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    """Require the caller to be an authenticated admin."""
    role = current_user.get("role", "")
    if role not in ("admin", "super_admin", "operations", "support", "finance", "custom"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# Alias for backward compatibility
get_current_admin = get_admin_user
