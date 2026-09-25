-- 484_settings_saved_place_shortcuts_enabled.sql
--
-- Admin-writable switch for the rider home-screen Home/Work/Saved shortcuts.
--
-- 2026-09-25 saved-places fix: GET /api/settings already returns
-- saved_place_shortcuts_enabled (routes/settings.py), read with
-- .get(..., True), but there was no column behind it -- so the only way to
-- turn the shortcuts off was a backend deploy. This column lets an admin flip
-- it from the dashboard (Settings > Operations > Rider & driver features).
--
-- While true (default): tapping Home/Work on the rider home screen pre-fills
-- the drop-off with that saved place. While false: rider-app falls back to
-- the old behaviour (all three shortcuts just open search).
--
-- Default TRUE matches the code default, so applying this migration changes
-- nothing any rider sees. Additive only: one new column with a constant
-- default; no existing row or column is read differently.
--
-- Change log: docs/change-log/2026-09-25-admin-flag-toggles.md
--
-- Rollback:
--   UPDATE settings SET saved_place_shortcuts_enabled = true WHERE id = 'app_settings';  -- behavioural rollback (re-enable), no deploy
--   ALTER TABLE settings DROP COLUMN IF EXISTS saved_place_shortcuts_enabled;  -- safe: code reads the key with a True default

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS saved_place_shortcuts_enabled BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN public.settings.saved_place_shortcuts_enabled IS
    'When true (default), the rider home-screen Home/Work shortcuts pre-fill the drop-off with the saved place. When false, rider-app falls back to opening search. Read by GET /api/settings with a True default.';
