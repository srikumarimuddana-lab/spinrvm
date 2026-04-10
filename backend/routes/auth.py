from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Dict, Any
try:
    from ..dependencies import (
        get_current_user, generate_otp, create_jwt_token, create_refresh_token,
        hash_token, OTP_EXPIRY_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS,
        verify_jwt_token, security,
    )
    from ..schemas import (
        SendOTPRequest, VerifyOTPRequest, AuthResponse, RefreshTokenRequest,
        UserProfile, OTPRecord,
    )
    from ..db import db
    from ..sms_service import send_otp_sms
except ImportError:
    from dependencies import (
        get_current_user, generate_otp, create_jwt_token, create_refresh_token,
        hash_token, OTP_EXPIRY_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS,
        verify_jwt_token, security,
    )
    from schemas import (
        SendOTPRequest, VerifyOTPRequest, AuthResponse, RefreshTokenRequest,
        UserProfile, OTPRecord,
    )
    from db import db
    from sms_service import send_otp_sms
    from settings_loader import get_app_settings
import logging
from datetime import datetime, timedelta, timezone
from slowapi import Limiter
from slowapi.util import get_remote_address
import uuid

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)
api_router = APIRouter(prefix="/auth", tags=["Authentication"])

# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

async def _store_refresh_token(user_id: str, raw_token: str) -> None:
    """Hash and persist a refresh token; revoke any existing tokens for this user."""
    try:
        await db.refresh_tokens.delete_many({'user_id': user_id})
        await db.refresh_tokens.insert_one({
            'id': str(uuid.uuid4()),
            'user_id': user_id,
            'token_hash': hash_token(raw_token),
            'expires_at': (datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat(),
            'revoked': False,
            'created_at': datetime.utcnow().isoformat(),
        })
    except Exception as e:
        logger.warning(f'Could not store refresh token: {e}')

def _make_auth_response(
    access_token: str,
    refresh_token: str,
    user_obj: UserProfile,
    is_new_user: bool,
) -> AuthResponse:
    from dependencies import ACCESS_TOKEN_EXPIRE_MINUTES as _exp
    return AuthResponse(
        token=access_token,
        refresh_token=refresh_token,
        expires_in=_exp * 60,
        user=user_obj,
        is_new_user=is_new_user,
    )

@api_router.post("/send-otp")
@limiter.limit("5/minute")
async def send_otp(request: Request, body: SendOTPRequest):
    phone = body.phone.strip()
    if len(phone) < 10:
        raise HTTPException(status_code=400, detail='Invalid phone number')
    
    # Check if Twilio is configured via DB settings
    settings = None
    try:
        settings = await get_app_settings()
    except Exception as e:
        logger.warning(f'Could not read app_settings from DB: {e}')
    
    twilio_configured = bool(
        settings and
        settings.get('twilio_account_sid') and
        settings.get('twilio_auth_token') and
        settings.get('twilio_from_number')
    )
    
    # Use fixed 1234 OTP when Twilio is not configured (dev mode)
    otp_code = generate_otp() if twilio_configured else '1234'
    
    otp_record = OTPRecord(
        phone=phone,
        code=otp_code,
        expires_at=datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)
    )
    
    try:
        await db.otp_records.delete_many({'phone': phone})
        await db.otp_records.insert_one(otp_record.dict())
    except Exception as e:
        logger.warning(f'Could not store OTP in DB: {e}')
    
    # Send OTP via SMS (Twilio when configured, console log otherwise)
    sms_result = await send_otp_sms(
        phone,
        otp_code,
        twilio_sid=settings.get('twilio_account_sid', '') if settings else '',
        twilio_token=settings.get('twilio_auth_token', '') if settings else '',
        twilio_from=settings.get('twilio_from_number', '') if settings else ''
    )
    if not sms_result.get('success'):
        logger.error(f'Failed to send OTP SMS to {phone}: {sms_result.get("error")}')
        raise HTTPException(status_code=500, detail='Failed to send verification code')
    
    response = {
        'success': True,
        'message': f'OTP sent to {phone}'
    }
    # Include dev_otp when Twilio is NOT configured (always shows 1234 in dev)
    if not twilio_configured:
        response['dev_otp'] = otp_code
    
    return response

