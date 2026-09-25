-- Default-off rollout: only a driver-app login signs the account's other devices out.
-- Today every login (rider app, company portal, driver app) tombstones the previous
-- session, kicks the user's sockets and takes the driver offline, and the driver app
-- signs itself out on that kick. When true, routes/auth.py
-- _login_supersedes_other_devices lets only a login carrying X-App-Platform: driver
-- do that. The driver_single_session_enabled rollout keeps its own rule.
-- Rollback (operational, no deploy):
-- UPDATE public.settings SET login_supersede_driver_app_only_enabled = false
-- WHERE id = 'app_settings';
-- Settings cache is 60s; logins after that sign other devices out again.
-- Optional schema rollback, after retiring the reader:
-- ALTER TABLE public.settings DROP COLUMN login_supersede_driver_app_only_enabled;
-- Existing table permissions/RLS remain unchanged. No new query or index.
SET lock_timeout = '5s';
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS login_supersede_driver_app_only_enabled BOOLEAN NOT NULL DEFAULT FALSE;
RESET lock_timeout;
COMMENT ON COLUMN public.settings.login_supersede_driver_app_only_enabled IS
    'When true, only a driver-app login (X-App-Platform: driver) signs the account''s other '
    'devices out; rider-app, portal and header-less logins leave them signed in.';
