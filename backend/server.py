from fastapi import FastAPI, APIRouter, HTTPException, Depends, Query, WebSocket, WebSocketDisconnect, UploadFile, File, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
import os
import shutil
import logging
import base64
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
import uuid
import random
import string
from datetime import datetime, timedelta, timezone
import jwt
import math
import json
try:
    from .sms_service import send_otp_sms
except ImportError:
    from sms_service import send_otp_sms
import asyncio

# Firebase admin for auth & messaging
import firebase_admin
from firebase_admin import credentials as firebase_credentials
from firebase_admin import auth as firebase_auth

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

ROOT_DIR = Path(__file__).resolve().parent
# Try loading .env from backend dir, or fallback to current dir
env_path = ROOT_DIR / '.env'
if not env_path.exists():
    env_path = Path.cwd() / '.env'

load_dotenv(env_path)

# Database connection
try:
    from .db import db
except ImportError:
    from db import db

# Security
try:
    from .dependencies import (
        get_current_user,
        get_admin_user,
        create_jwt_token,
        verify_jwt_token,
        generate_otp,
        JWT_SECRET,
        JWT_ALGORITHM,
        OTP_EXPIRY_MINUTES,
        security
    )
except ImportError:
    from dependencies import (
        get_current_user,
        get_admin_user,
        create_jwt_token,
        verify_jwt_token,
        generate_otp,
        JWT_SECRET,
        JWT_ALGORITHM,
        OTP_EXPIRY_MINUTES,
        security
    )

# Firebase initialization (expects JSON service account in env var `FIREBASE_SERVICE_ACCOUNT_JSON`)
FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
if FIREBASE_SERVICE_ACCOUNT_JSON:
    try:
        sa_info = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
        cred = firebase_credentials.Certificate(sa_info)
        try:
            firebase_admin.initialize_app(cred)
        except ValueError:
            # already initialized
            pass
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to initialize Firebase Admin from JSON: {e}")
else:
    # Attempt default initialization (works if GOOGLE_APPLICATION_CREDENTIALS is set)
    try:
        firebase_admin.initialize_app()
    except Exception:
        pass


from contextlib import asynccontextmanager

# Import feature routers
try:
    from .features import support_router, admin_support_router, pricing_router, check_scheduled_rides, calculate_airport_fee, send_push_notification
    from .documents import documents_router, admin_documents_router
except ImportError:
    from features import support_router, admin_support_router, pricing_router, check_scheduled_rides, calculate_airport_fee, send_push_notification
    from documents import documents_router, admin_documents_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start scheduled ride checker background task
    scheduler_task = asyncio.create_task(check_scheduled_rides())
    # Create indexes for location history (idempotent)
    try:
        await db.driver_location_history.create_index([('driver_id', 1), ('timestamp', 1)])
        await db.driver_location_history.create_index([('ride_id', 1), ('timestamp', 1)])
    except Exception as e:
        logger.warning(f"Could not create location history indexes: {e}")
    yield
    # Shutdown: cancel the scheduler
    scheduler_task.cancel()

# Create the main app
app = FastAPI(title="Spinr API", version="1.0.0", lifespan=lifespan)

# CORS Middleware
# CORS Middleware
origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:8081",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:8000",
    "https://spinr-admin.vercel.app",
    "https://spinr-admin-git-main-mkkreddys-projects.vercel.app",
]

# Allow dynamic origins from environment variable
env_origins = os.environ.get("ALLOWED_ORIGINS")
if env_origins:
    origins.extend([origin.strip() for origin in env_origins.split(",")])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Create routers
api_router = APIRouter(prefix="/api")
admin_router = APIRouter(prefix="/api/admin")

# File Upload Handling - Using Supabase Storage or Database
# Documents are stored in Supabase Storage for security

@api_router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to Supabase Storage and return its public URL"""
    try:
        from supabase import create_client
        import os
        import base64
        
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        
        # Read file content
        file_content = await file.read()
        file_ext = os.path.splitext(file.filename or '.jpg')[1]
        content_type = file.content_type or 'application/octet-stream'
        
                # Try Supabase Storage first
        if supabase_url and supabase_key:
            try:
                supabase = create_client(supabase_url, supabase_key)
                
                # Generate unique filename
                unique_filename = f"{uuid.uuid4()}{file_ext}"
                bucket_name = 'driver-documents'
                
                # Check/Create bucket
                try:
                    buckets = supabase.storage.list_buckets()
                    bucket_exists = any(b.name == bucket_name for b in buckets)
                    if not bucket_exists:
                         print(f"Creating bucket: {bucket_name}")
                         supabase.storage.create_bucket(bucket_name, {'public': True})
                except Exception as bucket_error:
                    print(f"Bucket check/create error (ignoring): {bucket_error}")

                # Upload to Supabase Storage
                bucket = supabase.storage.from_(bucket_name)
                response = bucket.upload(
                    unique_filename,
                    file_content,
                    file_options={
                        "content-type": content_type,
                        "upsert": "false"
                    }
                )
                
                # Get public URL
                public_url = f"{supabase_url}/storage/v1/object/public/{bucket_name}/{unique_filename}"
                return {"url": public_url, "filename": unique_filename}
                
            except Exception as storage_error:
                print(f"Supabase Storage error: {storage_error}")
                # Raise the storage error directly so we can debug it
                print(f"Storage failed, attempting DB fallback. Error: {storage_error}")
        
        # Fallback: Store as base64 in database (document_files table)
        # NOTE: User must create 'document_files' table manually in Supabase SQL editor
        file_id = str(uuid.uuid4())
        base64_content = base64.b64encode(file_content).decode('utf-8')
        
        # Store in database
        doc_record = {
            'id': file_id,
            'filename': file.filename or 'document',
            'content_type': content_type,
            'data': base64_content,
            'created_at': datetime.utcnow().isoformat()
        }
        
        try:
            await db.document_files.insert_one(doc_record)
            return {
                "url": f"/api/documents/{file_id}", 
                "filename": file.filename or 'document',
                "storage_type": "database"
            }
        except Exception as db_error:
            print(f"Database error: {db_error}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail='Upload failed - Storage not configured and DB fallback failed. Ensure "document_files" table exists.')
                
    except HTTPException:
        raise
    except Exception as e:
        print(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=f'Upload error: {str(e)}')

@api_router.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    """Retrieve a document from database storage"""
    from fastapi.responses import Response
    
    try:
        # Try fetching from document_files (fallback storage)
        doc = await db.document_files.find_one({'id': doc_id})
        
        if not doc:
            raise HTTPException(status_code=404, detail='Document file not found')
        
        # Decode base64
        file_content = base64.b64decode(doc['data'])
        
        return Response(
            content=file_content,
            media_type=doc.get('content_type', 'application/octet-stream'),
            headers={'Content-Disposition': f'inline; filename="{doc["filename"]}"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Error retrieving document: {str(e)}')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# WebSocket connection manager for real-time tracking
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.driver_locations: Dict[str, Dict] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket connected: {client_id}")
    
    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        logger.info(f"WebSocket disconnected: {client_id}")
    
    async def send_personal_message(self, message: dict, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_json(message)
    
    async def broadcast(self, message: dict):
        for connection in self.active_connections.values():
            await connection.send_json(message)
    
    def update_driver_location(self, driver_id: str, lat: float, lng: float):
        self.driver_locations[driver_id] = {
            'lat': lat,
            'lng': lng,
            'updated_at': datetime.utcnow().isoformat()
        }
    
    def get_driver_location(self, driver_id: str):
        return self.driver_locations.get(driver_id)

manager = ConnectionManager()

# Helper to convert MongoDB documents (Legacy, now a pass-through)
def serialize_doc(doc):
    return doc

# ============ Models ============

class SendOTPRequest(BaseModel):
    phone: str

class VerifyOTPRequest(BaseModel):
    phone: str
    code: str

class CreateProfileRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    gender: str

class UserProfile(BaseModel):
    id: str
    phone: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    gender: Optional[str] = None
    profile_image: Optional[str] = None  # Base64 encoded image
    role: str = 'rider'
    created_at: datetime
    profile_complete: bool = False
    is_driver: bool = False

class OTPRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    phone: str
    code: str
    expires_at: datetime
    verified: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AuthResponse(BaseModel):
    token: str
    user: UserProfile
    is_new_user: bool

class AppSettings(BaseModel):
    id: str = "app_settings"
    google_maps_api_key: str = ""
    stripe_publishable_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    driver_matching_algorithm: str = "nearest"
    min_driver_rating: float = 4.0
    search_radius_km: float = 10.0
    cancellation_fee_admin: float = 0.50  # Admin gets 50 cents
    cancellation_fee_driver: float = 2.50  # Default driver gets $2.50 (rest of $3 total)
    platform_fee_percent: float = 0.0  # 0% commission - driver keeps all fare
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ServiceArea(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    city: str
    polygon: List[Dict[str, float]]
    is_active: bool = True
    is_airport: bool = False
    airport_fee: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)

class VehicleType(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    icon: str
    capacity: int
    image_url: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

class FareConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    service_area_id: str
    vehicle_type_id: str
    base_fare: float
    per_km_rate: float
    per_minute_rate: float
    minimum_fare: float
    booking_fee: float = 2.0
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SavedAddress(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    name: str
    address: str
    lat: float
    lng: float
    icon: str = "location"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Driver(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    name: str
    phone: str
    photo_url: str = ""
    vehicle_type_id: str
    vehicle_make: str
    vehicle_model: str
    vehicle_color: str
    license_plate: str
    city: Optional[str] = None
    
    # Verification & Compliance Fields
    license_number: Optional[str] = None
    license_expiry_date: Optional[datetime] = None
    work_eligibility_expiry_date: Optional[datetime] = None
    vehicle_year: Optional[int] = None
    vehicle_vin: Optional[str] = None
    vehicle_inspection_expiry_date: Optional[datetime] = None
    insurance_expiry_date: Optional[datetime] = None
    background_check_expiry_date: Optional[datetime] = None
    documents: Dict[str, str] = {}  # { "license_front": "url" }
    is_verified: bool = False
    rejection_reason: Optional[str] = None
    submitted_at: Optional[datetime] = None

    rating: float = 5.0
    total_rides: int = 0
    lat: float
    lng: float
    is_online: bool = True
    is_available: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Ride(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rider_id: str
    driver_id: Optional[str] = None
    vehicle_type_id: str
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    distance_km: float
    duration_minutes: int
    base_fare: float
    distance_fare: float = 0.0
    time_fare: float = 0.0
    booking_fee: float = 2.0
    total_fare: float
    tip_amount: float = 0.0
    payment_method: str = "card"
    payment_intent_id: Optional[str] = None
    payment_status: str = "pending"
    status: str = "searching"
    pickup_otp: str = ""
    # Timeline tracking
    ride_requested_at: datetime = Field(default_factory=datetime.utcnow)
    driver_notified_at: Optional[datetime] = None
    driver_accepted_at: Optional[datetime] = None
    driver_arrived_at: Optional[datetime] = None
    ride_started_at: Optional[datetime] = None
    ride_completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    # Earnings split
    driver_earnings: float = 0.0  # Distance fare goes to driver
    admin_earnings: float = 0.0   # Booking fee goes to admin
    cancellation_fee_driver: float = 0.0
    cancellation_fee_admin: float = 0.0
    # Rating
    rider_rating: Optional[int] = None
    rider_comment: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class RideRatingRequest(BaseModel):
    rating: int
    comment: Optional[str] = None
    tip_amount: float = 0.0

# ============ Helper Functions ============

def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=4))

def create_jwt_token(user_id: str, phone: str) -> str:
    payload = {
        'user_id': user_id,
        'phone': phone,
        'exp': datetime.utcnow() + timedelta(days=30)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

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
        logger.info(f"JWT Valid. Payload: {payload}")
    except Exception as e:
        logger.warning(f"JWT Verification Failed: {e}")
        raise HTTPException(status_code=401, detail='Invalid token')

    user = None
    try:
        user = await db.users.find_one({'id': payload['user_id']})
        logger.info(f"DB User Lookup Result: {user}")
    except Exception as e:
        logger.warning(f'Could not look up user from DB: {e}')

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


# (Auth routes with rate limiting are defined below, after WebSocket routes)


@api_router.get("/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Return current user profile (synced from Firebase)."""
    driver_profile = None
    if current_user.get('is_driver'):
        driver_profile = await db.drivers.find_one({'user_id': current_user['id']})
    
    # Return user data with role info
    return {
        **serialize_doc(current_user),
        'driver_profile': serialize_doc(driver_profile) if driver_profile else None
    }

def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def point_in_polygon(lat: float, lng: float, polygon: List[Dict[str, float]]) -> bool:
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        if ((polygon[i]['lng'] > lng) != (polygon[j]['lng'] > lng) and
            lat < (polygon[j]['lat'] - polygon[i]['lat']) * (lng - polygon[i]['lng']) / 
            (polygon[j]['lng'] - polygon[i]['lng']) + polygon[i]['lat']):
            inside = not inside
        j = i
    return inside

# ============ WebSocket Routes ============

