import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

try:
    from .. import db_supabase
    from ..core.config import settings
    from ..dependencies import (
        OTP_EXPIRY_MINUTES,
        create_jwt_token,
        generate_otp,
        get_current_user,
    )
    from ..schemas import AuthResponse, OTPRecord, SendOTPRequest, UserProfile, VerifyOTPRequest
    from ..settings_loader import get_app_settings
    from ..sms_service import send_otp_sms
    from ..utils.crypto import hash_otp
    from ..utils.redis_client import redis_delete, redis_expire, redis_get, redis_incr, redis_set
    from ..utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )
    from ..validators import validate_phone
except ImportError:
    import db_supabase
    from core.config import settings
    from dependencies import (
        OTP_EXPIRY_MINUTES,
        create_jwt_token,
        generate_otp,
        get_current_user,
    )
    from schemas import AuthResponse, OTPRecord, SendOTPRequest, UserProfile, VerifyOTPRequest
    from settings_loader import get_app_settings
    from sms_service import send_otp_sms
    from utils.crypto import hash_otp
    from utils.redis_client import redis_delete, redis_expire, redis_get, redis_incr, redis_set
    from utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )
    from validators import validate_phone

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)
api_router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── OTP brute-force lockout (SEC-008) ───────────────────────────────────
# Redis key prefixes; falls back to in-process dict when REDIS_URL unset.
_FAIL_KEY = "otp_fail:{}"
_LOCK_KEY = "otp_lock:{}"


