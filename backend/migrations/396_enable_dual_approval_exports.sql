-- 396_enable_dual_approval_exports.sql
--
-- Purpose:
--   Turn on the already-built dual-approval gate for large PII-bearing
--   admin exports (compliance reports > 1,000 rows and every Data
--   Transfer export). The gate, table, and admin queue shipped in
--   migration 268 dark-launched (DEFAULT false). This flips the live
--   setting and the column default so a missed-row cannot silently
--   ungate the control.
--
-- Kill switch (no redeploy):
--   UPDATE public.settings
--     SET dual_approval_exports_enabled = false
--     WHERE id = 'app_settings';
--
-- Rollback:
--   ALTER TABLE public.settings
--     ALTER COLUMN dual_approval_exports_enabled SET DEFAULT false;
--   UPDATE public.settings
--     SET dual_approval_exports_enabled = false
--     WHERE id = 'app_settings';
--
-- Forward-compatible: boolean flip only; older backends that still
-- treat a missing key as off keep working until they pick up the
-- fail-closed reader.

ALTER TABLE public.settings
    ALTER COLUMN dual_approval_exports_enabled SET DEFAULT true;

UPDATE public.settings
    SET dual_approval_exports_enabled = true
    WHERE id = 'app_settings';

COMMENT ON COLUMN public.settings.dual_approval_exports_enabled IS
    'Fail-closed dual-approval gate for large PII exports (compliance '
    '>1000 rows, Data Transfer any non-empty batch). true = require a '
    'second super_admin; false = kill switch. Enabled by migration 396.';
