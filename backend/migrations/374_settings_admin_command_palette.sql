-- 374_settings_admin_command_palette.sql
--
-- Feature flag for the admin-dashboard Cmd+K / Ctrl+K command palette
-- (fuzzy-jump to any of the ~90 admin routes). Same shape as migration
-- 269's admin_theme_v2_enabled: read via the existing GET
-- /api/admin/settings endpoint (60s in-process cache in
-- settings_loader.py) by the frontend's useFeatureFlag() hook, toggled via
-- a new checkbox on the admin Settings page.
--
-- Defaults to FALSE (off) so merging this migration and the palette
-- component/wiring has zero visible effect until a super-admin
-- deliberately flips it on.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS admin_command_palette_enabled;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS admin_command_palette_enabled BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN public.settings.admin_command_palette_enabled IS
    'Feature flag for the admin-dashboard Cmd+K/Ctrl+K command palette (fuzzy-jump to any route). Defaults false; toggle via the admin Settings page to canary/roll out without a redeploy.';
