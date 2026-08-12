-- 304_settings_notification_throttling.sql
-- Global notification-throttling controls (quiet hours + a daily
-- non-critical-push cap), edited via the admin Settings page. Same
-- single-row app_settings pattern as every other global toggle (see
-- ai_assistant_enabled, safety_alert_emails, max_simultaneous_offers).
--
-- Scope: this ships one global default for every rider/driver, not a
-- per-user preference. Per-user override is deliberately deferred —
-- there is no settings UI for it yet, and Spinr operates in a single
-- timezone today (Saskatchewan, no DST), so a global window is
-- sufficient for V1. If per-user quiet hours are added later, that is
-- a separate migration against notification_preferences, not a change
-- to these columns.
--
-- Enforcement point: backend/features.py::send_push_notification (and,
-- in a follow-up subtask, sms_service.py / email_provider.py). The
-- existing time_critical bypass (dispatch/safety/account priority)
-- is untouched by this migration and must continue to skip throttling
-- entirely — see backend/utils/notification_throttle.py.
--
-- Ships OFF (notification_throttling_enabled defaults false): existing
-- notification delivery behavior is unchanged until an admin opts in
-- after staging verification.
--
-- Rollback:
--   ALTER TABLE settings
--       DROP COLUMN IF EXISTS notification_throttling_enabled,
--       DROP COLUMN IF EXISTS notification_quiet_hours_start,
--       DROP COLUMN IF EXISTS notification_quiet_hours_end,
--       DROP COLUMN IF EXISTS notification_daily_cap;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS notification_throttling_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS notification_quiet_hours_start   TEXT NOT NULL DEFAULT '22:00',
    ADD COLUMN IF NOT EXISTS notification_quiet_hours_end     TEXT NOT NULL DEFAULT '07:00',
    ADD COLUMN IF NOT EXISTS notification_daily_cap           INTEGER NOT NULL DEFAULT 6;

COMMENT ON COLUMN public.settings.notification_throttling_enabled IS
    'Global kill switch for quiet-hours + daily-cap notification throttling. Defaults false (ships dark). Dispatch/safety/account-priority pushes always bypass this regardless of value.';
COMMENT ON COLUMN public.settings.notification_quiet_hours_start IS
    'HH:MM (24h, America/Regina) — start of the window during which non-critical push/SMS/email are suppressed. Only enforced when notification_throttling_enabled is true.';
COMMENT ON COLUMN public.settings.notification_quiet_hours_end IS
    'HH:MM (24h, America/Regina) — end of the quiet-hours window. May wrap past midnight (e.g. 22:00-07:00).';
COMMENT ON COLUMN public.settings.notification_daily_cap IS
    'Max non-critical notifications per user per rolling 24h before further sends are suppressed for that day. 0 = no cap. Only enforced when notification_throttling_enabled is true.';
