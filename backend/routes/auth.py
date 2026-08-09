import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, EmailStr, Field
from slowapi.util import get_remote_address

try:
    from .. import db_supabase
    from ..core.config import settings
    from ..core.csrf import clear_csrf_cookie, generate_csrf_token, set_csrf_cookie
    from ..dependencies import (
        OTP_EXPIRY_MINUTES,
        _enforce_account_active,
        _firebase_session_revoked,
        create_jwt_token,
        create_reactivation_token,
        generate_otp,
        get_current_user,
        get_token_session_id,
        verify_reactivation_token,
    )
    from ..schemas import (
        AuthResponse,
        OTPRecord,
        SendOTPRequest,
        UserProfile,
        VerifyOTPRequest,
    )
    from ..settings_loader import get_app_settings
    from ..sms_service import send_otp_sms
    from ..utils.audit_logger import log_user_action as _audit_log_user
    from ..utils.crypto import hash_otp, verify_otp_hash
    from ..utils.email_provider import send_transactional_email
    from ..utils.error_handling import (
        ErrorCode,
        SpinrException,
        TokenExpiredException,
    )
    from ..utils.error_keys import ErrorKeys
    from ..utils.metrics import inc as _metric_inc
    from ..utils.rate_limiter import default_limiter as limiter
    from ..utils.redis_client import (
        redis_delete,
        redis_expire,
        redis_get,
        redis_incr,
        redis_set,
    )
    from ..utils.refresh_tokens import (
        issue_refresh_token,
        lookup_refresh_token,
        revoke_all_for_user,
        revoke_refresh_token,
    )
    from ..utils.session_revocation import revoke_session, should_tombstone
    from ..validators import validate_phone
except ImportError:
    import db_supabase
    from core.config import settings
    from core.csrf import clear_csrf_cookie, generate_csrf_token, set_csrf_cookie
    from dependencies import (
        OTP_EXPIRY_MINUTES,
        _enforce_account_active,
        _firebase_session_revoked,
        create_jwt_token,
        create_reactivation_token,
        generate_otp,
        get_current_user,
        get_token_session_id,
        verify_reactivation_token,
    )
    from schemas import (
        AuthResponse,
        OTPRecord,
        SendOTPRequest,
        UserProfile,
        VerifyOTPRequest,
    )
    from settings_loader import get_app_settings
    from sms_service import send_otp_sms
    from utils.audit_logger import log_user_action as _audit_log_user
    from utils.crypto import hash_otp, verify_otp_hash
    from utils.email_provider import send_transactional_email
    from utils.error_handling import (
        ErrorCode,
        SpinrException,
        TokenExpiredException,
    )
    from utils.error_keys import ErrorKeys
    from utils.metrics import inc as _metric_inc
    from utils.rate_limiter import default_limiter as limiter
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
    from utils.session_revocation import revoke_session, should_tombstone
    from validators import validate_phone

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/auth", tags=["Authentication"])
_CORPORATE_EMAIL_OTP_TABLE = "corporate_email_otp_records"


class CompanyEmailOtpSendRequest(BaseModel):
    email: EmailStr


class CompanyEmailOtpVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$")


# ── OTP brute-force lockout (SEC-008) ───────────────────────────────────
# Redis key prefixes; falls back to in-process dict when REDIS_URL unset.
_FAIL_KEY = "otp_fail:{}"
_LOCK_KEY = "otp_lock:{}"

# ── Per-destination-phone SMS SEND cap (C5) ─────────────────────────────
# Distinct from the failure lockout above (which counts VERIFY failures). The
# IP limiter is XFF-spoofable and there was no per-number send throttle, so one
# spoofed header could SMS-bomb any victim number and burn Twilio spend. Keyed
# on the phone so it survives IP rotation.
_SEND_COUNT_KEY = "otp_sendc:{}"
_SEND_INTERVAL_KEY = "otp_sendi:{}"
_OTP_SEND_MAX_PER_HOUR = 5
_OTP_SEND_WINDOW_SECONDS = 3600
_OTP_SEND_MIN_INTERVAL_SECONDS = 30


async def _enforce_otp_send_cap(phone: str) -> None:
    """Throttle real OTP SMS per destination phone (a 30s min-interval + an
    hourly cap). Fail-CLOSED on Redis errors per the OTP security policy:
    briefly refusing a send beats allowing unbounded SMS during an outage."""
    interval_key = _SEND_INTERVAL_KEY.format(phone)
    count_key = _SEND_COUNT_KEY.format(phone)
    try:
        if await redis_get(interval_key):
            raise HTTPException(
                status_code=429,
                detail="A code was just sent — please wait a moment before requesting another",
                headers={"Retry-After": str(_OTP_SEND_MIN_INTERVAL_SECONDS)},
            )
        count = await redis_incr(count_key)
        if count == 1:
            await redis_expire(count_key, _OTP_SEND_WINDOW_SECONDS)
        if count > _OTP_SEND_MAX_PER_HOUR:
            logger.warning("OTP_SEND_CAP phone=...%s count=%s", phone[-4:], count)
            raise HTTPException(
                status_code=429,
                detail="Too many code requests for this number — try again later",
                headers={"Retry-After": str(_OTP_SEND_WINDOW_SECONDS)},
            )
        await redis_set(interval_key, "1", _OTP_SEND_MIN_INTERVAL_SECONDS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Redis unavailable in OTP send cap: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Auth service temporarily unavailable, please try again",
        ) from e


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
                detail="Too many failed attempts — try again later",
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
        raise HTTPException(
            status_code=503,
            detail="Auth service temporarily unavailable, please try again",
        ) from None


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
            # Security-relevant event: brute-force lockout fired. Route to Sentry
            # via logger.error so on-call can correlate spikes to potential
            # credential-stuffing campaigns. Audit row also written below.
            logger.error(f"OTP_LOCKOUT_TRIGGERED phone=...{phone[-4:]} after {count} failures")
            _metric_inc("spinr_auth_otp_lockout_total")
            try:
                import asyncio

                asyncio.create_task(
                    _audit_log_user(
                        {"id": f"phone:{phone[-4:]}", "role": "anonymous"},
                        "otp_lockout_triggered",
                        "users",
                        phone[-4:],
                        {"failures": count, "phone_last4": phone[-4:]},
                    )
                )
            except Exception:
                logger.debug("audit_log write failed for OTP failure event", exc_info=True)
    except Exception as e:
        logger.error(f"_record_otp_failure: {e}", exc_info=True)


async def _clear_otp_failures(phone: str) -> None:
    """Reset counter + lockout on successful verify. Best-effort."""
    try:
        await redis_delete(_FAIL_KEY.format(phone))
        await redis_delete(_LOCK_KEY.format(phone))
    except Exception as e:
        logger.error(f"_clear_otp_failures: {e}", exc_info=True)


# ── Helpers for Auth Responses ──────────────────────────────────────────
def _make_auth_response(
    response: Response,
    token: str,
    refresh_token: str,
    user_obj: UserProfile,
    is_new_user: bool,
    access_expires_at: datetime,
    refresh_expires_at: datetime,
    csrf_token: Optional[str] = None,
    admin_ttl_minutes: int = 15,
    meta_event_id: Optional[str] = None,
) -> AuthResponse:
    # P3: Set HTTP-only cookies instead of returning tokens in response
    try:
        from ..utils.cookie_manager import CookieManager
    except ImportError:
        from utils.cookie_manager import CookieManager

    CookieManager.set_auth_cookie(response, token, ttl_minutes=admin_ttl_minutes)
    CookieManager.set_refresh_cookie(response, refresh_token, ttl_days=30)

    # Return tokens in BOTH the JSON body AND cookies.
    # Web clients use the HTTP-only cookies; mobile clients (React Native)
    # read the JSON body because RN's fetch has no browser cookie jar.
    return AuthResponse(
        token=token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user_obj,
        is_new_user=is_new_user,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        csrf_token=csrf_token,
        meta_event_id=meta_event_id,
    )


