-- Default-off rollout for the Android "Allow all the time" location gate
-- (PR #5775). When true, GET /drivers/config returns
-- always_location_required=true and the driver app:
--   * shows the background-location disclosure and requires Allow all the
--     time before Go online, and
--   * takes an idle, online driver offline on resume if the grant is missing.
-- False keeps the previous behaviour (go-online still rolls back when
-- background tracking cannot start; no forced offline on resume).
-- Rollback (operational, no mobile release):
-- UPDATE public.settings SET driver_always_location_gate_enabled = false
-- WHERE id = 'app_settings';
-- Clients pick it up on their next /drivers/config refresh (10 min staleTime,
-- persisted cache), so this is not an instant kill switch.
-- Optional schema rollback, after retiring readers:
-- ALTER TABLE public.settings DROP COLUMN driver_always_location_gate_enabled;
-- Existing table permissions/RLS remain unchanged. No new query or index.
SET lock_timeout = '5s';
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS driver_always_location_gate_enabled BOOLEAN NOT NULL DEFAULT FALSE;
RESET lock_timeout;
COMMENT ON COLUMN public.settings.driver_always_location_gate_enabled IS
    'Canary for requiring Android Allow-all-the-time location to go/stay online. '
    'Served to the driver app as always_location_required on /drivers/config.';
