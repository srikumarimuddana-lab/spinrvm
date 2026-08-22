# backend/tests/test_admin_settings_write_allowlist_drift.py
"""Static drift guard: every `settings` table column should be reachable via
`PUT /api/admin/settings` (SettingsUpdateRequest), unless explicitly excluded.

Why this exists: `legacy_consent_notice_enabled` (migration 356) was live in
the DB and fully wired into both apps' read paths for weeks, but was never
added to `SettingsUpdateRequest` -- so there was no supported way to turn it
on except a direct DB write (found 2026-08-22, fixed same day). This is a
recurring pattern: `rideless_sos_enabled` had the identical gap before it.
Nothing in the codebase enforced that a new `settings` column also gets a
matching admin-write field, so gaps like this drift in silently.

Design note: this repo has no live-Supabase tier that actually runs in CI
(see test_corporate_b2b_schema.py's `@pytest.mark.skip` integration tests --
they're documented as "run manually against staging/prod"). Reconstructing
the full `settings` schema by regex-replaying all ~450 migration files was
tried and rejected: many ADD COLUMN statements span multiple columns per
statement or live inside larger DDL blocks the naive replay missed dozens of
columns from (verified against a live `information_schema.columns` query
2026-08-22: the regex replay found only 36 of 129 real columns). A live
schema is the only reliable source of truth for this, so `KNOWN_SETTINGS_COLUMNS`
below is a maintained snapshot, not a scan.

**Maintenance**: whenever a migration adds/drops a `settings` column, update
`KNOWN_SETTINGS_COLUMNS` in the same PR (query `information_schema.columns`
for `table_name = 'settings'` against the live project, or just add/remove
the one column name you touched). This test's whole value is in being
inconvenient to stay green without a real diff.
"""

from routes.admin.settings import SettingsUpdateRequest

# Snapshot of `settings` table columns, taken directly from
# `information_schema.columns` on the live Supabase project
# (soavhtdhefowwvforzwb) on 2026-08-22. 129 columns.
KNOWN_SETTINGS_COLUMNS = frozenset(
    {
        "admin_theme_v2_enabled",
        "ai_api_key_anthropic",
        "ai_api_key_gemini",
        "ai_api_key_openai",
        "ai_api_key_openrouter",
        "ai_assistant_enabled",
        "ai_daily_message_cap",
        "ai_disabled_mode",
        "ai_disclaimer",
        "ai_embedding_model",
        "ai_embedding_provider",
        "ai_escalation_creates_ticket",
        "ai_faq_cache_enabled",
        "ai_faq_cache_ttl_seconds",
        "ai_faq_semantic_enabled",
        "ai_faq_semantic_min_score",
        "ai_history_max_messages",
        "ai_max_output_tokens",
        "ai_max_tool_iterations",
        "ai_mcp_daily_tool_cap",
        "ai_mcp_enabled",
        "ai_model",
        "ai_provider",
        "apns_bundle_id",
        "apns_key_id",
        "apns_p8_key",
        "apns_team_id",
        "aws_ses_access_key_id",
        "aws_ses_from_email",
        "aws_ses_region",
        "aws_ses_secret_access_key",
        "aws_ses_sns_topic_arn",
        "branded_receipt_enabled",
        "cancellation_fee_admin",
        "cancellation_fee_driver",
        "company_address",
        "company_app_name",
        "company_city",
        "company_email",
        "company_logo_url",
        "company_name",
        "company_phone",
        "company_postal_code",
        "company_province",
        "company_website",
        "corporate_billing_enabled",
        "corporate_kyb_reverification_enabled",
        "corporate_kyb_reverify_after_months",
        "corporate_subscription_billing_enabled",
        "corporate_wallet_admin_adjust_daily_cap",
        "driver_discreet_sos_enabled",
        "driver_heatmap_enabled",
        "driver_heatmap_v2_enabled",
        "driver_matching_algorithm",
        "dual_approval_exports_enabled",
        "fare_distance_basis",
        "fare_lock_enabled",
        "google_maps_api_key",
        "heatmap_cell_lat_deg",
        "heatmap_cell_lng_deg",
        "heatmap_decay_half_life_days",
        "heatmap_internal_driver_ids",
        "heatmap_k_floor",
        "heatmap_refresh_seconds",
        "id",
        "idle_breadcrumb_retention_hours",
        "idle_location_v2_enabled",
        "is_referral_active",
        "legacy_consent_notice_enabled",
        "lifecycle_emails_enabled",
        "lms_api_base_url",
        "lms_api_key",
        "location_health_push_nudge_enabled",
        "marketing_from_email",
        "max_simultaneous_offers",
        "meta_capi_access_token",
        "meta_driver_dataset_id",
        "meta_rider_dataset_id",
        "meta_test_event_code",
        "min_driver_app_version",
        "min_driver_rating",
        "min_rider_app_version",
        "notification_daily_cap",
        "notification_quiet_hours_end",
        "notification_quiet_hours_start",
        "notification_throttling_enabled",
        "p2_route_geometry_enabled",
        "period1_distance_tracking_enabled",
        "platform_fee_percent",
        "privacy_policy_text",
        "promo_redemption_enabled",
        "referral_payout_velocity_cap_per_day",
        "referral_reward_amount",
        "referral_rides_required",
        "require_driver_subscription",
        "resend_api_key",
        "resend_from_email",
        "ride_offer_sound_url",
        "ride_offer_timeout_seconds",
        "rideless_sos_enabled",
        "rider_show_pickup_leg_enabled",
        "route_booked_dropoff_anchor_enabled",
        "route_finalize_grace_seconds",
        "route_integrity_v2_mode",
        "route_location_gap_alert_seconds",
        "safety_alert_emails",
        "scheduled_dispatch_enabled",
        "search_radius_km",
        "sendgrid_api_key",
        "sendgrid_from_email",
        "sos_paging_routing_key",
        "sos_paging_webhook_url",
        "stale_intent_offline_hours",
        "stale_p3_autoclose_enabled",
        "stripe_auto_heal_processing",
        "stripe_connect_webhook_secret",
        "stripe_publishable_key",
        "stripe_reprovision_stale_ids",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "surge_engine_enabled",
        "terms_of_service_text",
        "track_base_url",
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_from_number",
        "twilio_proxy_service_sid",
        "updated_at",
        "use_eta_ranking",
    }
)

