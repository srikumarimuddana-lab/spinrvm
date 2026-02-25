import os
import jwt
import logging
import random
import string
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin import auth as firebase_auth

try:
    from .db import db
except ImportError:
    from db import db

# Security Configuration
_env = os.environ.get('ENV', 'development')
JWT_SECRET = os.environ.get('JWT_SECRET')
if not JWT_SECRET:
    if _env == 'production':
        # In a real app we might raise error, but to avoid breaking things during migration we'll warn
        logging.warning('JWT_SECRET not set — using insecure dev key.')
    JWT_SECRET = 'spinr-dev-secret-key-NOT-FOR-PRODUCTION'

JWT_ALGORITHM = 'HS256'
OTP_EXPIRY_MINUTES = 5

security = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)

# Helper Functions
def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=4))

def create_jwt_token(user_id: str, phone: str, session_id: str = None) -> str:
    payload = {
        'user_id': user_id,
        'phone': phone,
        'exp': datetime.utcnow() + timedelta(days=30)
    }
    if session_id:
        payload['session_id'] = session_id
        
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    logger.info(f"DEBUG: Created JWT token for user_id={user_id}, session_id={session_id}, JWT_SECRET prefix used: {JWT_SECRET[:10] if JWT_SECRET else 'None'}...")
    return token

def verify_jwt_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Token has expired')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail='Invalid token')

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Resolve the current user using Firebase ID token (preferred) or fallback to legacy JWT."""
    if not credentials:
        raise HTTPException(status_code=401, detail='No authorization token provided')
    token = credentials.credentials

    # First, try Firebase ID token
    try:
        try:
            payload = firebase_auth.verify_id_token(token)
        except Exception:
            payload = None

        if payload:
            uid = payload.get('uid') or payload.get('user_id')
            # Try to find user by Firebase UID
            user = await db.users.find_one({'id': uid})
            if not user:
                # Fallback: try to match by phone number
                phone = payload.get('phone_number')
                if phone:
                    user = await db.users.find_one({'phone': phone})
                # If still not found, create a new user record tied to Firebase UID
                if not user:
                    new_user = {
                        'id': uid,
                        'phone': phone or '',
                        'role': 'rider',
                        'created_at': datetime.utcnow(),
                        'profile_complete': False
                    }
                    await db.users.insert_one(new_user)
                    user = new_user

            if user:
                driver = await db.drivers.find_one({'user_id': user['id']})
                user['is_driver'] = True if driver else False
            return user
    except HTTPException:
        # fall through to try legacy JWT
        pass

    # Fallback: existing JWT behavior
    try:
        payload = verify_jwt_token(token)
        # logger.info(f"JWT Valid. Payload: {payload}")
    except Exception as e:
        logger.error(f"JWT Verification Failed: {e} | Token prefix: {token[:20] if token else 'None'}...")
        logger.error(f"DEBUG: Active JWT_SECRET being used for verification: '{JWT_SECRET}' (length: {len(JWT_SECRET) if JWT_SECRET else 0})")
        raise HTTPException(status_code=401, detail=f'Invalid token: {str(e)}')

    user = None
    try:
        user = await db.users.find_one({'id': payload['user_id']})
    except Exception as e:
        logger.warning(f'Could not look up user from DB: {e}')

    if user:
        # Enforce single-device login: check if the session_id matches the one in DB
        token_session = payload.get('session_id')
        db_session = user.get('current_session_id')
        if db_session and token_session != db_session:
            raise HTTPException(status_code=401, detail='Session expired. Logged in from another device.')

    if not user:
        # User not in DB yet — create them
        user = {
            'id': payload['user_id'],
            'phone': payload.get('phone', ''),
            'role': 'rider',
            'created_at': datetime.utcnow().isoformat(),
            'profile_complete': False,
        }
        try:
            await db.users.insert_one(user)
            logger.info(f'Created new user {user["id"]} from JWT')
        except Exception as e:
            logger.warning(f'Could not insert user into DB: {e}')
        user['is_driver'] = False
        return user

    try:
        driver = await db.drivers.find_one({'user_id': user['id']})
        user['is_driver'] = True if driver else False
    except Exception:
        user['is_driver'] = False
    return user

async def get_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    """Require the caller to be an authenticated admin."""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')
    return current_user

