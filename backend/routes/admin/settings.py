import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
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
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from ... import db_supabase
    from ...core.config import settings as _core_settings
    from ...dependencies import get_admin_user
    from ...settings_loader import get_app_settings
    from ...supabase_client import supabase  # noqa: F401
except ImportError:
    import db_supabase
    from core.config import settings as _core_settings
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
        # Meta Conversions API access token. The dataset ids are identifiers
        # left visible (same treatment as twilio_account_sid) — they're useless
        # without the token, and operators need to read them back to confirm
        # the rider/driver datasets aren't swapped. Only the token is masked.
        "meta_capi_access_token",
        # SOS on-call paging routing key (ACTION_ITEMS.md B15(b)) — PagerDuty
        # "Integration Key" or Opsgenie equivalent. The webhook URL is left
        # visible (same treatment as lms_api_base_url); only the key is the
        # secret. See utils/safety_paging.py.
        "sos_paging_routing_key",
    }
)

# Fields only super_admin may CHANGE (not just reveal). The LMS base URL
# receives lms_api_key on every training lookup, so a settings-module admin
# who could repoint it would exfiltrate the secret (reveal is super_admin-
# only) and gain direct access to the LMS's driver PII — plus backend SSRF.
# Changing either half of the pair therefore requires the same privilege as
# the credential-reveal flow.
#
# posthog_host + posthog_api_key are the same destination shape: a
# settings-module admin who could repoint them would send session recordings
# (masked, but still a view of the rider/driver UI) to a project they
# control. The enable flag stays settings-writable so any settings admin
# can kill-switch without waiting for super_admin.
#
# The Meta dataset ids + access token are the same shape of risk as the LMS
# pair: they are a DESTINATION for user data. A settings-module admin who could
# change them could point the conversions sender at a Meta dataset they
# control, after which the backend exports hashed rider/driver identities (plus
# client IP and user agent) to that destination. Changing the destination or
# the token therefore requires the same privilege as revealing a credential.
# meta_test_event_code is deliberately NOT here — it only routes events to the
# Test Events tab and leaks nothing.
#
# sos_paging_webhook_url is the same shape of risk again: a settings-module
# admin who could repoint it could redirect every future SOS page (which
# carries ride_id / reported_by_user_id / a geohashed area — see
# utils/safety_paging.py's PIPEDA note) to a destination they control, plus
# backend SSRF via the outbound POST. Changing either half of the pair
# requires the same privilege as revealing the routing key.
#
# Corporate + admin portal review, High #4: the live payment/messaging
# credentials were the one class of field where WRITE was still only gated
# by the "settings" module — reveal (read) already required super_admin, but
# a settings-module admin could silently repoint stripe_secret_key,
# stripe_webhook_secret, stripe_connect_webhook_secret, or
# twilio_auth_token to an attacker-controlled Stripe/Twilio account, or swap
# aws_ses_secret_access_key/resend_api_key to redirect outbound email —
# with no way for anyone to even read back the current value and notice it
# changed (that also requires super_admin). This is the same "destination
# credential a lower-privileged admin could silently repoint" shape as the
# LMS/Meta/SOS-paging fields above; changing any of these six now requires
# the same privilege as revealing them.
_SUPER_ADMIN_ONLY_FIELDS = frozenset(
    {
        "lms_api_base_url",
        "lms_api_key",
        "meta_rider_dataset_id",
        "meta_driver_dataset_id",
        "meta_capi_access_token",
        "sos_paging_webhook_url",
        "sos_paging_routing_key",
        "posthog_host",
        "posthog_api_key",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "stripe_connect_webhook_secret",
        "twilio_auth_token",
        "aws_ses_secret_access_key",
        "resend_api_key",
    }
)


# Columns that live on the settings row but are NOT settings — internal state
# that happens to share the singleton, and that no admin UI reads. Dropped from
# the admin-facing GET entirely rather than masked, because masking implies
# "reveal it via /settings/reveal/{field}" and there is nothing here an operator
# should read back.
#
# env_admin_token_version is the super admin's live revocation generation
# (migration 433). Disclosing it to every staff account tells them how many
# times the super admin has been force-logged-out and what version a forged
# token would need to claim — useless without JWT_SECRET, but there is no
# reason to hand it out.
_INTERNAL_ONLY_FIELDS = frozenset({"env_admin_token_version"})