@app.websocket("/ws/{client_type}/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_type: str, client_id: str):
    """Require clients to authenticate via a first 'auth' message that contains a Firebase ID token or legacy JWT.

    After successful verification we register the connection as '{client_type}_{user_id}' and proceed to handle messages.
    """
    await websocket.accept()
    authenticated = False
    user = None
    connection_key = None

    try:
        # Require the first message to be an auth message containing a token
        auth_msg = await websocket.receive_json()
        if not auth_msg or auth_msg.get('type') != 'auth' or not auth_msg.get('token'):
            await websocket.send_json({'type': 'error', 'message': 'authentication_required'})
            await websocket.close()
            return

        token = auth_msg.get('token')
        # Try Firebase token first
        try:
            payload = firebase_auth.verify_id_token(token)
            uid = payload.get('uid') or payload.get('user_id')
            user = await db.users.find_one({'id': uid})
            if not user:
                phone = payload.get('phone_number')
                if phone:
                    user = await db.users.find_one({'phone': phone})
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
        except Exception:
            # Fallback to legacy JWT
            try:
                payload = verify_jwt_token(token)
                user = await db.users.find_one({'id': payload['user_id']})
            except Exception:
                user = None

        if not user:
            await websocket.send_json({'type': 'error', 'message': 'invalid_token_or_user_not_found'})
            await websocket.close()
            return

        # If connecting as driver, ensure user has a driver profile
        if client_type == 'driver':
             driver_profile = await db.drivers.find_one({'user_id': user['id']})
             if not driver_profile:
                 await websocket.send_json({'type': 'error', 'message': 'user_is_not_a_driver'})
                 await websocket.close()
                 return

        # Register the connection with a server-controlled key to prevent impersonation
        connection_key = f"{client_type}_{user['id']}"
        await manager.connect(websocket, connection_key)
        authenticated = True

        # Main message loop
        while True:
            data = await websocket.receive_json()

            if data.get('type') in ('driver_location', 'location_update'):
                # Accept both message types for backwards compat
                driver_id = data.get('driver_id')
                lat = data.get('lat')
                lng = data.get('lng')

                # If driver_id not sent, look it up from the authenticated user
                if not driver_id and client_type == 'driver':
                    dp = await db.drivers.find_one({'user_id': user['id']})
                    if dp:
                        driver_id = dp['id']

                # Verify driver ownership
                is_valid_driver = False
                if client_type == 'driver' and driver_id:
                    owned_driver = await db.drivers.find_one({'id': driver_id, 'user_id': user['id']})
                    if owned_driver:
                        is_valid_driver = True

                if driver_id and lat and lng and is_valid_driver:
                    manager.update_driver_location(driver_id, lat, lng)
                    await db.drivers.update_one({'id': driver_id}, {'$set': {'lat': lat, 'lng': lng}})

                    # ── Persist GPS breadcrumb ──────────────────────
                    active_ride = await db.rides.find_one({
                        'driver_id': driver_id,
                        'status': {'$in': ['driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress']}
                    })
                    ride_id = active_ride['id'] if active_ride else None

                    # Determine tracking phase
                    tracking_phase = 'online_idle'
                    if active_ride:
                        status_map = {
                            'driver_assigned': 'navigating_to_pickup',
                            'driver_accepted': 'navigating_to_pickup',
                            'driver_arrived': 'arrived_at_pickup',
                            'in_progress': 'trip_in_progress',
                        }
                        tracking_phase = status_map.get(active_ride.get('status', ''), 'online_idle')

                    breadcrumb = {
                        'id': str(uuid.uuid4()),
                        'driver_id': driver_id,
                        'ride_id': ride_id,
                        'lat': lat,
                        'lng': lng,
                        'speed': data.get('speed'),
                        'heading': data.get('heading'),
                        'accuracy': data.get('accuracy'),
                        'altitude': data.get('altitude'),
                        'tracking_phase': tracking_phase,
                        'timestamp': datetime.utcnow(),
                    }
                    await db.driver_location_history.insert_one(breadcrumb)

                    # Forward to rider in real-time
                    rides = await db.rides.find({
                        'driver_id': driver_id,
                        'status': {'$in': ['driver_assigned', 'driver_arrived', 'in_progress']}
                    }).to_list(10)
                    for ride in rides:
                        await manager.send_personal_message(
                            {
                                'type': 'driver_location_update',
                                'driver_id': driver_id,
                                'lat': lat,
                                'lng': lng,
                                'speed': data.get('speed'),
                                'heading': data.get('heading'),
                            },
                            f"rider_{ride['rider_id']}"
                        )

            elif data.get('type') == 'location_batch':
                # Batch upload of buffered GPS points (offline recovery)
                points = data.get('points', [])
                driver_id = data.get('driver_id')
                if not driver_id and client_type == 'driver':
                    dp = await db.drivers.find_one({'user_id': user['id']})
                    if dp:
                        driver_id = dp['id']
                if driver_id and points and client_type == 'driver':
                    owned = await db.drivers.find_one({'id': driver_id, 'user_id': user['id']})
                    if owned:
                        docs = []
                        for pt in points[:500]:  # cap at 500 points per batch
                            docs.append({
                                'id': str(uuid.uuid4()),
                                'driver_id': driver_id,
                                'ride_id': pt.get('ride_id'),
                                'lat': pt.get('lat'),
                                'lng': pt.get('lng'),
                                'speed': pt.get('speed'),
                                'heading': pt.get('heading'),
                                'accuracy': pt.get('accuracy'),
                                'altitude': pt.get('altitude'),
                                'tracking_phase': pt.get('tracking_phase', 'online_idle'),
                                'timestamp': datetime.fromisoformat(pt['timestamp']) if pt.get('timestamp') else datetime.utcnow(),
                            })
                        if docs:
                            await db.driver_location_history.insert_many(docs)
                        await websocket.send_json({'type': 'location_batch_ack', 'count': len(docs)})

        # Notify driver via WebSocket

            elif data.get('type') == 'ride_status_update':
                ride_id = data.get('ride_id')
                status = data.get('status')
                if ride_id and status:
                    ride = await db.rides.find_one({'id': ride_id})
                    if ride:
                        await manager.send_personal_message(
                            {
                                'type': 'ride_status_changed',
                                'ride_id': ride_id,
                                'status': status
                            },
                            f"rider_{ride['rider_id']}"
        )

        # Notify driver via WebSocket

            elif data.get('type') == 'get_nearby_drivers':
                lat = data.get('lat')
                lng = data.get('lng')
                radius = data.get('radius', 5)  # km
                if lat and lng:
                    drivers = await db.drivers.find({
                        'is_online': True,
                        'is_available': True
                    }).to_list(100)

                    nearby = []
                    for driver in drivers:
                        dist = calculate_distance(lat, lng, driver['lat'], driver['lng'])
                        if dist <= radius:
                            nearby.append({
                                'id': driver['id'],
                                'lat': driver['lat'],
                                'lng': driver['lng'],
                                'vehicle_type_id': driver['vehicle_type_id']
                            })

                    await websocket.send_json({'type': 'nearby_drivers', 'drivers': nearby})

    except WebSocketDisconnect:
        if connection_key:
            manager.disconnect(connection_key)
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        if connection_key:
            manager.disconnect(connection_key)
        try:
            await websocket.close()
        except Exception:
            pass

# ============ Auth Routes ============

@api_router.post("/auth/send-otp")
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

@api_router.post("/auth/verify-otp", response_model=AuthResponse)
@limiter.limit("10/minute")
async def verify_otp(request: Request, body: VerifyOTPRequest):
    phone = body.phone.strip()
    code = body.code.strip()
    
    otp_record = None
    db_available = True
    try:
        otp_record = await db.otp_records.find_one({
            'phone': phone,
            'code': code,
            'verified': False
        })
    except Exception as e:
        logger.warning(f'Could not query OTP from DB: {e}')
        db_available = False
    
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
            print(f"Searching for user with phone: {phone}")
            existing_user = await db.users.find_one({'phone': phone})
            print(f"User search result: {existing_user}")
        except Exception as e:
            logger.warning(f'Could not query user from DB: {e}')
        
        if existing_user:
            print("User exists, creating token")
            token = create_jwt_token(existing_user['id'], phone)
            print("Token created. Validating UserProfile...")
            try:
                user_obj = UserProfile(**existing_user)
                print(f"UserProfile valid: {user_obj}")
            except Exception as e:
                print(f"UserProfile validation failed: {e}")
                # Fallback constructs if validation fails to inspect why
                raise e
            
            return AuthResponse(token=token, user=user_obj, is_new_user=False)
        else:
            print("Creating new user")
            user_id = str(uuid.uuid4())
            new_user = {
                'id': user_id,
                'phone': phone,
                'role': 'rider',
                'created_at': datetime.utcnow().isoformat(),
                'profile_complete': False
            }
            try:
                await db.users.insert_one(new_user)
            except Exception as e:
                logger.warning(f'Could not create user in DB: {e}')
            token = create_jwt_token(user_id, phone)
            return AuthResponse(token=token, user=UserProfile(**new_user), is_new_user=True)
    except Exception as e:
        print(f"CRITICAL ERROR IN VERIFY_OTP: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Login Error: {str(e)}")

@api_router.get("/auth/me", response_model=UserProfile)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserProfile(**current_user)

@api_router.post("/users/profile", response_model=UserProfile)
async def create_profile(request: CreateProfileRequest, current_user: dict = Depends(get_current_user)):
    valid_genders = ['Male', 'Female', 'Other']
    if request.gender not in valid_genders:
        raise HTTPException(status_code=400, detail=f'Gender must be one of: {", ".join(valid_genders)}')
    
    update_data = {
        'first_name': request.first_name.strip(),
        'last_name': request.last_name.strip(),
        'email': request.email.strip().lower(),
        'gender': request.gender,
        'profile_complete': True
    }
    
    await db.users.update_one({'id': current_user['id']}, {'$set': update_data})
    updated_user = await db.users.find_one({'id': current_user['id']})
    return UserProfile(**updated_user)

@api_router.put("/users/profile-image", response_model=UserProfile)
async def upload_profile_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Upload a profile image for the current user (stored as base64 in database)."""
    # Validate file type
    allowed_types = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail='File must be an image (JPEG, PNG, WebP, or GIF)')

    # Validate file size (max 5MB)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail='Image must be smaller than 5MB')
    
    # Convert to base64
    base64_image = base64.b64encode(content).decode('utf-8')
    # Store as data URI
    data_uri = f"data:{file.content_type};base64,{base64_image}"
    
    await db.users.update_one(
        {'id': current_user['id']},
        {'$set': {'profile_image': data_uri}}
    )
    updated_user = await db.users.find_one({'id': current_user['id']})
    return UserProfile(**updated_user)

# ============ Settings Routes ============

@api_router.get("/settings")
async def get_public_settings():
    settings = await db.settings.find_one({'id': 'app_settings'})
    if not settings:
        return {'google_maps_api_key': '', 'stripe_publishable_key': ''}
    return {
        'google_maps_api_key': settings.get('google_maps_api_key', ''),
        'stripe_publishable_key': settings.get('stripe_publishable_key', '')
    }

# ============ Saved Addresses Routes ============

class SavedAddressCreate(BaseModel):
    name: str
    address: str
    lat: float
    lng: float
    icon: str = "location"

@api_router.get("/addresses")
async def get_saved_addresses(current_user: dict = Depends(get_current_user)):
    addresses = await db.saved_addresses.find({'user_id': current_user['id']}).to_list(100)
    return serialize_doc(addresses)

@api_router.post("/addresses")
async def create_saved_address(request: SavedAddressCreate, current_user: dict = Depends(get_current_user)):
    address = SavedAddress(
        user_id=current_user['id'],
        name=request.name,
        address=request.address,
        lat=request.lat,
        lng=request.lng,
        icon=request.icon
    )
    await db.saved_addresses.insert_one(address.dict())
    return address.dict()

@api_router.delete("/addresses/{address_id}")
async def delete_saved_address(address_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.saved_addresses.delete_one({'id': address_id, 'user_id': current_user['id']})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail='Address not found')
    return {'success': True}

# ============ Vehicle Types & Fares Routes ============

@api_router.get("/vehicle-types")
async def get_vehicle_types():
    types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    return serialize_doc(types)

@api_router.get("/fares")
async def get_fares_for_location(lat: float = Query(...), lng: float = Query(...)):
    # Use Python to find matching service area (since DB RPC might be missing/requires PostGIS)
    # areas = await db.rpc('get_service_area_for_point', {'lat': lat, 'lng': lng})
    all_areas = await db.service_areas.find({'is_active': True}).to_list(100)
    matching_area = None
    for area in all_areas:
        # Check if point is inside polygon
        # Ensure polygon is loaded correctly
        poly = area.get('polygon', [])
        if point_in_polygon(lat, lng, poly):
            matching_area = area
            break
    
    if not matching_area:
        vehicle_types = await db.vehicle_types.find({'is_active': True}).to_list(100)
        return [serialize_doc({
            'vehicle_type': vt,
            'base_fare': 3.50,
            'per_km_rate': 1.50,
            'per_minute_rate': 0.25,
            'minimum_fare': 8.00,
            'booking_fee': 2.00
        }) for vt in vehicle_types]
    
    fares = await db.fare_configs.find({
        'service_area_id': matching_area['id'],
        'is_active': True
    }).to_list(100)
    
    vehicle_types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    vt_map = {vt['id']: serialize_doc(vt) for vt in vehicle_types}
    
    result = []
    for fare in fares:
        vt = vt_map.get(fare['vehicle_type_id'])
        if vt:
            result.append({
                'vehicle_type': vt,
                'base_fare': fare['base_fare'],
                'per_km_rate': fare['per_km_rate'],
                'per_minute_rate': fare['per_minute_rate'],
                'minimum_fare': fare['minimum_fare'],
                'booking_fee': fare['booking_fee']
            })
    
    return result

# ============ Stripe Payment Routes ============

@api_router.post("/payments/create-intent")
async def create_payment_intent(request: Dict[str, Any], current_user: dict = Depends(get_current_user)):
    """Create a Stripe payment intent"""
    settings = await db.settings.find_one({'id': 'app_settings'})
    stripe_secret = settings.get('stripe_secret_key', '') if settings else ''
    
    if not stripe_secret:
        # Return mock response if Stripe not configured
        return {
            'client_secret': 'mock_secret_' + str(uuid.uuid4()),
            'payment_intent_id': 'pi_mock_' + str(uuid.uuid4()),
            'mock': True
        }
    
    try:
        import stripe
        stripe.api_key = stripe_secret
        
        amount = int(request.get('amount', 0) * 100)  # Convert to cents
        
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='cad',
            automatic_payment_methods={'enabled': True},
            metadata={
                'user_id': current_user['id'],
                'ride_id': request.get('ride_id', '')
            }
        )
        
        return {
            'client_secret': intent.client_secret,
            'payment_intent_id': intent.id,
            'mock': False
        }
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/payments/confirm")
async def confirm_payment(request: Dict[str, Any], current_user: dict = Depends(get_current_user)):
    """Confirm payment was successful"""
    payment_intent_id = request.get('payment_intent_id')
    ride_id = request.get('ride_id')
    
    if payment_intent_id and payment_intent_id.startswith('pi_mock_'):
        # Mock payment
        if ride_id:
            await db.rides.update_one(
                {'id': ride_id},
                {'$set': {'payment_status': 'paid', 'payment_intent_id': payment_intent_id}}
            )
        return {'status': 'succeeded', 'mock': True}
    
    settings = await db.settings.find_one({'id': 'app_settings'})
    stripe_secret = settings.get('stripe_secret_key', '') if settings else ''
    
    if stripe_secret:
        try:
            import stripe
            stripe.api_key = stripe_secret
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            
            if ride_id:
                await db.rides.update_one(
                    {'id': ride_id},
                    {'$set': {'payment_status': intent.status, 'payment_intent_id': payment_intent_id}}
                )
            
            return {'status': intent.status, 'mock': False}
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    return {'status': 'unknown', 'mock': True}

# ============ Stripe Webhook ============

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for server-side payment confirmation."""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')

    settings = await db.settings.find_one({'id': 'app_settings'})
    webhook_secret = settings.get('stripe_webhook_secret', '') if settings else ''
    stripe_secret = settings.get('stripe_secret_key', '') if settings else ''

    if not webhook_secret:
        logger.warning('stripe_webhook_secret not set in admin settings — webhook verification disabled')
        return {'received': True, 'verified': False}

    if not stripe_secret:
        logger.error('Stripe secret key not configured in app settings')
        raise HTTPException(status_code=500, detail='Stripe not configured')

    try:
        import stripe
        stripe.api_key = stripe_secret
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid payload')
    except Exception as e:
        logger.error(f'Stripe webhook signature verification failed: {e}')
        raise HTTPException(status_code=400, detail='Invalid signature')

    event_type = event.get('type', '')
    data_object = event.get('data', {}).get('object', {})

    if event_type == 'payment_intent.succeeded':
        ride_id = data_object.get('metadata', {}).get('ride_id')
        user_id = data_object.get('metadata', {}).get('user_id')
        payment_intent_id = data_object.get('id')

        if ride_id:
            await db.rides.update_one(
                {'id': ride_id},
                {'$set': {
                    'payment_status': 'paid',
                    'payment_intent_id': payment_intent_id,
                    'paid_at': datetime.utcnow()
                }}
            )
            logger.info(f'Payment confirmed via webhook for ride {ride_id}')

        if user_id:
            await send_push_notification(
                user_id,
                'Payment Confirmed ✅',
                'Your payment has been processed successfully.',
                {'type': 'payment_confirmed', 'ride_id': ride_id or ''}
            )

    elif event_type == 'payment_intent.payment_failed':
        ride_id = data_object.get('metadata', {}).get('ride_id')
        user_id = data_object.get('metadata', {}).get('user_id')
        payment_intent_id = data_object.get('id')
        failure_message = data_object.get('last_payment_error', {}).get('message', 'Payment failed')

        if ride_id:
            await db.rides.update_one(
                {'id': ride_id},
                {'$set': {
                    'payment_status': 'failed',
                    'payment_intent_id': payment_intent_id,
                    'payment_failure_reason': failure_message
                }}
            )
            logger.warning(f'Payment failed for ride {ride_id}: {failure_message}')

        if user_id:
            await send_push_notification(
                user_id,
                'Payment Failed ❌',
                f'Your payment could not be processed: {failure_message}',
                {'type': 'payment_failed', 'ride_id': ride_id or ''}
            )

    else:
        logger.info(f'Unhandled Stripe event type: {event_type}')

    return {'received': True}

# ============ Ride Routes ============

class CreateRideRequest(BaseModel):
    vehicle_type_id: str
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    payment_method: str = "card"

@api_router.post("/rides")
async def create_ride(request: CreateRideRequest, current_user: dict = Depends(get_current_user)):
    distance_km = calculate_distance(
        request.pickup_lat, request.pickup_lng,
        request.dropoff_lat, request.dropoff_lng
    )
    duration_minutes = int(distance_km / 30 * 60) + 5
    
    fares = await get_fares_for_location(request.pickup_lat, request.pickup_lng)
    fare_info = next((f for f in fares if f['vehicle_type']['id'] == request.vehicle_type_id), fares[0] if fares else None)
    
    if not fare_info:
        raise HTTPException(status_code=400, detail='Invalid vehicle type')
    
    distance_fare = fare_info['per_km_rate'] * distance_km
    time_fare = fare_info['per_minute_rate'] * duration_minutes
    booking_fee = fare_info.get('booking_fee', 2.0)
    
    total_fare = fare_info['base_fare'] + distance_fare + time_fare + booking_fee
    total_fare = max(total_fare, fare_info['minimum_fare'])
    
    # Earnings split: Distance fare goes to driver, booking fee goes to admin
    driver_earnings = fare_info['base_fare'] + distance_fare + time_fare
    admin_earnings = booking_fee
    
    ride = Ride(
        rider_id=current_user['id'],
        vehicle_type_id=request.vehicle_type_id,
        pickup_address=request.pickup_address,
        pickup_lat=request.pickup_lat,
        pickup_lng=request.pickup_lng,
        dropoff_address=request.dropoff_address,
        dropoff_lat=request.dropoff_lat,
        dropoff_lng=request.dropoff_lng,
        distance_km=round(distance_km, 2),
        duration_minutes=duration_minutes,
        base_fare=fare_info['base_fare'],
        distance_fare=round(distance_fare, 2),
        time_fare=round(time_fare, 2),
        booking_fee=booking_fee,
        total_fare=round(total_fare, 2),
        driver_earnings=round(driver_earnings, 2),
        admin_earnings=round(admin_earnings, 2),
        payment_method=request.payment_method,
        status='searching',
        pickup_otp=generate_otp(),
        ride_requested_at=datetime.utcnow()
    )
    
    await db.rides.insert_one(ride.dict())
    
    # Match driver
    await match_driver_to_ride(ride.id)
    
    updated_ride = await db.rides.find_one({'id': ride.id})
    return serialize_doc(updated_ride)

async def match_driver_to_ride(ride_id: str):
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        return
    
    settings = await db.settings.find_one({'id': 'app_settings'})
    algorithm = settings.get('driver_matching_algorithm', 'nearest') if settings else 'nearest'
    min_rating = settings.get('min_driver_rating', 4.0) if settings else 4.0
    search_radius = settings.get('search_radius_km', 10.0) if settings else 10.0
    
    # Use PostGIS to find nearby drivers
    # Note: radius in meters for RPC
    try:
        nearby_drivers = await db.rpc('find_nearby_drivers', {
            'lat': ride['pickup_lat'],
            'lng': ride['pickup_lng'],
            'radius_meters': search_radius * 1000
        })
    except Exception as e:
        logger.warning(f"find_nearby_drivers RPC not available in match_driver: {e}")
        nearby_drivers = []
    
    if not nearby_drivers:
        nearby_drivers = []
    
    # Filter by vehicle type (rpc returns all types nearby)
    drivers = [d for d in nearby_drivers if d.get('vehicle_type_id') == ride['vehicle_type_id']]

    if not drivers:
        # Create demo drivers if none found (for testing)
        await create_demo_drivers(ride['vehicle_type_id'], ride['pickup_lat'], ride['pickup_lng'])
        # Try finding again
        try:
            nearby_drivers = await db.rpc('find_nearby_drivers', {
                'lat': ride['pickup_lat'],
                'lng': ride['pickup_lng'],
                'radius_meters': search_radius * 1000
            })
        except Exception as e:
            logger.warning(f"find_nearby_drivers RPC retry failed: {e}")
            nearby_drivers = []
        if not nearby_drivers:
            nearby_drivers = []
        drivers = [d for d in nearby_drivers if d.get('vehicle_type_id') == ride['vehicle_type_id']]
    
    if not drivers:
        return

    if algorithm in ['rating_based', 'combined']:
        # Fetch full driver details for rating if not in RPC (RPC returns basic info)
        # For efficiency we might want to include rating in RPC, but let's assume we need to fetch or trust RPC has it if modified
        # The RPC definition I wrote returns: id, name, vehicle_type_id, lat, lng, distance_meters.
        # It misses 'rating'.
        # I should probably update RPC or fetch details.
        # Let's fetch details for candidates.
        driver_ids = [d['id'] for d in drivers]
        # Fetch full details
        full_drivers = await db.drivers.find({'id': {'$in': driver_ids}}).to_list(len(driver_ids))
        # Filter by rating
        full_drivers = [d for d in full_drivers if d.get('rating', 5.0) >= min_rating]

        # Map distance back
        dist_map = {d['id']: d['distance_meters'] / 1000.0 for d in drivers} # Convert m to km
        drivers_with_distance = []
        for d in full_drivers:
            if d['id'] in dist_map:
                drivers_with_distance.append((d, dist_map[d['id']]))

    else:
        # For 'nearest', we can use the RPC result directly but we need full driver object for assignment logic later?
        # RPC result is partial. Let's fetch full objects.
        driver_ids = [d['id'] for d in drivers]
        full_drivers = await db.drivers.find({'id': {'$in': driver_ids}}).to_list(len(driver_ids))
        dist_map = {d['id']: d['distance_meters'] / 1000.0 for d in drivers}

        drivers_with_distance = []
        for d in full_drivers:
             if d['id'] in dist_map:
                drivers_with_distance.append((d, dist_map[d['id']]))

    if not drivers_with_distance:
        return
    
    selected_driver = None
    
    if algorithm == 'nearest' or algorithm == 'combined':
        drivers_with_distance.sort(key=lambda x: x[1])
        selected_driver = drivers_with_distance[0][0]
    elif algorithm == 'rating_based':
        drivers_with_distance.sort(key=lambda x: x[0].get('rating', 5.0), reverse=True)
        selected_driver = drivers_with_distance[0][0]
    elif algorithm == 'round_robin':
        last_ride = await db.rides.find_one(
            {'driver_id': {'$ne': None}},
            sort=[('created_at', -1)]
        )
        if last_ride:
            last_driver_idx = next(
                (i for i, (d, _) in enumerate(drivers_with_distance) if d['id'] == last_ride['driver_id']),
                -1
            )
            next_idx = (last_driver_idx + 1) % len(drivers_with_distance)
            selected_driver = drivers_with_distance[next_idx][0]
        else:
            selected_driver = drivers_with_distance[0][0]
    elif algorithm == 'rating_based':
        drivers_with_distance.sort(key=lambda x: x[0].get('rating', 5.0), reverse=True)
        selected_driver = drivers_with_distance[0][0]
    elif algorithm == 'round_robin':
        last_ride = await db.rides.find_one(
            {'driver_id': {'$ne': None}},
            sort=[('created_at', -1)]
        )
        if last_ride:
            last_driver_idx = next(
                (i for i, (d, _) in enumerate(drivers_with_distance) if d['id'] == last_ride['driver_id']),
                -1
            )
            next_idx = (last_driver_idx + 1) % len(drivers_with_distance)
            selected_driver = drivers_with_distance[next_idx][0]
        else:
            selected_driver = drivers_with_distance[0][0]
    
    if selected_driver:
        # Attempt to atomically claim the driver (only if still available)
        claim_result = await db.drivers.update_one(
            {'id': selected_driver['id'], 'is_available': True},
            {'$set': {'is_available': False}}
        )

        if claim_result.modified_count == 0:
            # Driver was taken by another process; try to find next candidate
            claimed = False
            for d, _ in drivers_with_distance:
                res = await db.drivers.update_one({'id': d['id'], 'is_available': True}, {'$set': {'is_available': False}})
                if res.modified_count > 0:
                    selected_driver = d
                    claimed = True
                    break
            if not claimed:
                # No drivers could be claimed
                return

        # Update ride with selected driver
        await db.rides.update_one(
            {'id': ride_id},
            {'$set': {
                'driver_id': selected_driver['id'],
                'status': 'driver_assigned',
                'driver_notified_at': datetime.utcnow(),
                'driver_accepted_at': datetime.utcnow(),  # Auto-accept for demo
                'updated_at': datetime.utcnow()
            }}
        )

        # Notify rider via WebSocket
        await manager.send_personal_message(
            {
                'type': 'driver_assigned',
                'ride_id': ride_id,
                'driver_id': selected_driver['id']
            },
            f"rider_{ride['rider_id']}"
        )

        # Notify driver via WebSocket
        if selected_driver.get('user_id'):
             await manager.send_personal_message(
                {
                    'type': 'new_ride_assignment',
                    'ride_id': ride_id,
                    'pickup_address': ride['pickup_address'],
                    'dropoff_address': ride['dropoff_address'],
                    'fare': ride['driver_earnings']
                },
                f"driver_{selected_driver['user_id']}"
            )

async def create_demo_drivers(vehicle_type_id: str, lat: float, lng: float):
    demo_drivers = [
        {'name': 'Mike Johnson', 'vehicle_make': 'Toyota', 'vehicle_model': 'Camry', 'vehicle_color': 'Silver', 'license_plate': 'SKT 4521'},
        {'name': 'Sarah Williams', 'vehicle_make': 'Honda', 'vehicle_model': 'Civic', 'vehicle_color': 'Blue', 'license_plate': 'REG 8832'},
        {'name': 'David Brown', 'vehicle_make': 'Hyundai', 'vehicle_model': 'Elantra', 'vehicle_color': 'White', 'license_plate': 'SKT 7743'},
    ]
    
    for i, dd in enumerate(demo_drivers):
        offset_lat = random.uniform(-0.02, 0.02)
        offset_lng = random.uniform(-0.02, 0.02)
        driver = Driver(
            name=dd['name'],
            phone=f'+1306555{1000+i}',
            vehicle_type_id=vehicle_type_id,
            vehicle_make=dd['vehicle_make'],
            vehicle_model=dd['vehicle_model'],
            vehicle_color=dd['vehicle_color'],
            license_plate=dd['license_plate'],
            rating=round(random.uniform(4.5, 5.0), 1),
            total_rides=random.randint(50, 500),
            lat=lat + offset_lat,
            lng=lng + offset_lng,
            is_online=True,
            is_available=True
        )
        await db.drivers.update_one(
            {'phone': driver.phone},
            {'$set': driver.dict()},
            upsert=True
        )

@api_router.get("/rides/{ride_id}")
async def get_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
    
    driver = None
    if ride.get('driver_id'):
        driver = await db.drivers.find_one({'id': ride['driver_id']})
    
    vehicle_type = await db.vehicle_types.find_one({'id': ride['vehicle_type_id']})
    
    return {
        'ride': serialize_doc(ride),
        'driver': serialize_doc(driver),
        'vehicle_type': serialize_doc(vehicle_type)
    }

@api_router.get("/rides")
async def get_user_rides(current_user: dict = Depends(get_current_user)):
    rides = await db.rides.find({'rider_id': current_user['id']}).sort('created_at', -1).to_list(100)
    return serialize_doc(rides)

@api_router.post("/rides/{ride_id}/cancel")
async def cancel_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    ride = await db.rides.find_one({'id': ride_id, 'rider_id': current_user['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
    
    if ride['status'] in ['completed', 'cancelled']:
        raise HTTPException(status_code=400, detail='Cannot cancel this ride')
    
    # Cannot cancel after ride has started
    if ride['status'] == 'in_progress':
        raise HTTPException(status_code=400, detail='Cannot cancel ride after it has started')
    
    # Get cancellation fee settings
    settings = await db.settings.find_one({'id': 'app_settings'})
    cancellation_fee_admin = settings.get('cancellation_fee_admin', 0.50) if settings else 0.50
    cancellation_fee_driver = settings.get('cancellation_fee_driver', 2.50) if settings else 2.50
    
    update_data = {
        'status': 'cancelled',
        'cancelled_at': datetime.utcnow(),
        'updated_at': datetime.utcnow()
    }
    
    # If driver has arrived at pickup, apply cancellation fee
    if ride['status'] == 'driver_arrived':
        update_data['cancellation_fee_admin'] = cancellation_fee_admin
        update_data['cancellation_fee_driver'] = cancellation_fee_driver
    
    if ride.get('driver_id'):
        await db.drivers.update_one(
            {'id': ride['driver_id']},
            {'$set': {'is_available': True}}
        )
    
    await db.rides.update_one({'id': ride_id}, {'$set': update_data})
    
    return {
        'success': True,
        'cancellation_fee_applied': ride['status'] == 'driver_arrived',
        'cancellation_fee_admin': cancellation_fee_admin if ride['status'] == 'driver_arrived' else 0,
        'cancellation_fee_driver': cancellation_fee_driver if ride['status'] == 'driver_arrived' else 0
    }

@api_router.post("/rides/{ride_id}/simulate-arrival")
async def simulate_driver_arrival(ride_id: str, current_user: dict = Depends(get_current_user)):
    ride = await db.rides.find_one({'id': ride_id, 'rider_id': current_user['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
    
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'driver_arrived',
            'driver_arrived_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )
    
    return {'success': True, 'pickup_otp': ride.get('pickup_otp', '')}

@api_router.post("/rides/{ride_id}/start")
async def start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Start the ride. Driver should start the ride or the rider can start after OTP verification (not implemented here)."""
    # Allow driver to start, or the owning rider (depending on app flow)
    if current_user.get('role') == 'driver':
        ride = await db.rides.find_one({'id': ride_id, 'driver_id': current_user['id']})
    else:
        ride = await db.rides.find_one({'id': ride_id, 'rider_id': current_user['id']})

    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    if ride['status'] != 'driver_arrived':
        raise HTTPException(status_code=400, detail='Driver has not arrived yet')

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'in_progress',
            'ride_started_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )

    return {'success': True}

@api_router.post("/rides/{ride_id}/complete")
async def complete_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Complete the ride — driver should complete the ride. Rider completion is not allowed in production."""
    # If driver, confirm driver is assigned to this ride. If rider, reject by default for security.
    if current_user.get('role') == 'driver':
        ride = await db.rides.find_one({'id': ride_id, 'driver_id': current_user['id']})
    else:
        # Disallow riders from completing rides in production for security; keep behavior restrictive
        raise HTTPException(status_code=403, detail='Only driver can complete the ride')

    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    if ride['status'] != 'in_progress':
        raise HTTPException(status_code=400, detail='Ride is not in progress')

    # Update ride as completed
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'completed',
            'ride_completed_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )

    # Make driver available again
    if ride.get('driver_id'):
        await db.drivers.update_one(
            {'id': ride['driver_id']},
            {
                '$set': {'is_available': True},
                '$inc': {'total_rides': 1}
            }
        )

    updated_ride = await db.rides.find_one({'id': ride_id})
    return serialize_doc(updated_ride)

@api_router.post("/rides/{ride_id}/rate")
async def rate_ride(ride_id: str, rating_data: RideRatingRequest, current_user: dict = Depends(get_current_user)):
    """Rate the ride and optionally add tip"""
    ride = await db.rides.find_one({'id': ride_id, 'rider_id': current_user['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
    
    if ride['status'] != 'completed':
        raise HTTPException(status_code=400, detail='Can only rate completed rides')
    
    if rating_data.rating < 1 or rating_data.rating > 5:
        raise HTTPException(status_code=400, detail='Rating must be between 1 and 5')
    
    # Update ride with rating and tip
    update_data = {
        'rider_rating': rating_data.rating,
        'rider_comment': rating_data.comment,
        'tip_amount': rating_data.tip_amount,
        'updated_at': datetime.utcnow()
    }
    
    # Add tip to driver earnings (100% of tip goes to driver)
    if rating_data.tip_amount > 0:
        update_data['driver_earnings'] = ride.get('driver_earnings', 0) + rating_data.tip_amount
    
    await db.rides.update_one({'id': ride_id}, {'$set': update_data})
    
    # Update driver's average rating
    if ride.get('driver_id'):
        driver = await db.drivers.find_one({'id': ride['driver_id']})
        if driver:
            # Calculate new average rating
            current_rating = driver.get('rating', 5.0)
            total_rides = driver.get('total_rides', 1)
            new_rating = ((current_rating * (total_rides - 1)) + rating_data.rating) / total_rides
            await db.drivers.update_one(
                {'id': ride['driver_id']},
                {'$set': {'rating': round(new_rating, 2)}}
            )
    
    return {'success': True, 'tip_added': rating_data.tip_amount}


# ============ Driver Routes ============

class DriverDocumentInput(BaseModel):
    requirement_id: str
    document_url: str
    side: Optional[str] = None
    document_type: Optional[str] = None

class DriverRegistration(BaseModel):
    # Personal Info
    first_name: str
    last_name: str
    email: EmailStr
    gender: str
    city: str
    # Vehicle Info
    vehicle_make: str
    vehicle_model: str
    vehicle_color: str
    vehicle_year: int
    license_plate: str
    vehicle_vin: str
    vehicle_type_id: str
    # Documents & Dates
    license_number: Optional[str] = None
    license_expiry_date: Optional[str] = None
    work_eligibility_expiry_date: Optional[str] = None
    vehicle_inspection_expiry_date: Optional[str] = None
    insurance_expiry_date: Optional[str] = None
    background_check_expiry_date: Optional[str] = None
    documents: List[DriverDocumentInput]

# Default document requirements for driver registration
DEFAULT_REQUIREMENTS = [
    {
        "id": "driving_license",
        "name": "Driving License",
        "description": "Your valid driver's license",
        "is_mandatory": True,
        "requires_back_side": False
    },
    {
        "id": "vehicle_insurance",
        "name": "Vehicle Insurance",
        "description": "Proof of vehicle insurance",
        "is_mandatory": True,
        "requires_back_side": True
    },
    {
        "id": "vehicle_inspection",
        "name": "Vehicle Inspection",
        "description": "Vehicle inspection certificate",
        "is_mandatory": True,
        "requires_back_side": False
    },
    {
        "id": "background_check",
        "name": "Background Check",
        "description": "Criminal background check",
        "is_mandatory": True,
        "requires_back_side": False
    }
]

@api_router.get("/drivers/requirements")
async def get_driver_requirements():
    """Get document requirements for driver registration."""
    # Try to fetch from database first
    try:
        requirements = await db.document_requirements.find({}).to_list(20)
        if requirements and len(requirements) > 0:
            return requirements
    except:
        pass
    # Return default requirements if database is empty
    return DEFAULT_REQUIREMENTS

@api_router.post("/drivers/register")
async def register_driver(data: DriverRegistration, current_user: dict = Depends(get_current_user)):
    existing = await db.drivers.find_one({'user_id': current_user['id']})
    if existing:
        if existing.get('is_verified'):
            raise HTTPException(status_code=400, detail='User is already a driver')
        else:
            # Cleanup partial/unverified registration to allow retry
            await db.drivers.delete_one({'id': existing['id']})
            # Optional: delete old documents if you want, but likely fine to leave or they use new IDs


    vt = await db.vehicle_types.find_one({'id': data.vehicle_type_id})
    if not vt:
        raise HTTPException(status_code=400, detail='Invalid vehicle type')

    # Parse dates
    try:
        lic_exp = datetime.fromisoformat(data.license_expiry_date.replace('Z', '')) if data.license_expiry_date else None
        
        insp_exp = None
        if data.vehicle_inspection_expiry_date:
            insp_exp = datetime.fromisoformat(data.vehicle_inspection_expiry_date.replace('Z', ''))
            
        ins_exp = None
        if data.insurance_expiry_date:
            ins_exp = datetime.fromisoformat(data.insurance_expiry_date.replace('Z', ''))
            
        bg_exp = None
        if data.background_check_expiry_date:
            bg_exp = datetime.fromisoformat(data.background_check_expiry_date.replace('Z', ''))
            
        work_exp = None
        if data.work_eligibility_expiry_date:
            work_exp = datetime.fromisoformat(data.work_eligibility_expiry_date.replace('Z', ''))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    new_driver = Driver(
        user_id=current_user['id'],
        name=f"{data.first_name} {data.last_name}",
        phone=current_user['phone'],
        vehicle_type_id=data.vehicle_type_id,
        vehicle_make=data.vehicle_make,
        vehicle_model=data.vehicle_model,
        vehicle_color=data.vehicle_color,
        license_plate=data.license_plate,
        
        # New Validation Fields
        license_number=data.license_number,
        license_expiry_date=lic_exp,
        work_eligibility_expiry_date=work_exp,
        vehicle_year=data.vehicle_year,
        vehicle_vin=data.vehicle_vin,
        vehicle_inspection_expiry_date=insp_exp,
        insurance_expiry_date=ins_exp,
        background_check_expiry_date=bg_exp,
        documents={}, # Legacy field empty
        is_verified=False,
        submitted_at=datetime.utcnow(),
        
        rating=5.0,
        lat=0.0,
        lng=0.0,
        city=data.city,
        is_online=False, # Must be verified first
        is_available=False
    )

    await db.drivers.insert_one(new_driver.dict())
    
    # Insert dynamic documents
    for doc in data.documents:
        doc_entry = {
            "id": str(uuid.uuid4()),
            "driver_id": new_driver.id,
            "requirement_id": doc.requirement_id,
            "document_url": doc.document_url,
            "side": doc.side,
            "document_type": doc.document_type or "Unknown",
            "status": "pending",
            "uploaded_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        await db.driver_documents.insert_one(doc_entry)
        
    # Update user profile
    await db.users.update_one(
        {'id': current_user['id']}, 
        {'$set': {
            'role': 'driver', 
            'first_name': data.first_name, 
            'last_name': data.last_name, 
            'email': data.email, 
            'gender': data.gender
        }}
    )

    return new_driver.dict()

@api_router.get("/drivers/me")
async def get_driver_profile(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')
    return serialize_doc(driver)

class DriverUpdate(BaseModel):
    vehicle_make: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_color: Optional[str] = None
    vehicle_year: Optional[int] = None
    license_plate: Optional[str] = None

@api_router.put("/drivers/me")
async def update_driver_profile(data: DriverUpdate, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    update_data = {k: v for k, v in data.dict().items() if v is not None}
    
    if not update_data:
        return serialize_doc(driver)

    # If critical vehicle info changes, reset verification
    critical_fields = ['vehicle_make', 'vehicle_model', 'license_plate', 'vehicle_year']
    if any(field in update_data for field in critical_fields):
        update_data['is_verified'] = False
        update_data['is_online'] = False
        update_data['is_available'] = False
        update_data['rejection_reason'] = None # Clear previous rejection if any

    update_data['updated_at'] = datetime.utcnow()

    await db.drivers.update_one(
        {'id': driver['id']},
        {'$set': update_data}
    )

    updated_driver = await db.drivers.find_one({'id': driver['id']})
    return serialize_doc(updated_driver)

class DocumentUpload(BaseModel):
    requirement_id: str
    document_url: str
    side: Optional[str] = None
    document_type: Optional[str] = None

@api_router.post("/drivers/documents")
async def upload_driver_document(data: DocumentUpload, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    # Check if document exists for this requirement and side
    query = {
        'driver_id': driver['id'],
        'requirement_id': data.requirement_id
    }
    if data.side:
        query['side'] = data.side

    existing = await db.driver_documents.find_one(query)

    doc_entry = {
        "driver_id": driver['id'],
        "requirement_id": data.requirement_id,
        "document_url": data.document_url,
        "side": data.side,
        "document_type": data.document_type or "Unknown",
        "status": "pending",
        "rejection_reason": None,
        "uploaded_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }

    if existing:
        await db.driver_documents.update_one(
            {'_id': existing['_id']},
            {'$set': doc_entry}
        )
    else:
        doc_entry['id'] = str(uuid.uuid4())
        await db.driver_documents.insert_one(doc_entry)
    
    # Reset driver verification status to pending since documents changed
    await db.drivers.update_one(
        {'id': driver['id']},
        {'$set': {
            'is_verified': False, 
            'is_online': False,
            'is_available': False,
            'rejection_reason': None
        }}
    )

    return {'success': True, 'message': 'Document uploaded and pending review'}

@api_router.get("/drivers/documents")
async def get_my_documents(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')
    
    docs = await db.driver_documents.find({'driver_id': driver['id']}).to_list(100)
    return serialize_doc(docs)

@api_router.post("/drivers/status")
async def update_driver_status(is_online: bool = Query(...), current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    if is_online and not driver.get('is_verified'):
         raise HTTPException(status_code=403, detail='Account not verified. Please complete your profile and wait for approval.')

    await db.drivers.update_one(
        {'id': driver['id']},
        {'$set': {'is_online': is_online, 'updated_at': datetime.utcnow()}}
    )
    return {'success': True, 'is_online': is_online}

@api_router.post("/admin/drivers/{driver_id}/verify")
async def verify_driver(
    driver_id: str,
    verification_data: dict = Body(...),
    current_user: dict = Depends(get_admin_user)
):
    """Verify or reject a driver application (admin only)."""
    driver = await db.drivers.find_one({'id': driver_id})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    
    is_verified = verification_data.get('is_verified', False)
    rejection_reason = verification_data.get('rejection_reason', None)
    
    update_data = {
        'is_verified': is_verified,
        'updated_at': datetime.utcnow()
    }
    
    if not is_verified and rejection_reason:
        update_data['rejection_reason'] = rejection_reason
    elif is_verified:
        update_data['rejection_reason'] = None
    
    await db.drivers.update_one(
        {'id': driver_id},
        {'$set': update_data}
    )
    
    return {'success': True, 'is_verified': is_verified}

@api_router.post("/drivers/push-token")
async def update_push_token(push_data: dict, current_user: dict = Depends(get_current_user)):
    """Register push notification token for the driver."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    push_token = push_data.get('push_token')
    platform = push_data.get('platform', 'unknown')

    if push_token:
        await db.drivers.update_one(
            {'id': driver['id']},
            {'$set': {'push_token': push_token, 'push_platform': platform, 'updated_at': datetime.utcnow()}}
        )
        return {'success': True}

    return {'success': False, 'error': 'No push token provided'}

@api_router.get("/drivers/rides/pending")
async def get_pending_rides(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    rides = await db.rides.find({
        'driver_id': driver['id'],
        'status': {'$in': ['driver_assigned', 'driver_arrived', 'in_progress']}
    }).to_list(10)
    return serialize_doc(rides)

@api_router.get("/drivers/rides/active")
async def get_active_ride(current_user: dict = Depends(get_current_user)):
    """Get the driver's current active ride (if any)."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    ride = await db.rides.find_one({
        'driver_id': driver['id'],
        'status': {'$in': ['driver_assigned', 'driver_arrived', 'in_progress']}
    })
    if not ride:
        return None

    # Enrich with rider info
    rider = await db.users.find_one({'id': ride['rider_id']})
    vehicle_type = await db.vehicle_types.find_one({'id': ride['vehicle_type_id']})
    return {
        'ride': serialize_doc(ride),
        'rider': serialize_doc(rider) if rider else None,
        'vehicle_type': serialize_doc(vehicle_type) if vehicle_type else None
    }

@api_router.get("/drivers/rides/history")
async def get_driver_ride_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Get completed/cancelled ride history for the driver."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    rides = await db.rides.find({
        'driver_id': driver['id'],
        'status': {'$in': ['completed', 'cancelled']}
    }).sort('created_at', -1).skip(offset).to_list(limit)

    total = await db.rides.count_documents({
        'driver_id': driver['id'],
        'status': {'$in': ['completed', 'cancelled']}
    })

    return {'rides': serialize_doc(rides), 'total': total, 'limit': limit, 'offset': offset}

@api_router.post("/drivers/rides/{ride_id}/accept")
async def driver_accept_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Driver accepts a ride assignment."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found or not assigned to you')

    if ride['status'] != 'driver_assigned':
        raise HTTPException(status_code=400, detail=f"Cannot accept ride in status '{ride['status']}'")

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'driver_accepted',
            'driver_accepted_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )

    # Notify rider via WebSocket
    await manager.send_personal_message(
        {'type': 'driver_accepted', 'ride_id': ride_id, 'driver_id': driver['id']},
        f"rider_{ride['rider_id']}"
    )

    # Push notification to rider
    try:
        await send_push_notification(
            ride['rider_id'],
            'Driver Accepted! 🚗',
            f"{driver.get('name', 'Your driver')} is on the way to pick you up.",
            {'type': 'driver_accepted', 'ride_id': ride_id}
        )
    except Exception as e:
        logger.warning(f"Push notification failed: {e}")

    updated_ride = await db.rides.find_one({'id': ride_id})
    return serialize_doc(updated_ride)

@api_router.post("/drivers/rides/{ride_id}/decline")
async def driver_decline_ride(ride_id: str, reason: str = Query(''), current_user: dict = Depends(get_current_user)):
    """Driver declines a ride. Ride goes back to searching for another driver."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found or not assigned to you')

    if ride['status'] not in ['driver_assigned']:
        raise HTTPException(status_code=400, detail='Cannot decline this ride')

    # Release driver
    await db.drivers.update_one(
        {'id': driver['id']},
        {'$set': {'is_available': True}}
    )

    # Put ride back to searching
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'driver_id': None,
            'status': 'searching',
            'driver_notified_at': None,
            'driver_accepted_at': None,
            'updated_at': datetime.utcnow()
        }}
    )

    # Try to match another driver
    await match_driver_to_ride(ride_id)

    return {'success': True, 'message': 'Ride declined, searching for another driver'}

@api_router.post("/drivers/rides/{ride_id}/arrive")
async def driver_arrive_at_pickup(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Driver marks arrival at pickup location."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    if ride['status'] not in ['driver_assigned', 'driver_accepted']:
        raise HTTPException(status_code=400, detail=f"Cannot mark arrival in status '{ride['status']}'")

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'driver_arrived',
            'driver_arrived_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )

    # Notify rider
    await manager.send_personal_message(
        {'type': 'driver_arrived', 'ride_id': ride_id, 'pickup_otp': ride.get('pickup_otp', '')},
        f"rider_{ride['rider_id']}"
    )

    try:
        await send_push_notification(
            ride['rider_id'],
            'Driver Has Arrived! 📍',
            f"Your driver is at the pickup. PIN: {ride.get('pickup_otp', 'N/A')}",
            {'type': 'driver_arrived', 'ride_id': ride_id}
        )
    except Exception as e:
        logger.warning(f"Push notification failed: {e}")

    return {'success': True, 'pickup_otp': ride.get('pickup_otp', '')}

class VerifyOTPBody(BaseModel):
    otp: str

@api_router.post("/drivers/rides/{ride_id}/verify-otp")
async def driver_verify_otp(ride_id: str, body: VerifyOTPBody, current_user: dict = Depends(get_current_user)):
    """Driver verifies the rider's OTP and starts the trip."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    if ride['status'] != 'driver_arrived':
        raise HTTPException(status_code=400, detail='Driver has not arrived yet')

    if ride.get('pickup_otp', '') != body.otp:
        raise HTTPException(status_code=400, detail='Invalid OTP')

    # OTP verified — start the ride
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'in_progress',
            'ride_started_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )

    # Notify rider ride started
    await manager.send_personal_message(
        {'type': 'ride_started', 'ride_id': ride_id},
        f"rider_{ride['rider_id']}"
    )

    return {'success': True, 'message': 'OTP verified, ride started'}

@api_router.post("/drivers/rides/{ride_id}/start")
async def driver_start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Driver starts the ride (without OTP, for flexibility)."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    if ride['status'] != 'driver_arrived':
        raise HTTPException(status_code=400, detail='Driver has not arrived yet')

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'in_progress',
            'ride_started_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )

    await manager.send_personal_message(
        {'type': 'ride_started', 'ride_id': ride_id},
        f"rider_{ride['rider_id']}"
    )

    return {'success': True}

@api_router.post("/drivers/rides/{ride_id}/complete")
async def driver_complete_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Driver completes the ride."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    if ride['status'] != 'in_progress':
        raise HTTPException(status_code=400, detail='Ride is not in progress')

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'completed',
            'ride_completed_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )

    # Make driver available again and increment total rides
    await db.drivers.update_one(
        {'id': driver['id']},
        {'$set': {'is_available': True}, '$inc': {'total_rides': 1}}
    )

    # Notify rider ride completed
    await manager.send_personal_message(
        {
            'type': 'ride_completed',
            'ride_id': ride_id,
            'total_fare': ride.get('total_fare', 0),
            'driver_earnings': ride.get('driver_earnings', 0)
        },
        f"rider_{ride['rider_id']}"
    )

    try:
        await send_push_notification(
            ride['rider_id'],
            'Ride Completed! ✅',
            f"You've arrived! Total fare: ${ride.get('total_fare', 0):.2f}",
            {'type': 'ride_completed', 'ride_id': ride_id}
        )
    except Exception as e:
        logger.warning(f"Push notification failed: {e}")

    updated_ride = await db.rides.find_one({'id': ride_id})
    return serialize_doc(updated_ride)

@api_router.post("/drivers/rides/{ride_id}/cancel")
async def driver_cancel_ride(ride_id: str, reason: str = Query(''), current_user: dict = Depends(get_current_user)):
    """Driver cancels a ride."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    if ride['status'] in ['completed', 'cancelled']:
        raise HTTPException(status_code=400, detail='Cannot cancel this ride')

    if ride['status'] == 'in_progress':
        raise HTTPException(status_code=400, detail='Cannot cancel ride after it has started')

    # Release driver
    await db.drivers.update_one(
        {'id': driver['id']},
        {'$set': {'is_available': True}}
    )

    # Cancel the ride
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'cancelled',
            'cancelled_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )

    # Notify rider
    await manager.send_personal_message(
        {'type': 'ride_cancelled', 'ride_id': ride_id, 'cancelled_by': 'driver', 'reason': reason},
        f"rider_{ride['rider_id']}"
    )

    try:
        await send_push_notification(
            ride['rider_id'],
            'Ride Cancelled',
            'Your driver has cancelled the ride. We\'re searching for a new driver.',
            {'type': 'ride_cancelled', 'ride_id': ride_id}
        )
    except Exception as e:
        logger.warning(f"Push notification failed: {e}")

    # Try to match a new driver
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {'driver_id': None, 'status': 'searching', 'updated_at': datetime.utcnow()}}
    )
    await match_driver_to_ride(ride_id)

    return {'success': True}

# ============ Driver Earnings Routes ============

@api_router.get("/drivers/earnings")
async def get_driver_earnings(
    period: str = Query('today', regex='^(today|week|month|all)$'),
    current_user: dict = Depends(get_current_user)
):
    """Get driver earnings summary for a given period."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    now = datetime.utcnow()
    date_filter = {}
    if period == 'today':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        date_filter = {'ride_completed_at': {'$gte': start}}
    elif period == 'week':
        from datetime import timedelta
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        date_filter = {'ride_completed_at': {'$gte': start}}
    elif period == 'month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        date_filter = {'ride_completed_at': {'$gte': start}}

    query = {
        'driver_id': driver['id'],
        'status': 'completed',
        **date_filter
    }

    rides = await db.rides.find(query).sort('ride_completed_at', -1).to_list(500)

    total_earnings = sum(r.get('driver_earnings', 0) for r in rides)
    total_tips = sum(r.get('tip_amount', 0) for r in rides)
    total_rides = len(rides)
    total_distance = sum(r.get('distance_km', 0) for r in rides)
    total_duration = sum(r.get('duration_minutes', 0) for r in rides)

    return {
        'period': period,
        'total_earnings': round(total_earnings, 2),
        'total_tips': round(total_tips, 2),
        'total_rides': total_rides,
        'total_distance_km': round(total_distance, 2),
        'total_duration_minutes': total_duration,
        'average_per_ride': round(total_earnings / total_rides, 2) if total_rides > 0 else 0
    }

@api_router.get("/drivers/earnings/daily")
async def get_driver_daily_earnings(
    days: int = Query(7, ge=1, le=30),
    current_user: dict = Depends(get_current_user)
):
    """Get daily earnings breakdown for the last N days."""
    from datetime import timedelta
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    now = datetime.utcnow()
    start = now - timedelta(days=days)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)

    rides = await db.rides.find({
        'driver_id': driver['id'],
        'status': 'completed',
        'ride_completed_at': {'$gte': start}
    }).to_list(500)

    # Group by day
    daily = {}
    for r in rides:
        completed = r.get('ride_completed_at')
        if completed:
            day_key = completed.strftime('%Y-%m-%d')
            if day_key not in daily:
                daily[day_key] = {'date': day_key, 'earnings': 0, 'tips': 0, 'rides': 0, 'distance_km': 0}
            daily[day_key]['earnings'] += r.get('driver_earnings', 0)
            daily[day_key]['tips'] += r.get('tip_amount', 0)
            daily[day_key]['rides'] += 1
            daily[day_key]['distance_km'] += r.get('distance_km', 0)

    # Fill missing days
    result = []
    for i in range(days):
        day = (start + timedelta(days=i)).strftime('%Y-%m-%d')
        if day in daily:
            entry = daily[day]
            entry['earnings'] = round(entry['earnings'], 2)
            entry['tips'] = round(entry['tips'], 2)
            entry['distance_km'] = round(entry['distance_km'], 2)
            result.append(entry)
        else:
            result.append({'date': day, 'earnings': 0, 'tips': 0, 'rides': 0, 'distance_km': 0})

    return result

@api_router.get("/drivers/earnings/trips")
async def get_driver_trip_earnings(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Get per-trip earnings for completed rides."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver profile not found')

    rides = await db.rides.find({
        'driver_id': driver['id'],
        'status': 'completed'
    }).sort('ride_completed_at', -1).skip(offset).to_list(limit)

    trips = []
    for r in rides:
        trips.append({
            'ride_id': r['id'],
            'pickup_address': r.get('pickup_address', ''),
            'dropoff_address': r.get('dropoff_address', ''),
            'distance_km': r.get('distance_km', 0),
            'duration_minutes': r.get('duration_minutes', 0),
            'base_fare': r.get('base_fare', 0),
            'distance_fare': r.get('distance_fare', 0),
            'time_fare': r.get('time_fare', 0),
            'driver_earnings': r.get('driver_earnings', 0),
            'tip_amount': r.get('tip_amount', 0),
            'rider_rating': r.get('rider_rating'),
            'completed_at': r.get('ride_completed_at')
        })

    return trips

# ============ Driver Rates Rider ============

class DriverRateRiderBody(BaseModel):
    rating: int
    comment: str = ''

@api_router.post("/drivers/rides/{ride_id}/rate-rider")
async def driver_rate_rider(ride_id: str, body: DriverRateRiderBody, current_user: dict = Depends(get_current_user)):
    """Driver rates a rider after completing a ride."""
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail='Rating must be between 1 and 5')

    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=403, detail='Not a driver')

    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
    if ride.get('driver_id') != driver['id']:
        raise HTTPException(status_code=403, detail='Not your ride')
    if ride.get('status') != 'completed':
        raise HTTPException(status_code=400, detail='Ride must be completed to rate')
    if ride.get('driver_rated_rider'):
        raise HTTPException(status_code=400, detail='Already rated this rider')

    # Update ride with driver's rating of rider
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'driver_rating_of_rider': body.rating,
            'driver_comment_on_rider': body.comment,
            'driver_rated_rider': True,
            'updated_at': datetime.utcnow()
        }}
    )

    # Update rider's average rating
    rider_id = ride.get('rider_id')
    if rider_id:
        rider_rides = await db.rides.find({
            'rider_id': rider_id,
            'driver_rated_rider': True
        }).to_list(1000)
        ratings = [r.get('driver_rating_of_rider', 0) for r in rider_rides if r.get('driver_rating_of_rider')]
        if ratings:
            avg_rating = round(sum(ratings) / len(ratings), 2)
            await db.users.update_one(
                {'id': rider_id},
                {'$set': {'rider_rating': avg_rating}}
            )

    return {'success': True, 'message': 'Rider rated successfully'}

# ============ Tip Routes ============

class TipRequest(BaseModel):
    amount: float

@api_router.post("/rides/{ride_id}/tip")
async def add_tip(ride_id: str, tip_data: TipRequest, current_user: dict = Depends(get_current_user)):
    """Add a tip to a completed ride."""
    if tip_data.amount <= 0:
        raise HTTPException(status_code=400, detail='Tip amount must be positive')

    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
    
    # Verify the user is the rider of this ride
    if ride.get('rider_id') != current_user['id']:
        raise HTTPException(status_code=403, detail='Not authorized to tip this ride')
    
    if ride.get('status') != 'completed':
        raise HTTPException(status_code=400, detail='Can only tip completed rides')

    # Update the tip amount
    current_tip = ride.get('tip_amount', 0)
    new_tip = current_tip + tip_data.amount
    
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'tip_amount': new_tip,
            'updated_at': datetime.utcnow()
        }}
    )

    # Update driver earnings (tip goes to driver)
    driver_id = ride.get('driver_id')
    if driver_id:
        driver = await db.drivers.find_one({'id': driver_id})
        if driver:
            current_earnings = driver.get('total_earnings', 0)
            await db.drivers.update_one(
                {'id': driver_id},
                {'$set': {'total_earnings': current_earnings + tip_data.amount}}
            )

    return {'success': True, 'tip_amount': new_tip}

# ============ Driver Location Trail Routes ============

@api_router.get("/drivers/location-history")
async def get_driver_location_history(
    start: str = Query(None, description='ISO date start'),
    end: str = Query(None, description='ISO date end'),
    ride_id: str = Query(None, description='Filter by ride'),
    limit: int = Query(500, ge=1, le=5000),
    current_user: dict = Depends(get_current_user)
):
    """Driver's own GPS breadcrumb trail with date range and ride filter."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=403, detail='Not a driver')

    query: dict = {'driver_id': driver['id']}
    if ride_id:
        query['ride_id'] = ride_id
    if start or end:
        ts_filter: dict = {}
        if start:
            ts_filter['$gte'] = datetime.fromisoformat(start)
        if end:
            ts_filter['$lte'] = datetime.fromisoformat(end)
        if ts_filter:
            query['timestamp'] = ts_filter

    points = await db.driver_location_history.find(query).sort('timestamp', 1).to_list(limit)
    return {
        'points': serialize_doc(points),
        'count': len(points),
    }


@api_router.get("/rides/{ride_id}/location-trail")
async def get_ride_location_trail(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Location trail for a specific ride — accessible by the ride's driver or rider."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    # Allow both the driver and rider of this ride to view the trail
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    is_driver = driver and ride.get('driver_id') == driver['id']
    is_rider = ride.get('rider_id') == current_user['id']
    if not is_driver and not is_rider:
        raise HTTPException(status_code=403, detail='Not authorized to view this trail')

    points = await db.driver_location_history.find(
        {'ride_id': ride_id}
    ).sort('timestamp', 1).to_list(5000)

    return {
        'ride_id': ride_id,
        'points': serialize_doc(points),
        'count': len(points),
    }


@admin_router.get("/drivers/{driver_id}/location-trail")
async def admin_get_driver_location_trail(
    driver_id: str,
    start: str = Query(None),
    end: str = Query(None),
    ride_id: str = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
):
    """Admin endpoint to view any driver's GPS trail."""
    query: dict = {'driver_id': driver_id}
    if ride_id:
        query['ride_id'] = ride_id
    if start or end:
        ts_filter: dict = {}
        if start:
            ts_filter['$gte'] = datetime.fromisoformat(start)
        if end:
            ts_filter['$lte'] = datetime.fromisoformat(end)
        if ts_filter:
            query['timestamp'] = ts_filter

    points = await db.driver_location_history.find(query).sort('timestamp', 1).to_list(limit)
    return {
        'driver_id': driver_id,
        'points': serialize_doc(points),
        'count': len(points),
    }


class LocationBatchRequest(BaseModel):
    points: list


@api_router.post("/drivers/location-batch")
async def post_location_batch(body: LocationBatchRequest, current_user: dict = Depends(get_current_user)):
    """REST fallback for batch-uploading buffered GPS points when WS is unavailable."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=403, detail='Not a driver')

    docs = []
    for pt in body.points[:500]:
        docs.append({
            'id': str(uuid.uuid4()),
            'driver_id': driver['id'],
            'ride_id': pt.get('ride_id'),
            'lat': pt.get('lat'),
            'lng': pt.get('lng'),
            'speed': pt.get('speed'),
            'heading': pt.get('heading'),
            'accuracy': pt.get('accuracy'),
            'altitude': pt.get('altitude'),
            'tracking_phase': pt.get('tracking_phase', 'online_idle'),
            'timestamp': datetime.fromisoformat(pt['timestamp']) if pt.get('timestamp') else datetime.utcnow(),
        })

    if docs:
        await db.driver_location_history.insert_many(docs)

    return {'success': True, 'count': len(docs)}

# ============ Admin Routes ============

@admin_router.get("/settings")
async def admin_get_settings():
    settings = await db.settings.find_one({'id': 'app_settings'})
    if not settings:
        default_settings = AppSettings()
        await db.settings.insert_one(default_settings.dict())
        return default_settings.dict()
    return serialize_doc(settings)

@admin_router.put("/settings")
async def admin_update_settings(settings: Dict[str, Any]):
    settings['id'] = 'app_settings'
    settings['updated_at'] = datetime.utcnow()
    await db.settings.update_one(
        {'id': 'app_settings'},
        {'$set': settings},
        upsert=True
    )
    return serialize_doc(await db.settings.find_one({'id': 'app_settings'}))

@admin_router.get("/service-areas")
async def admin_get_service_areas():
    areas = await db.service_areas.find().to_list(100)
    return serialize_doc(areas)

@admin_router.post("/service-areas")
async def admin_create_service_area(area: Dict[str, Any]):
    new_area = ServiceArea(**area)
    await db.service_areas.insert_one(new_area.dict())
    return new_area.dict()

@admin_router.put("/service-areas/{area_id}")
async def admin_update_service_area(area_id: str, area: Dict[str, Any]):
    area['id'] = area_id
    await db.service_areas.update_one({'id': area_id}, {'$set': area})
    return serialize_doc(await db.service_areas.find_one({'id': area_id}))

@admin_router.delete("/service-areas/{area_id}")
async def admin_delete_service_area(area_id: str):
    await db.service_areas.delete_one({'id': area_id})
    return {'success': True}

@admin_router.get("/vehicle-types")
async def admin_get_vehicle_types():
    types = await db.vehicle_types.find().to_list(100)
    return serialize_doc(types)

@admin_router.post("/vehicle-types")
async def admin_create_vehicle_type(vtype: Dict[str, Any]):
    new_type = VehicleType(**vtype)
    await db.vehicle_types.insert_one(new_type.dict())
    return new_type.dict()

@admin_router.put("/vehicle-types/{type_id}")
async def admin_update_vehicle_type(type_id: str, vtype: Dict[str, Any]):
    vtype['id'] = type_id
    await db.vehicle_types.update_one({'id': type_id}, {'$set': vtype})
    return serialize_doc(await db.vehicle_types.find_one({'id': type_id}))

@admin_router.delete("/vehicle-types/{type_id}")
async def admin_delete_vehicle_type(type_id: str):
    await db.vehicle_types.delete_one({'id': type_id})
    return {'success': True}

@admin_router.get("/fare-configs")
async def admin_get_fare_configs():
    configs = await db.fare_configs.find().to_list(100)
    return serialize_doc(configs)

@admin_router.post("/fare-configs")
async def admin_create_fare_config(config: Dict[str, Any]):
    new_config = FareConfig(**config)
    await db.fare_configs.insert_one(new_config.dict())
    return new_config.dict()

@admin_router.put("/fare-configs/{config_id}")
async def admin_update_fare_config(config_id: str, config: Dict[str, Any]):
    config['id'] = config_id
    await db.fare_configs.update_one({'id': config_id}, {'$set': config})
    return serialize_doc(await db.fare_configs.find_one({'id': config_id}))

@admin_router.delete("/fare-configs/{config_id}")
async def admin_delete_fare_config(config_id: str):
    await db.fare_configs.delete_one({'id': config_id})
    return {'success': True}

@admin_router.get("/drivers")
async def admin_get_drivers():
    drivers = await db.drivers.find().to_list(100)
    return serialize_doc(drivers)

@admin_router.get("/rides")
async def admin_get_rides():
    rides = await db.rides.find().sort('created_at', -1).to_list(100)
    return serialize_doc(rides)

class DriverVerifyRequest(BaseModel):
    is_verified: bool
    rejection_reason: Optional[str] = None

@admin_router.post("/drivers/{driver_id}/verify")
async def admin_verify_driver(driver_id: str, req: DriverVerifyRequest):
    driver = await db.drivers.find_one({'id': driver_id})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
        
    update_data = {
        'is_verified': req.is_verified,
        'rejection_reason': req.rejection_reason if not req.is_verified else None,
        'updated_at': datetime.utcnow()
    }
    
    # If verified, we can set them to allowable state (though they still need to go online themselves)
    if req.is_verified:
        # Send push notification
        if driver.get('user_id'):
            await send_push_notification(
                driver['user_id'],
                "Account Verified! 🎉",
                "Your driver account has been approved. You can now go online.",
                {'type': 'driver_verified'}
            )
    else:
        if driver.get('user_id'):
             await send_push_notification(
                driver['user_id'],
                "Action Required ⚠️",
                f"Your driver application needs attention: {req.rejection_reason}",
                {'type': 'driver_rejected'}
            )

    await db.drivers.update_one({'id': driver_id}, {'$set': update_data})
    return {'success': True}

@admin_router.get("/stats")
async def admin_get_stats():
    total_rides = await db.rides.count_documents({})
    completed_rides = await db.rides.count_documents({'status': 'completed'})
    cancelled_rides = await db.rides.count_documents({'status': 'cancelled'})
    active_rides = await db.rides.count_documents({'status': {'$in': ['searching', 'driver_assigned', 'driver_arrived', 'in_progress']}})
    total_drivers = await db.drivers.count_documents({})
    online_drivers = await db.drivers.count_documents({'is_online': True})
    total_users = await db.users.count_documents({})
    
    # Calculate earnings
    rides_cursor = db.rides.find({'status': 'completed'})
    # Supabase fetches all rows at once, so we get the list
    rides = await rides_cursor.to_list(limit=10000)

    total_driver_earnings = 0
    total_admin_earnings = 0
    total_tips = 0
    
    for ride in rides:
        total_driver_earnings += ride.get('driver_earnings', 0) + ride.get('tip_amount', 0)
        total_admin_earnings += ride.get('admin_earnings', 0)
        total_tips += ride.get('tip_amount', 0)
    
    # Add cancellation earnings
    cancelled_cursor = db.rides.find({'status': 'cancelled', 'cancellation_fee_admin': {'$gt': 0}})
    cancelled_rides = await cancelled_cursor.to_list(limit=10000)

    for ride in cancelled_rides:
        total_admin_earnings += ride.get('cancellation_fee_admin', 0)
        total_driver_earnings += ride.get('cancellation_fee_driver', 0)
    
    return {
        'total_rides': total_rides,
        'completed_rides': completed_rides,
        'cancelled_rides': cancelled_rides,
        'active_rides': active_rides,
        'total_drivers': total_drivers,
        'online_drivers': online_drivers,
        'total_users': total_users,
        'total_driver_earnings': round(total_driver_earnings, 2),
        'total_admin_earnings': round(total_admin_earnings, 2),
        'total_tips': round(total_tips, 2)
    }

@admin_router.get("/rides/{ride_id}/details")
async def admin_get_ride_details(ride_id: str):
    """Get detailed ride information including timeline"""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
    
    driver = None
    if ride.get('driver_id'):
        driver = await db.drivers.find_one({'id': ride['driver_id']})
    
    rider = await db.users.find_one({'id': ride['rider_id']})
    vehicle_type = await db.vehicle_types.find_one({'id': ride['vehicle_type_id']})
    
    return {
        'ride': serialize_doc(ride),
        'driver': serialize_doc(driver),
        'rider': serialize_doc(rider),
        'vehicle_type': serialize_doc(vehicle_type)
    }

@admin_router.get("/drivers/{driver_id}/rides")
async def admin_get_driver_rides(driver_id: str):
    """Get all rides for a specific driver"""
    driver = await db.drivers.find_one({'id': driver_id})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    
    rides = await db.rides.find({'driver_id': driver_id}).sort('created_at', -1).to_list(100)
    
    # Calculate driver stats
    total_earnings = 0
    total_tips = 0
    completed_count = 0
    cancelled_count = 0
    
    for ride in rides:
        if ride['status'] == 'completed':
            total_earnings += ride.get('driver_earnings', 0) + ride.get('tip_amount', 0)
            total_tips += ride.get('tip_amount', 0)
            completed_count += 1
        elif ride['status'] == 'cancelled':
            total_earnings += ride.get('cancellation_fee_driver', 0)
            cancelled_count += 1
    
    return {
        'driver': serialize_doc(driver),
        'rides': serialize_doc(rides),
        'stats': {
            'total_earnings': round(total_earnings, 2),
            'total_tips': round(total_tips, 2),
            'completed_rides': completed_count,
            'cancelled_rides': cancelled_count
        }
    }

@admin_router.get("/earnings")
async def admin_get_earnings():
    """Get detailed earnings breakdown"""
    rides = await db.rides.find().sort('created_at', -1).to_list(1000)
    
    earnings_data = []
    for ride in rides:
        earnings_data.append({
            'ride_id': ride['id'],
            'date': ride.get('ride_completed_at') or ride.get('cancelled_at') or ride.get('created_at'),
            'status': ride['status'],
            'total_fare': ride.get('total_fare', 0),
            'driver_earnings': ride.get('driver_earnings', 0),
            'admin_earnings': ride.get('admin_earnings', 0),
            'tip_amount': ride.get('tip_amount', 0),
            'cancellation_fee_admin': ride.get('cancellation_fee_admin', 0),
            'cancellation_fee_driver': ride.get('cancellation_fee_driver', 0),
            'pickup_address': ride.get('pickup_address', ''),
            'dropoff_address': ride.get('dropoff_address', ''),
            'distance_km': ride.get('distance_km', 0),
            'driver_id': ride.get('driver_id', ''),
            'rider_id': ride.get('rider_id', '')
        })
    
    return earnings_data

@admin_router.get("/export/rides")
async def admin_export_rides():
    """Export all rides as CSV-ready JSON"""
    rides = await db.rides.find().sort('created_at', -1).to_list(10000)
    
    export_data = []
    for ride in rides:
        driver = None
        if ride.get('driver_id'):
            driver = await db.drivers.find_one({'id': ride['driver_id']})
        
        export_data.append({
            'ride_id': ride['id'],
            'status': ride['status'],
            'pickup_address': ride.get('pickup_address', ''),
            'dropoff_address': ride.get('dropoff_address', ''),
            'distance_km': ride.get('distance_km', 0),
            'duration_minutes': ride.get('duration_minutes', 0),
            'total_fare': ride.get('total_fare', 0),
            'driver_earnings': ride.get('driver_earnings', 0),
            'admin_earnings': ride.get('admin_earnings', 0),
            'tip_amount': ride.get('tip_amount', 0),
            'driver_name': driver['name'] if driver else '',
            'driver_rating': driver['rating'] if driver else '',
            'driver_license_plate': driver['license_plate'] if driver else '',
            'ride_requested_at': str(ride.get('ride_requested_at', '')),
            'driver_notified_at': str(ride.get('driver_notified_at', '')),
            'driver_accepted_at': str(ride.get('driver_accepted_at', '')),
            'driver_arrived_at': str(ride.get('driver_arrived_at', '')),
            'ride_started_at': str(ride.get('ride_started_at', '')),
            'ride_completed_at': str(ride.get('ride_completed_at', '')),
            'cancelled_at': str(ride.get('cancelled_at', '')),
            'rider_rating': ride.get('rider_rating', ''),
            'rider_comment': ride.get('rider_comment', '')
        })
    
    return export_data

@admin_router.get("/export/drivers")
async def admin_export_drivers():
    """Export all drivers with their ride stats"""
    drivers = await db.drivers.find().to_list(1000)
    
    export_data = []
    for driver in drivers:
        # Get ride stats for each driver
        rides = await db.rides.find({'driver_id': driver['id']}).to_list(10000)
        
        completed_rides = [r for r in rides if r['status'] == 'completed']
        cancelled_rides = [r for r in rides if r['status'] == 'cancelled']
        
        total_earnings = sum(r.get('driver_earnings', 0) + r.get('tip_amount', 0) for r in completed_rides)
        total_earnings += sum(r.get('cancellation_fee_driver', 0) for r in cancelled_rides)
        total_tips = sum(r.get('tip_amount', 0) for r in completed_rides)
        
        export_data.append({
            'driver_id': driver['id'],
            'name': driver['name'],
            'phone': driver['phone'],
            'vehicle_make': driver['vehicle_make'],
            'vehicle_model': driver['vehicle_model'],
            'vehicle_color': driver['vehicle_color'],
            'license_plate': driver['license_plate'],
            'rating': driver.get('rating', 5.0),
            'total_rides': driver.get('total_rides', 0),
            'completed_rides': len(completed_rides),
            'cancelled_rides': len(cancelled_rides),
            'total_earnings': round(total_earnings, 2),
            'total_tips': round(total_tips, 2),
            'is_online': driver.get('is_online', False),
            'is_available': driver.get('is_available', True),
            # New Fields
            'is_verified': driver.get('is_verified', False),
            'vehicle_year': driver.get('vehicle_year', ''),
            'vehicle_vin': driver.get('vehicle_vin', ''),
            'license_number': driver.get('license_number', ''),
            'license_expiry': str(driver.get('license_expiry_date', '')),
            'inspection_expiry': str(driver.get('vehicle_inspection_expiry_date', ''))
        })
    
    return export_data

# ============ Corporate Accounts CRUD ============

@admin_router.get("/corporate-accounts")
async def admin_get_corporate_accounts():
    """List all corporate accounts"""
    accounts = await db.corporate_accounts.find().sort('created_at', -1).to_list(100)
    return serialize_doc(accounts)

@admin_router.post("/corporate-accounts")
async def admin_create_corporate_account(account: Dict[str, Any]):
    """Create a new corporate account"""
    account['id'] = str(uuid.uuid4())
    account['created_at'] = datetime.utcnow()
    account['updated_at'] = datetime.utcnow()
    await db.corporate_accounts.insert_one(account)
    return serialize_doc(account)

@admin_router.put("/corporate-accounts/{account_id}")
async def admin_update_corporate_account(account_id: str, account: Dict[str, Any]):
    """Update a corporate account"""
    account['updated_at'] = datetime.utcnow()
    await db.corporate_accounts.update_one({'id': account_id}, {'$set': account})
    return serialize_doc(await db.corporate_accounts.find_one({'id': account_id}))

@admin_router.delete("/corporate-accounts/{account_id}")
async def admin_delete_corporate_account(account_id: str):
    """Delete a corporate account"""
    await db.corporate_accounts.delete_one({'id': account_id})
    return {'success': True}

# ============ Heat Map Data ============

@admin_router.get("/rides/heatmap-data")
async def admin_get_heatmap_data(
    filter: str = Query("all", description="Filter: all | corporate | regular"),
    start_date: Optional[str] = Query(None, description="ISO date string"),
    end_date: Optional[str] = Query(None, description="ISO date string"),
    service_area_id: Optional[str] = Query(None),
    group_by: str = Query("both", description="pickup | dropoff | both")
):
    """
    Get heat map data for rides.
    Returns pickup/dropoff points with intensity based on ride count.
    """
    # Build base query filter
    query_filter = {}
    
    # Add date filters
    if start_date or end_date:
        query_filter['created_at'] = {}
        if start_date:
            query_filter['created_at']['$gte'] = start_date
        if end_date:
            query_filter['created_at']['$lte'] = end_date
    
    # Add service area filter if provided
    if service_area_id:
        query_filter['service_area_id'] = service_area_id
    
    # Get all rides (completed only for meaningful heat data)
    query_filter['status'] = 'completed'
    
    # Apply filter for corporate vs regular
    if filter == 'corporate':
        query_filter['corporate_account_id'] = {'$ne': None}
    elif filter == 'regular':
        query_filter['corporate_account_id'] = None
    
    rides = await db.rides.find(query_filter).to_list(10000)
    
    # Aggregate pickup points
    pickup_agg = {}
    dropoff_agg = {}
    
    for ride in rides:
        # Pickup points
        if group_by in ('pickup', 'both'):
            pickup_key = (round(ride.get('pickup_lat', 0), 3), round(ride.get('pickup_lng', 0), 3))
            if pickup_key not in pickup_agg:
                pickup_agg[pickup_key] = 0
            pickup_agg[pickup_key] += 1
        
        # Dropoff points
        if group_by in ('dropoff', 'both'):
            dropoff_key = (round(ride.get('dropoff_lat', 0), 3), round(ride.get('dropoff_lng', 0), 3))
            if dropoff_key not in dropoff_agg:
                dropoff_agg[dropoff_key] = 0
            dropoff_agg[dropoff_key] += 1
    
    # Convert to heat map format [lat, lng, intensity]
    # Normalize intensity to 0-1 range
    max_pickup = max(pickup_agg.values()) if pickup_agg else 1
    max_dropoff = max(dropoff_agg.values()) if dropoff_agg else 1
    
    pickup_points = [
        [lat, lng, round(count / max_pickup, 2)]
        for (lat, lng), count in pickup_agg.items()
    ]
    
    dropoff_points = [
        [lat, lng, round(count / max_dropoff, 2)]
        for (lat, lng), count in dropoff_agg.items()
    ]
    
    # Calculate stats
    corporate_rides = [r for r in rides if r.get('corporate_account_id')]
    regular_rides = [r for r in rides if not r.get('corporate_account_id')]
    
    return {
        'pickup_points': pickup_points,
        'dropoff_points': dropoff_points,
        'stats': {
            'total_rides': len(rides),
            'corporate_rides': len(corporate_rides),
            'regular_rides': len(regular_rides)
        }
    }

# ============ Heat Map Settings ============

@admin_router.get("/settings/heatmap")
async def admin_get_heatmap_settings():
    """Get heat map configuration settings"""
    settings = await db.settings.find_one({'id': 'app_settings'})
    if not settings:
        # Return default settings
        return {
            'heat_map_enabled': True,
            'heat_map_default_range': '30d',
            'heat_map_intensity': 'medium',
            'heat_map_radius': 25,
            'heat_map_blur': 15,
            'heat_map_gradient_start': '#00ff00',
            'heat_map_gradient_mid': '#ffff00',
            'heat_map_gradient_end': '#ff0000',
            'heat_map_show_pickups': True,
            'heat_map_show_dropoffs': True,
            'corporate_heat_map_enabled': True,
            'regular_rider_heat_map_enabled': True
        }
    
    return {
        'heat_map_enabled': settings.get('heat_map_enabled', True),
        'heat_map_default_range': settings.get('heat_map_default_range', '30d'),
        'heat_map_intensity': settings.get('heat_map_intensity', 'medium'),
        'heat_map_radius': settings.get('heat_map_radius', 25),
        'heat_map_blur': settings.get('heat_map_blur', 15),
        'heat_map_gradient_start': settings.get('heat_map_gradient_start', '#00ff00'),
        'heat_map_gradient_mid': settings.get('heat_map_gradient_mid', '#ffff00'),
        'heat_map_gradient_end': settings.get('heat_map_gradient_end', '#ff0000'),
        'heat_map_show_pickups': settings.get('heat_map_show_pickups', True),
        'heat_map_show_dropoffs': settings.get('heat_map_show_dropoffs', True),
        'corporate_heat_map_enabled': settings.get('corporate_heat_map_enabled', True),
        'regular_rider_heat_map_enabled': settings.get('regular_rider_heat_map_enabled', True)
    }

@admin_router.put("/settings/heatmap")
async def admin_update_heatmap_settings(settings: Dict[str, Any]):
    """Update heat map configuration settings"""
    settings['id'] = 'app_settings'
    settings['updated_at'] = datetime.utcnow()
    
    await db.settings.update_one(
        {'id': 'app_settings'},
        {'$set': settings},
        upsert=True
    )
    
    return serialize_doc(await db.settings.find_one({'id': 'app_settings'}))

# ============ Admin Panel HTML ============

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Spinr Admin Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Plus Jakarta Sans', sans-serif; }
        .tab-active { border-bottom: 2px solid #ee2b2b; color: #ee2b2b; }
    </style>
</head>
<body class="bg-gray-100">
    <div id="app">
        <!-- Header -->
        <header class="bg-white shadow">
            <div class="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
                <h1 class="text-2xl font-bold text-red-500">Spinr Admin</h1>
                <div class="flex items-center gap-4">
                    <span class="text-sm text-gray-500">{{ stats.online_drivers || 0 }} drivers online</span>
                </div>
            </div>
        </header>

        <!-- Navigation -->
        <nav class="bg-white border-b">
            <div class="max-w-7xl mx-auto px-4">
                <div class="flex gap-8 overflow-x-auto">
                    <button @click="tab = 'dashboard'" :class="{'tab-active': tab === 'dashboard'}" class="py-4 px-2 text-sm font-medium whitespace-nowrap">Dashboard</button>
                    <button @click="tab = 'settings'" :class="{'tab-active': tab === 'settings'}" class="py-4 px-2 text-sm font-medium whitespace-nowrap">Settings</button>
                    <button @click="tab = 'areas'" :class="{'tab-active': tab === 'areas'}" class="py-4 px-2 text-sm font-medium whitespace-nowrap">Service Areas</button>
                    <button @click="tab = 'vehicles'" :class="{'tab-active': tab === 'vehicles'}" class="py-4 px-2 text-sm font-medium whitespace-nowrap">Vehicle Types</button>
                    <button @click="tab = 'fares'" :class="{'tab-active': tab === 'fares'}" class="py-4 px-2 text-sm font-medium whitespace-nowrap">Fare Config</button>
                    <button @click="tab = 'rides'" :class="{'tab-active': tab === 'rides'}" class="py-4 px-2 text-sm font-medium whitespace-nowrap">Rides</button>
                    <button @click="tab = 'drivers'" :class="{'tab-active': tab === 'drivers'}" class="py-4 px-2 text-sm font-medium whitespace-nowrap">Drivers</button>
                    <button @click="tab = 'earnings'" :class="{'tab-active': tab === 'earnings'}" class="py-4 px-2 text-sm font-medium whitespace-nowrap">Earnings</button>
                </div>
            </div>
        </nav>

        <main class="max-w-7xl mx-auto px-4 py-8">
            <!-- Dashboard -->
            <div v-if="tab === 'dashboard'">
                <h2 class="text-xl font-bold mb-6">Dashboard</h2>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div class="bg-white rounded-lg p-6 shadow">
                        <p class="text-gray-500 text-sm">Total Rides</p>
                        <p class="text-3xl font-bold">{{ stats.total_rides || 0 }}</p>
                    </div>
                    <div class="bg-white rounded-lg p-6 shadow">
                        <p class="text-gray-500 text-sm">Active Rides</p>
                        <p class="text-3xl font-bold text-green-500">{{ stats.active_rides || 0 }}</p>
                    </div>
                    <div class="bg-white rounded-lg p-6 shadow">
                        <p class="text-gray-500 text-sm">Total Users</p>
                        <p class="text-3xl font-bold">{{ stats.total_users || 0 }}</p>
                    </div>
                    <div class="bg-white rounded-lg p-6 shadow">
                        <p class="text-gray-500 text-sm">Total Drivers</p>
                        <p class="text-3xl font-bold">{{ stats.total_drivers || 0 }}</p>
                    </div>
                    <div class="bg-white rounded-lg p-6 shadow">
                        <p class="text-gray-500 text-sm">Online Drivers</p>
                        <p class="text-3xl font-bold text-green-500">{{ stats.online_drivers || 0 }}</p>
                    </div>
                    <div class="bg-white rounded-lg p-6 shadow">
                        <p class="text-gray-500 text-sm">Completed Rides</p>
                        <p class="text-3xl font-bold">{{ stats.completed_rides || 0 }}</p>
                    </div>
                    <div class="bg-white rounded-lg p-6 shadow border-l-4 border-green-500">
                        <p class="text-gray-500 text-sm">Total Driver Earnings</p>
                        <p class="text-3xl font-bold text-green-600">${{ stats.total_driver_earnings || '0.00' }}</p>
                    </div>
                    <div class="bg-white rounded-lg p-6 shadow border-l-4 border-red-500">
                        <p class="text-gray-500 text-sm">Admin/Platform Earnings</p>
                        <p class="text-3xl font-bold text-red-600">${{ stats.total_admin_earnings || '0.00' }}</p>
                    </div>
                    <div class="bg-white rounded-lg p-6 shadow border-l-4 border-yellow-500">
                        <p class="text-gray-500 text-sm">Total Tips</p>
                        <p class="text-3xl font-bold text-yellow-600">${{ stats.total_tips || '0.00' }}</p>
                    </div>
                </div>
            </div>

            <!-- Settings -->
            <div v-if="tab === 'settings'">
                <h2 class="text-xl font-bold mb-6">App Settings</h2>
                <div class="bg-white rounded-lg p-6 shadow max-w-2xl">
                    <div class="space-y-6">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Google Maps API Key</label>
                            <input v-model="settings.google_maps_api_key" type="text" class="w-full px-4 py-2 border rounded-lg" placeholder="AIza...">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Stripe Publishable Key</label>
                            <input v-model="settings.stripe_publishable_key" type="text" class="w-full px-4 py-2 border rounded-lg" placeholder="pk_...">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Stripe Secret Key</label>
                            <input v-model="settings.stripe_secret_key" type="password" class="w-full px-4 py-2 border rounded-lg" placeholder="sk_...">
                        </div>
                        <hr>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Driver Matching Algorithm</label>
                            <select v-model="settings.driver_matching_algorithm" class="w-full px-4 py-2 border rounded-lg">
                                <option value="nearest">Nearest Driver</option>
                                <option value="round_robin">Round Robin</option>
                                <option value="rating_based">Rating Based</option>
                                <option value="combined">Combined (Nearest + Rating)</option>
                            </select>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-2">Min Driver Rating</label>
                                <input v-model="settings.min_driver_rating" type="number" step="0.1" min="0" max="5" class="w-full px-4 py-2 border rounded-lg">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-2">Search Radius (km)</label>
                                <input v-model="settings.search_radius_km" type="number" step="1" min="1" class="w-full px-4 py-2 border rounded-lg">
                            </div>
                        </div>
                        <hr>
                        <h3 class="text-md font-semibold text-gray-800">Cancellation Fees</h3>
                        <p class="text-sm text-gray-500">Applied when rider cancels after driver has arrived at pickup</p>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-2">Admin Fee ($)</label>
                                <input v-model="settings.cancellation_fee_admin" type="number" step="0.01" min="0" class="w-full px-4 py-2 border rounded-lg" placeholder="0.50">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-2">Driver Fee ($)</label>
                                <input v-model="settings.cancellation_fee_driver" type="number" step="0.01" min="0" class="w-full px-4 py-2 border rounded-lg" placeholder="2.50">
                            </div>
                        </div>
                        <button @click="saveSettings" class="bg-red-500 text-white px-6 py-2 rounded-lg font-medium hover:bg-red-600">Save Settings</button>
                    </div>
                </div>
            </div>

            <!-- Service Areas -->
            <div v-if="tab === 'areas'">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold">Service Areas (Geo-fencing)</h2>
                    <button @click="showAreaModal = true" class="bg-red-500 text-white px-4 py-2 rounded-lg font-medium">+ Add Area</button>
                </div>
                <div class="bg-white rounded-lg shadow overflow-hidden">
                    <table class="w-full">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">City</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y">
                            <tr v-for="area in areas" :key="area.id">
                                <td class="px-6 py-4 font-medium">{{ area.name }}</td>
                                <td class="px-6 py-4">{{ area.city }}</td>
                                <td class="px-6 py-4">
                                    <span :class="area.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'" class="px-2 py-1 rounded text-xs">{{ area.is_active ? 'Active' : 'Inactive' }}</span>
                                </td>
                                <td class="px-6 py-4">
                                    <button @click="deleteArea(area.id)" class="text-red-500 text-sm">Delete</button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Vehicle Types -->
            <div v-if="tab === 'vehicles'">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold">Vehicle Types</h2>
                    <button @click="showVehicleModal = true" class="bg-red-500 text-white px-4 py-2 rounded-lg font-medium">+ Add Vehicle Type</button>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div v-for="vt in vehicleTypes" :key="vt.id" class="bg-white rounded-lg p-6 shadow">
                        <h3 class="font-bold text-lg">{{ vt.name }}</h3>
                        <p class="text-gray-500 text-sm mt-1">{{ vt.description }}</p>
                        <p class="text-sm mt-2">Capacity: {{ vt.capacity }} seats</p>
                        <div class="mt-4 flex gap-2">
                            <button @click="deleteVehicleType(vt.id)" class="text-red-500 text-sm">Delete</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Fare Config -->
            <div v-if="tab === 'fares'">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold">Fare Configuration</h2>
                    <button @click="showFareModal = true" class="bg-red-500 text-white px-4 py-2 rounded-lg font-medium">+ Add Fare</button>
                </div>
                <div class="bg-white rounded-lg shadow overflow-hidden">
                    <table class="w-full">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Area</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Vehicle</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Base</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Per KM</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Per Min</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Min Fare</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y">
                            <tr v-for="fare in fares" :key="fare.id">
                                <td class="px-6 py-4">{{ getAreaName(fare.service_area_id) }}</td>
                                <td class="px-6 py-4">{{ getVehicleName(fare.vehicle_type_id) }}</td>
                                <td class="px-6 py-4">${{ fare.base_fare }}</td>
                                <td class="px-6 py-4">${{ fare.per_km_rate }}</td>
                                <td class="px-6 py-4">${{ fare.per_minute_rate }}</td>
                                <td class="px-6 py-4">${{ fare.minimum_fare }}</td>
                                <td class="px-6 py-4">
                                    <button @click="deleteFare(fare.id)" class="text-red-500 text-sm">Delete</button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Rides -->
            <div v-if="tab === 'rides'">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold">Rides</h2>
                    <button @click="downloadRides" class="bg-green-500 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                        Download CSV
                    </button>
                </div>
                <div class="bg-white rounded-lg shadow overflow-hidden">
                    <table class="w-full">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">ID</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Pickup</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Dropoff</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Fare</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Driver $</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Admin $</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tip</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Rating</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y">
                            <tr v-for="ride in rides" :key="ride.id">
                                <td class="px-4 py-4 text-sm font-mono">{{ ride.id.substring(0, 8) }}...</td>
                                <td class="px-4 py-4 text-sm">{{ (ride.pickup_address || '').substring(0, 25) }}...</td>
                                <td class="px-4 py-4 text-sm">{{ (ride.dropoff_address || '').substring(0, 25) }}...</td>
                                <td class="px-4 py-4 font-medium">${{ ride.total_fare || 0 }}</td>
                                <td class="px-4 py-4 text-green-600">${{ ride.driver_earnings || 0 }}</td>
                                <td class="px-4 py-4 text-red-600">${{ ride.admin_earnings || 0 }}</td>
                                <td class="px-4 py-4 text-yellow-600">${{ ride.tip_amount || 0 }}</td>
                                <td class="px-4 py-4">
                                    <span :class="getStatusClass(ride.status)" class="px-2 py-1 rounded text-xs">{{ ride.status }}</span>
                                </td>
                                <td class="px-4 py-4">
                                    <span v-if="ride.rider_rating" class="flex items-center gap-1">{{ ride.rider_rating }} ⭐</span>
                                    <span v-else class="text-gray-400">-</span>
                                </td>
                                <td class="px-4 py-4">
                                    <button @click="viewRideDetails(ride)" class="text-blue-500 text-sm">View Timeline</button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Drivers -->
            <div v-if="tab === 'drivers'">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold">Drivers</h2>
                    <button @click="downloadDrivers" class="bg-green-500 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                        Download CSV
                    </button>
                </div>
                <div class="bg-white rounded-lg shadow overflow-hidden">
                    <table class="w-full">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Vehicle</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Rating</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Rides</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y">
                            <tr v-for="driver in drivers" :key="driver.id">
                                <td class="px-4 py-4 font-medium">{{ driver.name }}</td>
                                <td class="px-4 py-4 text-sm">{{ driver.vehicle_make }} {{ driver.vehicle_model }} ({{ driver.license_plate }})</td>
                                <td class="px-4 py-4">{{ driver.rating }} ⭐</td>
                                <td class="px-4 py-4">{{ driver.total_rides }}</td>
                                <td class="px-4 py-4">
                                    <span :class="driver.is_online ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'" class="px-2 py-1 rounded text-xs">{{ driver.is_online ? 'Online' : 'Offline' }}</span>
                                </td>
                                <td class="px-4 py-4">
                                    <button @click="viewDriverRides(driver)" class="text-blue-500 text-sm">View Rides</button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Earnings -->
            <div v-if="tab === 'earnings'">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold">Earnings Breakdown</h2>
                    <button @click="downloadEarnings" class="bg-green-500 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                        Download CSV
                    </button>
                </div>
                
                <!-- Summary Cards -->
                <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
                    <div class="bg-green-50 rounded-lg p-4 border border-green-200">
                        <p class="text-green-600 text-sm font-medium">Driver Earnings</p>
                        <p class="text-2xl font-bold text-green-700">${{ stats.total_driver_earnings || '0.00' }}</p>
                        <p class="text-xs text-green-500 mt-1">100% of trip fare goes to drivers</p>
                    </div>
                    <div class="bg-red-50 rounded-lg p-4 border border-red-200">
                        <p class="text-red-600 text-sm font-medium">Platform Earnings</p>
                        <p class="text-2xl font-bold text-red-700">${{ stats.total_admin_earnings || '0.00' }}</p>
                        <p class="text-xs text-red-500 mt-1">Booking fees + cancellation fees</p>
                    </div>
                    <div class="bg-yellow-50 rounded-lg p-4 border border-yellow-200">
                        <p class="text-yellow-600 text-sm font-medium">Total Tips</p>
                        <p class="text-2xl font-bold text-yellow-700">${{ stats.total_tips || '0.00' }}</p>
                        <p class="text-xs text-yellow-500 mt-1">100% of tips go to drivers</p>
                    </div>
                    <div class="bg-blue-50 rounded-lg p-4 border border-blue-200">
                        <p class="text-blue-600 text-sm font-medium">Total Revenue</p>
                        <p class="text-2xl font-bold text-blue-700">${{ ((stats.total_driver_earnings || 0) + (stats.total_admin_earnings || 0)).toFixed(2) }}</p>
                        <p class="text-xs text-blue-500 mt-1">All rides combined</p>
                    </div>
                </div>
                
                <!-- Earnings Table -->
                <div class="bg-white rounded-lg shadow overflow-hidden">
                    <table class="w-full">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Ride ID</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Total Fare</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Driver Gets</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Platform Gets</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tip</th>
                                <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Cancel Fees</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y">
                            <tr v-for="ride in rides" :key="ride.id">
                                <td class="px-4 py-4 text-sm font-mono">{{ ride.id.substring(0, 8) }}...</td>
                                <td class="px-4 py-4">
                                    <span :class="getStatusClass(ride.status)" class="px-2 py-1 rounded text-xs">{{ ride.status }}</span>
                                </td>
                                <td class="px-4 py-4 font-medium">${{ ride.total_fare || 0 }}</td>
                                <td class="px-4 py-4 text-green-600 font-medium">${{ ((ride.driver_earnings || 0) + (ride.tip_amount || 0)).toFixed(2) }}</td>
                                <td class="px-4 py-4 text-red-600">${{ ride.admin_earnings || 0 }}</td>
                                <td class="px-4 py-4 text-yellow-600">${{ ride.tip_amount || 0 }}</td>
                                <td class="px-4 py-4 text-sm">
                                    <span v-if="ride.cancellation_fee_admin > 0">
                                        Admin: ${{ ride.cancellation_fee_admin }}<br>
                                        Driver: ${{ ride.cancellation_fee_driver }}
                                    </span>
                                    <span v-else class="text-gray-400">-</span>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </main>

        <!-- Area Modal -->
        <div v-if="showAreaModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div class="bg-white rounded-lg p-6 w-full max-w-md">
                <h3 class="text-lg font-bold mb-4">Add Service Area</h3>
                <div class="space-y-4">
                    <input v-model="newArea.name" type="text" placeholder="Area Name" class="w-full px-4 py-2 border rounded-lg">
                    <select v-model="newArea.city" class="w-full px-4 py-2 border rounded-lg">
                        <option value="">Select City</option>
                        <option value="Saskatoon">Saskatoon</option>
                        <option value="Regina">Regina</option>
                    </select>
                    <p class="text-sm text-gray-500">Polygon coordinates (lat,lng pairs):</p>
                    <textarea v-model="newArea.polygonText" rows="4" placeholder="52.18,-106.75&#10;52.18,-106.55&#10;52.08,-106.55&#10;52.08,-106.75" class="w-full px-4 py-2 border rounded-lg font-mono text-sm"></textarea>
                </div>
                <div class="mt-6 flex gap-4">
                    <button @click="showAreaModal = false" class="px-4 py-2 border rounded-lg">Cancel</button>
                    <button @click="addArea" class="bg-red-500 text-white px-4 py-2 rounded-lg">Add Area</button>
                </div>
            </div>
        </div>

        <!-- Vehicle Modal -->
        <div v-if="showVehicleModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div class="bg-white rounded-lg p-6 w-full max-w-md">
                <h3 class="text-lg font-bold mb-4">Add Vehicle Type</h3>
                <div class="space-y-4">
                    <input v-model="newVehicle.name" type="text" placeholder="Name (e.g., Spinr Go)" class="w-full px-4 py-2 border rounded-lg">
                    <input v-model="newVehicle.description" type="text" placeholder="Description" class="w-full px-4 py-2 border rounded-lg">
                    <input v-model="newVehicle.capacity" type="number" placeholder="Capacity" class="w-full px-4 py-2 border rounded-lg">
                </div>
                <div class="mt-6 flex gap-4">
                    <button @click="showVehicleModal = false" class="px-4 py-2 border rounded-lg">Cancel</button>
                    <button @click="addVehicle" class="bg-red-500 text-white px-4 py-2 rounded-lg">Add Vehicle</button>
                </div>
            </div>
        </div>

        <!-- Fare Modal -->
        <div v-if="showFareModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div class="bg-white rounded-lg p-6 w-full max-w-md">
                <h3 class="text-lg font-bold mb-4">Add Fare Config</h3>
                <div class="space-y-4">
                    <select v-model="newFare.service_area_id" class="w-full px-4 py-2 border rounded-lg">
                        <option value="">Select Service Area</option>
                        <option v-for="area in areas" :key="area.id" :value="area.id">{{ area.name }}</option>
                    </select>
                    <select v-model="newFare.vehicle_type_id" class="w-full px-4 py-2 border rounded-lg">
                        <option value="">Select Vehicle Type</option>
                        <option v-for="vt in vehicleTypes" :key="vt.id" :value="vt.id">{{ vt.name }}</option>
                    </select>
                    <div class="grid grid-cols-2 gap-4">
                        <input v-model="newFare.base_fare" type="number" step="0.01" placeholder="Base Fare" class="px-4 py-2 border rounded-lg">
                        <input v-model="newFare.per_km_rate" type="number" step="0.01" placeholder="Per KM" class="px-4 py-2 border rounded-lg">
                        <input v-model="newFare.per_minute_rate" type="number" step="0.01" placeholder="Per Min" class="px-4 py-2 border rounded-lg">
                        <input v-model="newFare.minimum_fare" type="number" step="0.01" placeholder="Min Fare" class="px-4 py-2 border rounded-lg">
                    </div>
                </div>
                <div class="mt-6 flex gap-4">
                    <button @click="showFareModal = false" class="px-4 py-2 border rounded-lg">Cancel</button>
                    <button @click="addFare" class="bg-red-500 text-white px-4 py-2 rounded-lg">Add Fare</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        const { createApp } = Vue;
        createApp({
            data() {
                return {
                    tab: 'dashboard',
                    settings: {},
                    areas: [],
                    vehicleTypes: [],
                    fares: [],
                    rides: [],
                    drivers: [],
                    stats: {},
                    showAreaModal: false,
                    showVehicleModal: false,
                    showFareModal: false,
                    newArea: { name: '', city: '', polygonText: '' },
                    newVehicle: { name: '', description: '', capacity: 4, icon: 'car' },
                    newFare: { service_area_id: '', vehicle_type_id: '', base_fare: 0, per_km_rate: 0, per_minute_rate: 0, minimum_fare: 0, booking_fee: 2 }
                }
            },
            async mounted() {
                await this.loadAll();
            },
            methods: {
                async loadAll() {
                    await Promise.all([
                        this.loadSettings(),
                        this.loadAreas(),
                        this.loadVehicleTypes(),
                        this.loadFares(),
                        this.loadRides(),
                        this.loadDrivers(),
                        this.loadStats()
                    ]);
                },
                async loadSettings() {
                    const res = await fetch('/api/admin/settings');
                    this.settings = await res.json();
                },
                async loadAreas() {
                    const res = await fetch('/api/admin/service-areas');
                    this.areas = await res.json();
                },
                async loadVehicleTypes() {
                    const res = await fetch('/api/admin/vehicle-types');
                    this.vehicleTypes = await res.json();
                },
                async loadFares() {
                    const res = await fetch('/api/admin/fare-configs');
                    this.fares = await res.json();
                },
                async loadRides() {
                    const res = await fetch('/api/admin/rides');
                    this.rides = await res.json();
                },
                async loadDrivers() {
                    const res = await fetch('/api/admin/drivers');
                    this.drivers = await res.json();
                },
                async loadStats() {
                    const res = await fetch('/api/admin/stats');
                    this.stats = await res.json();
                },
                async saveSettings() {
                    await fetch('/api/admin/settings', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(this.settings)
                    });
                    alert('Settings saved!');
                },
                async addArea() {
                    const lines = this.newArea.polygonText.trim().split('\\n');
                    const polygon = lines.map(line => {
                        const [lat, lng] = line.split(',').map(Number);
                        return { lat, lng };
                    });
                    await fetch('/api/admin/service-areas', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ...this.newArea, polygon, is_active: true })
                    });
                    this.showAreaModal = false;
                    this.newArea = { name: '', city: '', polygonText: '' };
                    await this.loadAreas();
                },
                async deleteArea(id) {
                    if (confirm('Delete this area?')) {
                        await fetch(`/api/admin/service-areas/${id}`, { method: 'DELETE' });
                        await this.loadAreas();
                    }
                },
                async addVehicle() {
                    await fetch('/api/admin/vehicle-types', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ...this.newVehicle, is_active: true })
                    });
                    this.showVehicleModal = false;
                    this.newVehicle = { name: '', description: '', capacity: 4, icon: 'car' };
                    await this.loadVehicleTypes();
                },
                async deleteVehicleType(id) {
                    if (confirm('Delete this vehicle type?')) {
                        await fetch(`/api/admin/vehicle-types/${id}`, { method: 'DELETE' });
                        await this.loadVehicleTypes();
                    }
                },
                async addFare() {
                    await fetch('/api/admin/fare-configs', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ...this.newFare, is_active: true })
                    });
                    this.showFareModal = false;
                    this.newFare = { service_area_id: '', vehicle_type_id: '', base_fare: 0, per_km_rate: 0, per_minute_rate: 0, minimum_fare: 0, booking_fee: 2 };
                    await this.loadFares();
                },
                async deleteFare(id) {
                    if (confirm('Delete this fare config?')) {
                        await fetch(`/api/admin/fare-configs/${id}`, { method: 'DELETE' });
                        await this.loadFares();
                    }
                },
                getAreaName(id) {
                    const area = this.areas.find(a => a.id === id);
                    return area ? area.name : id;
                },
                getVehicleName(id) {
                    const vt = this.vehicleTypes.find(v => v.id === id);
                    return vt ? vt.name : id;
                },
                getStatusClass(status) {
                    const classes = {
                        'searching': 'bg-yellow-100 text-yellow-700',
                        'driver_assigned': 'bg-blue-100 text-blue-700',
                        'driver_arrived': 'bg-purple-100 text-purple-700',
                        'in_progress': 'bg-green-100 text-green-700',
                        'completed': 'bg-gray-100 text-gray-700',
                        'cancelled': 'bg-red-100 text-red-700'
                    };
                    return classes[status] || 'bg-gray-100';
                },
                async downloadRides() {
                    const res = await fetch('/api/admin/export/rides');
                    const data = await res.json();
                    this.downloadCSV(data, 'spinr_rides_export.csv');
                },
                async downloadDrivers() {
                    const res = await fetch('/api/admin/export/drivers');
                    const data = await res.json();
                    this.downloadCSV(data, 'spinr_drivers_export.csv');
                },
                async downloadEarnings() {
                    const res = await fetch('/api/admin/earnings');
                    const data = await res.json();
                    this.downloadCSV(data, 'spinr_earnings_export.csv');
                },
                downloadCSV(data, filename) {
                    if (!data.length) {
                        alert('No data to export');
                        return;
                    }
                    const headers = Object.keys(data[0]);
                    const csvContent = [
                        headers.join(','),
                        ...data.map(row => headers.map(h => '"' + (row[h] || '').toString().replace(/"/g, '""') + '"').join(','))
                    ].join('\\n');
                    const blob = new Blob([csvContent], { type: 'text/csv' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    a.click();
                    URL.revokeObjectURL(url);
                },
                viewRideDetails(ride) {
                    const timeline = [
                        { event: 'Ride Requested', time: ride.ride_requested_at },
                        { event: 'Driver Notified', time: ride.driver_notified_at },
                        { event: 'Driver Accepted', time: ride.driver_accepted_at },
                        { event: 'Driver Arrived', time: ride.driver_arrived_at },
                        { event: 'Ride Started', time: ride.ride_started_at },
                        { event: 'Ride Completed', time: ride.ride_completed_at },
                        { event: 'Cancelled', time: ride.cancelled_at }
                    ].filter(t => t.time);
                    
                    let msg = 'RIDE TIMELINE\\n\\n';
                    msg += 'Pickup: ' + ride.pickup_address + '\\n';
                    msg += 'Dropoff: ' + ride.dropoff_address + '\\n';
                    msg += 'Distance: ' + ride.distance_km + ' km\\n';
                    msg += 'Duration: ' + ride.duration_minutes + ' min\\n';
                    msg += 'Total Fare: $' + ride.total_fare + '\\n';
                    msg += 'Driver Earnings: $' + (ride.driver_earnings || 0) + '\\n';
                    msg += 'Tip: $' + (ride.tip_amount || 0) + '\\n';
                    msg += 'Rating: ' + (ride.rider_rating || 'Not rated') + '\\n';
                    msg += '\\nTIMELINE:\\n';
                    timeline.forEach(t => {
                        msg += '• ' + t.event + ': ' + new Date(t.time).toLocaleString() + '\\n';
                    });
                    alert(msg);
                },
                async viewDriverRides(driver) {
                    try {
                        const res = await fetch('/api/admin/drivers/' + driver.id + '/rides');
                        const data = await res.json();
                        let msg = 'DRIVER: ' + driver.name + '\\n\\n';
                        msg += 'Rating: ' + driver.rating + ' ⭐\\n';
                        msg += 'Total Rides: ' + data.stats.completed_rides + '\\n';
                        msg += 'Total Earnings: $' + data.stats.total_earnings + '\\n';
                        msg += 'Total Tips: $' + data.stats.total_tips + '\\n';
                        msg += '\\nRECENT RIDES:\\n';
                        data.rides.slice(0, 5).forEach(r => {
                            msg += '• ' + r.status + ' - $' + r.total_fare + ' (' + new Date(r.created_at).toLocaleDateString() + ')\\n';
                        });
                        alert(msg);
                    } catch (e) {
                        alert('Error fetching driver rides');
                    }
                }
            }
        }).mount('#app');
    </script>
</body>
</html>
"""

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    return ADMIN_HTML

@api_router.get("/admin-panel", response_class=HTMLResponse)
async def admin_panel_page():
    return ADMIN_HTML

# ============ Seed Default Data ============

@api_router.post("/seed-defaults")
async def seed_default_data():
    existing = await db.vehicle_types.find_one()
    if existing:
        return {'message': 'Already seeded'}
    
    vehicle_types = [
        VehicleType(id='spinr-go', name='Spinr Go', description='Affordable rides', icon='car', capacity=4, image_url='https://img.icons8.com/3d-fluency/200/sedan.png'),
        VehicleType(id='spinr-xl', name='Spinr XL', description='Extra space for groups', icon='car-sport', capacity=6, image_url='https://img.icons8.com/3d-fluency/200/suv.png'),
        VehicleType(id='spinr-comfort', name='Comfort', description='Premium comfort', icon='car-outline', capacity=4, image_url='https://img.icons8.com/3d-fluency/200/car.png'),
    ]
    
    for vt in vehicle_types:
        await db.vehicle_types.insert_one(vt.dict())
    
    saskatoon_area = ServiceArea(
        id='saskatoon',
        name='Saskatoon',
        city='Saskatoon',
        polygon=[
            {'lat': 52.18, 'lng': -106.75},
            {'lat': 52.18, 'lng': -106.55},
            {'lat': 52.08, 'lng': -106.55},
            {'lat': 52.08, 'lng': -106.75},
        ]
    )
    
    regina_area = ServiceArea(
        id='regina',
        name='Regina',
        city='Regina',
        polygon=[
            {'lat': 50.50, 'lng': -104.70},
            {'lat': 50.50, 'lng': -104.50},
            {'lat': 50.40, 'lng': -104.50},
            {'lat': 50.40, 'lng': -104.70},
        ]
    )
    
    await db.service_areas.insert_one(saskatoon_area.dict())
    await db.service_areas.insert_one(regina_area.dict())
    
    fare_configs = [
        FareConfig(service_area_id='saskatoon', vehicle_type_id='spinr-go', base_fare=3.50, per_km_rate=1.50, per_minute_rate=0.25, minimum_fare=8.00),
        FareConfig(service_area_id='saskatoon', vehicle_type_id='spinr-xl', base_fare=5.00, per_km_rate=2.00, per_minute_rate=0.35, minimum_fare=12.00),
        FareConfig(service_area_id='saskatoon', vehicle_type_id='spinr-comfort', base_fare=4.50, per_km_rate=1.80, per_minute_rate=0.30, minimum_fare=10.00),
        FareConfig(service_area_id='regina', vehicle_type_id='spinr-go', base_fare=3.00, per_km_rate=1.40, per_minute_rate=0.22, minimum_fare=7.50),
        FareConfig(service_area_id='regina', vehicle_type_id='spinr-xl', base_fare=4.50, per_km_rate=1.90, per_minute_rate=0.32, minimum_fare=11.00),
        FareConfig(service_area_id='regina', vehicle_type_id='spinr-comfort', base_fare=4.00, per_km_rate=1.70, per_minute_rate=0.28, minimum_fare=9.50),
    ]
    
    for fc in fare_configs:
        await db.fare_configs.insert_one(fc.dict())
    
    return {'message': 'Default data seeded successfully'}

    
# ============ Nearby Drivers & Estimate ============

@api_router.get("/nearby-drivers")
async def get_nearby_drivers(lat: float, lng: float, radius_km: float = 10.0):
    """Find available drivers near a location."""
    try:
        nearby_drivers = await db.rpc('find_nearby_drivers', {
            'lat': lat,
            'lng': lng,
            'radius_meters': radius_km * 1000
        })
    except Exception as e:
        logger.warning(f"find_nearby_drivers RPC not available: {e}")
        return []
    
    if not nearby_drivers:
        return []
    
    # Return limited info for public API
    return [
        {
            'id': d['id'],
            'lat': d['lat'],
            'lng': d['lng'],
            'vehicle_type_id': d.get('vehicle_type_id'),
            'vehicle_make': d.get('vehicle_make'),
            'vehicle_model': d.get('vehicle_model'),
        }
        for d in nearby_drivers
    ]

class RideEstimateRequest(BaseModel):
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    stops: Optional[List[Dict[str, Any]]] = None

@api_router.post("/rides/estimate")
async def estimate_ride(req: RideEstimateRequest):
    """Calculate fare estimates for all available vehicle types."""
    print(f"DEBUG: estimate_ride start with {req}")
    # Calculate distance using haversine formula
    distance_km = calculate_distance(req.pickup_lat, req.pickup_lng, req.dropoff_lat, req.dropoff_lng)
    
    # Add stops distance (simplified)
    if req.stops:
        points = [(req.pickup_lat, req.pickup_lng)]
        for stop in req.stops:
            if stop.get('lat') and stop.get('lng'):
                points.append((stop['lat'], stop['lng']))
        points.append((req.dropoff_lat, req.dropoff_lng))
        
        total_dist = 0
        for i in range(len(points) - 1):
            total_dist += calculate_distance(points[i][0], points[i][1], points[i+1][0], points[i+1][1])
        distance_km = total_dist
    
    # Round to 1 decimal place
    distance_km = round(distance_km, 1)
    
    # Estimate duration based on average speed (30 km/h city driving)
    avg_speed_kmh = 30
    duration_minutes = max(5, round((distance_km / avg_speed_kmh) * 60))
    
    # Get all active vehicle types
    vehicle_types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    
    # If no vehicle types in DB, use hardcoded fallback (for dev)
    if not vehicle_types:
        vehicle_types = [
            {'id': 'spinr-go', 'name': 'Spinr Go', 'description': 'Affordable rides', 'icon': 'car', 'capacity': 4, 'is_active': True, 'image_url': 'https://img.icons8.com/3d-fluency/200/sedan.png'},
            {'id': 'spinr-xl', 'name': 'Spinr XL', 'description': 'Extra space for groups', 'icon': 'car-sport', 'capacity': 6, 'is_active': True, 'image_url': 'https://img.icons8.com/3d-fluency/200/suv.png'},
            {'id': 'spinr-comfort', 'name': 'Comfort', 'description': 'Premium comfort', 'icon': 'car-outline', 'capacity': 4, 'is_active': True, 'image_url': 'https://img.icons8.com/3d-fluency/200/car.png'},
        ]
    
    # Check driver availability for each type
    nearby_drivers = []
    try:
        nearby_drivers = await db.rpc('find_nearby_drivers', {
            'lat': req.pickup_lat,
            'lng': req.pickup_lng,
            'radius_meters': 30000 
        })
    except Exception as e:
        logger.warning(f"find_nearby_drivers RPC not available: {e}")
        nearby_drivers = []
    
    # Group drivers by vehicle type
    drivers_by_type = {}
    for d in (nearby_drivers or []):
        vt_id = d.get('vehicle_type_id')
        if vt_id not in drivers_by_type:
            drivers_by_type[vt_id] = []
        drivers_by_type[vt_id].append(d)

    # Get fare configs
    fare_map = {}
    try:
        all_areas = await db.service_areas.find({'is_active': True}).to_list(100)
        matching_area = None
        for area in all_areas:
            poly = area.get('polygon', [])
            if point_in_polygon(req.pickup_lat, req.pickup_lng, poly):
                matching_area = area
                break
        
        if matching_area:
            fares = await db.fare_configs.find({
                'service_area_id': matching_area['id'],
                'is_active': True
            }).to_list(100)
            fare_map = {f['vehicle_type_id']: f for f in fares}
    except Exception as e:
        logger.warning(f"Error fetching fare configs: {e}")
    
    # Default fare rates per vehicle type fallback
    default_fares = {
        'spinr-go': {'base_fare': 3.50, 'per_km_rate': 1.50, 'per_minute_rate': 0.25, 'minimum_fare': 8.00, 'booking_fee': 2.00},
        'spinr-xl': {'base_fare': 5.00, 'per_km_rate': 2.00, 'per_minute_rate': 0.35, 'minimum_fare': 12.00, 'booking_fee': 2.50},
        'spinr-comfort': {'base_fare': 6.00, 'per_km_rate': 2.50, 'per_minute_rate': 0.40, 'minimum_fare': 15.00, 'booking_fee': 3.00},
    }
    
    # Check for surge pricing
    surge_multiplier = 1.0
    try:
        surge = await db.surge_pricing.find_one({'is_active': True})
        if surge:
            surge_multiplier = surge.get('multiplier', 1.0)
    except Exception as e:
        logger.warning(f"Surge pricing lookup failed: {e}")
    
    # Calculate airport fee if applicable
    airport_fee = 0.0
    try:
        airport_info = await calculate_airport_fee(req.pickup_lat, req.pickup_lng, req.dropoff_lat, req.dropoff_lng)
        airport_fee = airport_info.get('airport_fee', 0.0)
    except Exception as e:
        logger.warning(f"Airport fee calculation failed: {e}")
    
    estimates = []
    for vt in vehicle_types:
        vt_id = vt['id']
        fare_config = fare_map.get(vt_id, default_fares.get(vt_id, default_fares.get('spinr-go'))) # fallback chain
        
        # Safety for fare config
        if not fare_config:
             fare_config = default_fares['spinr-go']

        base_fare = fare_config.get('base_fare', 3.50)
        distance_fare = distance_km * fare_config.get('per_km_rate', 1.50)
        time_fare = duration_minutes * fare_config.get('per_minute_rate', 0.25)
        booking_fee = fare_config.get('booking_fee', 2.00)
        minimum_fare = fare_config.get('minimum_fare', 8.00)
        
        # Apply surge
        subtotal = (base_fare + distance_fare + time_fare) * surge_multiplier
        total = max(subtotal + booking_fee + airport_fee, minimum_fare)
        
        # Availability Logic
        type_drivers = drivers_by_type.get(vt_id, [])
        is_available = len(type_drivers) > 0
        eta_minutes = -1
        
        if is_available:
            # Find nearest driver
            min_dist = float('inf')
            for d in type_drivers:
                # Calculate distance to driver
                dist = calculate_distance(req.pickup_lat, req.pickup_lng, d['lat'], d['lng'])
                if dist < min_dist:
                    min_dist = dist
            
            # Estimate ETA: 2 mins overhead + 2 mins per km (30km/h)
            eta_minutes = int(2 + (min_dist * 2))
        
        estimates.append({
            'vehicle_type': {
                'id': vt_id,
                'name': vt.get('name', vt_id),
                'description': vt.get('description', ''),
                'icon': vt.get('icon', 'car'),
                'capacity': vt.get('capacity', 4),
                'image_url': vt.get('image_url'),
            },
            'distance_km': distance_km,
            'duration_minutes': duration_minutes,
            'base_fare': round(base_fare * surge_multiplier, 2),
            'distance_fare': round(distance_fare * surge_multiplier, 2),
            'time_fare': round(time_fare * surge_multiplier, 2),
            'booking_fee': round(booking_fee, 2),
            'airport_fee': round(airport_fee, 2),
            'surge_multiplier': surge_multiplier,
            'total_fare': round(total, 2),
            'available': is_available,
            'eta_minutes': eta_minutes,
            'driver_count': len(type_drivers)
        })
    
    # Sort: available first, then price
    estimates.sort(key=lambda x: (not x['available'], x['total_fare']))
    
    return estimates


# ============ Health & Root ============

@api_router.get("/")
@app.get("/")
async def root():
    print("Received request at root")
    return {"message": "Spinr API is running", "version": "1.0.0"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# Include routers
app.include_router(api_router)
app.include_router(admin_router, dependencies=[Depends(get_admin_user)])
app.include_router(support_router)
app.include_router(admin_support_router, dependencies=[Depends(get_admin_user)])
app.include_router(pricing_router, dependencies=[Depends(get_admin_user)])
app.include_router(documents_router)
app.include_router(admin_documents_router, dependencies=[Depends(get_admin_user)])




if __name__ == "__main__":
    import uvicorn
    # When running as script, we can pass the app object directly
    uvicorn.run(app, host="0.0.0.0", port=8000)