def _fire_signup_conversion(
    user: Dict[str, Any],
    *,
    client_app: str,
    client_ip: Optional[str],
    user_agent: Optional[str],
) -> Optional[str]:
    """Fire the server-side CompleteRegistration and return its event_id.

    Backgrounded off the auth path: Meta's endpoint is not on any Spinr SLA
    and signup must never wait on it, or fail because of it. The returned id
    is handed to the client in the same auth response so its SDK event carries
    the identical event_id and Meta de-duplicates the pair.

    Returns None if anything at all goes wrong — the client then simply fires
    no app event, which costs a de-duplication opportunity but never a signup.
    """
    try:
        from ..services import meta_conversions_service as _meta
        from ..utils.background import spawn as _spawn
    except ImportError:
        try:
            from services import meta_conversions_service as _meta  # type: ignore
            from utils.background import spawn as _spawn  # type: ignore
        except ImportError:
            logger.error("meta: conversions service unavailable — skipping CompleteRegistration", exc_info=True)
            return None

    try:
        event_id = _meta.new_event_id()
        sender = _meta.send_driver_registration if client_app == "driver" else _meta.send_rider_registration
        _spawn(
            sender(
                user,
                event_id=event_id,
                registration_method="phone",
                client_ip=client_ip,
                client_user_agent=user_agent,
            )
        )
        return event_id
    except Exception:
        logger.error("meta: failed to queue CompleteRegistration for new signup", exc_info=True)
        return None