def _mask_credentials(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the settings dict with credential values masked and
    internal-only columns removed."""
    result = {}
    for k, v in settings.items():
        if k in _INTERNAL_ONLY_FIELDS:
            continue
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
    # Rolling-window cap on referrer_reward payouts per referrer
    # (utils/referral_payout.py, ranked blocker #6 / audit finding N2,
    # 2026-08-19) — closes a real-money leak (a $0-cost first_ride_only promo
    # ride otherwise satisfied rider-referral qualification with no cap on
    # repeat payouts to one referrer). 0 explicitly disables the cap
    # (documented escape hatch — only with a legal/fraud sign-off); plain
    # numeric threshold, no credential masking/super-admin gate needed.
    referral_payout_velocity_cap_per_day: Optional[int] = Field(default=None, ge=0, le=1000)
    # Dual-approval gate for large PII-bearing exports (migration 268,
    # routes/admin/compliance.py, routes/admin/data_transfer_export.py) —
    # requires a second super_admin to approve before a large driver/rider
    # export or a >1,000-row compliance report actually runs. The column
    # and the enforcement code already existed; this was the only field
    # missing from this model, so there was no way to turn the gate on
    # without a direct SQL update. Corporate + admin portal review, High #5.
    # Not a credential — plain boolean, no masking/super-admin gate needed
    # to CHANGE it (the gate itself is already super_admin-only to approve,
    # per export_approvals.py's router-level require_super_admin).
    dual_approval_exports_enabled: Optional[bool] = None
    # Daily cumulative cap on one admin's corporate wallet /adjust calls
    # (routes/corporate_wallet.py::manual_adjust). That endpoint accepts up
    # to $100,000 per call with no limit on repeated calls — a compromised
    # or malicious admin session could move an unbounded amount in minutes.
    # Corporate + admin portal review, "$100k/minute" finding. Plain numeric
    # cap, no masking/super-admin gate needed to change it (same posture as
    # dual_approval_exports_enabled above — a process control, not a secret).
    corporate_wallet_admin_adjust_daily_cap: Optional[Decimal] = Field(default=None, gt=0, decimal_places=2)
    # Ships dark (default false / unset): gates POST
    # /admin/corporate-accounts/{id}/subscription (routes/corporate_subscriptions.py),
    # which starts a real recurring Stripe charge against a company. Flip on
    # only after verifying the flow in staging with a real Stripe test-mode
    # account — corporate + admin portal review round 2, flat SaaS
    # subscription billing. Cancelling an existing subscription is never
    # gated behind this flag.
    corporate_subscription_billing_enabled: Optional[bool] = None
    # Kill switch (default true) + threshold for the KYB re-verification
    # staleness reminder loop (utils/kyb_reverification.py) — corporate +
    # admin portal review round 2. Visibility only: flips no company's
    # status, just a log line + metric an admin can act on manually.
    corporate_kyb_reverification_enabled: Optional[bool] = None
    corporate_kyb_reverify_after_months: Optional[int] = Field(default=None, ge=1, le=60)
    # Forced-upgrade gate (ACTION_ITEMS.md E3) — core/middleware.py's
    # ForcedUpgradeMiddleware rejects any request whose X-App-Version header
    # is below this with 426. Empty string (default) = enforcement off for
    # that app. Semver only — free text here would silently disable the
    # comparison for every client.
    min_rider_app_version: Optional[str] = Field(default=None, pattern=r"^$|^\d+\.\d+\.\d+$")
    min_driver_app_version: Optional[str] = Field(default=None, pattern=r"^$|^\d+\.\d+\.\d+$")
    # Ships dark (default false/unset): gates POST /admin/disputes/{id}/
    # submit-evidence (routes/admin/dispute_evidence_submission.py, C23
    # item 5), which calls stripe.Dispute.modify(evidence=...) -- a
    # real, effectively irreversible submission to Stripe on a live
    # chargeback (evidence can be updated but not un-submitted before the
    # dispute's due_by). Same "ship dark, flip on after staging
    # verification" posture as corporate_subscription_billing_enabled
    # above. The endpoint also requires an explicit confirm:true on every
    # call -- this flag alone does not make a single request submit.
    dispute_stripe_evidence_submission_enabled: Optional[bool] = None
    # GPS tracking-overhaul rollout flags (migrations 345/349, all ship dark).
    # These existed as settings columns but were missing from this model, so
    # the admin dashboard's save silently dropped them (extra="ignore") and
    # the owner's flag flips only worked via a direct DB edit. Wiring them
    # here is what makes the documented "flip via admin settings" rollout
    # real. Semantics live at the consumers: breadcrumbs.py (idle capture),
    # period1_distance_finalizer.py, route_finalizer.py (P2 geometry, booked-
    # dropoff tail anchor), ride_repo.py (rider pickup-leg display),
    # route_gap_monitor.py (FCM nudge), stale_p3_closer.py (autoclose).
    idle_location_v2_enabled: Optional[bool] = None
    background_location_fanout_enabled: Optional[bool] = None
    driver_stationary_tracking_enabled: Optional[bool] = None
    period1_distance_tracking_enabled: Optional[bool] = None
    # Migration 370. Driver location marker write gate (utils/
    # location_write_gate.py): False = shadow mode (count-only), True =
    # coalesced REST marker writes actually skip. Added in the same PR as
    # the gate after review caught the column/field pair missing — the
    # exact drift pattern documented above (legacy_consent_notice_enabled).
    location_marker_write_gate_enabled: Optional[bool] = None
    p2_route_geometry_enabled: Optional[bool] = None
    rider_show_pickup_leg_enabled: Optional[bool] = None
    location_health_push_nudge_enabled: Optional[bool] = None
    stale_p3_autoclose_enabled: Optional[bool] = None
    route_booked_dropoff_anchor_enabled: Optional[bool] = None
    incentive_eligibility_enforced: Optional[bool] = None
    # 24h floor: below one day the purge loop would eat evidence the route
    # finalizer's late-tail revisions still need. 90 days (2160h) default per
    # the owner's retention decision, ceiling matches the blanket GPS purge.
    idle_breadcrumb_retention_hours: Optional[int] = Field(default=None, ge=24, le=2160)
    # C50 Phase 1 rollback switch (schemas.AppSettings.dispatch_direct_pool_enabled
    # is the source of truth for the comment/rationale). Not a credential, no
    # masking/super-admin gate needed — same posture as the other kill switches
    # above (scheduled_dispatch_enabled etc.). Default False; Phase 2 (T12/T13,
    # not yet built) is the only thing that reads this as True having any effect.
    dispatch_direct_pool_enabled: Optional[bool] = None
    # #1231 finding 15 kill switch (schemas.AppSettings.minimal_fcm_offer_payload_enabled
    # is the source of truth for the comment/rationale). Not a credential, no
    # masking/super-admin gate needed — same posture as the other kill switches
    # above. Default False; requires human device verification before True has
    # any real effect in production.
    minimal_fcm_offer_payload_enabled: Optional[bool] = None

    @field_validator("dispatch_geo_provider")
    @classmethod
    def _check_geo_provider(cls, v: Optional[str]) -> Optional[str]:
        """The global provider is NOT NULL in the DB — never store blank.

        None means "not sent" (exclude_none drops it from the payload), but
        an explicit empty string would land as '' and make resolve_provider
        fall back to legacy silently, so reject it. Unknown values are a 422
        here rather than a bad value quietly degrading dispatch.
        """
        if v is None:
            return None
        cleaned = v.strip().lower()
        # Lazy import: dispatch_candidates pulls in utils.h3_cells, which
        # hard-imports the optional `h3` wheel. Importing it at module scope
        # would make admin API boot depend on that extra being installed.
        try:
            from ...services.dispatch_candidates import VALID_PROVIDERS
        except ImportError:
            from services.dispatch_candidates import VALID_PROVIDERS  # type: ignore
        if cleaned not in VALID_PROVIDERS:
            raise ValueError(f"dispatch_geo_provider must be one of: {', '.join(sorted(VALID_PROVIDERS))}")
        return cleaned

    @field_validator("lms_api_base_url")
    @classmethod
    def _lms_base_url_scheme(cls, v: Optional[str]) -> Optional[str]:
        """The LMS API key rides on every request to this host — require TLS
        so it can't be sniffed in transit (plain http allowed only for
        localhost during development)."""
        if not v:
            return v
        if v.startswith("https://"):
            return v
        if v.startswith(("http://localhost", "http://127.0.0.1")):
            return v
        raise ValueError("lms_api_base_url must use https:// (http:// is allowed only for localhost)")

    @field_validator("sos_paging_webhook_url")
    @classmethod
    def _sos_paging_webhook_url_scheme(cls, v: Optional[str]) -> Optional[str]:
        """The routing key rides in every POST body to this host, and the
        payload carries safety-incident data (ride_id, reported_by_user_id,
        a geohashed area) — same TLS requirement as lms_api_base_url."""
        if not v:
            return v
        if v.startswith("https://"):
            return v
        if v.startswith(("http://localhost", "http://127.0.0.1")):
            return v
        raise ValueError("sos_paging_webhook_url must use https:// (http:// is allowed only for localhost)")

    @field_validator("posthog_host")
    @classmethod
    def _posthog_host_scheme(cls, v: Optional[str]) -> Optional[str]:
        """Session recordings POST to this host — require TLS so a
        settings-module typo (or a hostile Save) cannot send them in the
        clear. localhost http is allowed for local PostHog only."""
        if not v:
            return v
        if v.startswith("https://"):
            return v
        if v.startswith(("http://localhost", "http://127.0.0.1")):
            return v
        raise ValueError("posthog_host must use https:// (http:// is allowed only for localhost)")

    @field_validator("posthog_api_key")
    @classmethod
    def _posthog_api_key_is_project_key(cls, v: Optional[str]) -> Optional[str]:
        """Reject personal API keys (phx_...) which can mutate the PostHog
        project. Empty stays valid — that is the fail-closed default."""
        if v is None:
            return v
        cleaned = v.strip() if isinstance(v, str) else v
        if not cleaned:
            return cleaned
        if not cleaned.startswith("phc_"):
            raise ValueError("posthog_api_key must be a project API key (phc_...), not a personal API key")
        return cleaned

    @field_validator("stripe_secret_key", "stripe_webhook_secret", "stripe_connect_webhook_secret", mode="before")
    @classmethod
    def _strip_stripe_credentials(cls, v):
        """A copy-pasted Stripe secret (from the Dashboard, a password
        manager, or a terminal) frequently carries a leading/trailing
        newline or space. Stored verbatim, that whitespace becomes part of
        the API key / HMAC signing key and breaks every Stripe call or
        webhook signature verification with no other symptom — see the
        matching defensive `.strip()` on read in routes/webhooks.py, which
        only protects against a value already corrupted before this fix.
        Runs before the environment-prefix check below so a leading space
        doesn't also trip a false "wrong environment" rejection.
        """
        return v.strip() if isinstance(v, str) else v

    @field_validator("stripe_secret_key")
    @classmethod
    def _stripe_secret_key_matches_environment(cls, v: Optional[str]) -> Optional[str]:
        """Corporate + admin portal review, High #4: a key with the wrong
        live/test prefix for the current environment is either a copy-paste
        mistake (accidentally shipping a test key to production, silently
        breaking real payment capture) or an attacker downgrading production
        to an attacker-controlled test key — reject outright rather than
        silently accepting whatever string is submitted. A masked preview
        value round-tripped from GET (see the mask-roundtrip guard in
        admin_update_settings) normally keeps the real key's own prefix, so
        it would pass this check unchanged -- except when the *stored* key's
        prefix no longer matches the current environment (e.g. a leftover
        sk_live_ value sitting in a non-production app_settings row). That
        mismatch used to reject the entire save -- every field on the page,
        not just the credential -- because this validator runs before the
        mask-roundtrip guard ever gets a chance to drop the field. A masked
        preview is never persisted regardless of prefix (the guard always
        strips any `*****`-suffixed value first), so exempting it here can't
        smuggle a real credential through; it only stops an unrelated save
        from being blocked by a credential the admin didn't touch."""
        if not v:
            return v
        if v.endswith("*****"):
            return v
        if _core_settings.ENV.lower() == "production":
            if not v.startswith("sk_live_"):
                raise ValueError("stripe_secret_key must start with sk_live_ in production")
        else:
            if not v.startswith("sk_test_"):
                raise ValueError("stripe_secret_key must start with sk_test_ outside production")
        return v


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
    # Decimal -> float only at this DB write boundary (supabase-py can't
    # serialize Decimal) -- same pattern as corporate_wallet.py's
    # WalletConfigPatch. Values are 2-dp clean by Pydantic validation, so
    # the conversion is exact.
    update_fields = {
        k: float(v) if isinstance(v, Decimal) else v for k, v in settings.model_dump(exclude_none=True).items()
    }

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

    # Privilege gate for fields whose CHANGE is equivalent to a credential
    # reveal (see _SUPER_ADMIN_ONLY_FIELDS). The frontend ships the full
    # settings object on every save, so only reject when the value actually
    # differs from what is stored — an unrelated save by a non-super-admin
    # must keep working.
    if admin.get("role") != "super_admin":
        current = existing or {}
        for field in _SUPER_ADMIN_ONLY_FIELDS:
            if field in update_fields and (update_fields[field] or "") != (current.get(field) or ""):
                raise HTTPException(
                    status_code=403,
                    detail=f"Only super admins can change {field}",
                )

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
    # Must match the enum the frontend actually offers (today/7d/30d/90d/1y —
    # see admin-dashboard/src/app/dashboard/settings/page.tsx and
    # dashboard/heatmap/page.tsx) and the DB column's own SQL DEFAULT
    # ('30d', migration 03_corporate_accounts_heatmap.sql). This dict is a
    # separate, in-Python fallback used only when no heatmap_settings row
    # exists yet — "month" was never a valid option anywhere and left the
    # Select showing nothing selected on a fresh install.
    "heat_map_default_range": "30d",
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

    await log_admin_action(
        admin,
        "heatmap_settings_updated",
        "settings",
        _HEATMAP_SETTINGS_ID,
        {"fields": sorted(k for k in payload if k not in ("id", "updated_at"))},
    )
    return {"message": "Heat map settings updated"}
