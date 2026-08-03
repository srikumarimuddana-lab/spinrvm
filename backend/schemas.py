import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator
from pydantic.functional_serializers import PlainSerializer

try:
    from .validators import validate_license_plate, validate_vehicle_year, validate_vin
except ImportError:
    from validators import validate_license_plate, validate_vehicle_year, validate_vin

# Decimal type that serializes as a plain string on the wire.
# Use this instead of bare `Decimal` for any money / rate field in a response
# model so that JSON output is "2.50" (string), never 2.5 (float).
DecimalStr = Annotated[Decimal, PlainSerializer(lambda x: str(x), return_type=str)]

# ============ Models ============


class DriverPublicView(BaseModel):
    """Safe subset of driver fields exposed to riders — no PII."""

    id: str
    name: str
    rating: Optional[float] = None
    total_rides: Optional[int] = None
    photo_url: Optional[str] = None
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
    # Which app the signup came from. Both apps authenticate through this one
    # endpoint, so without a hint every driver-app signup would also be
    # reported to Meta as a rider acquisition. Optional and defaulting to
    # "rider" so existing clients (and any build shipped before this field
    # existed) keep working unchanged — it only affects conversion reporting,
    # never auth behaviour or the role assigned to the new row.
    client_app: Optional[Literal["rider", "driver"]] = "rider"


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
    rating: Optional[float] = None
    total_rides: Optional[int] = None
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
    # Meta conversion de-duplication id, present only on a genuine signup
    # (is_new_user=True) and only when Meta tracking is configured. The client
    # fires its CompleteRegistration app event with this exact value so Meta
    # collapses the app copy and the server copy into one conversion instead
    # of counting two. Not a secret and not an auth credential — it is a
    # random UUID with no meaning outside Events Manager.
    meta_event_id: Optional[str] = None


