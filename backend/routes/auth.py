import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

try:
    from .. import db_supabase
    from ..core.config import settings
    from ..core.config import settings as _core_settings
    from ..dependencies import (
        OTP_EXPIRY_MINUTES,
        create_jwt_token,
        generate_otp,
        get_current_user,
    )
    from ..schemas import AuthResponse, OTPRecord, SendOTPRequest, UserProfile, VerifyOTPRequest
    from ..settings_loader import get_app_settings
    from ..sms_service import send_otp_sms
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
    from core.config import settings as _core_settings
    from dependencies import (
        OTP_EXPIRY_MINUTES,
        create_jwt_token,
        generate_otp,
        get_current_user,
    )
    from schemas import AuthResponse, OTPRecord, SendOTPRequest, UserProfile, VerifyOTPRequest
    from settings_loader import get_app_settings
    from sms_service import send_otp_sms
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


@api_router.post("/send-otp")
@limiter.limit("5/minute")
async def send_otp(request: Request, body: SendOTPRequest):
    phone = body.phone.strip()
    # Validate phone using E.164 format validator (raises HTTPException on failure)
    _, normalized = validate_phone(phone)
    phone = normalized or phone

    # Check if Twilio is configured via DB settings
    settings = None
    try:
        settings = await get_app_settings()
    except Exception as e:
        logger.warning(f"Could not read app_settings from DB: {e}")

    twilio_configured = bool(
        settings
        and settings.get("twilio_account_sid")
        and settings.get("twilio_auth_token")
        and settings.get("twilio_from_number")
    )

    is_dev = _core_settings.ENV.lower() in ("development", "test")

    if not twilio_configured and not is_dev:
        # In production, refuse to silently fall back to a known OTP.
        raise HTTPException(status_code=503, detail="SMS service not configured")

    # Dev fallback: fixed OTP so local testing doesn't need Twilio.
    # The 6-digit length matches the real generated OTP so OTP screens accept it.
    otp_code = generate_otp() if twilio_configured else "123456"

    otp_record = OTPRecord(
        phone=phone, code=otp_code, expires_at=datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)
    )

    try:
        await db_supabase.delete_many("otp_records", {"phone": phone})
        await db_supabase.insert_otp_record(otp_record.dict())
    except Exception as e:
        logger.warning(f"Could not store OTP in DB: {e}")

    # Send OTP via SMS (Twilio when configured, console log otherwise)
    sms_result = await send_otp_sms(
        phone,
        otp_code,
        twilio_sid=settings.get("twilio_account_sid", "") if settings else "",
        twilio_token=settings.get("twilio_auth_token", "") if settings else "",
        twilio_from=settings.get("twilio_from_number", "") if settings else "",
    )
    if not sms_result.get("success"):
        logger.error(f"Failed to send OTP SMS: {sms_result.get('error')}")
        raise HTTPException(status_code=500, detail="Failed to send verification code")

    response = {"success": True, "message": f"OTP sent to {phone}"}
    # Dev OTP is logged to server console via sms_service.py — never return it
    # in the API response to avoid accidental exposure in client-side logs.

    return response


