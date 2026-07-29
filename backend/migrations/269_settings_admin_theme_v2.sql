-- 269_settings_admin_theme_v2.sql
--
-- Feature flag for the admin-dashboard visual-refresh epic (#2785 Phase 3:
-- shared primitives/nav/shell restyle). Blast radius is all 34 dashboard
-- routes via shared components (sidebar, typography, radius tokens), so per
-- CLAUDE.md's pre-merge gate #3 this ships behind a flag rather than
-- big-bang. Read via the existing GET /api/admin/settings endpoint (60s
-- in-process cache in settings_loader.py) by a new frontend
-- useFeatureFlag() hook; toggled via a new checkbox on the admin Settings
-- page — see the paired frontend PR.
--
-- Defaults to FALSE (off) so merging this migration and the Phase 3 restyle
-- code has zero visible effect until a super-admin deliberately flips it on
-- for a canary/rollout.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS admin_theme_v2_enabled;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS admin_theme_v2_enabled BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN public.settings.admin_theme_v2_enabled IS
    'Feature flag for the admin-dashboard visual-refresh epic (#2785 Phase 3 and later) — typography/radius/shell restyle. Defaults false; toggle via the admin Settings page to canary/roll out without a redeploy.';
