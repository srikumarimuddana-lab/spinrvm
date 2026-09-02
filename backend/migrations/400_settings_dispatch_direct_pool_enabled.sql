-- 400_settings_dispatch_direct_pool_enabled.sql
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS dispatch_direct_pool_enabled;
--
-- Why this migration exists
-- ------------------------
-- C50 Phase 1 (docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md,
-- T10) adds `dispatch_direct_pool_enabled` to schemas.AppSettings and
-- SettingsUpdateRequest so the direct-pool dispatch rollback switch is
-- editable via the admin dashboard, per repo convention (app_settings, not an
-- env var). Per backend/tests/test_settings_column_parity.py's guard: `settings`
-- is one flat row with no JSON catch-all and PUT /api/admin/settings has no
-- column allowlist, so any API field without a matching column 500s the WHOLE
-- save (PGRST204) the first time an admin tries to set it -- not just a
-- silently-dropped value. This migration is that column.
--
-- Default FALSE matches the Pydantic field's default and the code's only
-- current behaviour (nothing reads this flag yet -- Phase 2, T12/T13, not
-- built). Applying this migration changes zero behaviour: the dispatch claim
-- path stays on PostgREST regardless of this column's value until Phase 2
-- ships and reads it.
--
-- Additive only: nullable-with-default column, no table rewrite (single-row
-- config table regardless).

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS dispatch_direct_pool_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.dispatch_direct_pool_enabled IS
    'C50 Phase 1 rollback switch for the PostgREST -> direct-pool (Supavisor) '
    'dispatch migration. Default FALSE = current PostgREST claim path, '
    'unchanged. Has no effect until Phase 2 (T12/T13) wires the dispatch '
    'claim path to read it. See docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md.';