async def _check_otp_lockout(phone: str) -> None:
    """Raise 429 if phone is currently locked out. Raises 503 on Redis errors (fail closed).

    B-P1-8: Mirrors the response shape pinned by the slowapi 429 path
    (utils/rate_limiter.py::rate_limit_exceeded_handler) so mobile
    clients can use a single 429 parser regardless of which gate
    (slowapi window or OTP lockout) tripped. RateLimit-* headers per
    draft-ietf-httpapi-ratelimit-headers; Retry-After per RFC 9110.
    """
    try:
        locked = await redis_get(_LOCK_KEY.format(phone))
        if locked:
            retry_after = int(settings.OTP_LOCKOUT_DURATION_SECONDS)
            raise HTTPException(
                status_code=429,
                detail="ERR_OTP_LOCKED",
                headers={
                    "Retry-After": str(retry_after),
                    "RateLimit-Limit": str(int(settings.OTP_MAX_FAILURES)),
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": str(retry_after),
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Redis unavailable in OTP lockout check: {e}")
        raise HTTPException(status_code=503, detail="ERR_AUTH_UNAVAILABLE") from None


async def _record_otp_failure(phone: str) -> None:
    """Increment failure counter; trigger lockout at threshold. Best-effort."""
    fail_key = _FAIL_KEY.format(phone)
    try:
        count = await redis_incr(fail_key)
        if count == 1:
            await redis_expire(fail_key, settings.OTP_FAILURE_WINDOW_SECONDS)
        if count >= settings.OTP_MAX_FAILURES:
            await redis_set(
                _LOCK_KEY.format(phone),
                "1",
                settings.OTP_LOCKOUT_DURATION_SECONDS,
            )
            logger.warning(f"OTP_LOCKOUT_TRIGGERED phone=...{phone[-4:]} after {count} failures")
    except Exception as e:
        logger.error(f"_record_otp_failure: {e}", exc_info=True)


async def _clear_otp_failures(phone: str) -> None:
    """Reset counter + lockout on successful verify. Best-effort."""
    try:
        await redis_delete(_FAIL_KEY.format(phone))
        await redis_delete(_LOCK_KEY.format(phone))
    except Exception as e:
        logger.warning(f"_clear_otp_failures: {e}")


def _is_dev_otp_bypass(otp: str) -> bool:
    if settings.ENV.lower() != "development":
        return False
    return otp in ("1234", "123456")


# ── Helpers for Auth Responses ──────────────────────────────────────────
def _make_auth_response(
    token: str,
    refresh_token: str,
    user_obj: UserProfile,
    is_new_user: bool,
    access_expires_at: datetime,
    refresh_expires_at: datetime,
) -> AuthResponse:
    return AuthResponse(
        token=token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user_obj,
        is_new_user=is_new_user,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
    )


@api_router.post("/send-otp")
@limiter.limit("3/minute")
async def send_otp(request: Request, body: SendOTPRequest):
    phone = body.phone.strip()
    # Validate phone using E.164 format validator (raises HTTPException on failure)
    _, normalized = validate_phone(phone)
    phone = normalized or phone

    # Check if Twilio is configured via DB settings. Use a distinct name
    # so the module-level `settings` config object (with .ENV etc.) isn't
    # shadowed — that bug broke /send-otp with AttributeError on prod.
    app_settings = None
    try:
        app_settings = await get_app_settings()
    except Exception as e:
        logger.error(f"Could not read app_settings from DB: {e}", exc_info=True)

    twilio_configured = bool(
        app_settings
        and app_settings.get("twilio_account_sid")
        and app_settings.get("twilio_auth_token")
        and app_settings.get("twilio_from_number")
    )

    is_dev = settings.ENV.lower() in ("development", "test")

    if not twilio_configured and not is_dev:
        # In production, refuse to silently fall back to a known OTP.
        raise HTTPException(status_code=503, detail="SMS service not configured")

    # Dev fallback: fixed OTP so local testing doesn't need Twilio.
    # The 4-digit length matches the real generated OTP so OTP screens accept it.
    otp_code = generate_otp() if twilio_configured else "1234"

    otp_record = OTPRecord(
        phone=phone,
        code=hash_otp(otp_code),  # stored as SHA-256 hash (SEC-016)
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES),
    )

    try:
        await db_supabase.delete_many("otp_records", {"phone": phone})
        await db_supabase.insert_otp_record(otp_record.dict())
    except Exception as e:
        # B-P1-5: warn-and-continue here meant the SMS was sent but the
        # OTP record was never written — verify-otp would 400 every code
        # the user typed because the row to compare against didn't exist.
        # Surface as 503 so the client retries the send-otp call.
        logger.error(f"Could not store OTP in DB: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="otp_store_failed") from e

    # Send OTP via SMS (Twilio when configured, console log otherwise)
    sms_result = await send_otp_sms(
        phone,
        otp_code,
        twilio_sid=app_settings.get("twilio_account_sid", "") if app_settings else "",
        twilio_token=app_settings.get("twilio_auth_token", "") if app_settings else "",
        twilio_from=app_settings.get("twilio_from_number", "") if app_settings else "",
    )
    if not sms_result.get("success"):
        logger.error(f"Failed to send OTP SMS: {sms_result.get('error')}")
        raise HTTPException(status_code=500, detail="Failed to send verification code")

    response = {"success": True, "message": f"OTP sent to {phone}"}
    # Dev OTP is logged to server console via sms_service.py — never return it
    # in the API response to avoid accidental exposure in client-side logs.

    return response


@api_router.post("/verify-otp", response_model=AuthResponse)
@limiter.limit("5/minute")
async def verify_otp(request: Request, body: VerifyOTPRequest):
    phone = body.phone.strip()
    code = body.code.strip()

    # SEC-008: Reject locked-out phones before touching the DB
    await _check_otp_lockout(phone)
    otp_record = None
    try:
        # R-P1-14: Fetch by phone only; compare hashes in constant time with
        # hmac.compare_digest to prevent timing-based hash-prefix leakage.
        import hmac as _hmac

        otp_record = await db_supabase.get_otp_record_by_phone(phone)
        if otp_record:
            expected = otp_record.get("code", "")
            actual = hash_otp(code)
            if not _hmac.compare_digest(str(expected), str(actual)):
                otp_record = None
    except Exception as e:
        logger.error(f"Could not query OTP from DB: {e}", exc_info=True)

    if not otp_record and _is_dev_otp_bypass(code):
        logger.info("Dev mode: accepting bypass OTP")
        otp_record = {
            "id": "dev",
            "phone": phone,
            "code": hash_otp(code),
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
    if not otp_record:
        # Wrong code — record the failure (may trigger lockout)
        await _record_otp_failure(phone)
        raise HTTPException(status_code=400, detail="ERR_OTP_INVALID")
    # Parse expires_at to datetime if it's a string (from Supabase)
    expires_at = otp_record.get("expires_at")
    if isinstance(expires_at, str):
        try:
            # Handle ISO format from Supabase (replace Z with +00:00 if present)
            expires_at = expires_at.replace("Z", "+00:00")
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            logger.error(f"Invalid date format for OTP expires_at: {expires_at}")
            raise HTTPException(status_code=500, detail="ERR_INTERNAL") from None

    if not expires_at:
        logger.error("OTP record missing expires_at field")
        raise HTTPException(status_code=500, detail="ERR_INTERNAL")

    # Ensure expires_at is timezone-aware for comparison
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expires_at:
        try:
            await db_supabase.delete_otp_record(otp_record["id"])
        except Exception:  # noqa: S110
            pass
        raise HTTPException(status_code=400, detail="ERR_OTP_EXPIRED")

    try:
        await db_supabase.update_one("otp_records", {"id": otp_record["id"]}, {"verified": True})
    except Exception:  # noqa: S110
        pass

    # SEC-008: Clear failure counter + lockout on successful verification
    await _clear_otp_failures(phone)
    try:
        # Find or create user
        existing_user = None
        try:
            logger.info(f"Searching for user with phone: ...{phone[-4:]}")
            existing_user = await db_supabase.get_user_by_phone(phone)
            logger.info(f"User search result found: {bool(existing_user)}")
        except Exception as e:
            # Surface the real underlying Supabase error. DatabaseError
            # wraps the original exception in .details["original"]; str(e)
            # only gives the generic "Database operation failed" message.
            original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
            logger.error(
                f"get_user_by_phone failed for ...{phone[-4:]}: type={type(e).__name__} msg={e} original={original}",
                exc_info=True,
            )
            # Refuse to silently fall through to user creation — a DB read
            # failure is NOT the same as "user doesn't exist". Creating a
            # new row here generates duplicate accounts on every retry and
            # locks the real user out of their own profile, wallet, and
            # ride history. Fail the login so the client retries.
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable, please try again",
            ) from e

        user_agent = request.headers.get("user-agent", "")
        client_ip = get_remote_address(request)

        if existing_user:
            logger.info("User exists, creating token")
            session_id = str(uuid.uuid4())
            try:
                await db_supabase.update_one("users", {"id": existing_user["id"]}, {"current_session_id": session_id})
                existing_user["current_session_id"] = session_id
            except Exception as e:
                logger.error(f"Could not update session_id for existing user: {e}", exc_info=True)
            # Mirror session_id in Redis so revocation propagates instantly across
            # all replicas without waiting for a Postgres read on every request.
            await redis_set(
                f"session:{existing_user['id']}",
                session_id,
                ttl=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )
            user_id = existing_user["id"]
            token_version = int(existing_user.get("token_version") or 0)
            access_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            token = create_jwt_token(
                user_id,
                phone,
                session_id=session_id,
                token_version=token_version,
            )
            refresh_raw, _, refresh_expires_at = await issue_refresh_token(
                user_id, audience="rider", user_agent=user_agent, ip=client_ip
            )
            logger.info("Token created. Validating UserProfile...")
            try:
                user_obj = UserProfile(**existing_user)
                logger.info("UserProfile valid")
            except Exception as e:
                logger.error(f"UserProfile validation failed, falling back to raw dict: {e}", exc_info=True)
                user_obj = existing_user
            return _make_auth_response(
                token,
                refresh_raw,
                user_obj,
                is_new_user=False,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
            )
        else:
            logger.info("Creating new user")
            user_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            new_user = {
                "id": user_id,
                "phone": phone,
                "role": "rider",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "profile_complete": False,
                "current_session_id": session_id,
                "token_version": 0,
            }
            try:
                await db_supabase.create_user(new_user)
            except Exception as e:
                # B-P1-5: warn-and-continue produced a partial-state bug —
                # we'd hand back an access token whose user_id has no DB row,
                # then every authenticated call would 401 because
                # `get_current_user` couldn't find the user. Force a 503 so
                # the client retries verify-otp instead of pretending login
                # succeeded.
                logger.error(f"Could not persist new user to DB: {e}", exc_info=True)
                raise HTTPException(status_code=503, detail="user_create_failed") from e
            await redis_set(
                f"session:{user_id}",
                session_id,
                ttl=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )
            access_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            token = create_jwt_token(user_id, phone, session_id=session_id, token_version=0)
            refresh_raw, _, refresh_expires_at = await issue_refresh_token(
                user_id, audience="rider", user_agent=user_agent, ip=client_ip
            )
            return _make_auth_response(
                token,
                refresh_raw,
                UserProfile(**new_user),
                is_new_user=True,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
            )
    except HTTPException:
        # Already a well-formed HTTP error (e.g. the 503 raised when
        # get_user_by_phone fails) — let it propagate unchanged instead
        # of being re-wrapped as a generic 500.
        raise
    except Exception as e:
        # B-P3-leak-cleanup: NEVER interpolate {e} into the client-
        # facing detail. This path is reachable UNAUTHENTICATED, so a
        # leak here exposes Supabase row IDs, Firebase JWT errors, and
        # internal stack frames to anyone with internet access. The
        # framework sanitiser (utils/error_handling.py) already replaces
        # 5xx detail with "Internal server error", but the manual
        # str(e) made the leak's intent explicit; clean it up so the
        # next contributor doesn't copy the pattern. logger.exception
        # captures the full traceback server-side automatically.
        logger.exception("CRITICAL ERROR IN VERIFY_OTP")
        raise HTTPException(
            status_code=500,
            detail="Internal Login Error",
        ) from e


class FirebaseAuthRequest(BaseModel):
    firebase_token: str


@api_router.post("/firebase", response_model=AuthResponse)
@limiter.limit("10/minute")
async def firebase_auth_login(request: Request, body: FirebaseAuthRequest):
    """Exchange a Firebase ID token for Spinr access + refresh tokens.

    Mirrors the OTP verify flow: verify identity, find-or-create the user
    record, create a session, and issue both a short-lived JWT and a
    long-lived opaque refresh token.
    """
    try:
        from firebase_admin import auth as _firebase_auth  # type: ignore

        payload = _firebase_auth.verify_id_token(body.firebase_token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid Firebase token") from e

    # B-P1-1 / DV-10: enforce audience binding unconditionally. Production fails
    # fast in core/config._guard_production_secrets when FIREBASE_DRIVER_APP_ID
    # is unset, so this branch is only reachable in dev/test.
    driver_app_id = settings.FIREBASE_DRIVER_APP_ID
    if not driver_app_id:
        raise HTTPException(status_code=503, detail="Driver Firebase audience not configured")
    if payload.get("aud") != driver_app_id:
        raise HTTPException(status_code=401, detail="Token not issued for driver app")

    uid: str = payload.get("uid") or payload.get("user_id") or ""
    phone: str = payload.get("phone_number") or ""

    user_agent = request.headers.get("user-agent", "")
    client_ip = get_remote_address(request)

    try:
        user = await db_supabase.get_user_by_id(uid)
        if not user and phone:
            user = await db_supabase.get_user_by_phone(phone)
    except Exception as e:
        logger.error(f"firebase_auth: user lookup failed uid={uid}: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="auth_lookup_failed") from e

    is_new_user = False
    session_id = str(uuid.uuid4())
    if not user:
        is_new_user = True
        new_user: Dict[str, Any] = {
            "id": uid,
            "phone": phone,
            "role": "rider",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_complete": False,
            "current_session_id": session_id,
            "token_version": 0,
        }
        try:
            await db_supabase.create_user(new_user)
        except Exception as e:
            # B-P1-5 / CLAUDE.md: never warn-and-continue on auth-DB failure.
            # If we can't persist the user, the access token we are about to
            # mint would point at no row — every subsequent request would
            # 401 against `get_current_user` and the client would loop. 503
            # so the rider client retries the Firebase exchange instead.
            logger.error(
                f"firebase_auth: could not persist user {uid}: {e}",
                exc_info=True,
            )
            raise HTTPException(status_code=503, detail="auth_persist_failed") from e
        user = new_user
    else:
        try:
            await db_supabase.update_one("users", {"id": uid}, {"current_session_id": session_id})
        except Exception as e:
            # B-P1-5: same partial-state class. Without a persisted
            # current_session_id, single-device login can't enforce
            # ERR_SESSION_EXPIRED on the prior device, and the new token
            # we're about to mint references a session_id that isn't
            # recorded server-side.
            logger.error(
                f"firebase_auth: could not update session_id for {uid}: {e}",
                exc_info=True,
            )
            raise HTTPException(status_code=503, detail="auth_session_update_failed") from e
        user["current_session_id"] = session_id

    user_id = user["id"]
    token_version = int(user.get("token_version") or 0)
    access_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_jwt_token(user_id, phone, session_id=session_id, token_version=token_version)
    refresh_raw, _, refresh_expires_at = await issue_refresh_token(
        user_id, audience="rider", user_agent=user_agent, ip=client_ip
    )

    try:
        user_obj = UserProfile(**user)
    except Exception:
        user_obj = user  # type: ignore[assignment]

    return _make_auth_response(
        token,
        refresh_raw,
        user_obj,
        is_new_user=is_new_user,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
    )


@api_router.get("/me", response_model=UserProfile)
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Return the current user plus the derived driver onboarding state.

    `profile_complete` is derived from the row data — if first_name/last_name/
    email are populated we treat the profile as complete, regardless of the
    stored flag. This protects against:
        - silent write failures where the column never flipped to true
      - expired driver documents (which are unrelated to profile completion)
      - legacy rows migrated without the flag set

    `driver_onboarding_status` is the full state machine (profile_incomplete,
    vehicle_required, documents_required, documents_rejected, documents_expired,
    pending_review, verified, suspended). Clients should route on this rather
    than the legacy boolean.
    """
    has_profile_data = bool(
        (current_user.get("first_name") or "").strip()
        and (current_user.get("last_name") or "").strip()
        and (current_user.get("email") or "").strip()
    )
    if has_profile_data and not current_user.get("profile_complete"):
        # Self-heal the column so the next login is fast and consistent.
        try:
            await db_supabase.update_one("users", {"id": current_user["id"]}, {"profile_complete": True})
        except Exception as e:
            # B-P1-5 / CLAUDE.md: this is a DB write failure, not a
            # recoverable anomaly. Mutating `current_user` in memory
            # below masks the persistence failure for the next login.
            logger.error(
                f"Could not self-heal profile_complete for {current_user.get('id')}: {e}",
                exc_info=True,
            )
        current_user["profile_complete"] = True

    # Derive driver onboarding status (None for non-drivers).
    try:
        from onboarding_status import derive_driver_onboarding_status  # type: ignore
    except ImportError:
        from ..onboarding_status import derive_driver_onboarding_status  # type: ignore
    try:
        status, detail, next_screen = await derive_driver_onboarding_status(current_user)
        current_user["driver_onboarding_status"] = status
        current_user["driver_onboarding_detail"] = detail
        current_user["driver_onboarding_next_screen"] = next_screen
    except Exception:
        logger.error("Could not derive onboarding status", exc_info=True)

    return UserProfile(**current_user)


# ── Refresh / logout (audit P0-S3) ──────────────────────────────────
# Access tokens carry a short TTL + token_version gate; long-term "keep
# me logged in" is provided by opaque refresh tokens stored (as sha256
# hashes) in refresh_tokens. Clients POST the refresh token here to
# get a fresh access token; every successful call rotates the refresh
# token so a stolen one is invalidated the moment the legitimate
# client refreshes.


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class LogoutRequest(BaseModel):
    # Optional — /auth/logout-all doesn't need the token, just auth.
    refresh_token: str | None = None


@api_router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("20/minute")
async def refresh_access_token(request: Request, body: RefreshRequest):
    """Exchange a refresh token for a new access token + rotated refresh token.

    Returns 401 on any lookup failure (revoked / expired / unknown) —
    the client's reaction to all three is the same (re-login), and
    distinguishing them would leak an oracle.
    """
    row = await lookup_refresh_token(body.refresh_token)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if row.get("audience") != "rider":
        # Admin refresh tokens go through /admin/auth/refresh; rider tokens
        # minted for admin use would be a privilege-escalation vector.
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = row.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = None
    try:
        user = await db.find_one("users", {"id": user_id})
    except Exception as e:
        logger.error(f"refresh: user lookup failed for {user_id}: {e}", exc_info=True)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_agent = request.headers.get("user-agent", "")
    client_ip = get_remote_address(request)

    # Rotate: issue a new refresh token and mark the old row as
    # replaced. If the user later presents the old token it'll be
    # revoked_at != null and the lookup returns None.
    new_raw, _, refresh_expires_at = await issue_refresh_token(
        user_id,
        audience="rider",
        user_agent=user_agent,
        ip=client_ip,
        replaces=row.get("id"),
    )

    session_id = user.get("current_session_id") or row.get("user_agent") or ""
    token_version = int(user.get("token_version") or 0)
    access_expires_at = datetime.now(timezone.utc) + timedelta(days=settings.ACCESS_TOKEN_TTL_DAYS)
    token = create_jwt_token(
        user_id,
        user.get("phone", ""),
        session_id=session_id if session_id else None,
        token_version=token_version,
    )

    return RefreshResponse(
        token=token,
        refresh_token=new_raw,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
    )


@api_router.post("/logout")
@limiter.limit("3/minute")
async def logout(request: Request, body: LogoutRequest, current_user: dict = Depends(get_current_user)):
    """Revoke the presented refresh token.

    Previously a no-op (the endpoint didn't exist). Now stamps
    revoked_at on the row so the refresh token can never be exchanged
    again. The current access token keeps working until its exp; for
    immediate kill use /auth/logout-all.
    """
    if body.refresh_token:
        await revoke_refresh_token(body.refresh_token)
    # Delete the Redis session key so the revocation propagates instantly
    # to all replicas rather than waiting for the access-token TTL.
    await redis_delete(f"session:{current_user['id']}")
    return {"success": True}


@api_router.post("/logout-all")
@limiter.limit("5/minute")
async def logout_all(request: Request, current_user: dict = Depends(get_current_user)):
    """Force-invalidate every session for the caller.

    Bumps ``users.token_version`` so all outstanding access tokens are
    rejected on their next request (the middleware re-reads the row on
    every call), and revokes every non-revoked refresh token for the
    user. This is what "sign out of all devices" / "my account was
    compromised" buttons should call.
    """
    user_id = current_user["id"]
    new_version = int(current_user.get("token_version") or 0) + 1
    try:
        await db.update_one("users", {"id": user_id}, {"$set": {"token_version": new_version}})
    except Exception as e:
        logger.error(f"logout-all: could not bump token_version for {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not invalidate sessions") from e

    revoked = await revoke_all_for_user(user_id)

    # B-P1-11: kick any live WebSocket sockets so the user is logged
    # out instantly rather than waiting up to 30s for the heartbeat
    # to re-validate token_version. Best-effort — the heartbeat is
    # the safety net, so a kick failure here must not fail the
    # logout-all response. The heartbeat re-read still closes the
    # socket on its next tick.
    try:
        try:
            from ..socket_manager import manager as ws_manager
        except ImportError:  # pragma: no cover — package-relative fallback
            from socket_manager import manager as ws_manager
        await ws_manager.kick_user(
            user_id,
            client_types=["rider", "driver"],
            reason="logout_all",
        )
    except Exception as e:
        # B-P1-5 / CLAUDE.md: WS kick is the only signal that propagates
        # logout to other devices in real time. The token-version bump and
        # refresh-token revocation above will eventually catch the next
        # API request, but the gap (up to 15 min for the access TTL) is
        # exactly the window an attacker exploits. exc_info captures
        # whether this was Redis pub/sub vs in-process registry vs socket
        # send so on-call can target the fix.
        logger.error(
            f"logout-all: WS kick failed for {user_id}: {e}",
            exc_info=True,
        )

    logger.info(f"logout-all: user={user_id} token_version→{new_version} revoked_refresh={revoked}")
    return {"success": True, "revoked_refresh_tokens": revoked}
