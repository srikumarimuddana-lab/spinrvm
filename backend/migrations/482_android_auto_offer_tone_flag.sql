-- Default-off rollout for the ride-offer tone on Android Auto.
-- When true, /drivers/config serves android_auto_offer_tone_enabled=true and a
-- driver-app build with the native RideOfferTone module plays the offer tone
-- through the car speakers (navigation-guidance usage, ducks music) while an
-- Android Auto session is connected. The phone's own loop and the Notifee
-- card's sound are muted for that offer so only one tone rings.
-- Builds without the module, iOS, and phone-only sessions ignore it.
-- Rollback (operational, no mobile release):
-- UPDATE public.settings SET android_auto_offer_tone_enabled = false
-- WHERE id = 'app_settings';
-- The car session re-reads /drivers/config about every 5 minutes; the next
-- offer after that rings on the phone as before.
-- Optional schema rollback, after retiring readers:
-- ALTER TABLE public.settings DROP COLUMN android_auto_offer_tone_enabled;
-- Existing table permissions/RLS remain unchanged. No new query or index.
SET lock_timeout = '5s';
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS android_auto_offer_tone_enabled BOOLEAN NOT NULL DEFAULT FALSE;
RESET lock_timeout;
COMMENT ON COLUMN public.settings.android_auto_offer_tone_enabled IS
    'Canary for playing the ride-offer tone through Android Auto car speakers '
    '(native RideOfferTone module). False keeps the phone tone/notification only. '
    'Android only; iOS ignores it.';