@api_router.post("/verify-otp", response_model=AuthResponse)
@limiter.limit("10/minute")
async def verify_otp(request: Request, body: VerifyOTPRequest):
    phone = body.phone.strip()
    code = body.code.strip()

    otp_record = None
    try:
        otp_record = await db_supabase.get_otp_record(phone, code)
    except Exception as e:
        logger.warning(f"Could not query OTP from DB: {e}")

    # Dev fallback: accept code 123456 when no OTP record found (Twilio not configured)
    # DISABLED IN PRODUCTION: only allow in development environment
    if not otp_record and code == "123456" and settings.ENV.lower() == "development":
        logger.info("Dev mode: accepting code 123456")
        otp_record = {"id": "dev", "phone": phone, "code": code, "expires_at": datetime.utcnow() + timedelta(minutes=5)}

    if not otp_record:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    # Parse expires_at to datetime if it's a string (from Supabase)
    expires_at = otp_record.get("expires_at")
    if isinstance(expires_at, str):
        try:
            # Handle ISO format from Supabase (replace Z with +00:00 if present)
            expires_at = expires_at.replace("Z", "+00:00")
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            logger.error(f"Invalid date format for OTP expires_at: {expires_at}")
            raise HTTPException(status_code=500, detail="Internal data error: invalid expiration date") from None

    if not expires_at:
        logger.error("OTP record missing expires_at field")
        raise HTTPException(status_code=500, detail="Internal data error: missing expiration date")

    # Ensure expires_at is timezone-aware for comparison
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expires_at:
        try:
            await db_supabase.delete_otp_record(otp_record["id"])
        except Exception:  # noqa: S110
            pass
        raise HTTPException(status_code=400, detail="OTP has expired")

    try:
        await db_supabase.update_one("otp_records", {"id": otp_record["id"]}, {"verified": True})
    except Exception:  # noqa: S110
        pass

    try:
        # Find or create user
        existing_user = None
        try:
            logger.info(f"Searching for user with phone: {phone}")
            existing_user = await db_supabase.get_user_by_phone(phone)
            logger.info(f"User search result found: {bool(existing_user)}")
        except Exception as e:
            logger.warning(f"Could not query user from DB: {e}")

        user_agent = request.headers.get("user-agent", "")
        client_ip = get_remote_address(request)

        if existing_user:
            logger.info("User exists, creating token")
            session_id = str(uuid.uuid4())
            try:
                await db_supabase.update_one("users", {"id": existing_user["id"]}, {"current_session_id": session_id})
                existing_user["current_session_id"] = session_id
            except Exception as e:
                logger.warning(f"Could not update current_session_id in DB: {e}")

            user_id = existing_user["id"]
            token_version = int(existing_user.get("token_version") or 0)
            access_expires_at = datetime.now(timezone.utc) + timedelta(days=_core_settings.ACCESS_TOKEN_TTL_DAYS)
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
                logger.error("UserProfile validation failed")
                # Fallback constructs if validation fails to inspect why
                raise e

            return AuthResponse(
                token=token,
                user=user_obj,
                is_new_user=False,
                refresh_token=refresh_raw,
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
                "created_at": datetime.utcnow().isoformat(),
                "profile_complete": False,
                "current_session_id": session_id,
                "token_version": 0,
            }
            try:
                await db_supabase.create_user(new_user)
            except Exception as e:
                logger.warning(f"Could not create user in DB: {e}")
            access_expires_at = datetime.now(timezone.utc) + timedelta(days=_core_settings.ACCESS_TOKEN_TTL_DAYS)
            token = create_jwt_token(user_id, phone, session_id=session_id, token_version=0)
            refresh_raw, _, refresh_expires_at = await issue_refresh_token(
                user_id, audience="rider", user_agent=user_agent, ip=client_ip
            )
            return AuthResponse(
                token=token,
                user=UserProfile(**new_user),
                is_new_user=True,
                refresh_token=refresh_raw,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
            )
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN VERIFY_OTP: {e}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Login Error: {str(e)}") from e


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
        except Exception:
            logger.warning("Could not self-heal profile_complete")
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
        logger.warning("Could not derive onboarding status")

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
        logger.warning(f"refresh: user lookup failed for {user_id}: {e}")
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
    access_expires_at = datetime.now(timezone.utc) + timedelta(days=_core_settings.ACCESS_TOKEN_TTL_DAYS)
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
@limiter.limit("10/minute")
async def logout(request: Request, body: LogoutRequest, current_user: dict = Depends(get_current_user)):
    """Revoke the presented refresh token.

    Previously a no-op (the endpoint didn't exist). Now stamps
    revoked_at on the row so the refresh token can never be exchanged
    again. The current access token keeps working until its exp; for
    immediate kill use /auth/logout-all.
    """
    if body.refresh_token:
        await revoke_refresh_token(body.refresh_token)
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
        logger.warning(f"logout-all: could not bump token_version for {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not invalidate sessions") from e

    revoked = await revoke_all_for_user(user_id)
    logger.info(f"logout-all: user={user_id} token_version→{new_version} revoked_refresh={revoked}")
    return {"success": True, "revoked_refresh_tokens": revoked}
