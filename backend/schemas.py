from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

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
    terms_of_service_text: str = ""
    privacy_policy_text: str = ""
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

class SavedAddressCreate(BaseModel):
    name: str
    address: str
    lat: float
    lng: float
    icon: str = "location"

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

class CreateRideRequest(BaseModel):
    vehicle_type_id: str
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    payment_method: str = "card"