# Columns that are deliberately NOT on SettingsUpdateRequest.
#
# - id / updated_at: DB-managed, never client-writable.
# - sendgrid_api_key / sendgrid_from_email / is_referral_active /
#   referral_reward_amount / referral_rides_required /
#   route_finalize_grace_seconds: legacy columns confirmed (2026-08-22, via
#   repo-wide grep) to be read by ZERO application code paths -- dead schema,
#   not a live gap. sendgrid_* specifically is legacy per migration 110's own
#   comment (superseded by resend_api_key); get_app_settings() still masks it
#   on GET for backward-compat display only (test_email_api_keys_masked_on_get).
NOT_ADMIN_WRITABLE_BY_DESIGN = frozenset(
    {
        "id",
        "updated_at",
        "sendgrid_api_key",
        "sendgrid_from_email",
        "is_referral_active",
        "referral_reward_amount",
        "referral_rides_required",
        "route_finalize_grace_seconds",
    }
)

# Real, confirmed-live gaps found by this test on 2026-08-22, fixed the same
# day: company_city/company_postal_code/company_province
# (utils/address_format.py), lifecycle_emails_enabled
# (utils/email_notifications.py), marketing_from_email
# (utils/marketing_email.py), route_location_gap_alert_seconds
# (utils/route_gap_monitor.py), fare_distance_basis (routes/rides/_shared.py,
# estimates.py, booking.py), route_integrity_v2_mode
# (routes/drivers/ride_complete.py) -- all 8 now have SettingsUpdateRequest
# fields, so this set is empty. Kept as a named constant (rather than deleted)
# so the next real gap has an obvious place to land, with the same "list it,
# don't silently exclude it" convention.
KNOWN_UNFIXED_GAPS_2026_08_22 = frozenset()


def test_every_settings_column_is_admin_writable_or_explicitly_excluded():
    model_fields = set(SettingsUpdateRequest.model_fields.keys())
    expected_gap = NOT_ADMIN_WRITABLE_BY_DESIGN | KNOWN_UNFIXED_GAPS_2026_08_22

    missing = KNOWN_SETTINGS_COLUMNS - model_fields - expected_gap
    assert not missing, (
        f"New settings column(s) with no admin-write field and no exclusion entry: {sorted(missing)}. "
        "Either add the field to SettingsUpdateRequest (routes/admin/settings.py), or add it to "
        "NOT_ADMIN_WRITABLE_BY_DESIGN here with a comment explaining why it's intentionally excluded."
    )

    # Catch the exclusion lists going stale in the other direction too: a
    # column marked as excluded that DID get a model field added (nobody
    # removed it from the exclusion set).
    stale_exclusions = expected_gap & model_fields
    assert not stale_exclusions, (
        f"Column(s) marked excluded here but now ARE on SettingsUpdateRequest: {sorted(stale_exclusions)}. "
        "Remove them from NOT_ADMIN_WRITABLE_BY_DESIGN / KNOWN_UNFIXED_GAPS_2026_08_22 above."
    )
