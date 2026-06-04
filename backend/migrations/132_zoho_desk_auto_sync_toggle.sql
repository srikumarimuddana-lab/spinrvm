-- 132_zoho_desk_auto_sync_toggle.sql
-- Splits "integration enabled" from "background auto-sync". The 10-minute
-- zoho_desk_sync_loop pulled from Zoho on every replica whenever the
-- integration was enabled. We want auto-sync OFF by default (manual "Sync now"
-- only) and admin-togglable from the dashboard.
--
-- auto_sync_enabled defaults FALSE: existing installs keep the integration on
-- (enabled) but stop the periodic pull until an admin opts in. The manual
-- /support-tickets/sync endpoint and on-write ticket creation are unaffected.
--
-- Rollback:
--   ALTER TABLE zoho_desk_config DROP COLUMN IF EXISTS auto_sync_enabled;

ALTER TABLE zoho_desk_config
    ADD COLUMN IF NOT EXISTS auto_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN zoho_desk_config.auto_sync_enabled IS
    'When true, the 10-min background loop pulls tickets from Zoho. Default false = manual Sync now only. Independent of `enabled` (the master integration switch).';
