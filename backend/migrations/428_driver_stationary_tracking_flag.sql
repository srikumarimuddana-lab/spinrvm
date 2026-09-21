-- Default-off rollout for high-accuracy, zero-distance idle sampling.
-- Operational rollback (no mobile release):
-- UPDATE public.settings SET driver_stationary_tracking_enabled = false
-- WHERE id = 'app_settings';
-- Settings cache is 60s. Clients refresh on native cadence application, at
-- most once/minute. Android may defer application until foreground resume.
-- Optional schema rollback, after retiring readers:
-- ALTER TABLE public.settings DROP COLUMN driver_stationary_tracking_enabled;
-- Existing table permissions/RLS remain unchanged. No new query or index.
SET lock_timeout = '5s';
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS driver_stationary_tracking_enabled BOOLEAN NOT NULL DEFAULT FALSE;
RESET lock_timeout;
COMMENT ON COLUMN public.settings.driver_stationary_tracking_enabled IS
    'Canary for high-accuracy zero-distance idle GPS. False restores four-second '
    'Balanced/10m idle sampling. Does not disable trip GPS or background uploads.';
