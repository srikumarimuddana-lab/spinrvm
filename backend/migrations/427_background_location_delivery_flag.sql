-- Default-off rollout for REST live-location delivery.
-- Rollback without redeploy (settings cache expires within 60 seconds):
-- UPDATE public.settings SET background_location_fanout_enabled = false
-- WHERE id = 'app_settings';
-- Optional schema rollback after retiring the code reader:
-- ALTER TABLE public.settings DROP COLUMN background_location_fanout_enabled;
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS background_location_fanout_enabled BOOLEAN NOT NULL DEFAULT FALSE;
COMMENT ON COLUMN public.settings.background_location_fanout_enabled IS
    'Canary gate for background REST live-position updates and assigned-rider fanout. '
    'Independent of durable trip/idle recording; disabling preserves history uploads.';