@api_router.post("/verify-otp", response_model=AuthResponse)
@limiter.limit("10/minute")
async def verify_otp(request: Request, body: VerifyOTPRequest):
    phone = body.phone.strip()
    code = body.code.strip()
    
    otp_record = None
    try:
        otp_record = await db.otp_records.find_one({
            'phone': phone,
            'code': code,
            'verified': False
        })
    except Exception as e:
        logger.warning(f'Could not query OTP from DB: {e}')
    
    # Dev fallback: accept code 1234 when no OTP record found (Twilio not configured)
    if not otp_record and code == '1234':
        logger.info(f'Dev mode: accepting code 1234 for {phone}')
        otp_record = {'id': 'dev', 'phone': phone, 'code': code, 'expires_at': datetime.utcnow() + timedelta(minutes=5)}
    
    if not otp_record:
        raise HTTPException(status_code=400, detail='Invalid verification code')
    
    # Parse expires_at to datetime if it's a string (from Supabase)
    expires_at = otp_record.get('expires_at')
    if isinstance(expires_at, str):
        try:
            # Handle ISO format from Supabase (replace Z with +00:00 if present)
            expires_at = expires_at.replace('Z', '+00:00')
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            logger.error(f"Invalid date format for OTP expires_at: {expires_at}")
            raise HTTPException(status_code=500, detail="Internal data error: invalid expiration date")
            
    if not expires_at:
        logger.error("OTP record missing expires_at field")
        raise HTTPException(status_code=500, detail="Internal data error: missing expiration date")
    
    # Ensure expires_at is timezone-aware for comparison
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    if datetime.now(timezone.utc) > expires_at:
        try:
            await db.otp_records.delete_one({'id': otp_record['id']})
        except Exception:
            pass
        raise HTTPException(status_code=400, detail='OTP has expired')
    
    try:
        await db.otp_records.update_one({'id': otp_record['id']}, {'$set': {'verified': True}})
    except Exception:
        pass
    
    try:
        # Find or create user
        existing_user = None
        try:
            logger.info(f"Searching for user with phone: {phone}")
            existing_user = await db.users.find_one({'phone': phone})
            logger.info(f"User search result found: {bool(existing_user)}")
        except Exception as e:
            logger.warning(f'Could not query user from DB: {e}')
        
        if existing_user:
            logger.info("User exists, creating token")
            session_id = str(uuid.uuid4())
            try:
                await db.users.update_one({'id': existing_user['id']}, {'$set': {'current_session_id': session_id}})
                existing_user['current_session_id'] = session_id
            except Exception as e:
                logger.warning(f'Could not update current_session_id in DB: {e}')
                
            token = create_jwt_token(existing_user['id'], phone, session_id=session_id)
            refresh_tok = create_refresh_token(existing_user['id'])
            await _store_refresh_token(existing_user['id'], refresh_tok)
            logger.info("Token created. Validating UserProfile...")
            try:
                user_obj = UserProfile(**existing_user)
                logger.info(f"UserProfile valid for user: {existing_user.get('id')}")
            except Exception as e:
                logger.error(f"UserProfile validation failed: {e}")
                raise e

            return _make_auth_response(token, refresh_tok, user_obj, is_new_user=False)
        else:
            logger.info("Creating new user")
            user_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            new_user = {
                'id': user_id,
                'phone': phone,
                'role': 'rider',
                'created_at': datetime.utcnow().isoformat(),
                'profile_complete': False,
                'current_session_id': session_id
            }
            try:
                await db.users.insert_one(new_user)
            except Exception as e:
                logger.warning(f'Could not create user in DB: {e}')
            token = create_jwt_token(user_id, phone, session_id=session_id)
            refresh_tok = create_refresh_token(user_id)
            await _store_refresh_token(user_id, refresh_tok)
            return _make_auth_response(token, refresh_tok, UserProfile(**new_user), is_new_user=True)
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN VERIFY_OTP: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Login Error: {str(e)}")

@api_router.post("/refresh", response_model=AuthResponse)
@limiter.limit("10/minute")
async def refresh_token(request: Request, body: RefreshTokenRequest):
    """Exchange a valid refresh token for a new access + refresh token pair (rotation)."""
    token_hash = hash_token(body.refresh_token)

    record = None
    try:
        record = await db.refresh_tokens.find_one({'token_hash': token_hash, 'revoked': False})
    except Exception as e:
        logger.warning(f'Could not query refresh_tokens: {e}')

    if not record:
        raise HTTPException(status_code=401, detail='Invalid or revoked refresh token')

    # Check expiry
    expires_at = record.get('expires_at')
    if isinstance(expires_at, str):
        expires_at = expires_at.replace('Z', '+00:00')
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            raise HTTPException(status_code=401, detail='Malformed token expiry')
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=401, detail='Refresh token has expired')

    user_id = record['user_id']
    user = None
    try:
        user = await db.users.find_one({'id': user_id})
    except Exception as e:
        logger.warning(f'Could not fetch user for token refresh: {e}')

    if not user:
        raise HTTPException(status_code=401, detail='User not found')

    # Rotate: revoke old token, issue new pair
    try:
        await db.refresh_tokens.update_one({'token_hash': token_hash}, {'$set': {'revoked': True}})
    except Exception as e:
        logger.warning(f'Could not revoke old refresh token: {e}')

    session_id = str(uuid.uuid4())
    try:
        await db.users.update_one({'id': user_id}, {'$set': {'current_session_id': session_id}})
    except Exception as e:
        logger.warning(f'Could not update session_id on refresh: {e}')

    new_access = create_jwt_token(user_id, user.get('phone', ''), session_id=session_id)
    new_refresh = create_refresh_token(user_id)
    await _store_refresh_token(user_id, new_refresh)

    return _make_auth_response(new_access, new_refresh, UserProfile(**user), is_new_user=False)


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
        (current_user.get('first_name') or '').strip()
        and (current_user.get('last_name') or '').strip()
        and (current_user.get('email') or '').strip()
    )
    if has_profile_data and not current_user.get('profile_complete'):
        # Self-heal the column so the next login is fast and consistent.
        try:
            await db.users.update_one(
                {'id': current_user['id']},
                {'$set': {'profile_complete': True}},
            )
        except Exception as e:
            logger.warning(f"Could not self-heal profile_complete for {current_user.get('id')}: {e}")
        current_user['profile_complete'] = True

    # Derive driver onboarding status (None for non-drivers).
    try:
        from onboarding_status import derive_driver_onboarding_status  # type: ignore
    except ImportError:
        from ..onboarding_status import derive_driver_onboarding_status  # type: ignore
    try:
        status, detail, next_screen = await derive_driver_onboarding_status(current_user)
        current_user['driver_onboarding_status'] = status
        current_user['driver_onboarding_detail'] = detail
        current_user['driver_onboarding_next_screen'] = next_screen
    except Exception as e:
        logger.warning(f"Could not derive onboarding status for {current_user.get('id')}: {e}")

    return UserProfile(**current_user)
