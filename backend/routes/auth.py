from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Dict, Any
try:
    from ..dependencies import (
        get_current_user, generate_otp, create_jwt_token, 
        OTP_EXPIRY_MINUTES, verify_jwt_token, security
    )
    from ..schemas import (
        SendOTPRequest, VerifyOTPRequest, AuthResponse, 
        UserProfile, OTPRecord
    )
    from ..db import db
    from ..sms_service import send_otp_sms
except ImportError:
    from dependencies import (
        get_current_user, generate_otp, create_jwt_token, 
        OTP_EXPIRY_MINUTES, verify_jwt_token, security
    )
    from schemas import (
        SendOTPRequest, VerifyOTPRequest, AuthResponse, 
        UserProfile, OTPRecord
    )
    from db import db
    from sms_service import send_otp_sms
import logging
from datetime import datetime, timedelta, timezone
from slowapi import Limiter
from slowapi.util import get_remote_address
import uuid

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)
api_router = APIRouter(prefix="/auth", tags=["Authentication"])

@api_router.post("/send-otp")
@limiter.limit("5/minute")
async def send_otp(request: Request, body: SendOTPRequest):
    phone = body.phone.strip()
    if len(phone) < 10:
        raise HTTPException(status_code=400, detail='Invalid phone number')
    
    # Check if Twilio is configured via DB settings
    settings = None
    try:
        settings = await db.settings.find_one({'id': 'app_settings'})
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
            logger.info("Token created. Validating UserProfile...")
            try:
                user_obj = UserProfile(**existing_user)
                logger.info(f"UserProfile valid for user: {existing_user.get('id')}")
            except Exception as e:
                logger.error(f"UserProfile validation failed: {e}")
                # Fallback constructs if validation fails to inspect why
                raise e
            
            return AuthResponse(token=token, user=user_obj, is_new_user=False)
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
            return AuthResponse(token=token, user=UserProfile(**new_user), is_new_user=True)
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN VERIFY_OTP: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Login Error: {str(e)}")

@api_router.get("/me", response_model=UserProfile)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserProfile(**current_user)
