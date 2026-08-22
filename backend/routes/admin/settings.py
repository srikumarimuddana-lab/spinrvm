import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

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
        "stripe_secret_key",
        "stripe_webhook_secret",
        "stripe_connect_webhook_secret",
        "twilio_auth_token",
        "aws_ses_secret_access_key",
        "resend_api_key",
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
    # Kill switch for re-provisioning Stripe identities stranded by a
    # test→live key rotation (see AppSettings for the full rationale).
    # Settable here so it can be turned off without a redeploy.
    stripe_reprovision_stale_ids: Optional[bool] = None
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
    # Global kill switch for lifecycle emails (utils/email_notifications.py) --
    # welcome, receipt, statement, etc. Defaults true (DB default). Found
    # missing from this model 2026-08-22 by the settings-write drift guard.
    lifecycle_emails_enabled: Optional[bool] = None
    # Dedicated CASL marketing-email sender, separate from the transactional
    # aws_ses_from_email/resend_from_email above (utils/marketing_email.py).
    # Found missing from this model 2026-08-22 by the settings-write drift guard.
    marketing_from_email: Optional[str] = None
    # Company info shown on rider receipts + driver T4A slips + the
    # admin dashboard footer. Edited via the Settings page → Company tab.
    company_name: Optional[str] = None
    # Product/brand name for email BODY copy, independent of the legal-entity
    # company_name above. See schemas.AppSettings.company_app_name and
    # ACTION_ITEMS.md N17.
    company_app_name: Optional[str] = None
    company_address: Optional[str] = None
    # City/province/postal came later (migration 192) than company_address
    # and were never added to the Settings page or this model -- so
    # address_lines()/postal_address() (utils/address_format.py) always saw
    # them blank on every receipt/email footer that used them, with no
    # admin path to set them except a direct DB write. Found 2026-08-22 by
    # the settings-write drift guard (test_admin_settings_write_allowlist_
    # drift.py). company_address alone can still hold a full address --
    # these are additive, not required.
    company_city: Optional[str] = None
    company_province: Optional[str] = None
    company_postal_code: Optional[str] = None
    company_phone: Optional[str] = None
    company_email: Optional[str] = None
    company_website: Optional[str] = None
    # Logo for transactional-email headers. Empty = the bundled Spinr asset
    # served at /api/v1/branding/spinr-logo.png, which is the normal setting.
    # Validated at render time by utils/company_details._safe_logo_url, which
    # falls back to the bundled asset for anything that is not an absolute
    # http(s) URL. Does NOT affect report PDF/Excel/Word headers.
    company_logo_url: Optional[str] = None
    # Renders the ride receipt and Spinr Pass invoice with the shared branded
    # shell and the company details above. Presentation only — never the fare
    # rows, GST/PST line items or totals. See migration 288.
    branded_receipt_enabled: Optional[bool] = None
    # Locks the rider's quoted fare at booking time so the receipt can't
    # drift if Maps changes the route mid-trip. Toggle on the Settings page.
    fare_lock_enabled: Optional[bool] = None
    # Fare-pricing distance basis (routes/rides/_shared.py's
    # select_fare_distance, read at estimate + booking time). "road" prices on
    # the actual road route (DB default); "shadow" bills haversine but still
    # fetches the road route to log the delta, for de-risking a rollout;
    # "haversine" is the straight-line kill switch. Money-adjacent -- typed as
    # a closed enum rather than free text so an admin can't set a value the
    # reader silently treats as non-"road". Found missing from this model
    # 2026-08-22 by the settings-write drift guard.
    fare_distance_basis: Optional[Literal["road", "shadow", "haversine"]] = None
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
    # Real on-call paging for rider/driver SOS (ACTION_ITEMS.md B15(b)),
    # additive to safety_alert_emails above — utils/safety_paging.py.
    # webhook_url is plain; routing_key is a credential (masked, in
    # _CREDENTIAL_FIELDS). Changing either is super_admin-only
    # (_SUPER_ADMIN_ONLY_FIELDS). Empty webhook_url = disabled (default);
    # this ships dark until an admin configures real PagerDuty/Opsgenie
    # credentials.
    sos_paging_webhook_url: Optional[str] = None
    sos_paging_routing_key: Optional[str] = None
    # Safety panel — global tiles (migration 316 covers the per-service-area
    # local-authority row instead). Surfaced to the apps via GET /settings.
    # Blank email/phone hides that tile, same "render only what's configured"
    # rule the authority row uses. Not credentials — these are published to
    # riders and drivers by design, so no masking or super_admin gate.
    safety_team_email: Optional[str] = None
    safety_team_phone: Optional[str] = None
    sos_show_share_trip: Optional[bool] = None
    sos_show_report_issue: Optional[bool] = None
    # Dispatch & matching — also configurable per service area (area overrides global).
    max_simultaneous_offers: Optional[int] = Field(default=None, ge=1, le=10)
    ride_offer_timeout_seconds: Optional[int] = Field(default=None, ge=5, le=60)
    use_eta_ranking: Optional[bool] = None
    # Hours of unreachability before the stale-intent reconciler flips a
    # driver's is_online=false (migration 146). Bounds mirror the DB CHECK.
    stale_intent_offline_hours: Optional[float] = Field(default=None, ge=1, le=48)
    # ── Driver demand heatmap (HM-13 / AD-05, columns from migration 311) ──
    # These MUST stay declared here. The model is extra="ignore", so an
    # undeclared field is dropped at validation while the endpoint still
    # returns 200 and writes an audit row with changed_keys: [] — which is
    # exactly how the heatmap config shipped as a silent no-op that reported
    # success. Bounds mirror the DB CHECK in migration 311 and the runtime
    # clamps in routes/drivers/profile.py (three layers, deliberately).
    driver_heatmap_enabled: Optional[bool] = None
    driver_heatmap_v2_enabled: Optional[bool] = None
    # Dark-launch allowlist of driver *user* IDs (users.id — not drivers.id).
    # Capped so an admin paste cannot park an unbounded list in the row.
    heatmap_internal_driver_ids: Optional[List[str]] = Field(default=None, max_length=500)
    heatmap_k_floor: Optional[int] = Field(default=None, ge=1, le=50)
    heatmap_cell_lat_deg: Optional[float] = Field(default=None, ge=0.0005, le=0.05)
    heatmap_cell_lng_deg: Optional[float] = Field(default=None, ge=0.0005, le=0.05)
    heatmap_decay_half_life_days: Optional[float] = Field(default=None, ge=0.5, le=30)
    # Floored at 30s: this interval multiplies across every online driver.
    heatmap_refresh_seconds: Optional[int] = Field(default=None, ge=30, le=600)
    # Payments — auto-heal of rides stranded in payment_status='processing'.
    # When true, the daily Stripe reconcile finalises such rides (mark-paid
    # ONLY, from Stripe truth) instead of just flagging them. Defaults OFF; see
    # utils/stripe_reconcile._maybe_heal_stuck_processing. Money-moving — enable
    # only after staging validation.
    stripe_auto_heal_processing: Optional[bool] = None
    # Notification throttling (quiet hours + daily cap) — see migration 304.
    # Defaults OFF; ship dark, verify in staging, then flip on. Global for
    # every rider/driver — no per-user override yet.
    notification_throttling_enabled: Optional[bool] = None
    notification_quiet_hours_start: Optional[str] = Field(default=None, pattern="^([01]\\d|2[0-3]):[0-5]\\d$")
    notification_quiet_hours_end: Optional[str] = Field(default=None, pattern="^([01]\\d|2[0-3]):[0-5]\\d$")
    notification_daily_cap: Optional[int] = Field(default=None, ge=0, le=100)
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
    # 0 = "not set" → /mcp falls back to ai_daily_message_cap (schemas.py).
    ai_mcp_daily_tool_cap: Optional[int] = Field(default=None, ge=0, le=5000)
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
    # Changing either field is super_admin-only (_SUPER_ADMIN_ONLY_FIELDS).
    lms_api_base_url: Optional[str] = Field(default=None, max_length=300)
    lms_api_key: Optional[str] = None
    # Meta (Facebook) Conversions API — utils/meta_capi.py. Dataset ids are
    # plain identifiers; the access token is a credential (masked on GET, in
    # _CREDENTIAL_FIELDS). meta_test_event_code routes events to the Events
    # Manager "Test Events" tab — it must be cleared for live conversions to
    # count, so it is deliberately editable here rather than env-only.
    meta_rider_dataset_id: Optional[str] = Field(default=None, max_length=64)
    meta_driver_dataset_id: Optional[str] = Field(default=None, max_length=64)
    meta_capi_access_token: Optional[str] = None
    meta_test_event_code: Optional[str] = Field(default=None, max_length=64)
    # admin-dashboard visual refresh (epic #2785 Phase 3+) — canary flag for
    # the shared shell/typography/radius restyle. Not a credential, no
    # special masking/super-admin gate needed.
    admin_theme_v2_enabled: Optional[bool] = None
    # Driver SOS discreet-hold-shield rollout gate (ACTION_ITEMS.md B16) —
    # dark-launched, driver-app only. Not a credential, no masking/
    # super-admin gate needed.
    driver_discreet_sos_enabled: Optional[bool] = None
    # Ride-less SOS rollout gate (ACTION_ITEMS.md B15(c)) -- dark-launched,
    # rider-app only. Not a credential, no masking/super-admin gate needed.
    # See schemas.py::AppSettings.rideless_sos_enabled for the sign-off
    # requirement before enabling in any environment.
    rideless_sos_enabled: Optional[bool] = None
    # Legacy/re-consent notice rollout gate (ACTION_ITEMS.md, 2026-08-19
    # legacy-migration audit) -- dark-launched, both apps. Not a credential,
    # no masking/super-admin gate needed. See schemas.py::AppSettings.
    # legacy_consent_notice_enabled for what flipping this on actually does
    # (both apps are live-wired to it; it is not a no-op).
    legacy_consent_notice_enabled: Optional[bool] = None
    # Kill switches (ACTION_ITEMS.md E5). scheduled_dispatch_enabled already
    # existed in AppSettings/gated the loop (2026-08-02) but was never added
    # here — there was previously no way to set it via the admin API at all,
    # only a direct DB update. All four are plain booleans, no credential
    # masking/super-admin gate needed.
    scheduled_dispatch_enabled: Optional[bool] = None
    surge_engine_enabled: Optional[bool] = None
    promo_redemption_enabled: Optional[bool] = None
    corporate_billing_enabled: Optional[bool] = None
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
    period1_distance_tracking_enabled: Optional[bool] = None
    p2_route_geometry_enabled: Optional[bool] = None
    rider_show_pickup_leg_enabled: Optional[bool] = None
    location_health_push_nudge_enabled: Optional[bool] = None
    stale_p3_autoclose_enabled: Optional[bool] = None
    route_booked_dropoff_anchor_enabled: Optional[bool] = None
    # 24h floor: below one day the purge loop would eat evidence the route
    # finalizer's late-tail revisions still need. 90 days (2160h) default per
    # the owner's retention decision, ceiling matches the blanket GPS purge.
    idle_breadcrumb_retention_hours: Optional[int] = Field(default=None, ge=24, le=2160)
    # Route-integrity v2 rollout gate (routes/drivers/ride_complete.py's
    # _get_route_integrity_mode) -- "off"/"shadow"/"on" only; the reader
    # 503s on any other value rather than silently weakening the guard, so
    # this is typed as a closed enum to match. Found missing from this model
    # 2026-08-22 by the settings-write drift guard.
    route_integrity_v2_mode: Optional[Literal["off", "shadow", "on"]] = None
    # GPS location-gap alert threshold, seconds (utils/route_gap_monitor.py).
    # Must be positive -- the reader raises on <= 0. Default 30s (DEFAULT_
    # GAP_ALERT_SECONDS). Found missing from this model 2026-08-22 by the
    # settings-write drift guard.
    route_location_gap_alert_seconds: Optional[int] = Field(default=None, gt=0)

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
        admin_update_settings) already starts with the real key's own
        prefix, so it passes this check unchanged."""
        if not v:
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

    await log_admin_action(
        admin,
        "heatmap_settings_updated",
        "settings",
        _HEATMAP_SETTINGS_ID,
        {"fields": sorted(k for k in payload if k not in ("id", "updated_at"))},
    )
    return {"message": "Heat map settings updated"}