class AppSettings(BaseModel):
    id: str = "app_settings"
    google_maps_api_key: str = ""
    stripe_publishable_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Separate signing secret for the Stripe Connect (Connected accounts)
    # webhook endpoint. account.updated / payout.paid / payout.failed are
    # delivered by an endpoint scoped to connected accounts, which Stripe
    # gives its own whsec_. The platform endpoint's stripe_webhook_secret
    # cannot verify those — see construct_event dual-secret logic in
    # routes/webhooks.py.
    stripe_connect_webhook_secret: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    # Spinr driver LMS (training platform) integration. Base URL of the LMS
    # deployment and the shared secret it expects in the x-api-key header
    # (SPINR_INTEGRATION_API_KEY on the LMS side). Used by services/lms_service
    # to pull driver training status into the admin dashboard.
    lms_api_base_url: str = ""
    lms_api_key: str = ""
    # ── Meta (Facebook) Conversions API ──────────────────────────────────
    # Server-side conversion tracking. Lives here rather than in .env for the
    # same reason the Stripe/Twilio credentials do: the access token expires
    # and gets rotated in Events Manager, and rotating it must not require a
    # backend redeploy.
    #
    # Each mobile app has its own Meta App and therefore its own app dataset,
    # which is what keeps the rider funnel and the driver funnel from being
    # reported as one blended audience. Do NOT point either of these at the
    # web pixel (793865613741737) — that dataset is for the website, and app
    # events sent to it are rejected/misattributed.
    #
    # Empty string = tracking disabled. That is the correct, safe default and
    # the state every call site must tolerate: with no token configured the
    # senders log at debug and return, and no Spinr flow changes behaviour.
    meta_rider_dataset_id: str = ""
    meta_driver_dataset_id: str = ""
    meta_capi_access_token: str = ""
    # Non-empty routes ALL events to the Events Manager "Test Events" tab
    # instead of live reporting. Set it to verify the integration, then clear
    # it — leaving it populated means live conversions silently never count.
    meta_test_event_code: str = ""
    driver_matching_algorithm: str = "nearest"
    min_driver_rating: float = 4.0
    search_radius_km: float = 10.0
    # ── Pre-match driver map visibility (PIPEDA / launch gate) ───────────
    # A driver's live position is personal information about a contractor's
    # whereabouts. Before a ride is assigned there is no relationship that
    # justifies exact coordinates, so the rider map gets a coarsened position;
    # exact coordinates are reserved for the assigned-ride tracking path.
    #
    # These live here (DB-backed app_settings) rather than in .env so the
    # granularity can be tuned, and the map killed, without a redeploy — the
    # launch plan requires map visibility to sit behind a kill switch.
    #
    # driver_map_show_locations=False is the "disable" option: the endpoints
    # return no driver positions at all. Riders still see availability counts
    # from /rides/estimate, which never carried coordinates.
    driver_map_show_locations: bool = True
    # Grid cell for coarsening, in metres. 500m keeps the map useful at city
    # zoom ("cars are around me, roughly there") while destroying the
    # resolution needed to follow one vehicle. 0 would mean exact — deliberately
    # not the default, and the pre-match callers clamp it to a floor so a
    # misconfiguration cannot silently re-expose exact positions.
    driver_map_cell_m: int = 500
    # Server-side ceiling on the caller-supplied radius. Without this, one
    # request with radius=1000 sweeps the province, which is the enumeration
    # case the launch gate calls out.
    driver_map_max_radius_km: float = 15.0
    cancellation_fee_admin: DecimalStr = Decimal("0.50")  # Admin gets $0.50
    cancellation_fee_driver: DecimalStr = Decimal("4.00")  # Driver gets $4.00
    platform_fee_percent: DecimalStr = Decimal("0.0")  # 0% commission - driver keeps all fare
    # When false, drivers can go online without an active Spinr Pass. Set
    # this to true to enforce the subscription gate at the "go online" call.
    # Defaults to false so the product works out of the box pre-launch.
    require_driver_subscription: bool = False
    # When true, suspending/closing a corporate account auto-cancels its
    # employees' pre-pickup rides (searching/driver_assigned/driver_accepted/
    # driver_arrived) instead of leaving them to run to completion as if the
    # company were still active. In-progress rides are always grandfathered
    # regardless of this flag — see routes/corporate_accounts.py. Defaults to
    # true because the un-flagged behavior (do nothing) was a bug, not the
    # intended design; flip to false only to roll back without a redeploy.
    corporate_suspend_cancels_pre_pickup_rides: bool = True
    # When true, closing a corporate account (terminal — cannot reopen)
    # refunds any remaining master-wallet balance to the company's Stripe
    # payment method(s), best-effort against original top-up PaymentIntents.
    # Defaults to false: this moves real money via Stripe and must be
    # verified in staging before being switched on for live closures — see
    # services/corporate_wallet_winddown_service.py.
    corporate_close_refunds_wallet_balance: bool = False
    # When true, booking a company_allowance ride with no matching *active*
    # corporate_members row is rejected at booking time (403) instead of
    # silently proceeding and only failing later at settlement. Defaults to
    # true because the un-flagged behavior (fail open) was a bug — a removed
    # member could complete a full ride that lands unbilled in payment
    # limbo. Flip to false only to roll back without a redeploy.
    corporate_member_removal_blocks_booking: bool = True
    # When true, removing/deactivating a corporate member auto-cancels that
    # member's pre-pickup rides (searching/driver_assigned/driver_accepted/
    # driver_arrived), mirroring corporate_suspend_cancels_pre_pickup_rides
    # at the member level. Defaults to true for the same reason. See
    # services/corporate_member_offboarding_service.py.
    corporate_member_removal_cancels_pre_pickup_rides: bool = True
    # When true, booking a company_allowance (or work_profile) ride for a
    # suspended or closed corporate account is rejected at booking time (403)
    # instead of silently proceeding. Suspension/close already cancels that
    # company's in-flight pre-pickup rides (corporate_suspend_cancels_pre_pickup_rides);
    # without this flag new bookings could keep being created against the same
    # inactive account, only failing later at settlement. Defaults to true —
    # the un-flagged behavior (fail open) was a bug. Flip to false only to
    # roll back without a redeploy. See routes/rides/booking.py.
    corporate_inactive_company_blocks_booking: bool = True
    # Kill switch for the scheduled-ride dispatcher loop (utils/scheduled_rides.py).
    # ACTION_ITEMS.md E5: scheduled dispatch was one of the risky background
    # loops with no way to pause it short of a redeploy. Defaults to true
    # (current, always-on behavior). Flip to false to stop the loop from
    # claiming/dispatching or sending reminders for scheduled rides — already-
    # scheduled rides stay parked in status='scheduled' and dispatch normally
    # once this is flipped back on; nothing is lost or cancelled by disabling it.
    scheduled_dispatch_enabled: bool = True
    # New driver-facing behavior (scheduled-rides gap review, Finding #06):
    # a best-effort heads-up push to already-online drivers near an upcoming
    # scheduled pickup, ~60 minutes out. Unlike scheduled_dispatch_enabled
    # above (a kill switch for existing always-on behavior), this gates a
    # genuinely new notification type — ships dark until reviewed for
    # notification-fatigue impact, then flip on from the admin dashboard.
    scheduled_ride_driver_nudge_enabled: bool = False
    # Notice-window cancellation fee for PRE-DISPATCH scheduled rides
    # (Finding #01). Rider-only — no driver is ever assigned pre-dispatch,
    # so unlike cancellation_fee_admin/_driver above nothing is disbursed.
    # New pricing decision — ships dark until reviewed/approved, then flip
    # on from the admin dashboard; no redeploy needed either way.
    scheduled_ride_notice_window_fee_enabled: bool = False
    scheduled_ride_notice_window_minutes: int = 60
    scheduled_ride_notice_window_fee_amount: DecimalStr = Decimal("3.00")
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
    # Postal address parts for the CASL-required marketing footer (migration
    # 192). Public info, not masked. company_address holds the street line;
    # these complete it into a full, legally-valid mailing address.
    company_city: str = ""
    company_province: str = ""
    company_postal_code: str = ""
    # Verified SES sender for MARKETING email, kept separate from
    # aws_ses_from_email so marketing complaints don't harm the transactional
    # sender reputation. Falls back to aws_ses_from_email when blank.
    marketing_from_email: str = ""
    # Admin-configurable URL of the alert tone the driver-app plays when a
    # new ride offer arrives. Null/empty → driver-app falls back to the
    # bundled placeholder mp3. Uploaded via the admin dashboard into
    # Supabase Storage bucket `audio-assets`.
    ride_offer_sound_url: str = ""
    # Distribution list for safety-incident transactional emails. See
    # migration 95 + the _notify_safety_team helper in features.py.
    safety_alert_emails: str = ""
    # ── SOS on-call paging (ACTION_ITEMS.md B15(b)) ───────────────────────
    # Real on-call paging for rider/driver SOS, on top of the admin WS
    # broadcast + safety_alert_emails above. Lives here (not .env) for the
    # same rotation-without-redeploy reason as the Stripe/Twilio/Meta
    # credentials. Empty webhook URL = disabled — the safe, correct default
    # until an admin pastes real PagerDuty/Opsgenie credentials in; see
    # utils/safety_paging.py, which every call site tolerates staying empty.
    # Shape defaults to PagerDuty Events API v2
    # ({"routing_key", "event_action", "payload": {...}}); pointing this at
    # an Opsgenie (or other) webhook that accepts/adapts that shape is a
    # config change here, not a code change.
    sos_paging_webhook_url: str = ""
    sos_paging_routing_key: str = ""
    # Hours of app unreachability before the stale-intent reconciler flips
    # a driver's is_online=false (utils/stale_intent_reconciler.py,
    # migration 146). Range 1-48 enforced by the admin API + DB CHECK.
    stale_intent_offline_hours: float = 4.0
    # ── AI assistant (rider AI mode, backend/ai/) ────────────────────────
    # Master kill switch. Defaults OFF: the feature ships dark and is enabled
    # from the admin dashboard once a provider key is set. Effective within
    # the 60s settings TTL — no redeploy.
    ai_assistant_enabled: bool = False
    # How the apps present AI entry points while ai_assistant_enabled is False:
    #   coming_soon → keep the icon/tab, show a "coming soon" placeholder
    #   hidden      → remove the icon/tab entirely
    # Returned by /ai/config as `mode`; ignored while the assistant is enabled.
    ai_disabled_mode: str = "coming_soon"
    # Mounts the /mcp streamable-HTTP server for external agent clients.
    # The in-app chat does NOT depend on this — tools run in-process.
    ai_mcp_enabled: bool = False
    # Active provider + model, swappable at runtime. Supported providers:
    # anthropic | openai | gemini | openrouter (see backend/ai/providers/).
    ai_provider: str = "anthropic"
    ai_model: str = "claude-haiku-4-5"
    # One key per provider; the admin UI shows a single field bound to the
    # selected provider. Masked in admin GET like the Stripe/Twilio keys.
    ai_api_key_anthropic: str = ""
    ai_api_key_openai: str = ""
    ai_api_key_gemini: str = ""
    ai_api_key_openrouter: str = ""
    # Spend guardrails (per chat turn / per user).
    ai_max_output_tokens: int = 1024
    ai_max_tool_iterations: int = 6
    ai_daily_message_cap: int = 50
    # Per-user/day tool-call cap on the /mcp surface (machine clients poll
    # harder than humans chat). 0 → fall back to ai_daily_message_cap.
    ai_mcp_daily_tool_cap: int = 0
    ai_history_max_messages: int = 12
    # FAQ response cache: replay a stored answer for identical, self-contained,
    # impersonal opener questions (same or different user) so common FAQ turns
    # skip the LLM entirely. Ships dark; TTL bounds FAQ-edit staleness.
    ai_faq_cache_enabled: bool = False
    ai_faq_cache_ttl_seconds: int = 3600
    # Semantic FAQ search: embed the query + FAQ questions and rank by cosine so
    # reworded questions with no shared keyword still match. Ships dark; falls
    # back to lexical matching when off or when a row has no embedding.
    ai_faq_semantic_enabled: bool = False
    ai_embedding_provider: str = ""  # "" | openai | gemini
    ai_embedding_model: str = ""  # blank → provider default
    ai_faq_semantic_min_score: float = 0.30  # cosine floor to count as a match
    # When true, escalate_to_support creates a Zoho ticket. Default is a
    # deep-link handoff only — the AI triggers no server-side side effects.
    ai_escalation_creates_ticket: bool = False
    # Shown under the chat input in both apps; also returned by /ai/config.
    ai_disclaimer: str = "AI answers can be inaccurate. For emergencies, call 911 or use the SOS button."
    # ── iOS Live Activity APNs (Phase 3, .p8 token auth) ─────────────────
    # Direct ActivityKit push (FCM can't carry the liveactivity push type). The
    # feature ships dark until all four are set. apns_p8_key is the secret (masked
    # like the Stripe/Twilio keys); the other three are public identifiers. See
    # utils/apns_client.py.
    apns_key_id: str = ""  # 10-char Key ID (from AuthKey_XXXXXXXXXX.p8)
    apns_team_id: str = ""  # 10-char Apple Team ID
    apns_bundle_id: str = ""  # rider bundle id; topic = this + ".push-type.liveactivity"
    apns_p8_key: str = ""  # full PEM of the .p8 private key (multi-line)
    # ── admin-dashboard visual refresh (epic #2785 Phase 3+) ─────────────
    # Gates the shared shell/typography/radius restyle behind a canary-able
    # flag rather than a big-bang release, given the blast radius is all 34
    # admin-dashboard routes. Read by the frontend's useFeatureFlag() hook
    # via GET /api/admin/settings; effective within the 60s settings TTL.
    admin_theme_v2_enabled: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ServiceArea(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    city: str
    polygon: List[Dict[str, float]]
    is_active: bool = True
    is_airport: bool = False
    airport_fee: DecimalStr = Decimal("0.0")
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
    base_fare: DecimalStr
    per_km_rate: DecimalStr
    per_minute_rate: DecimalStr
    minimum_fare: DecimalStr
    booking_fee: DecimalStr = Decimal("2.0")
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
    # B9 enhancement (ACTION_ITEMS.md): captured from the write-time
    # geocode-verify check when a result was returned; None when
    # verification failed open (no API key, budget exhausted, no match).
    place_id: Optional[str] = None
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
    first_name: Optional[str] = None
    last_name: Optional[str] = None
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

    @field_validator("license_plate")
    @classmethod
    def _check_license_plate(cls, value: str) -> str:
        return validate_license_plate(value)

    @field_validator("vehicle_vin", mode="before")
    @classmethod
    def _check_vin(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return validate_vin(value)

    @field_validator("vehicle_year", mode="before")
    @classmethod
    def _check_vehicle_year(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        return validate_vehicle_year(value)


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
    requires_wav: bool = False
    quiet_mode: bool = False
    rider_notes: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    corporate_account_id: Optional[str] = None
    requires_wav: bool = False
    distance_km: float
    duration_minutes: int
    base_fare: DecimalStr
    distance_fare: DecimalStr = Decimal("0.0")
    time_fare: DecimalStr = Decimal("0.0")
    booking_fee: DecimalStr = Decimal("2.0")
    surge_multiplier: float = 1.0
    total_fare: DecimalStr
    # Rider-facing bill: total_fare + area_fees + tax − discount. Source of
    # truth for the amount displayed on in-progress / end-ride / receipt /
    # wallet screens. Optional in the model so legacy rows without the
    # column (pre-migration 46) still validate.
    grand_total: Optional[DecimalStr] = None
    # Pre-promo fare-side subtotal (mirror of total_fare at creation time,
    # kept stable for receipt math even if a refund or recalc later mutates
    # total_fare).
    subtotal_fare: Optional[DecimalStr] = None
    discount_amount: DecimalStr = Decimal("0.0")
    promo_code: Optional[str] = None
    tip_amount: DecimalStr = Decimal("0.0")
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
    driver_earnings: DecimalStr = Decimal("0.0")  # Distance fare goes to driver
    admin_earnings: DecimalStr = Decimal("0.0")  # Booking fee + platform/city fees go to admin
    cancellation_fee_driver: DecimalStr = Decimal("0.0")
    cancellation_fee_admin: DecimalStr = Decimal("0.0")
    # Rating
    rider_rating: Optional[int] = None
    rider_comment: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RideRatingRequest(BaseModel):
    rating: int = Field(ge=1, le=5, description="Rating must be between 1 and 5")
    comment: Optional[str] = None
    tip_amount: DecimalStr = Field(
        default=Decimal("0.0"), ge=0, le=500, description="Tip amount must be between $0 and $500"
    )


# Scheduled-ride booking window. Single source of truth — the rider app's
# date-picker constraints and the AI booking assistant's own proposal-time
# check (backend/ai/tools_booking.py) both import these rather than hardcode
# a second copy, so the three surfaces can't drift out of sync with each
# other or with the confirm-time validator below.
SCHEDULE_MIN_LEAD_MINUTES = 15
SCHEDULE_MAX_ADVANCE_DAYS = 7


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
    requires_wav: bool = False
    quiet_mode: bool = False
    rider_notes: Optional[str] = None
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
    # SCA two-step: a manual-capture PaymentIntent the rider-app already confirmed
    # on-device (3DS / Apple Pay biometric) after a prior create_ride returned
    # requires_action. When present, create_ride verifies + attaches this hold
    # instead of authorizing afresh — so the SCA challenge completes at BOOKING.
    preauthorized_payment_intent_id: Optional[str] = None
    work_profile: Optional[bool] = None
    promo_code: Optional[str] = None
    requires_wav: bool = False
    planned_route_polyline: Optional[List[List[float]]] = None

    # ── Input validation (SEC-017) ──────────────────────────────────────── #

    @field_validator("pickup_address", "dropoff_address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Address must be at least 3 characters")
        if len(value) > 500:
            raise ValueError("Address must be 500 characters or fewer")
        return value

    @field_validator("pickup_lat", "dropoff_lat")
    @classmethod
    def validate_lat(cls, value: float) -> float:
        if not (-90.0 <= value <= 90.0):
            raise ValueError("Latitude must be between -90 and 90")
        return value

    @field_validator("pickup_lng", "dropoff_lng")
    @classmethod
    def validate_lng(cls, value: float) -> float:
        if not (-180.0 <= value <= 180.0):
            raise ValueError("Longitude must be between -180 and 180")
        return value

    @field_validator("stops")
    @classmethod
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

    @field_validator("scheduled_time", mode="after")
    @classmethod
    def validate_scheduled_time(cls, value: Optional[datetime], info) -> Optional[datetime]:
        if value is not None:
            from datetime import timedelta

            # Normalise to UTC-aware for the "in the future" comparison, then
            # strip tz for the DST-gap round-trip check which needs a naive wall time.
            v_utc = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if v_utc < now_utc + timedelta(minutes=SCHEDULE_MIN_LEAD_MINUTES):
                raise ValueError(f"Scheduled time must be at least {SCHEDULE_MIN_LEAD_MINUTES} minutes in the future")
            # Server-side ceiling matching the rider app's date-picker maxDate.
            # Previously enforced client-only, so any other caller — a direct
            # API request or the AI booking assistant — could schedule
            # arbitrarily far ahead with nothing to reject it.
            if v_utc > now_utc + timedelta(days=SCHEDULE_MAX_ADVANCE_DAYS):
                raise ValueError(f"Scheduled time cannot be more than {SCHEDULE_MAX_ADVANCE_DAYS} days in the future")

            naive = v_utc.replace(tzinfo=None)

            tz_name: Optional[str] = info.data.get("scheduled_timezone")
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

                # DST fall-back guard: the gap check above only catches a
                # local time that doesn't exist (spring-forward). The
                # opposite case — a local time that occurs TWICE (the
                # repeated hour when clocks fall back) — round-trips fine
                # under either interpretation, so it isn't caught by that
                # check at all. Compare the UTC offset under fold=0 (first
                # occurrence) vs fold=1 (second occurrence): equal offsets
                # means the time is unambiguous; different offsets means
                # this exact wall-clock time happens twice on this date, and
                # naive.replace(tzinfo=tz, fold=0) above would have silently
                # picked the first occurrence with no way for the rider (or
                # the regulatory trip-log record) to know which one they meant.
                fold0_offset = local.utcoffset()
                fold1_offset = naive.replace(tzinfo=tz, fold=1).utcoffset()
                if fold0_offset != fold1_offset:
                    raise ValueError(
                        f"The time {naive.strftime('%H:%M')} is ambiguous in {tz_name} on that "
                        "date (DST fall-back — this local time occurs twice). Please choose a "
                        "different time, or specify scheduled_time with an explicit UTC offset."
                    )
        return value