@api_router.post("/send-otp")
# 6/minute: 3/minute proved too tight in production — the key is per client IP
# (CF-Connecting-IP), and carrier CGNAT can put several riders behind one IP;
# a single user retrying + resending also burns 3 fast. SMS cost/abuse is still
# bounded by the per-destination-phone send cap (_enforce_otp_send_cap) and the
# verify-side 5-fail/hour lockout, so the per-IP window can afford headroom.
@limiter.limit("6/minute")
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

    # OTP code selection:
    #  - Twilio configured  → real random OTP delivered via SMS.
    #  - Twilio NOT configured + non-production (development/preview/staging)
    #                        → fixed code "1234" so testing works without SMS.
    #  - Twilio NOT configured + production
    #                        → refuse. A missing Twilio config in production is a
    #                          misconfiguration; falling back to a static "1234"
    #                          would let anyone log in as any phone number, so we
    #                          fail loudly instead of silently bypassing auth.
    is_production = settings.ENV.lower() == "production"
    review_otp = settings.review_login_map().get(phone)
    deliver_via_sms = True
    if review_otp is not None:
        # App Store / Play reviewer account: issue the pre-shared fixed code and
        # never send an SMS — the reviewer gets the code from the store-console
        # review notes, not a text. Permitted in every ENV (including production)
        # but ONLY for the explicit numbers in REVIEW_LOGIN_ACCOUNTS.
        otp_code = review_otp
        deliver_via_sms = False
        logger.info(
            "Reviewer-account OTP issued without SMS for ...%s (ENV=%s)",
            phone[-4:],
            settings.ENV,
        )
    elif twilio_configured:
        otp_code = generate_otp()
    elif not is_production:
        otp_code = "1234"
        logger.info(
            "Twilio not configured — OTP bypass active (code=1234) for ...%s (ENV=%s)",
            phone[-4:],
            settings.ENV,
        )
    else:
        logger.error(
            "Twilio not configured in production — refusing to issue OTP (static-code bypass is disabled in production)"
        )
        raise SpinrException(
            message="Verification is temporarily unavailable, please try again later",
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_SERVICE_UNAVAILABLE,
        )

    # Per-destination-phone SMS send cap (C5) — enforced BEFORE the stored OTP
    # is replaced, so a throttled resend (429) doesn't wipe the still-valid code
    # already on the user's phone (which would lock them out until the cap
    # resets). Only real Twilio sends are capped; dev-bypass and reviewer
    # accounts skip SMS and so skip the cap.
    if deliver_via_sms and twilio_configured:
        await _enforce_otp_send_cap(phone)

    otp_record = OTPRecord(
        phone=phone,
        code=hash_otp(otp_code),  # stored as SHA-256 hash (SEC-016)
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES),
    )

    try:
        await db_supabase.delete_many("otp_records", {"phone": phone})
        await db_supabase.insert_otp_record(otp_record.dict())
    except Exception as e:
        # Returning 200 here would lie to the client: a missing OTP row means
        # every subsequent /verify-otp will fail. Surface the DB error so the
        # client retries instead of getting stuck.
        logger.error(f"Could not store OTP in DB: {e}", exc_info=True)
        raise SpinrException(
            message="Could not store OTP, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    # Send OTP via SMS (Twilio when configured, console log otherwise).
    # Reviewer accounts skip SMS entirely — the code is pre-shared out of band.
    # (The per-phone send cap already ran above, before the OTP row was stored.)
    if deliver_via_sms:
        sms_result = await send_otp_sms(
            phone,
            otp_code,
            twilio_sid=app_settings.get("twilio_account_sid", "") if app_settings else "",
            twilio_token=app_settings.get("twilio_auth_token", "") if app_settings else "",
            twilio_from=app_settings.get("twilio_from_number", "") if app_settings else "",
        )
        if not sms_result.get("success"):
            logger.error(f"Failed to send OTP SMS: {sms_result.get('error')}")
            raise SpinrException(
                message="Failed to send verification code",
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                status_code=503,
                message_key=ErrorKeys.SYSTEM_SERVICE_UNAVAILABLE,
            )

    response = {"success": True, "message": f"OTP sent to ***{phone[-4:]}"}
    # Dev OTP is logged to server console via sms_service.py — never return it
    # in the API response to avoid accidental exposure in client-side logs.
    import asyncio
    import hashlib

    _ph = hashlib.sha256(phone.encode()).hexdigest()[:16]
    # Tag the reviewer-bypass path distinctly so operators can query audit_logs
    # for fixed-code issuance separately from ordinary SMS OTP sends (e.g. to
    # confirm the allow-list was cleared after a review, or investigate abuse).
    _is_reviewer = review_otp is not None
    _audit_action = "otp_sent_reviewer_bypass" if _is_reviewer else "otp_sent"
    _audit_meta = {"phone_last4": phone[-4:]}
    if _is_reviewer:
        _audit_meta["reviewer_bypass"] = True
    try:
        asyncio.create_task(
            _audit_log_user(
                {"id": f"phone_hash:{_ph}", "role": "anonymous"},
                _audit_action,
                "users",
                _ph,
                _audit_meta,
            )
        )
    except Exception:
        logger.error("audit_log write failed for otp_sent", exc_info=True)

    return response


def _normalize_company_email(email: str) -> str:
    return (email or "").strip().lower()


def _email_log_id(email: str) -> str:
    digest = hashlib.sha256(_normalize_company_email(email).encode()).hexdigest()[:16]
    return f"email_sha256:{digest}"


def _synthetic_phone_for_company_email(email: str) -> str:
    digest = hashlib.sha256(_normalize_company_email(email).encode()).hexdigest()[:32]
    return f"email:{digest}"


async def _latest_company_email_otp(email: str) -> Optional[Dict[str, Any]]:
    rows = await db_supabase.get_rows(
        _CORPORATE_EMAIL_OTP_TABLE,
        {"email": email, "verified": False},
        order="created_at",
        desc=True,
        limit=1,
    )
    return rows[0] if rows else None


async def _find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    rows = await db_supabase.get_rows("users", {"email": email}, limit=1)
    return rows[0] if rows else None


async def _activate_pending_company_invites(email: str, user_id: str) -> None:
    invites = await db_supabase.get_rows(
        "corporate_members",
        {"invited_email": email, "status": "invited"},
        limit=100,
    )
    for invite in invites:
        member_id = invite.get("id")
        if not member_id:
            continue
        await db_supabase.accept_member_invite(member_id=member_id, user_id=user_id)


async def _issue_company_email_session(
    *,
    request: Request,
    response: Response,
    email: str,
) -> AuthResponse:
    try:
        existing_user = await _find_user_by_email(email)
    except Exception as e:
        logger.error("company email auth: user lookup failed for %s", _email_log_id(email), exc_info=True)
        raise SpinrException(
            message="Service temporarily unavailable, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    session_id = str(uuid.uuid4())
    user_agent = request.headers.get("user-agent", "")
    client_ip = get_remote_address(request)

    if existing_user:
        status = str(existing_user.get("status") or "").lower()
        if status == "deleted" or existing_user.get("deleted_at"):
            raise HTTPException(status_code=410, detail="ERR_ACCOUNT_DELETED")
        if status == "pending_deletion":
            raise HTTPException(status_code=403, detail="ERR_ACCOUNT_DELETED")
        user = dict(existing_user)
        try:
            await db_supabase.update_one("users", {"id": user["id"]}, {"current_session_id": session_id})
            user["current_session_id"] = session_id
        except Exception as e:
            logger.error("company email auth: session update failed for user_id=%s", user.get("id"), exc_info=True)
            raise SpinrException(
                message="Service temporarily unavailable, please try again",
                error_code=ErrorCode.DATABASE_ERROR,
                status_code=503,
                message_key=ErrorKeys.SYSTEM_DATABASE,
            ) from e
        is_new_user = False
    else:
        user = {
            "id": str(uuid.uuid4()),
            "phone": _synthetic_phone_for_company_email(email),
            "email": email,
            "role": "rider",
            "is_rider": True,
            "is_driver": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_complete": False,
            "current_session_id": session_id,
            "token_version": 0,
        }
        try:
            created = await db_supabase.create_user(user)
            if created:
                user = {**user, **created}
        except Exception as e:
            logger.error("company email auth: user create failed for %s", _email_log_id(email), exc_info=True)
            raise SpinrException(
                message="Could not create user account, please try again",
                error_code=ErrorCode.DATABASE_ERROR,
                status_code=503,
                message_key=ErrorKeys.SYSTEM_DATABASE,
            ) from e
        is_new_user = True

    await redis_set(
        f"session:{user['id']}",
        session_id,
        ttl=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    await _activate_pending_company_invites(email, user["id"])

    access_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_jwt_token(
        user["id"],
        email,
        session_id=session_id,
        token_version=int(user.get("token_version") or 0),
    )
    refresh_raw, _, refresh_expires_at = await issue_refresh_token(
        user["id"], audience="rider", user_agent=user_agent, ip=client_ip
    )
    csrf = generate_csrf_token()
    set_csrf_cookie(
        response,
        csrf,
        secure=settings.ENV == "production",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return _make_auth_response(
        response,
        token,
        refresh_raw,
        UserProfile(**user),
        is_new_user=is_new_user,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        csrf_token=csrf,
        admin_ttl_minutes=15,
    )


@api_router.post("/send-email-otp")
@limiter.limit("3/minute")
async def send_company_email_otp(request: Request, body: CompanyEmailOtpSendRequest):
    email = _normalize_company_email(str(body.email))
    # Send cap only — deliberately NOT _check_otp_lockout, matching the phone
    # send_otp path. The lockout is enforced at verify, so gating send buys no
    # security: an attacker who can request codes still cannot verify one.
    # It would only strip the recovery path from a legitimate user whose
    # address someone else mistyped into the lockout.
    await _enforce_otp_send_cap(_synthetic_phone_for_company_email(email))

    # Email-provider bypass, mirroring the phone /send-otp dev bypass above:
    #  - SES or Resend configured  → real random OTP delivered via email.
    #  - Neither configured + non-production (dev/preview/staging)
    #                        → fixed code "1234" so testing works without a
    #                          working email provider.
    #  - Neither configured + production
    #                        → refuse; a static-code bypass in production would
    #                          let anyone log in as any company email.
    app_settings = None
    try:
        app_settings = await get_app_settings()
    except Exception as e:
        logger.error(f"Could not read app_settings from DB: {e}", exc_info=True)

    email_provider_configured = bool(
        app_settings
        and (
            (app_settings.get("aws_ses_access_key_id") and app_settings.get("aws_ses_secret_access_key"))
            or app_settings.get("resend_api_key")
        )
    )
    is_production = settings.ENV.lower() == "production"
    deliver_via_email = True
    if email_provider_configured:
        otp_code = generate_otp()
    elif not is_production:
        otp_code = "1234"
        deliver_via_email = False
        logger.info(
            "Email provider not configured — OTP bypass active (code=1234) for %s (ENV=%s)",
            _email_log_id(email),
            settings.ENV,
        )
    else:
        logger.error(
            "Email provider not configured in production — refusing to issue OTP (static-code bypass is disabled in production)"
        )
        raise SpinrException(
            message="Verification is temporarily unavailable, please try again later",
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_SERVICE_UNAVAILABLE,
        )

    otp_row = {
        "id": str(uuid.uuid4()),
        "email": email,
        "code_hash": hash_otp(otp_code),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat(),
        "verified": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        await db_supabase.delete_many(_CORPORATE_EMAIL_OTP_TABLE, {"email": email})
        await db_supabase.insert_one(_CORPORATE_EMAIL_OTP_TABLE, otp_row)
    except Exception as e:
        logger.error("company email auth: OTP persist failed for %s", _email_log_id(email), exc_info=True)
        raise SpinrException(
            message="Could not store verification code, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    if deliver_via_email:
        try:
            from ..utils.email_layout import render_email
        except ImportError:
            from utils.email_layout import render_email  # type: ignore

        # A verification code arriving as two bare <p> tags with no branding is
        # exactly what a phishing attempt looks like. Rendering it through the
        # shared shell puts the real logo and the configured company details on
        # the one email whose whole job is to be trusted.
        _otp_rendered = await render_email(
            heading="Your verification code",
            paragraphs=[
                f"Your Spinr for Business verification code is {otp_code}.",
                f"It expires in {OTP_EXPIRY_MINUTES} minutes. If you didn't request it, you can ignore this email.",
            ],
        )
        sent = await send_transactional_email(
            to=email,
            subject="Your Spinr for Business verification code",
            text=_otp_rendered.text,
            html=_otp_rendered.html,
            log_id=_email_log_id(email),
            email_type="corporate_email_otp",
        )
        if not sent:
            try:
                await db_supabase.delete_many(_CORPORATE_EMAIL_OTP_TABLE, {"id": otp_row["id"]})
            except Exception:
                logger.error(
                    "company email auth: failed-code cleanup failed for %s", _email_log_id(email), exc_info=True
                )
            raise HTTPException(status_code=502, detail="Could not send verification code")

    return {"success": True, "message": "Verification code sent"}


@api_router.post("/verify-email-otp", response_model=AuthResponse)
@limiter.limit("5/minute")
async def verify_company_email_otp(
    request: Request,
    response: Response,
    body: CompanyEmailOtpVerifyRequest,
):
    email = _normalize_company_email(str(body.email))
    code = body.code.strip()
    lockout_key = _synthetic_phone_for_company_email(email)

    await _check_otp_lockout(lockout_key)

    try:
        otp_record = await _latest_company_email_otp(email)
    except Exception as e:
        logger.error("company email auth: OTP lookup failed for %s", _email_log_id(email), exc_info=True)
        raise SpinrException(
            message="Service temporarily unavailable, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    if not otp_record or not verify_otp_hash(str(otp_record.get("code_hash", "")), code):
        await _record_otp_failure(lockout_key)
        raise SpinrException(
            message="ERR_OTP_INVALID",
            error_code=ErrorCode.AUTH_OTP_INVALID,
            status_code=400,
            message_key=ErrorKeys.AUTH_OTP_INVALID,
            action_hint="Re-enter the verification code",
        )

    expires_at = otp_record.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            logger.error("company email auth: invalid OTP expires_at for %s", _email_log_id(email), exc_info=True)
            raise SpinrException(
                message="Internal error processing verification code",
                error_code=ErrorCode.INTERNAL_ERROR,
                status_code=500,
                message_key=ErrorKeys.SYSTEM_INTERNAL,
            ) from None
    if not expires_at:
        raise SpinrException(
            message="Internal error processing verification code",
            error_code=ErrorCode.INTERNAL_ERROR,
            status_code=500,
            message_key=ErrorKeys.SYSTEM_INTERNAL,
        )
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise SpinrException(
            message="ERR_OTP_EXPIRED",
            error_code=ErrorCode.AUTH_OTP_EXPIRED,
            status_code=400,
            message_key=ErrorKeys.AUTH_OTP_EXPIRED,
            action_hint="Request a new code",
        )

    await db_supabase.update_one(
        _CORPORATE_EMAIL_OTP_TABLE,
        {"id": otp_record["id"]},
        {"verified": True},
    )
    await _clear_otp_failures(lockout_key)
    return await _issue_company_email_session(request=request, response=response, email=email)


@api_router.post("/verify-otp", response_model=AuthResponse)
@limiter.limit("5/minute")
async def verify_otp(request: Request, response: Response, body: VerifyOTPRequest):
    phone = body.phone.strip()
    code = body.code.strip()

    # Normalize to E.164 so it matches what send-otp stored
    _, normalized = validate_phone(phone)
    phone = normalized or phone

    # Reviewer-bypass accounts log in with a fixed code; tag their audit rows so
    # operators can distinguish reviewer logins from real SMS verifications.
    _is_reviewer = settings.review_login_map().get(phone) is not None

    # SEC-008: Reject locked-out phones before touching the DB
    await _check_otp_lockout(phone)
    otp_record = None
    try:
        # R-P1-14: Fetch by phone only; compare hashes in constant time with
        # hmac.compare_digest to prevent timing-based hash-prefix leakage.

        otp_record = await db_supabase.get_otp_record_by_phone(phone)
        if otp_record:
            expected = otp_record.get("code", "")
            if not verify_otp_hash(str(expected), code):
                otp_record = None
    except Exception as e:
        # C3: a DB read failure is NOT a wrong code. Surface 503 so the client
        # retries — do NOT fall through to _record_otp_failure below, which
        # would count a correct code as a failure and can trip the 5-strike
        # 24h lockout, locking out a user who entered the right code during a
        # DB blip. (CLAUDE.md: never swallow a DB error and continue.)
        original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
        logger.error(
            f"verify_otp: OTP lookup failed for ***{phone[-4:]}: type={type(e).__name__} msg={e} original={original}",
            exc_info=True,
        )
        raise SpinrException(
            message="Service temporarily unavailable, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    if not otp_record:
        # Wrong code — record the failure (may trigger lockout)
        await _record_otp_failure(phone)
        raise SpinrException(
            message="ERR_OTP_INVALID",
            error_code=ErrorCode.AUTH_OTP_INVALID,
            status_code=400,
            message_key=ErrorKeys.AUTH_OTP_INVALID,
            action_hint="Re-enter the 4-digit code",
        )
    # Parse expires_at to datetime if it's a string (from Supabase)
    expires_at = otp_record.get("expires_at")
    if isinstance(expires_at, str):
        try:
            # Handle ISO format from Supabase (replace Z with +00:00 if present)
            expires_at = expires_at.replace("Z", "+00:00")
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            logger.error(f"Invalid date format for OTP expires_at: {expires_at}")
            raise SpinrException(
                message="Internal error processing OTP record",
                error_code=ErrorCode.INTERNAL_ERROR,
                status_code=500,
                message_key=ErrorKeys.SYSTEM_INTERNAL,
            ) from None

    if not expires_at:
        logger.error("OTP record missing expires_at field")
        raise SpinrException(
            message="Internal error processing OTP record",
            error_code=ErrorCode.INTERNAL_ERROR,
            status_code=500,
            message_key=ErrorKeys.SYSTEM_INTERNAL,
        )

    # Ensure expires_at is timezone-aware for comparison
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expires_at:
        try:
            await db_supabase.delete_otp_record(otp_record["id"])
        except Exception:
            # DB write failure on cleanup path — surface to Sentry per
            # CLAUDE.md "Do not silently swallow errors" rule. Non-fatal for
            # the request (we still return ERR_OTP_EXPIRED below) but the
            # backlog of orphaned OTP rows needs operator attention.
            logger.error(
                "Failed to delete expired OTP record %s",
                otp_record["id"],
                exc_info=True,
            )
        raise SpinrException(
            message="ERR_OTP_EXPIRED",
            error_code=ErrorCode.AUTH_OTP_EXPIRED,
            status_code=400,
            message_key=ErrorKeys.AUTH_OTP_EXPIRED,
            action_hint="Request a new code",
        )

    try:
        # Delete the OTP record after successful verification to prevent reuse.
        # A stale verified record would cause confusion on retries — the phone
        # lookup would find it, the hash wouldn't match a newly-requested code,
        # and the user would get ERR_OTP_INVALID even with the correct code.
        await db_supabase.delete_otp_record(otp_record["id"])
    except Exception:
        # Non-fatal: if deletion fails, fall back to marking as verified so at
        # least a reuse check could catch it.
        logger.error(
            "auth: failed to delete verified OTP %s — falling back to mark-as-verified",
            otp_record.get("id"),
            exc_info=True,
        )
        try:
            await db_supabase.update_one("otp_records", {"id": otp_record["id"]}, {"verified": True})
        except Exception:
            logger.error(
                "auth: failed to mark OTP %s as verified — reuse risk",
                otp_record.get("id"),
                exc_info=True,
            )

    # SEC-008: Clear failure counter + lockout on successful verification
    await _clear_otp_failures(phone)
    try:
        # Find or create user
        existing_user = None
        try:
            logger.info(f"Searching for user with phone: ***{phone[-4:]}")
            existing_user = await db_supabase.get_user_by_phone(phone)
            logger.info(f"User search result found: {bool(existing_user)}")
        except Exception as e:
            # Surface the real underlying Supabase error. DatabaseError
            # wraps the original exception in .details["original"]; str(e)
            # only gives the generic "Database operation failed" message.
            original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
            logger.error(
                f"get_user_by_phone failed for ***{phone[-4:]}: type={type(e).__name__} msg={e} original={original}",
                exc_info=True,
            )
            # Refuse to silently fall through to user creation — a DB read
            # failure is NOT the same as "user doesn't exist". Creating a
            # new row here generates duplicate accounts on every retry and
            # locks the real user out of their own profile, wallet, and
            # ride history. Fail the login so the client retries.
            raise SpinrException(
                message="Service temporarily unavailable, please try again",
                error_code=ErrorCode.DATABASE_ERROR,
                status_code=503,
                message_key=ErrorKeys.SYSTEM_DATABASE,
            ) from e

        user_agent = request.headers.get("user-agent", "")
        client_ip = get_remote_address(request)

        if existing_user:
            # PIPEDA: a deletion-requested account is in its 30-day grace window.
            # Do NOT mint normal tokens — that would silently undelete it. Hand the
            # client a single-purpose reactivation token + the scheduled date so it
            # can offer deliberate self-serve reactivation (POST /auth/reactivate).
            if str(existing_user.get("status") or "").lower() == "pending_deletion":
                logger.info("verify_otp: account pending_deletion — returning reactivation handoff")
                try:
                    asyncio.create_task(
                        _audit_log_user(existing_user, "otp_verify_pending_deletion", "users", existing_user["id"], {})
                    )
                except Exception:
                    logger.error("audit_log write failed for otp_verify_pending_deletion", exc_info=True)
                return JSONResponse(
                    status_code=200,
                    content={
                        "requires_reactivation": True,
                        "deletion_scheduled_at": existing_user.get("deletion_scheduled_at"),
                        "reactivation_token": create_reactivation_token(existing_user["id"], phone),
                    },
                )
            # A fully-deleted / purged account (e.g. via DELETE /profile, which
            # leaves phone intact, or a row mid-purge) cannot be reactivated — its
            # PII is gone. Refuse to mint tokens; the client should sign up fresh.
            if str(existing_user.get("status") or "").lower() == "deleted" or existing_user.get("deleted_at"):
                raise HTTPException(status_code=410, detail="ERR_ACCOUNT_DELETED")
            logger.info("User exists, creating token")
            session_id = str(uuid.uuid4())
            try:
                _session_update: dict = {"current_session_id": session_id}
                if existing_user.get("is_guest"):
                    # Row was provisioned by a corporate guest booking
                    # (services/guest_user_service). The phone owner just
                    # proved possession via OTP — the account and its guest
                    # ride history are theirs now.
                    _session_update["is_guest"] = False
                await db_supabase.update_one(
                    "users",
                    {"id": existing_user["id"]},
                    _session_update,
                )
                existing_user["current_session_id"] = session_id
                if existing_user.get("is_guest"):
                    existing_user["is_guest"] = False
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
                logger.error(
                    f"UserProfile validation failed, falling back to raw dict: {e}",
                    exc_info=True,
                )
                user_obj = existing_user
            csrf = generate_csrf_token()
            set_csrf_cookie(
                response,
                csrf,
                secure=settings.ENV == "production",
                max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )
            try:
                import asyncio

                asyncio.create_task(
                    _audit_log_user(
                        existing_user,
                        "otp_verify_success",
                        "users",
                        user_id,
                        {"is_new_user": False, **({"reviewer_bypass": True} if _is_reviewer else {})},
                    )
                )
            except Exception:
                logger.error(
                    "audit_log write failed for otp_verify_success (returning user)",
                    exc_info=True,
                )
            return _make_auth_response(
                response,
                token,
                refresh_raw,
                user_obj,
                is_new_user=False,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
                csrf_token=csrf,
                admin_ttl_minutes=15,
            )
        else:
            logger.info("Creating new user")
            user_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            new_user = {
                "id": user_id,
                "phone": phone,
                "role": "rider",
                "is_rider": True,
                "is_driver": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "profile_complete": False,
                "current_session_id": session_id,
                "token_version": 0,
            }
            try:
                await db_supabase.create_user(new_user)
            except Exception as e:
                # Issuing a JWT for a row that doesn't exist would break
                # every subsequent authenticated call. Refuse to mint a
                # token until persistence is confirmed.
                logger.error(f"Could not persist new user to DB: {e}", exc_info=True)
                raise SpinrException(
                    message="Could not create user account, please try again",
                    error_code=ErrorCode.DATABASE_ERROR,
                    status_code=503,
                    message_key=ErrorKeys.SYSTEM_DATABASE,
                ) from e
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
            csrf = generate_csrf_token()
            set_csrf_cookie(
                response,
                csrf,
                secure=settings.ENV == "production",
                max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )
            try:
                import asyncio

                asyncio.create_task(
                    _audit_log_user(
                        new_user,
                        "otp_verify_success",
                        "users",
                        user_id,
                        {"is_new_user": True, **({"reviewer_bypass": True} if _is_reviewer else {})},
                    )
                )
            except Exception:
                logger.error(
                    "audit_log write failed for otp_verify_success (new user)",
                    exc_info=True,
                )
            # Meta CompleteRegistration. This branch is the only place a rider
            # account is actually created, so it is the true "registration
            # succeeded" moment — not the OTP screen mounting, not the Verify
            # tap, and not profile-setup (which a user can abandon and return
            # to, and which would fire again on every edit).
            _meta_event_id = _fire_signup_conversion(
                new_user,
                client_app=(body.client_app or "rider"),
                client_ip=client_ip,
                user_agent=user_agent,
            )
            return _make_auth_response(
                response,
                token,
                refresh_raw,
                UserProfile(**new_user),
                is_new_user=True,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
                csrf_token=csrf,
                admin_ttl_minutes=15,
                meta_event_id=_meta_event_id,
            )
    except (HTTPException, SpinrException):
        # Already a well-formed HTTP/Spinr error (e.g. the 503 raised when
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
        raise SpinrException(
            message="Internal Login Error",
            error_code=ErrorCode.INTERNAL_ERROR,
            status_code=500,
            message_key=ErrorKeys.SYSTEM_INTERNAL,
        ) from e


class ReactivateRequest(BaseModel):
    reactivation_token: str


@api_router.post("/reactivate", response_model=AuthResponse)
@limiter.limit("5/minute")
async def reactivate_account(request: Request, response: Response, body: ReactivateRequest):
    """Self-serve reactivation inside the 30-day deletion grace window (PIPEDA).

    Authorised by the single-purpose reactivation token issued at /auth/verify-otp
    for a pending_deletion account (proves the phone+OTP just verified). Clears the
    pending deletion, restores access, and logs the user in.

    Ride PII anonymised at deletion time is NOT restored — that is irreversible by
    design; reactivation restores account ACCESS, not already-scrubbed history.
    """
    user_id = verify_reactivation_token(body.reactivation_token)

    user = await db_supabase.get_user_by_id(user_id)
    status = str((user or {}).get("status") or "").lower()
    if not user or status == "deleted":
        # Grace elapsed and the purge already scrubbed PII → nothing to reactivate.
        raise HTTPException(status_code=410, detail="ERR_ACCOUNT_DELETED")

    phone = user.get("phone") or ""
    user_agent = request.headers.get("user-agent", "")
    client_ip = get_remote_address(request)

    # Restore the account. Idempotent: a repeat call on an already-active account
    # skips the write and just re-issues a session.
    if status == "pending_deletion":
        try:
            await db_supabase.update_one(
                "users",
                {"id": user_id},
                {"status": "active", "deletion_requested_at": None, "deletion_scheduled_at": None},
            )
            await db_supabase.update_one("drivers", {"user_id": user_id}, {"deleted_at": None})
        except Exception as e:
            logger.error(f"Account reactivation failed for user {user_id}: {e}", exc_info=True)
            raise SpinrException(
                message="Could not reactivate account, please try again",
                error_code=ErrorCode.DATABASE_ERROR,
                status_code=503,
                message_key=ErrorKeys.SYSTEM_DATABASE,
            ) from e
        user["status"] = "active"
        try:
            asyncio.create_task(_audit_log_user(user, "dsar_reactivated", "users", user_id, {"pipeda": True}))
        except Exception:
            logger.error("audit_log write failed for dsar_reactivated", exc_info=True)

    # Log the user back in (fresh session + tokens), mirroring verify-otp.
    session_id = str(uuid.uuid4())
    try:
        await db_supabase.update_one("users", {"id": user_id}, {"current_session_id": session_id})
        user["current_session_id"] = session_id
    except Exception as e:
        logger.error(f"reactivate: could not set session_id for user {user_id}: {e}", exc_info=True)
    await redis_set(f"session:{user_id}", session_id, ttl=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    token_version = int(user.get("token_version") or 0)
    access_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_jwt_token(user_id, phone, session_id=session_id, token_version=token_version)
    refresh_raw, _, refresh_expires_at = await issue_refresh_token(
        user_id, audience="rider", user_agent=user_agent, ip=client_ip
    )
    try:
        user_obj = UserProfile(**user)
    except Exception:
        user_obj = user
    csrf = generate_csrf_token()
    set_csrf_cookie(
        response, csrf, secure=settings.ENV == "production", max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    logger.info(f"Account reactivated for user {user_id}")
    return _make_auth_response(
        response,
        token,
        refresh_raw,
        user_obj,
        is_new_user=False,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        csrf_token=csrf,
        admin_ttl_minutes=15,
    )


class FirebaseAuthRequest(BaseModel):
    firebase_token: str


@api_router.post("/firebase", response_model=AuthResponse)
@limiter.limit("10/minute")
async def firebase_auth_login(request: Request, response: Response, body: FirebaseAuthRequest):
    """Exchange a Firebase ID token for Spinr access + refresh tokens.

    Mirrors the OTP verify flow: verify identity, find-or-create the user
    record, create a session, and issue both a short-lived JWT and a
    long-lived opaque refresh token.
    """
    try:
        from firebase_admin import auth as _firebase_auth  # type: ignore

        payload = _firebase_auth.verify_id_token(body.firebase_token, check_revoked=True)
    except Exception as e:
        raise SpinrException(
            message="Invalid Firebase token",
            error_code=ErrorCode.AUTH_INVALID_CREDENTIALS,
            status_code=401,
            message_key=ErrorKeys.AUTH_INVALID_CREDENTIALS,
        ) from e

    # B-P1-1 / DV-10: enforce audience binding unconditionally. Production fails
    # fast in core/config._guard_production_secrets when FIREBASE_DRIVER_APP_ID
    # is unset, so this branch is only reachable in dev/test.
    driver_app_id = settings.FIREBASE_DRIVER_APP_ID
    if not driver_app_id:
        raise SpinrException(
            message="Driver Firebase audience not configured",
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_SERVICE_UNAVAILABLE,
        )
    if payload.get("aud") != driver_app_id:
        raise SpinrException(
            message="Token not issued for driver app",
            error_code=ErrorCode.AUTH_INVALID_CREDENTIALS,
            status_code=401,
            message_key=ErrorKeys.AUTH_INVALID_CREDENTIALS,
        )

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
        raise SpinrException(
            message="User lookup failed, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e

    is_new_user = False
    session_id = str(uuid.uuid4())
    if not user:
        is_new_user = True
        new_user: Dict[str, Any] = {
            "id": uid,
            "phone": phone,
            "role": "rider",
            "is_rider": True,
            "is_driver": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_complete": False,
            "current_session_id": session_id,
            "token_version": 0,
        }
        try:
            await db_supabase.create_user(new_user)
        except Exception as e:
            # Never hand back a token for an unpersisted row — the access
            # token we're about to mint would point at no row, and every
            # subsequent request would 401 against `get_current_user`.
            logger.error(
                f"firebase_auth: could not persist user {uid}: {e}",
                exc_info=True,
            )
            raise SpinrException(
                message="Could not persist user account, please try again",
                error_code=ErrorCode.DATABASE_ERROR,
                status_code=503,
                message_key=ErrorKeys.SYSTEM_DATABASE,
            ) from e
        user = new_user
    else:
        # Enforce the logout-all / reuse-cascade watermark at token EXCHANGE,
        # BEFORE rotating the session. verify_id_token(check_revoked=True) only
        # catches tokens once Firebase's own revocation propagated; our
        # revoke_refresh_tokens is best-effort, so without this a pre-logout
        # Firebase ID token (auth_time <= sessions_invalid_before) could be
        # exchanged for a fresh Spinr JWT, bypassing logout-all. Checking before
        # the current_session_id write matters: rotating the session on a
        # rejected exchange would invalidate the user's legitimate in-flight JWT
        # (ERR_SESSION_EXPIRED) and let a stale-token holder repeatedly force
        # re-sign-in. New users (handled above) have no watermark, so unaffected.
        if _firebase_session_revoked(payload, user.get("sessions_invalid_before")):
            raise SpinrException(
                message="Session has been revoked, please sign in again",
                error_code=ErrorCode.AUTH_INVALID_CREDENTIALS,
                status_code=401,
                message_key=ErrorKeys.AUTH_INVALID_CREDENTIALS,
            )
        # PIPEDA: a deletion-requested account must not be logged in. Mirror
        # verify-otp — hand back a reactivation token instead of minting tokens.
        _fb_status = str(user.get("status") or "").lower()
        if _fb_status == "pending_deletion":
            logger.info("firebase_auth: account pending_deletion — returning reactivation handoff")
            return JSONResponse(
                status_code=200,
                content={
                    "requires_reactivation": True,
                    "deletion_scheduled_at": user.get("deletion_scheduled_at"),
                    "reactivation_token": create_reactivation_token(user["id"], phone),
                },
            )
        if _fb_status == "deleted" or user.get("deleted_at"):
            raise HTTPException(status_code=410, detail="ERR_ACCOUNT_DELETED")
        try:
            await db_supabase.update_one("users", {"id": uid}, {"current_session_id": session_id})
        except Exception as e:
            # Without a persisted current_session_id, single-device login
            # can't enforce ERR_SESSION_EXPIRED on the prior device, and
            # the new token we're about to mint references a session_id
            # that isn't recorded server-side.
            logger.error(
                f"firebase_auth: could not update session_id for {uid}: {e}",
                exc_info=True,
            )
            raise SpinrException(
                message="Could not update session, please try again",
                error_code=ErrorCode.DATABASE_ERROR,
                status_code=503,
                message_key=ErrorKeys.SYSTEM_DATABASE,
            ) from e
        user["current_session_id"] = session_id

    user_id = user["id"]
    token_version = int(user.get("token_version") or 0)
    access_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_jwt_token(user_id, phone, session_id=session_id, token_version=token_version)
    refresh_raw, _, refresh_expires_at = await issue_refresh_token(
        user_id, audience="driver", user_agent=user_agent, ip=client_ip
    )

    try:
        user_obj = UserProfile(**user)
    except Exception:
        user_obj = user  # type: ignore[assignment]

    csrf = generate_csrf_token()
    set_csrf_cookie(
        response,
        csrf,
        secure=settings.ENV == "production",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    try:
        import asyncio

        asyncio.create_task(
            _audit_log_user(
                user,
                "firebase_auth_login",
                "users",
                user_id,
                {"is_new_user": is_new_user},
            )
        )
    except Exception:
        logger.error("audit_log write failed for firebase_auth_login", exc_info=True)
    return _make_auth_response(
        response,
        token,
        refresh_raw,
        user_obj,
        is_new_user=is_new_user,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        csrf_token=csrf,
        admin_ttl_minutes=15,
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

    # Rider stats: completed ride count for the profile hero card.
    try:
        ride_count = await db_supabase.count_documents("rides", {"rider_id": current_user["id"], "status": "completed"})
        current_user["total_rides"] = ride_count
    except Exception:
        logger.error("Could not fetch rider ride count", exc_info=True)

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
    csrf_token: Optional[str] = None


class LogoutRequest(BaseModel):
    # Optional — /auth/logout-all doesn't need the token, just auth.
    refresh_token: str | None = None
    # Which app surface is signing out ("rider" / "driver"), mirroring
    # POST /notifications/register-token. Decides which fcm_token_* column is
    # detached so a dual-role user signing out of one app keeps the other's
    # pushes. Absent on older builds — see _push_token_columns_to_clear.
    client_type: str | None = None


@api_router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("20/minute")
async def refresh_access_token(request: Request, response: Response, body: Optional[RefreshRequest] = None):
    """Exchange a refresh token for a new access token + rotated refresh token.

    P3: Now reads refresh_token from HTTP-only cookie instead of request body.
    Returns 401 on any lookup failure (revoked / expired / unknown) —
    the client's reaction to all three is the same (re-login), and
    distinguishing them would leak an oracle.
    """
    # P3: Read refresh token from cookie first, fall back to request body
    # for mobile clients (React Native fetch has no browser cookie jar).
    refresh_token_from_cookie = request.cookies.get("refresh_token")
    if not refresh_token_from_cookie and body and body.refresh_token:
        refresh_token_from_cookie = body.refresh_token
    if not refresh_token_from_cookie:
        raise TokenExpiredException(
            message="Missing refresh token",
            message_key=ErrorKeys.AUTH_TOKEN_EXPIRED,
            action_hint="Sign in again",
        )

    row = await lookup_refresh_token(refresh_token_from_cookie)
    if not row:
        raise TokenExpiredException(
            message="Invalid refresh token",
            message_key=ErrorKeys.AUTH_TOKEN_EXPIRED,
            action_hint="Sign in again",
        )

    if row.get("audience") not in {"rider", "driver"}:
        # Admin refresh tokens go through /admin/auth/refresh. Only rider and
        # driver tokens are valid here; anything else is a privilege-escalation
        # attempt or a minted-for-wrong-endpoint token.
        raise TokenExpiredException(
            message="Invalid refresh token",
            message_key=ErrorKeys.AUTH_TOKEN_EXPIRED,
            action_hint="Sign in again",
        )

    user_id = row.get("user_id")
    if not user_id:
        raise TokenExpiredException(
            message="Invalid refresh token",
            message_key=ErrorKeys.AUTH_TOKEN_EXPIRED,
            action_hint="Sign in again",
        )

    user = None
    try:
        user = await db.find_one("users", {"id": user_id})
    except Exception as e:
        # Distinguish "DB unavailable" (503, retry with back-off) from
        # "user not found" (401, prompt re-login). Returning 401 on a DB
        # outage causes clients to wipe their session pointlessly.
        logger.error(f"refresh: user lookup failed for {user_id}: {e}", exc_info=True)
        raise SpinrException(
            message="Service temporarily unavailable, please try again",
            error_code=ErrorCode.DATABASE_ERROR,
            status_code=503,
            message_key=ErrorKeys.SYSTEM_DATABASE,
        ) from e
    if not user:
        raise TokenExpiredException(
            message="Invalid refresh token",
            message_key=ErrorKeys.AUTH_TOKEN_EXPIRED,
            action_hint="Sign in again",
        )

    # PIPEDA: never rotate/mint tokens for a deletion-requested or purged account.
    # (Deletion also revokes refresh tokens, so this is belt-and-suspenders.)
    _enforce_account_active(user)

    user_agent = request.headers.get("user-agent", "")
    client_ip = get_remote_address(request)

    # Rotate: issue a new refresh token and mark the old row as
    # replaced. If the user later presents the old token it'll be
    # revoked_at != null and the lookup returns None.
    new_raw, _, refresh_expires_at = await issue_refresh_token(
        user_id,
        audience=row.get("audience", "rider"),
        user_agent=user_agent,
        ip=client_ip,
        replaces=row.get("id"),
    )

    session_id = user.get("current_session_id") or row.get("user_agent") or ""
    token_version = int(user.get("token_version") or 0)
    access_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_jwt_token(
        user_id,
        user.get("phone", ""),
        session_id=session_id if session_id else None,
        token_version=token_version,
    )

    csrf = generate_csrf_token()
    set_csrf_cookie(
        response,
        csrf,
        secure=settings.ENV == "production",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    # P3: Set HTTP-only cookies instead of returning tokens in response
    try:
        from ..utils.cookie_manager import CookieManager
    except ImportError:
        from utils.cookie_manager import CookieManager
    CookieManager.set_auth_cookie(response, token, ttl_minutes=15)
    CookieManager.set_refresh_cookie(response, new_raw, ttl_days=30)

    # Return tokens in BOTH the JSON body AND cookies.
    # Web clients use the HTTP-only cookies; mobile clients (React Native)
    # read the JSON body because RN's fetch has no browser cookie jar.
    return RefreshResponse(
        token=token,
        refresh_token=new_raw,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        csrf_token=csrf,
    )


def _push_token_columns_to_clear(user: Dict[str, Any], client_type: Optional[str]) -> Dict[str, None]:
    """Which users.fcm_token* columns this logout should null out.

    Mirrors the client_type inference in POST /notifications/register-token so
    a token registered by one surface is detached by the same surface. A
    dual-role user signing out of the driver app must keep fcm_token_rider.

    The legacy generic `fcm_token` is cleared only when it still holds one of
    the values we are clearing — otherwise a driver logout would silently kill
    the rider app's pushes for a dual-role account.
    """
    normalized = client_type if client_type in ("rider", "driver") else None
    if not normalized:
        is_driver = bool(user.get("is_driver", False))
        is_rider = bool(user.get("is_rider", True))
        if is_driver and not is_rider:
            normalized = "driver"
        elif is_rider and not is_driver:
            normalized = "rider"

    columns = ["fcm_token_driver", "fcm_token_rider"] if normalized is None else [f"fcm_token_{normalized}"]
    updates: Dict[str, None] = {col: None for col in columns if user.get(col)}

    generic = user.get("fcm_token")
    if not generic:
        return updates

    if updates:
        # Clear the legacy column only when it still mirrors a surface we are
        # clearing. Otherwise it belongs to the app that is staying signed in,
        # and nulling it would kill that app's pushes for a dual-role account.
        if generic in {user.get(col) for col in columns}:
            updates["fcm_token"] = None
        return updates

    # No per-app column set at all: a row written before migration 102 added
    # them, where `fcm_token` is the only token on file. It is still a live
    # delivery target, so leaving it would defeat the whole point of clearing
    # on logout — but it carries no client_type, so it can only be attributed
    # when the account has a single role.
    if not user.get("is_driver", False) or not user.get("is_rider", True):
        return {"fcm_token": None}

    # Genuinely ambiguous: dual-role account whose only token is the legacy
    # one. Clearing it could silently kill the other app's pushes, so leave it
    # and say so — the next registration from either app writes the per-app
    # column and the ambiguity resolves itself.
    logger.info(
        "logout: leaving legacy fcm_token in place for dual-role user %s (no per-app token to attribute it to)",
        user.get("id"),
    )
    return {}


async def _clear_push_token_on_logout(user: Dict[str, Any], client_type: Optional[str]) -> None:
    """Detach this device's push token so a signed-out app stops receiving pushes.

    Gated on the `logout_clears_push_token` app setting, DEFAULT OFF. Shipped
    dark on purpose: installed rider/driver binaries register their FCM token
    once per app process (a `fcmRegisteredRef` useRef guard in each app's
    _layout.tsx, never reset on sign-out). Against those builds, clearing the
    token here would leave a user who signs out and back in without killing the
    app with no push token at all — for a driver that means missed ride
    offers, so the flag stays off until a build that re-registers on re-login
    has rolled out.

    Best-effort: a failure here must not fail the logout itself (the session is
    already revoked by the time we get here), but it is logged at error level —
    a stale token means the signed-out device keeps receiving pushes.
    """
    try:
        settings = await get_app_settings()
    except Exception:
        logger.error("logout: failed to read app_settings for push-token clear", exc_info=True)
        return
    if not settings.get("logout_clears_push_token", False):
        return

    updates = _push_token_columns_to_clear(user, client_type)
    if not updates:
        return
    try:
        await db.update_one("users", {"id": user["id"]}, updates)
        logger.info(
            "logout: cleared push token columns %s for user %s",
            sorted(updates),
            user["id"],
        )
    except Exception as exc:
        logger.error(
            "logout: failed to clear push token columns %s: %s",
            sorted(updates),
            exc,
            exc_info=True,
        )


@api_router.post("/logout")
@limiter.limit("3/minute")
async def logout(
    request: Request,
    response: Response,
    body: Optional[LogoutRequest] = None,
    current_user: dict = Depends(get_current_user),
    token_session_id: Optional[str] = Depends(get_token_session_id),
):
    """Revoke the presented refresh token.

    Previously a no-op (the endpoint didn't exist). Now stamps
    revoked_at on the row so the refresh token can never be exchanged
    again. The current access token keeps working until its exp; for
    immediate kill use /auth/logout-all.

    P3: Now also clears HTTP-only cookies.
    """
    # P3: Clear HTTP-only cookies
    try:
        from ..utils.cookie_manager import CookieManager
    except ImportError:
        from utils.cookie_manager import CookieManager
    CookieManager.clear_all_cookies(response)

    # Read refresh token from cookie if present
    refresh_token_from_cookie = request.cookies.get("refresh_token")
    if refresh_token_from_cookie:
        await revoke_refresh_token(refresh_token_from_cookie)
    elif body and body.refresh_token:
        # Fallback to body for backwards compatibility
        await revoke_refresh_token(body.refresh_token)

    # Delete the Redis session key so the revocation propagates instantly
    # to all replicas rather than waiting for the access-token TTL.
    if current_user:
        await redis_delete(f"session:{current_user['id']}")
        # Stop server-driven pushes (onboarding reminders, promos) from
        # reaching a device that has signed out. No-op unless the
        # logout_clears_push_token flag is on — see the helper's docstring.
        await _clear_push_token_on_logout(current_user, body.client_type if body else None)
        # Tombstone the signed-out session so opt-in ingest paths can reject the
        # still-valid access token instead of trusting it for the rest of its
        # exp. Absence of the key above is NOT usable for this — it is also
        # absent when it was never written or Redis restarted — so revocation
        # needs positive evidence. Only tombstone when this token still owns
        # users.current_session_id; otherwise another device has since logged in
        # and revoking would take that device's traffic down with it.
        if should_tombstone(token_session_id, current_user.get("current_session_id")):
            await revoke_session(str(token_session_id))
        try:
            import asyncio

            asyncio.create_task(
                _audit_log_user(
                    current_user,
                    "user_logged_out",
                    "users",
                    current_user["id"],
                )
            )
        except Exception:
            logger.error("audit_log write failed for logout event", exc_info=True)
    clear_csrf_cookie(response)
    return {"success": True}


def _revoke_firebase_refresh_tokens(user_id: str) -> None:
    """Best-effort revocation of a user's Firebase refresh tokens on logout-all.

    Forces a real Firebase re-sign-in by invalidating refresh tokens. The
    sessions_invalid_before watermark is the authoritative session-kill; this is
    hardening on top. For OTP/JWT users with no Firebase uid this no-ops with
    UserNotFoundError, which is expected — not an error. Isolated into a
    module-level seam so the (best-effort, Firebase-SDK-dependent) side effect is
    patchable without coupling tests to global firebase_admin module state.
    """
    try:
        from firebase_admin import auth as _firebase_auth  # type: ignore

        _firebase_auth.revoke_refresh_tokens(user_id)
    except Exception as e:
        logger.info(f"logout-all: firebase refresh-token revoke skipped for {user_id}: {type(e).__name__}")


@api_router.post("/logout-all")
@limiter.limit("5/minute")
async def logout_all(request: Request, response: Response, current_user: dict = Depends(get_current_user)):
    """Force-invalidate every session for the caller.

    Bumps ``users.token_version`` so all outstanding access tokens are
    rejected on their next request (the middleware re-reads the row on
    every call), and revokes every non-revoked refresh token for the
    user. This is what "sign out of all devices" / "my account was
    compromised" buttons should call.
    """
    user_id = current_user["id"]
    new_version = int(current_user.get("token_version") or 0) + 1
    # Firebase-authed riders carry no token_version claim, so the watermark
    # (sessions_invalid_before, compared against each token's auth_time on every
    # request) is what revokes their existing Firebase ID tokens.
    invalidate_ts = datetime.now(timezone.utc).isoformat()
    try:
        await db.update_one(
            "users",
            {"id": user_id},
            {"$set": {"token_version": new_version, "sessions_invalid_before": invalidate_ts}},
        )
    except Exception as e:
        logger.error(
            f"logout-all: could not bump token_version for {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Could not invalidate sessions",
        ) from e

    revoked = await revoke_all_for_user(user_id)

    # Revoking Firebase refresh tokens additionally stops the device from
    # minting fresh ID tokens, forcing a real re-sign-in. Best-effort only —
    # the sessions_invalid_before watermark above is the authoritative
    # enforcement (a refreshed ID token keeps its original auth_time, so the
    # watermark rejects it regardless). The Firebase Admin SDK call is
    # synchronous/blocking, so run it in a worker thread to avoid stalling the
    # event loop (and other requests on this worker) if Firebase is slow.
    await asyncio.to_thread(_revoke_firebase_refresh_tokens, user_id)

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

    # P3: Clear HTTP-only cookies
    try:
        from ..utils.cookie_manager import CookieManager
    except ImportError:
        from utils.cookie_manager import CookieManager
    CookieManager.clear_all_cookies(response)

    clear_csrf_cookie(response)
    return {"success": True, "revoked_refresh_tokens": revoked}
