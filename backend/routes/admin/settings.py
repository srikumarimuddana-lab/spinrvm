import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from ...utils.audit_logger import log_admin_action  # noqa: F401
except ImportError:
    from utils.audit_logger import log_admin_action  # noqa: F401

from fastapi import (  # noqa: F401
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...settings_loader import get_app_settings
    from ...supabase_client import supabase  # noqa: F401
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from settings_loader import get_app_settings
    from supabase_client import supabase  # noqa: F401

# Driver-app alert ping uploads — 500 KB cap; bucket `audio-assets` must be
# public-read in the Supabase dashboard (see migration 83 comment).
_MAX_SOUND_BYTES = 500 * 1024
_SOUND_BUCKET = "audio-assets"
_SOUND_MIME_TYPES = frozenset({"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav"})

logger = logging.getLogger(__name__)

router = APIRouter()

# Credential fields that must never be returned verbatim in API responses.
_CREDENTIAL_FIELDS = frozenset(
    {
        "stripe_secret_key",
        "stripe_webhook_secret",
        "stripe_connect_webhook_secret",
        "twilio_auth_token",
        "google_maps_api_key",
        # Resend API key is a credential too — without masking it would
        # otherwise round-trip in plaintext on every settings GET.
        "resend_api_key",
        # AWS SES secret (primary email provider, migration 154). The access
        # key id is left visible (identifier, unusable without the secret —
        # same treatment as twilio_account_sid); only the secret is masked.
        "aws_ses_secret_access_key",
        # Legacy SendGrid key: no longer read for sending, but migration 110
        # leaves the column in place, so a still-populated value would round-
        # trip in plaintext on GET unless it stays masked here. Keeps the
        # super-admin-only reveal flow as the only way to read it.
        "sendgrid_api_key",
        # AI assistant provider keys (one per provider; the AI settings card
        # shows a single field bound to the selected provider).
        "ai_api_key_anthropic",
        "ai_api_key_openai",
        "ai_api_key_gemini",
        "ai_api_key_openrouter",
        # iOS Live Activity APNs private key (.p8 PEM). The key_id/team_id/
        # bundle_id are identifiers left visible (same treatment as
        # twilio_account_sid); only the PEM is the secret.
        "apns_p8_key",
        # Driver LMS integration shared secret (x-api-key header) — the base
        # URL stays visible; only the key is the secret.
        "lms_api_key",
    }
)


def _mask_credentials(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the settings dict with credential values masked."""
    result = {}
    for k, v in settings.items():
        if k in _CREDENTIAL_FIELDS and isinstance(v, str) and v:
            result[k] = v[:8] + "*****"
        else:
            result[k] = v
    return result


# ---------- Settings (single row id='app_settings', flat keys) ----------


class SettingsUpdateRequest(BaseModel):
    """Schema for admin settings updates.

    The frontend ships the full settings object on every save (it doesn't
    track which fields the user actually changed). That object includes
    DB-managed columns like ``id``, ``created_at``, ``updated_at`` and any
    fields persisted by older migrations that aren't on this Pydantic
    model. With ``extra="forbid"`` any one of them caused a 422 and the
    whole save failed — operators reported it as "I can't save the
    tracking URL" but track_base_url itself was innocent.

    Now ``extra="ignore"``: unknown keys are silently dropped at
    validation. Persistence is still constrained to schema-defined fields
    because admin_update_settings calls ``model_dump(exclude_none=True)``,
    which never includes keys outside the model. So unknowns can't be
    smuggled into the DB through this endpoint.
    """

    model_config = ConfigDict(extra="ignore")

    google_maps_api_key: Optional[str] = None
    stripe_publishable_key: Optional[str] = None
    stripe_secret_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None
    # Connected-accounts endpoint signing secret (account.updated, payout.*).
    stripe_connect_webhook_secret: Optional[str] = None
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_from_number: Optional[str] = None
    # Twilio Proxy is what masks rider↔driver phone numbers during a ride;
    # SID lives in app_settings so it rotates without a redeploy.
    twilio_proxy_service_sid: Optional[str] = None
    # Resend powers transactional email (receipts, T4A links, support).
    # api_key is a credential (masked on GET); from_email is plain.
    resend_api_key: Optional[str] = None
    resend_from_email: Optional[str] = None
    # AWS SES is the PRIMARY transactional-email provider (migration 154);
    # Resend above is the guardrail fallback. secret_access_key is a
    # credential (masked on GET); the rest are plain.
    aws_ses_region: Optional[str] = None
    aws_ses_access_key_id: Optional[str] = None
    aws_ses_secret_access_key: Optional[str] = None
    aws_ses_from_email: Optional[str] = None
    # Expected SNS topic ARN for the SES bounce/complaint webhook. When set,
    # /webhooks/ses rejects SNS messages from any other topic.
    aws_ses_sns_topic_arn: Optional[str] = None
    # Company info shown on rider receipts + driver T4A slips + the
    # admin dashboard footer. Edited via the Settings page → Company tab.
    company_name: Optional[str] = None
    company_address: Optional[str] = None
    company_phone: Optional[str] = None
    company_email: Optional[str] = None
    company_website: Optional[str] = None
    # Locks the rider's quoted fare at booking time so the receipt can't
    # drift if Maps changes the route mid-trip. Toggle on the Settings page.
    fare_lock_enabled: Optional[bool] = None
    driver_matching_algorithm: Optional[str] = None
    min_driver_rating: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    search_radius_km: Optional[float] = Field(default=None, ge=1, le=100)
    cancellation_fee_admin: Optional[float] = Field(default=None, ge=0, le=50)
    cancellation_fee_driver: Optional[float] = Field(default=None, ge=0, le=50)
    platform_fee_percent: Optional[float] = Field(default=None, ge=0, le=1.0)
    require_driver_subscription: Optional[bool] = None
    terms_of_service_text: Optional[str] = None
    privacy_policy_text: Optional[str] = None
    # Driver-app alert ping. URL points at an mp3/wav in Supabase Storage
    # bucket `audio-assets`. Empty string clears the override and reverts
    # the driver-app to the bundled placeholder.
    ride_offer_sound_url: Optional[str] = None
    # Public base URL for the rider-app "Share Trip" tracking page (the
    # admin-dashboard `/track/[token]` route). Set this to your deployed
    # admin domain so safety contacts can open ${base}/${share_token}.
    # Empty string disables the in-app share link until configured.
    track_base_url: Optional[str] = None
    # Comma-separated email addresses notified when a safety_incidents
    # row opens (rider SOS, driver safety report, auto check-in
    # escalation). Edited via Settings → Safety. Blank disables outbound
    # email; WS broadcast + DB row still fire.
    safety_alert_emails: Optional[str] = None
    # Dispatch & matching — also configurable per service area (area overrides global).
    max_simultaneous_offers: Optional[int] = Field(default=None, ge=1, le=10)
    ride_offer_timeout_seconds: Optional[int] = Field(default=None, ge=5, le=60)
    use_eta_ranking: Optional[bool] = None
    # Hours of unreachability before the stale-intent reconciler flips a
    # driver's is_online=false (migration 146). Bounds mirror the DB CHECK.
    stale_intent_offline_hours: Optional[float] = Field(default=None, ge=1, le=48)
    # Payments — auto-heal of rides stranded in payment_status='processing'.
    # When true, the daily Stripe reconcile finalises such rides (mark-paid
    # ONLY, from Stripe truth) instead of just flagging them. Defaults OFF; see
    # utils/stripe_reconcile._maybe_heal_stuck_processing. Money-moving — enable
    # only after staging validation.
    stripe_auto_heal_processing: Optional[bool] = None
    # AI assistant (rider AI mode, backend/ai/) — provider/model swap at
    # runtime, keys masked like the Stripe/Twilio credentials above.
    ai_assistant_enabled: Optional[bool] = None
    ai_disabled_mode: Optional[str] = Field(default=None, pattern="^(coming_soon|hidden)$")
    ai_mcp_enabled: Optional[bool] = None
    ai_provider: Optional[str] = Field(default=None, pattern="^(anthropic|openai|gemini|openrouter)$")
    ai_model: Optional[str] = Field(default=None, max_length=120)
    ai_api_key_anthropic: Optional[str] = None
    ai_api_key_openai: Optional[str] = None
    ai_api_key_gemini: Optional[str] = None
    ai_api_key_openrouter: Optional[str] = None
    ai_max_output_tokens: Optional[int] = Field(default=None, ge=128, le=4096)
    ai_max_tool_iterations: Optional[int] = Field(default=None, ge=1, le=10)
    ai_daily_message_cap: Optional[int] = Field(default=None, ge=1, le=500)
    ai_history_max_messages: Optional[int] = Field(default=None, ge=2, le=50)
    ai_faq_cache_enabled: Optional[bool] = None
    ai_faq_cache_ttl_seconds: Optional[int] = Field(default=None, ge=60, le=86400)
    ai_faq_semantic_enabled: Optional[bool] = None
    # Allow "" (the unconfigured default the frontend sends back on every save)
    # in addition to a real provider — otherwise an unrelated settings save 422s.
    ai_embedding_provider: Optional[str] = Field(default=None, pattern="^(openai|gemini|)$")
    ai_embedding_model: Optional[str] = Field(default=None, max_length=120)
    ai_faq_semantic_min_score: Optional[float] = Field(default=None, ge=0, le=1)
    ai_escalation_creates_ticket: Optional[bool] = None
    ai_disclaimer: Optional[str] = Field(default=None, max_length=300)
    # iOS Live Activity APNs (.p8 token auth). key_id/team_id/bundle_id are
    # identifiers (visible); apns_p8_key is the secret (masked, in
    # _CREDENTIAL_FIELDS). Feature ships dark until all four are set.
    apns_key_id: Optional[str] = Field(default=None, max_length=20)
    apns_team_id: Optional[str] = Field(default=None, max_length=20)
    apns_bundle_id: Optional[str] = Field(default=None, max_length=200)
    apns_p8_key: Optional[str] = None
    # Driver LMS (training platform) integration — services/lms_service.py.
    # base_url is plain; api_key is a credential (masked, in
    # _CREDENTIAL_FIELDS) matching SPINR_INTEGRATION_API_KEY on the LMS.
    lms_api_base_url: Optional[str] = Field(default=None, max_length=300)
    lms_api_key: Optional[str] = None


@router.get("/settings")
async def admin_get_settings(admin: dict = Depends(get_admin_user)):
    """Get all settings. Credential fields are masked — use /settings/reveal/{field} to read a value."""
    raw = await get_app_settings()
    return _mask_credentials(raw)


@router.get("/ai/catalog")
async def admin_ai_catalog(admin: dict = Depends(get_admin_user)):
    """Provider → model suggestions for the AI Assistant settings card.

    Entries are suggestions only — the card also accepts a custom model id
    (an invalid id surfaces as a provider error on the next chat turn).
    """
    try:
        from ai.catalog import get_catalog
    except ImportError:
        from ...ai.catalog import get_catalog
    return get_catalog()


@router.get("/settings/reveal/{field}")
async def admin_reveal_setting(field: str, admin: dict = Depends(get_admin_user)):
    """Return the plaintext value of a single credential field. super_admin only. Always audited."""
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admins can reveal credential values")
    if field not in _CREDENTIAL_FIELDS:
        raise HTTPException(status_code=400, detail=f"Field '{field}' is not a revealable credential")
    raw = await get_app_settings()
    value = raw.get(field)
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "settings_credential_revealed",
            "entity_type": "settings",
            "entity_id": field,
            "details": {"field": field},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"field": field, "value": value}


@router.put("/settings")
async def admin_update_settings(settings: SettingsUpdateRequest, admin: dict = Depends(get_admin_user)):
    """Update settings (upsert single app_settings row). Writes an audit log entry."""
    # First check if settings row exists
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("settings", {"id": "app_settings"}, limit=1)
    )

    # Only persist fields the caller actually set (None = leave unchanged).
    update_fields = settings.model_dump(exclude_none=True)

    # Mask-roundtrip guard. _mask_credentials returns `v[:8] + "*****"` on
    # GET so the admin UI never sees the plaintext secret. The frontend
    # ships the full settings object back on save without distinguishing
    # masked from edited values, so without this filter a save would
    # overwrite the real credential with its masked preview. Drop any
    # credential field whose incoming value looks like a mask preview —
    # the user has to use the per-field reveal+edit flow to actually
    # change a credential.
    for field in _CREDENTIAL_FIELDS:
        v = update_fields.get(field)
        if isinstance(v, str) and v.endswith("*****"):
            update_fields.pop(field, None)

    payload = {
        "id": "app_settings",
        **update_fields,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if existing:
        update_payload = {k: v for k, v in payload.items() if k != "id"}
        await db_supabase.update_one("settings", {"id": "app_settings"}, update_payload)
    else:
        await db_supabase.insert_one("settings", payload)

    # Audit log — record which keys changed, never the values.
    changed_keys = list(update_fields.keys())
    audit_id = str(uuid.uuid4())
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": audit_id,
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "settings_updated",
            "entity_type": "settings",
            "entity_id": "app_settings",
            "details": {"changed_keys": changed_keys},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"message": "Settings updated", "audit_log_id": audit_id}


@router.post("/settings/ride-offer-sound")
async def admin_upload_ride_offer_sound(
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    """Upload the driver-app ride-offer alert tone (mp3/wav, ≤500 KB).

    Stores the file in Supabase Storage bucket `audio-assets` under
    `ride-offer/{uuid4}{ext}` and writes the public URL to
    `settings.ride_offer_sound_url`. Driver-app pulls the URL on next
    `/drivers/config` refresh; null/empty falls back to the bundled
    placeholder mp3 in driver-app/assets/sounds/.
    """
    content_type = file.content_type or "application/octet-stream"
    # iOS/Android pickers sometimes report mp3 as audio/mp3 vs audio/mpeg.
    if content_type == "audio/mp3":
        content_type = "audio/mpeg"
    if content_type not in _SOUND_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content-type {content_type}. Accepted: {sorted(_SOUND_MIME_TYPES)}",
        )

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_SOUND_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 500 KB limit")

    ext = ".mp3" if content_type == "audio/mpeg" else ".wav"
    object_path = f"ride-offer/{uuid.uuid4()}{ext}"

    try:
        supabase.storage.from_(_SOUND_BUCKET).upload(
            file=file_bytes,
            path=object_path,
            file_options={"content-type": content_type, "upsert": "true"},
        )
    except Exception as e:
        logger.error("Ride-offer sound upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Storage upload failed") from e

    public_url_res = supabase.storage.from_(_SOUND_BUCKET).get_public_url(object_path)
    public_url = public_url_res if isinstance(public_url_res, str) else getattr(public_url_res, "public_url", None)
    if not public_url:
        raise HTTPException(status_code=502, detail="Could not resolve public URL for uploaded sound")

    # Persist on the single app_settings row.
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("settings", {"id": "app_settings"}, limit=1)
    )
    payload = {
        "ride_offer_sound_url": public_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if existing:
        await db_supabase.update_one("settings", {"id": "app_settings"}, payload)
    else:
        await db_supabase.insert_one("settings", {"id": "app_settings", **payload})

    # Audit — log the URL change but not the file contents.
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "ride_offer_sound_uploaded",
            "entity_type": "settings",
            "entity_id": "app_settings",
            "details": {"url": public_url, "bytes": len(file_bytes)},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info(
        "[admin] ride-offer sound uploaded admin_id=%s bytes=%d",
        admin.get("id"),
        len(file_bytes),
    )
    return {"ride_offer_sound_url": public_url}


# ---------- Heat Map Settings ----------

_HEATMAP_SETTINGS_ID = "heatmap_settings"

_DEFAULT_HEATMAP_SETTINGS = {
    "heat_map_enabled": True,
    "heat_map_default_range": "month",
    "heat_map_intensity": "medium",
    "heat_map_radius": 25,
    "heat_map_blur": 15,
    "heat_map_gradient_start": "#00ff00",
    "heat_map_gradient_mid": "#ffff00",
    "heat_map_gradient_end": "#ff0000",
    "heat_map_show_pickups": True,
    "heat_map_show_dropoffs": True,
    "corporate_heat_map_enabled": True,
    "regular_rider_heat_map_enabled": True,
}


class HeatmapSettingsRequest(BaseModel):
    heat_map_enabled: Optional[bool] = None
    heat_map_default_range: Optional[str] = None
    heat_map_intensity: Optional[str] = None
    heat_map_radius: Optional[int] = None
    heat_map_blur: Optional[int] = None
    heat_map_gradient_start: Optional[str] = None
    heat_map_gradient_mid: Optional[str] = None
    heat_map_gradient_end: Optional[str] = None
    heat_map_show_pickups: Optional[bool] = None
    heat_map_show_dropoffs: Optional[bool] = None
    corporate_heat_map_enabled: Optional[bool] = None
    regular_rider_heat_map_enabled: Optional[bool] = None


@router.get("/settings/heatmap")
async def admin_get_heatmap_settings(admin: dict = Depends(get_admin_user)):
    """Return heat-map display settings (single settings row)."""
    row = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("settings", {"id": _HEATMAP_SETTINGS_ID}, limit=1)
    )
    if row:
        # Merge defaults with stored values so new keys always appear
        merged = {**_DEFAULT_HEATMAP_SETTINGS, **row}
        merged.pop("_id", None)
        return merged
    return {**_DEFAULT_HEATMAP_SETTINGS, "id": _HEATMAP_SETTINGS_ID}


@router.put("/settings/heatmap")
async def admin_update_heatmap_settings(data: HeatmapSettingsRequest, admin: dict = Depends(get_admin_user)):
    """Update heat-map display settings."""
    payload = {
        "id": _HEATMAP_SETTINGS_ID,
        **{k: v for k, v in data.model_dump(exclude_none=True).items() if k in _DEFAULT_HEATMAP_SETTINGS},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("settings", {"id": _HEATMAP_SETTINGS_ID}, limit=1)
    )
    if existing:
        update_fields = {k: v for k, v in payload.items() if k != "id"}
        await db_supabase.update_one("settings", {"id": _HEATMAP_SETTINGS_ID}, update_fields)
    else:
        await db_supabase.insert_one("settings", payload)

    return {"message": "Heat map settings updated"}
