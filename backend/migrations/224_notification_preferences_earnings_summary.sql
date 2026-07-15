-- 224: add earnings_summary to notification_preferences.
--
-- The driver app exposes an "Earnings Summary" notification toggle, but the
-- table (48_notifications_tables.sql) has no backing column, so the PUT
-- silently dropped the field. Default TRUE matches the app's default and the
-- other opt-out-style preference columns.
--
-- Rollback plan:
--   ALTER TABLE notification_preferences DROP COLUMN IF EXISTS earnings_summary;

ALTER TABLE notification_preferences
    ADD COLUMN IF NOT EXISTS earnings_summary BOOLEAN NOT NULL DEFAULT TRUE;

-- Force PostgREST to reload its schema cache so the new column is visible
-- without waiting for the periodic auto-reload.
NOTIFY pgrst, 'reload schema';
