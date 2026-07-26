-- 256_users_legacy_import_metadata.sql
-- Purpose: Give users the same import-provenance surface drivers already have
--          (migration 221), so the legacy Stripe mapping import can record
--          which batch wrote users.stripe_customer_id and where it came from.
-- Rollback:
--   ALTER TABLE public.users
--     DROP COLUMN IF EXISTS legacy_import_metadata;
--
-- Notes:
-- - Additive, nullable-with-constant-default column: metadata-only change,
--   instant on PG11+, safe with production traffic in flight.
-- - No index: only rare batch-scoped ops queries read this column.
-- - Keep raw PII (names, phones, addresses) out of this blob; it is for
--   provenance keys (batch, source, old IDs, timestamps) only.

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS legacy_import_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.users.legacy_import_metadata IS
  'Provenance for rows touched by legacy-app imports (e.g. stripe_migration: {batch, source, old_stripe_customer_id, imported_at}). Admin/compliance use only; never raw PII.';
