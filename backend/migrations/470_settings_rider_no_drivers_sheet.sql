-- 470_settings_rider_no_drivers_sheet.sql
--
-- Rollout switch for the rider-app "No drivers available right now" sheet
-- (Try again / Schedule for later) shown when an on-demand ride is
-- auto-cancelled because no driver accepted (cancellation_type
-- 'no_drivers_found'). Exposed to the rider app via GET /settings.
--
-- Off (default): the rider app behaves exactly as before this sheet existed —
-- jump to home with the "No drivers were available" toast, the per-round
-- "driver did not respond" toast kept, no resume lookup.
--
-- Plan: .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md (Phase 3)
--
-- Rollback:
--   UPDATE settings SET rider_no_drivers_sheet_enabled = false;  -- behavioural rollback, no deploy
--   ALTER TABLE settings DROP COLUMN IF EXISTS rider_no_drivers_sheet_enabled;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS rider_no_drivers_sheet_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.rider_no_drivers_sheet_enabled IS
    'Rider app: show the "No drivers available right now" sheet on a no_drivers_found auto-cancel. Public via GET /settings. Default false.';
