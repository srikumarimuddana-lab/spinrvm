-- Migration 287: Add stripe_reprovision_stale_ids to settings
--
-- This is the kill switch for the Stripe identity re-provisioning added in
-- migration 286 and its accompanying code (rider customers, driver Connect
-- accounts, corporate customers). Turning it off stops every identity
-- mutation across all three surfaces within the 60 s settings cache, with no
-- redeploy — it is the documented rollback path for that feature.
--
-- WHY THIS EXISTS AS ITS OWN COLUMN: `settings` is a wide table with one
-- real column per setting, and `settings_loader.get_app_settings()` merges
-- the stored row over the AppSettings schema defaults. A key that has no
-- column can be *read* (it falls back to the schema default, True) but can
-- never be *written* — the admin PUT would fail PGRST204 "column does not
-- exist". Without this migration the flag is permanently stuck on and the
-- rollback plan does not actually work.
--
-- Default TRUE matches AppSettings.stripe_reprovision_stale_ids and is the
-- deliberate choice: with repair off, a rider stranded by a key-mode change
-- has no in-app way to fix their payment method, so "off" is the exceptional
-- state an operator opts into, not the resting one.
--
-- Forward-compatible: nullable-with-default, no backfill needed. NULL would
-- also read as True via the schema default, so an older replica that has not
-- yet seen this column behaves identically.
--
-- Rollback:
--   ALTER TABLE settings DROP COLUMN IF EXISTS stripe_reprovision_stale_ids;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS stripe_reprovision_stale_ids BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN public.settings.stripe_reprovision_stale_ids IS
    'Kill switch for re-provisioning Stripe identities stranded by a test/live key '
    'rotation (migration 286). TRUE = repair automatically on the paths where the '
    'affected user is present. FALSE = leave every row untouched and surface 503 '
    'instead; the rollback for that feature, effective within the 60 s settings cache.';
