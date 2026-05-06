import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field, validator

try:
    from .validators import validate_license_plate, validate_vehicle_year, validate_vin
except ImportError:
    from validators import validate_license_plate, validate_vehicle_year, validate_vin

# ============ Models ============


class DriverPublicView(BaseModel):
    """Safe subset of driver fields exposed to riders — no PII."""

    id: str
    name: str
    rating: Optional[float] = None
    total_rides: Optional[int] = None
    profile_image_url: Optional[str] = None
    vehicle_make: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_color: Optional[str] = None
    license_plate: Optional[str] = None
    vehicle_year: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class SendOTPRequest(BaseModel):
    phone: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\+1\d{10}$",
        description="Canadian/US phone in E.164 format: +1XXXXXXXXXX",
    )


class VerifyOTPRequest(BaseModel):
    phone: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\+1\d{10}$",
        description="Canadian/US phone in E.164 format: +1XXXXXXXXXX",
    )
    code: str = Field(..., min_length=4, max_length=4, pattern=r"^\d{4}$")


class CreateProfileRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    gender: str
    role: Optional[str] = None  # 'driver' when coming from driver app


class UserProfile(BaseModel):
    id: str
    phone: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    gender: Optional[str] = None
    profile_image: Optional[str] = None  # Base64 encoded image
    profile_image_status: Optional[str] = None  # pending_review | approved | rejected
    role: str = "rider"
    corporate_account_id: Optional[str] = None
    created_at: datetime
    profile_complete: bool = False
    is_driver: bool = False
    # Driver onboarding state machine — derived each request from user + driver
    # + documents rows. One of: profile_incomplete | vehicle_required |
    # documents_required | documents_rejected | documents_expired |
    # pending_review | verified | suspended. None for non-drivers.
    driver_onboarding_status: Optional[str] = None
    driver_onboarding_detail: Optional[str] = None  # human-readable explanation
    driver_onboarding_next_screen: Optional[str] = None  # route hint for the app


class OTPRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    phone: str
    code: str
    expires_at: datetime
    verified: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class AuthResponse(BaseModel):
    token: str
    refresh_token: str
    user: UserProfile
    is_new_user: bool
    # Token expiration fields (SEC-014)
    expires_in: int  # access token lifetime in seconds
    access_expires_at: Optional[datetime] = None
    refresh_expires_at: Optional[datetime] = None
    # CSRF double-submit token — echo back as X-CSRF-Token on state-changing requests
    csrf_token: Optional[str] = None


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
    cancellation_fee_admin: Decimal = Decimal("0.50")  # Admin gets 50 cents
    cancellation_fee_driver: Decimal = Decimal("2.50")  # Default driver gets $2.50 (rest of $3 total)
    platform_fee_percent: Decimal = Decimal("0.0")  # 0% commission - driver keeps all fare
    # When false, drivers can go online without an active Spinr Pass. Set
    # this to true to enforce the subscription gate at the "go online" call.
    # Defaults to false so the product works out of the box pre-launch.
    require_driver_subscription: bool = False
    terms_of_service_text: str = ""
    privacy_policy_text: str = ""
    # Public company / contact info. Exposed via GET /api/company-info (no
    # auth) so the rider and driver apps can embed these in their Support
    # / Profile footers without each app hard-coding them. None of these
    # fields are sensitive — they're the same info on a business card.
    company_name: str = "Spinr"
    company_address: str = ""
    company_phone: str = ""
    company_email: str = ""
    company_website: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ServiceArea(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    city: str
    polygon: List[Dict[str, float]]
    is_active: bool = True
    is_airport: bool = False
    airport_fee: Decimal = Decimal("0.0")
    surge_multiplier: float = 1.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VehicleType(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    icon: str
    capacity: int
    image_url: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FareConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    service_area_id: str
    vehicle_type_id: str
    base_fare: Decimal
    per_km_rate: Decimal
    per_minute_rate: Decimal
    minimum_fare: Decimal
    booking_fee: Decimal = Decimal("2.0")
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SavedAddress(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    name: str
    address: str
    lat: float
    lng: float
    icon: str = "location"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SavedAddressCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=5, max_length=300)
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @validator("license_plate")
    def _check_license_plate(cls, v: str) -> str:
        return validate_license_plate(v)

    @validator("vehicle_vin", pre=True, always=True)
    def _check_vin(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return validate_vin(v)

    @validator("vehicle_year", pre=True, always=True)
    def _check_vehicle_year(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        return validate_vehicle_year(v)


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
    stops: Optional[List[Dict[str, Any]]] = []
    is_scheduled: bool = False
    scheduled_time: Optional[datetime] = None
    corporate_account_id: Optional[str] = None
    requires_wav: bool = False
    distance_km: float
    duration_minutes: int
    base_fare: Decimal
    distance_fare: Decimal = Decimal("0.0")
    time_fare: Decimal = Decimal("0.0")
    booking_fee: Decimal = Decimal("2.0")
    surge_multiplier: float = 1.0
    total_fare: Decimal
    tip_amount: Decimal = Decimal("0.0")
    payment_method: str = "card"
    payment_method_id: Optional[str] = None
    payment_intent_id: Optional[str] = None
    payment_status: str = "pending"
    status: str = "searching"
    pickup_otp: str = ""
    # Timeline tracking
    ride_requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    driver_notified_at: Optional[datetime] = None
    driver_accepted_at: Optional[datetime] = None
    driver_arrived_at: Optional[datetime] = None
    ride_started_at: Optional[datetime] = None
    ride_completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    # Earnings split
    driver_earnings: Decimal = Decimal("0.0")  # Distance fare goes to driver
    admin_earnings: Decimal = Decimal("0.0")  # Booking fee goes to admin
    cancellation_fee_driver: Decimal = Decimal("0.0")
    cancellation_fee_admin: Decimal = Decimal("0.0")
    # Rating
    rider_rating: Optional[int] = None
    rider_comment: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RideRatingRequest(BaseModel):
    rating: int = Field(ge=1, le=5, description="Rating must be between 1 and 5")
    comment: Optional[str] = None
    tip_amount: Decimal = Field(default=Decimal("0.0"), ge=0, description="Tip amount must be non-negative")


class CreateRideRequest(BaseModel):
    vehicle_type_id: str
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    stops: Optional[List[Dict[str, Any]]] = Field(default=[], max_length=5)
    is_scheduled: bool = False
    scheduled_timezone: Optional[str] = None  # IANA name e.g. "America/Toronto"; used for DST-gap guard
    scheduled_time: Optional[datetime] = None
    corporate_account_id: Optional[str] = None
    payment_method: str = "card"
    # P0-4 surge-lock: optional signed token returned by /rides/estimate.
    # When present + valid, the backend reuses the surge_multiplier that
    # was shown to the rider instead of re-reading the service area, so
    # the confirmed fare can't bait-and-switch from the estimate.
    estimate_token: Optional[str] = None
    payment_method_id: Optional[str] = None
    work_profile: Optional[bool] = None
    requires_wav: bool = False

    # ── Input validation (SEC-017) ──────────────────────────────────────── #

    @validator("pickup_address", "dropoff_address")
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Address must be at least 3 characters")
        if len(v) > 500:
            raise ValueError("Address must be 500 characters or fewer")
        return v

    @validator("pickup_lat", "dropoff_lat")
    def validate_lat(cls, v: float) -> float:
        if not (-90.0 <= v <= 90.0):
            raise ValueError("Latitude must be between -90 and 90")
        return v

    @validator("pickup_lng", "dropoff_lng")
    def validate_lng(cls, v: float) -> float:
        if not (-180.0 <= v <= 180.0):
            raise ValueError("Longitude must be between -180 and 180")
        return v

    @validator("stops")
    def validate_stops(cls, stops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for stop in stops:
            lat = stop.get("lat")
            lng = stop.get("lng")
            if lat is None or lng is None:
                raise ValueError("Each stop must have lat and lng")
            if not (-90 <= float(lat) <= 90):
                raise ValueError(f"Stop latitude out of range: {lat}")
            if not (-180 <= float(lng) <= 180):
                raise ValueError(f"Stop longitude out of range: {lng}")
        return stops

    @validator("scheduled_time", always=True)
    def validate_scheduled_time(cls, v: Optional[datetime], values: dict) -> Optional[datetime]:
        if v is not None:
            from datetime import timedelta

            # Normalise to UTC-aware for the "in the future" comparison, then
            # strip tz for the DST-gap round-trip check which needs a naive wall time.
            v_utc = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
            if v_utc < datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ValueError("Scheduled time must be at least 5 minutes in the future")

            naive = v_utc.replace(tzinfo=None)

            tz_name: Optional[str] = values.get("scheduled_timezone")
            if tz_name:
                try:
                    import zoneinfo

                    tz = zoneinfo.ZoneInfo(tz_name)
                except (ImportError, KeyError) as exc:
                    raise ValueError(f"Unknown or unsupported timezone: {tz_name}") from exc

                # DST-gap guard: construct the wall-clock time in the named
                # timezone (fold=0 = pre-transition assumption), convert to UTC,
                # then convert back and verify the hour/minute round-trips.
                # A mismatch means the local time doesn't exist (clock was
                # skipped forward over it).
                utc_tz = zoneinfo.ZoneInfo("UTC")
                local = naive.replace(tzinfo=tz, fold=0)
                back = local.astimezone(utc_tz).astimezone(tz)
                if back.hour != naive.hour or back.minute != naive.minute:
                    raise ValueError(
                        f"The time {naive.strftime('%H:%M')} does not exist in "
                        f"{tz_name} on that date (DST spring-forward gap). "
                        "Please choose a time after the clocks change."
                    )
        return v
