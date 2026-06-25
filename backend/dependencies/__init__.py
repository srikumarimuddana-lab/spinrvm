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
# Audience constants — present in every token we mint so cross-environment
# token reuse is rejected at decode time (rider token can't hit admin endpoint
# and vice-versa). Missing aud is tolerated during the 15-min rollout window
# when old tokens (no aud) are still in circulation; wrong aud is always rejected.
JWT_AUD_MOBILE = "spinr:rider"
JWT_AUD_ADMIN = "spinr:admin"
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
        "aud": JWT_AUD_MOBILE,
        "iat": now,
        "exp": now + ttl,
        "token_version": int(token_version or 0),
    }
    if session_id:
        payload["session_id"] = session_id

    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_aud": False})
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


def _to_epoch(value) -> "int | None":
    """Best-effort conversion of an ISO-8601 string / datetime / epoch number to
    integer Unix seconds. Returns None when the value is empty or unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        return int(value.timestamp())
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def _firebase_session_revoked(payload: dict, invalid_before) -> bool:
    """Return True if a Firebase ID token has been revoked by /auth/logout-all.

    Firebase ID tokens do not carry our token_version claim, so revocation is
    enforced by comparing the token's ``auth_time`` (the moment the user signed
    in — unchanged when the ID token is refreshed) against the user row's
    ``sessions_invalid_before`` watermark. A token from a sign-in before the
    watermark is rejected; a fresh sign-in (auth_time > watermark) is accepted.
    Fail closed: if the watermark is set but the token's age cannot be
    determined, treat the token as revoked.
    """
    watermark = _to_epoch(invalid_before)
    if watermark is None:
        return False
    issued = payload.get("auth_time") or payload.get("iat")
    if issued is None:
        return True
    try:
        # `<=`, not `<`: the watermark is truncated to whole seconds by
        # _to_epoch while Firebase auth_time is already whole seconds, so a
        # token signed in during the same second as logout-all must be
        # rejected. Worst case this forces one extra re-sign-in for a login
        # that lands in the exact logout-all second — safe over-rejection.
        return int(issued) <= watermark
    except (TypeError, ValueError):
        return True


async def _verify_admin_payload(payload: dict) -> "dict | None":
    """Full admin verification: aud, JTI revocation, staff active, token_version, idle timeout.

    Returns the admin user dict on success. Returns None when the payload is not an admin
    token. Raises HTTPException when the token looks admin but fails a security check.
    Shared by the HTTP path (get_current_user) and the WebSocket auth path so the two
    can never diverge.
    """
    _admin_roles = {"admin", "super_admin", "operations", "support", "finance", "custom"}
    _token_aud = payload.get("aud")
    # Admin token: aud MUST equal JWT_AUD_ADMIN. The former legacy branch that
    # accepted a no-aud token with role+email claims let a crafted admin-001
    # token through with zero DB verification — every admin token minted since
    # the aud rollout carries the claim, so the grace path is retired.
    _is_admin_payload = _token_aud == JWT_AUD_ADMIN
    if _token_aud is None and payload.get("role") in _admin_roles and bool(payload.get("email")):
        raise HTTPException(status_code=401, detail="ERR_TOKEN_AUDIENCE")
    _expected_aud = JWT_AUD_ADMIN if _is_admin_payload else JWT_AUD_MOBILE
    if _token_aud is not None and _token_aud != _expected_aud:
        raise HTTPException(status_code=401, detail="ERR_TOKEN_AUDIENCE")
    if not (_is_admin_payload and payload.get("role") in _admin_roles and payload.get("email")):
        return None
    user_id = payload["user_id"]
    jti = payload.get("jti")
    if jti:
        # Per-JTI revocation denylist is a Redis FAST-PATH for single-session
        # logout. When Redis (Upstash) is unreachable we fail OPEN on this one
        # check rather than locking every admin out of the live dashboard on a
        # cache blip — the industry-standard "degrade auth on cache-dependency
        # failure, keep the authoritative control" pattern (Uber/Lyft/Netflix).
        # It's safe because the AUTHORITATIVE revocation for staff —
        # /auth/logout-all — bumps admin_staff.token_version, verified below
        # against the DB (no Redis). Every cryptographic / audience / expiry /
        # account-active check also still runs. Worst case during an outage: a
        # single explicitly-revoked token stays usable until it expires. Logged
        # loudly so the degraded decision is auditable.
        try:
            _jti_revoked = await redis_get(f"admin:revoked:{jti}")
        except Exception as _revoke_err:
            logger.error(
                "[auth] admin revocation denylist unreachable (Redis down) — "
                f"failing OPEN for jti={jti}; DB token_version still enforced: {_revoke_err}"
            )
            _jti_revoked = None
        if _jti_revoked:
            raise HTTPException(status_code=401, detail="ERR_TOKEN_REVOKED")
    if user_id != "admin-001":
        staff_rows = await db_supabase.get_rows("admin_staff", {"id": user_id}, limit=1)
        staff = staff_rows[0] if staff_rows else None
        if not staff or not staff.get("is_active", True):
            raise HTTPException(status_code=401, detail="ERR_ACCOUNT_INACTIVE")
        if _token_version_mismatch(payload, staff):
            raise HTTPException(status_code=401, detail="ERR_SESSION_REVOKED")
        _IDLE_SECONDS = 30 * 60
        last_active_raw = staff.get("last_activity_at")
        if last_active_raw:
            try:
                last_active = datetime.fromisoformat(last_active_raw.replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - last_active).total_seconds() > _IDLE_SECONDS:
                    raise HTTPException(status_code=401, detail="ERR_IDLE_TIMEOUT")
            except HTTPException:
                raise
            except Exception as _ts_err:
                logger.warning(f"Malformed last_activity_at for staff {user_id} — letting through: {_ts_err}")
        try:
            await db_supabase.update_one(
                "admin_staff", {"id": user_id}, {"last_activity_at": datetime.now(timezone.utc).isoformat()}
            )
        except Exception as _upd_err:
            logger.warning(f"Could not update last_activity_at for staff {user_id}: {_upd_err}")
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
            # R-P1-12 / B-P1-1 / DV-10: enforce rider app audience unconditionally.
            # Production fails fast in core/config._guard_production_secrets when
            # FIREBASE_RIDER_APP_ID is unset, so the empty-string branch below is
            # only reachable in dev/test.
            rider_app_id = getattr(settings, "FIREBASE_RIDER_APP_ID", None) or ""
            if not rider_app_id:
                raise HTTPException(status_code=503, detail="Rider Firebase audience not configured")
            if payload.get("aud") != rider_app_id:
                raise HTTPException(status_code=401, detail="ERR_TOKEN_AUDIENCE")

            uid = payload.get("uid") or payload.get("user_id")
            # Try to find user by Firebase UID
            user = await db_supabase.get_user_by_id(uid)
            if not user:
                # Fallback: try to match by phone number
                phone = payload.get("phone_number")
                if phone:
                    user = await db_supabase.get_user_by_phone(phone)
                # Do NOT auto-create here (C2). A valid Firebase token whose
                # user row is missing is almost always a transient Supabase
                # replica miss — get_user_by_id / get_user_by_phone return None
                # rather than raising. Forging a row forks a phantom account
                # with a fresh identity and silently masks the outage. New
                # Firebase users are created only by the /auth/firebase endpoint.
                # Fail closed with 503 so the client retries. (CLAUDE.md: never
                # fall through to "create new user" on a None lookup.)
                if not user:
                    logger.error(
                        "get_current_user(firebase): no user row for uid=%s — "
                        "refusing to auto-create (likely transient DB miss)",
                        uid,
                    )
                    raise ServiceUnavailableException("user lookup")

            # R-P1-13: Apply the same revocation intent as the JWT path so that
            # /auth/logout-all also invalidates Firebase-authenticated sessions.
            # Firebase ID tokens carry no token_version claim, so revocation is
            # enforced via the sessions_invalid_before watermark compared against
            # the token's auth_time. (The former _token_version_mismatch({}, user)
            # approach was broken both ways: it never revoked version-0 users and
            # permanently locked out anyone who had ever bumped token_version,
            # since the claim was hard-coded to 0.)
            if user:
                if _firebase_session_revoked(payload, user.get("sessions_invalid_before")):
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
        # Static client message (C4): interpolating the PyJWT reason lets an
        # attacker fingerprint which claim failed (alg/aud/exp/sig). The real
        # cause is in the server log above; the client only learns the token is
        # invalid — matching every other auth path.
        raise HTTPException(status_code=401, detail="Invalid token") from e

    # Full admin verification (aud, JTI revocation, staff active/version/idle) is
    # delegated to _verify_admin_payload — the WS path calls the same function so
    # the checks can never diverge.
    admin_user = await _verify_admin_payload(payload)
    if admin_user is not None:
        return admin_user

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
        # Do NOT auto-create here (C2). A valid JWT means this user was already
        # created at /auth/verify-otp or /auth/firebase. A missing row now is
        # NOT "new user" — it's a transient Supabase replica miss
        # (get_user_by_id returns None rather than raising). Auto-creating forks
        # a phantom account with a fresh identity and silently masks the outage,
        # which previously produced duplicate accounts. Fail closed with 503 so
        # the client retries; user creation belongs only in the auth endpoints.
        # (CLAUDE.md: never fall through to "create new user" on a None lookup.)
        logger.error(
            "get_current_user(jwt): valid JWT but no user row for %s — "
            "refusing to auto-create (likely transient DB miss)",
            payload["user_id"],
        )
        raise ServiceUnavailableException("user lookup")

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


# Safety-critical grace window: an SOS tap mid-trip must not bounce off a
# 401 because the 15-minute access token lapsed before the client's
# reactive refresh ran. 24h comfortably covers any plausible trip length
# while still bounding how long a stale token stays usable on this path.
SOS_EXPIRED_TOKEN_GRACE_SECONDS = 24 * 3600


async def get_current_user_allow_expired(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Resolve the current user for safety-critical endpoints (SOS only).

    Identical to ``get_current_user`` except that a Spinr *mobile* access
    token whose ONLY defect is expiry — signature still valid, expired less
    than ``SOS_EXPIRED_TOKEN_GRACE_SECONDS`` ago — is accepted, per the
    safety rule that SOS is never gated behind an auth refresh.

    No grace is granted to: forged/garbled tokens, admin-audience tokens,
    tokens revoked via ``users.token_version`` (force-logout-all means
    suspected compromise), Firebase ID tokens (their client SDK refreshes
    transparently), or 401s whose cause is anything other than expiry
    (session mismatch, revocation). Endpoint-level ownership checks —
    caller must be the ride's rider or driver — still apply unchanged.
    """
    try:
        return await get_current_user(credentials)
    except HTTPException as exc:
        if exc.status_code != 401 or not credentials:
            raise
        original = exc

    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"verify_aud": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError:
        raise original from None

    if payload.get("aud") not in (None, JWT_AUD_MOBILE):
        raise original
    exp = payload.get("exp")
    now_ts = datetime.now(timezone.utc).timestamp()
    if not exp or float(exp) > now_ts:
        # Token isn't actually expired — the 401 came from a session or
        # revocation check, which the grace path must not override.
        raise original
    if now_ts - float(exp) > SOS_EXPIRED_TOKEN_GRACE_SECONDS:
        raise original
    user_id = payload.get("user_id")
    if not user_id:
        raise original

    # DB errors propagate as 503 (client retries) per error conventions.
    user = await db_supabase.get_user_by_id(user_id)
    if not user:
        raise original
    if _token_version_mismatch(payload, user):
        raise original
    # Single-device login still applies on the grace path. An expired token
    # from a superseded session (the user logged in elsewhere, rotating
    # current_session_id) must not trigger SOS for the account — mirror the
    # Redis fast-path + DB comparison get_current_user does, INCLUDING the
    # sessionless case: a legacy token with no session_id claim is rejected
    # whenever the account has a current session, exactly as in
    # get_current_user (`db_session and token_session != db_session`). A
    # stolen old handset must not fire emergency alerts after the owner has
    # re-logged-in on a new device.
    token_session = payload.get("session_id")
    if token_session:
        redis_session = await redis_get(f"session:{user['id']}")
        if redis_session is not None and redis_session != token_session:
            raise original
    db_session = user.get("current_session_id")
    if db_session and token_session != db_session:
        raise original
    driver = await db_supabase.get_driver_by_user_id_cached(user["id"])
    user["is_driver"] = True if driver else False
    logger.warning(
        f"[auth] expired-token grace used on safety endpoint by user {user_id} "
        f"(token expired {int(now_ts - float(exp))}s ago)"
    )
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
