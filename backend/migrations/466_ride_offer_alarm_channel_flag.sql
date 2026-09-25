-- Default-off rollout for ringing Android ride offers on the alarm volume.
-- When true, dispatch payloads carry ring_mode='alarm' and a driver-app build
-- that has the native ride-offers-alarm-v1 channel posts the minimised offer
-- there (plays through silent/vibrate mode and Do Not Disturb's "alarms").
-- Builds without that channel keep ride-offers-v3 (notification volume).
-- Operational rollback (no mobile release):
-- UPDATE public.settings SET ride_offer_alarm_channel_enabled = false
-- WHERE id = 'app_settings';
-- Settings cache is 60s; the next offer after that carries ring_mode='notification'.
-- Optional schema rollback, after retiring readers:
-- ALTER TABLE public.settings DROP COLUMN ride_offer_alarm_channel_enabled;
-- Existing table permissions/RLS remain unchanged. No new query or index.
SET lock_timeout = '5s';
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS ride_offer_alarm_channel_enabled BOOLEAN NOT NULL DEFAULT FALSE;
RESET lock_timeout;
COMMENT ON COLUMN public.settings.ride_offer_alarm_channel_enabled IS
    'Canary for Android ride offers on the alarm-volume channel (ride-offers-alarm-v1). '
    'False keeps the notification-volume channel. Android only; iOS ignores it.';
