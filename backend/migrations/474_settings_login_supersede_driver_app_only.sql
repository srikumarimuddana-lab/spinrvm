-- Default-off rollout: per-login sessions, and only a driver-app login signs the
-- account's other devices out (as Uber: rider and driver apps sign in
-- independently; one driver device at a time).
--
-- Today every login (rider app, company portal, driver app) overwrites the shared
-- users.current_session_id, tombstones the previous session, kicks the user's
-- sockets and takes the driver offline, and every refreshed access token re-reads
-- that shared column. With settings.login_supersede_driver_app_only_enabled = true
-- (routes/auth.py _login_session_policy):
--   * only a login carrying X-App-Platform: driver owns current_session_id and
--     signs other devices out;
--   * every login stores its session id on its refresh-token chain
--     (refresh_tokens.session_id), rotation carries it forward, and refreshed
--     access tokens use it; a device's logout tombstones its own session.
--
-- Both columns ship together so the flag can only be enabled once the column the
-- code writes exists.
--
-- Rollback (operational, no deploy):
-- UPDATE public.settings SET login_supersede_driver_app_only_enabled = false
-- WHERE id = 'app_settings';
-- Settings cache is 60s. The flag-off code never reads refresh_tokens.session_id,
-- so rows written while it was on are simply ignored.
-- Optional schema rollback, after retiring the readers:
-- ALTER TABLE public.settings DROP COLUMN login_supersede_driver_app_only_enabled;
-- ALTER TABLE public.refresh_tokens DROP COLUMN session_id;
-- Existing table permissions/RLS remain unchanged. Nullable column, no backfill, no
-- new index (the column is only read from rows already fetched by token_hash).
SET lock_timeout = '5s';
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS login_supersede_driver_app_only_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.refresh_tokens
    ADD COLUMN IF NOT EXISTS session_id TEXT;
RESET lock_timeout;
COMMENT ON COLUMN public.settings.login_supersede_driver_app_only_enabled IS
    'When true, only a driver-app login (X-App-Platform: driver) owns users.current_session_id '
    'and signs other devices out; every login keeps its own session id on its refresh chain.';
COMMENT ON COLUMN public.refresh_tokens.session_id IS
    'Login session this refresh chain belongs to; carried forward on rotation. '
    'Written only while settings.login_supersede_driver_app_only_enabled is true.';
