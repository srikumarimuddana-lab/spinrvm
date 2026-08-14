-- 313_settings_missing_columns.sql
--
-- Rollback:
--   ALTER TABLE public.settings
--       DROP COLUMN IF EXISTS ai_disabled_mode,
--       DROP COLUMN IF EXISTS apns_bundle_id,
--       DROP COLUMN IF EXISTS apns_key_id,
--       DROP COLUMN IF EXISTS apns_p8_key,
--       DROP COLUMN IF EXISTS apns_team_id,
--       DROP COLUMN IF EXISTS company_app_name,
--       DROP COLUMN IF EXISTS corporate_billing_enabled,
--       DROP COLUMN IF EXISTS corporate_kyb_reverification_enabled,
--       DROP COLUMN IF EXISTS corporate_kyb_reverify_after_months,
--       DROP COLUMN IF EXISTS corporate_subscription_billing_enabled,
--       DROP COLUMN IF EXISTS corporate_wallet_admin_adjust_daily_cap,
--       DROP COLUMN IF EXISTS driver_discreet_sos_enabled,
--       DROP COLUMN IF EXISTS driver_heatmap_v2_enabled,
--       DROP COLUMN IF EXISTS heatmap_internal_driver_ids,
--       DROP COLUMN IF EXISTS min_driver_app_version,
--       DROP COLUMN IF EXISTS min_rider_app_version,
--       DROP COLUMN IF EXISTS promo_redemption_enabled,
--       DROP COLUMN IF EXISTS resend_api_key,
--       DROP COLUMN IF EXISTS resend_from_email,
--       DROP COLUMN IF EXISTS scheduled_dispatch_enabled,
--       DROP COLUMN IF EXISTS sos_paging_routing_key,
--       DROP COLUMN IF EXISTS sos_paging_webhook_url,
--       DROP COLUMN IF EXISTS stripe_auto_heal_processing,
--       DROP COLUMN IF EXISTS surge_engine_enabled;
--   Reverting restores the current broken state (saves 500), so only roll back
--   alongside a revert of whatever made these settable.
--
-- Why this migration exists
-- ------------------------
-- `settings` stores one row (id='app_settings') with FLAT columns — there is no
-- JSON catch-all. PUT /api/admin/settings builds its payload straight from the
-- request model with no column allowlist:
--
--     update_fields = {k: ... for k, v in settings.model_dump(exclude_none=True).items()}
--     await db_supabase.update_one("settings", {"id": "app_settings"}, update_payload)
--
-- So every field the API accepts MUST exist as a column. Twenty-four did not.
-- Because PostgREST rejects an unknown column with PGRST204 (the same failure
-- CLAUDE.md documents for service_areas.updated_at), the effect is not a
-- silently-dropped field — it is a 500 that fails the ENTIRE save, including
-- the valid fields alongside it. `exclude_none=True` is why this went
-- unnoticed: a field is only in the payload once an admin actually sets it, so
-- the failure fires exactly when someone first tries to change one of these.
--
-- Settings that could not be changed through any supported path:
--
--   surge_engine_enabled                     surge recalculation kill switch
--   scheduled_dispatch_enabled               scheduled-dispatch kill switch
--   driver_discreet_sos_enabled              safety
--   sos_paging_webhook_url / _routing_key    safety paging escalation
--   min_driver_app_version / min_rider_...   force-upgrade gates
--   corporate_billing_enabled                corporate money path
--   corporate_subscription_billing_enabled   corporate money path
--   corporate_wallet_admin_adjust_daily_cap  corporate money path (abuse cap)
--   corporate_kyb_reverification_enabled     corporate compliance
--   corporate_kyb_reverify_after_months      corporate compliance
--   stripe_auto_heal_processing              payments
--   promo_redemption_enabled                 promotions
--   driver_heatmap_v2_enabled                heatmap v2 flag
--   heatmap_internal_driver_ids              heatmap v2 dark-launch allowlist
--   apns_* (4)                               iOS push credentials
--   resend_api_key / resend_from_email       email provider
--   ai_disabled_mode, company_app_name       misc
--
-- Migration 311 added six heatmap columns and its comment asserted that
-- driver_heatmap_v2_enabled and heatmap_internal_driver_ids already existed.
-- They never did — that assumption was wrong, and this migration corrects it
-- along with the rest of the class.
--
-- DEFAULTS ARE DELIBERATELY THE VALUES THE CODE ALREADY BEHAVES AS.
-- Every default below was read from the reader that consumes it (schemas.py
-- AppSettings, or the module's own fallback constant), NOT chosen here. So
-- applying this changes no behaviour: each setting keeps the value the system
-- has effectively been using, and only becomes persistable. Kill switches
-- default to their "on//normal operation" value for the same reason — this
-- migration must not itself turn anything off.
--
-- Additive only: every column is nullable-with-default, so PG11+ applies the
-- default as catalog metadata (no table rewrite). `settings` is a single-row
-- config table regardless.

ALTER TABLE public.settings
    -- ── Kill switches ────────────────────────────────────────────────────
    -- Defaults match the code's fallbacks: surge/dispatch run unless disabled.
    ADD COLUMN IF NOT EXISTS surge_engine_enabled                    BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS scheduled_dispatch_enabled              BOOLEAN NOT NULL DEFAULT TRUE,

    -- ── Safety ───────────────────────────────────────────────────────────
    -- driver_discreet_sos_enabled defaults FALSE (schemas.py) — this is an
    -- opt-in feature, and defaulting it on would enable an unreviewed safety
    -- flow fleet-wide as a side effect of a schema migration.
    ADD COLUMN IF NOT EXISTS driver_discreet_sos_enabled             BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS sos_paging_webhook_url                  TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS sos_paging_routing_key                  TEXT NOT NULL DEFAULT '',

    -- ── Force-upgrade gates ──────────────────────────────────────────────
    -- Empty = no minimum enforced, which is today's behaviour. A non-empty
    -- default here would lock out every client below that version on apply.
    ADD COLUMN IF NOT EXISTS min_driver_app_version                  TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS min_rider_app_version                   TEXT NOT NULL DEFAULT '',

    -- ── Corporate ────────────────────────────────────────────────────────
    -- corporate_subscription_billing_enabled defaults FALSE, matching
    -- routes/corporate_subscriptions.py's _DEFAULT_BILLING_ENABLED: that path
    -- is explicitly gated off until verified in staging, and this migration is
    -- not the place to turn on a money path.
    ADD COLUMN IF NOT EXISTS corporate_billing_enabled               BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS corporate_subscription_billing_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS corporate_kyb_reverification_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS corporate_kyb_reverify_after_months     INTEGER NOT NULL DEFAULT 12,
    -- NUMERIC, never float — this is a money cap (utils/money conventions).
    -- 50000.00 is routes/corporate_wallet.py's _DEFAULT_ADJUST_DAILY_CAP.
    ADD COLUMN IF NOT EXISTS corporate_wallet_admin_adjust_daily_cap NUMERIC(12,2) NOT NULL DEFAULT 50000.00,

    -- ── Payments / promotions ────────────────────────────────────────────
    ADD COLUMN IF NOT EXISTS stripe_auto_heal_processing             BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS promo_redemption_enabled                BOOLEAN NOT NULL DEFAULT TRUE,

    -- ── Heatmap v2 (the two migration 311 assumed already existed) ────────
    ADD COLUMN IF NOT EXISTS driver_heatmap_v2_enabled               BOOLEAN NOT NULL DEFAULT FALSE,
    -- jsonb array of user IDs. Dark-launch allowlist: grants v2 while the
    -- global flag is OFF. Empty array = nobody, i.e. today's behaviour.
    ADD COLUMN IF NOT EXISTS heatmap_internal_driver_ids             JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- ── Credentials / providers ──────────────────────────────────────────
    -- Empty defaults: an unset credential must read as absent, and the admin
    -- API masks these on GET (see _CREDENTIAL_FIELDS).
    ADD COLUMN IF NOT EXISTS apns_bundle_id                          TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS apns_key_id                             TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS apns_p8_key                             TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS apns_team_id                            TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS resend_api_key                          TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS resend_from_email                       TEXT NOT NULL DEFAULT '',

    -- ── Misc ─────────────────────────────────────────────────────────────
    ADD COLUMN IF NOT EXISTS ai_disabled_mode                        TEXT NOT NULL DEFAULT 'coming_soon',
    ADD COLUMN IF NOT EXISTS company_app_name                        TEXT NOT NULL DEFAULT 'Spinr';

-- Bound the money cap at the DB layer too, so a direct console edit cannot
-- park a negative or absurd ceiling on admin wallet adjustments. Same
-- defence-in-depth pattern as settings_heatmap_bounds_chk (migration 311).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'settings_corporate_caps_chk'
    ) THEN
        ALTER TABLE public.settings
            ADD CONSTRAINT settings_corporate_caps_chk CHECK (
                corporate_wallet_admin_adjust_daily_cap >= 0
                AND corporate_wallet_admin_adjust_daily_cap <= 10000000
                AND corporate_kyb_reverify_after_months BETWEEN 1 AND 120
            );
    END IF;
END $$;

COMMENT ON COLUMN public.settings.surge_engine_enabled IS
    'Kill switch for the automatic surge recalculation loop. False pauses the engine; per-area surge_enabled/surge_source still apply.';
COMMENT ON COLUMN public.settings.heatmap_internal_driver_ids IS
    'jsonb array of users.id granted heatmap v2 while driver_heatmap_v2_enabled is FALSE (dark launch). Ignored when the global flag is on.';
COMMENT ON COLUMN public.settings.corporate_wallet_admin_adjust_daily_cap IS
    'Per-admin daily ceiling on manual corporate wallet adjustments, in dollars. NUMERIC — never float.';
