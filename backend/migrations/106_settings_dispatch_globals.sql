-- 106_settings_dispatch_globals.sql
--
-- The admin Settings page now exposes three global dispatch controls that
-- the backend already reads via resolve_matching_config (with hardcoded
-- defaults). Without these columns, Supabase/PostgREST returns:
--
--   PGRST204: Could not find the 'max_simultaneous_offers' column of
--             'settings' in the schema cache
--
-- on the first PUT /settings save that includes any of these fields.
--
-- Note: max_simultaneous_offers and use_eta_ranking were already added to
-- service_areas in migration 100_batch_dispatch.sql. This migration adds
-- the *global* counterparts to the settings table so operators can set a
-- platform-wide default without touching every service area row.
-- resolve_matching_config prefers the service_area override when present;
-- falls back to settings; falls back to the in-code constant.
--
-- Fields added:
--   max_simultaneous_offers  INT            — drivers offered per ride (1-10, default 3)
--   ride_offer_timeout_seconds INT          — seconds before a pending offer expires (default 15)
--   use_eta_ranking          BOOLEAN        — rank by Distance Matrix ETA vs haversine (default true)
--
-- Rollback:
--   ALTER TABLE settings DROP COLUMN IF EXISTS max_simultaneous_offers;
--   ALTER TABLE settings DROP COLUMN IF EXISTS ride_offer_timeout_seconds;
--   ALTER TABLE settings DROP COLUMN IF EXISTS use_eta_ranking;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS max_simultaneous_offers    INT     DEFAULT 3
        CONSTRAINT settings_max_simultaneous_offers_range CHECK (max_simultaneous_offers BETWEEN 1 AND 10),
    ADD COLUMN IF NOT EXISTS ride_offer_timeout_seconds INT     DEFAULT 15
        CONSTRAINT settings_ride_offer_timeout_range    CHECK (ride_offer_timeout_seconds BETWEEN 5 AND 120),
    ADD COLUMN IF NOT EXISTS use_eta_ranking            BOOLEAN DEFAULT TRUE;

COMMENT ON COLUMN public.settings.max_simultaneous_offers IS
    'Global default: number of drivers offered a ride simultaneously. Service-area override takes precedence. Clamped 1-10 in resolve_matching_config.';
COMMENT ON COLUMN public.settings.ride_offer_timeout_seconds IS
    'Global default: seconds a pending offer stays open before the batch-timeout handler expires it. Range 5-120.';
COMMENT ON COLUMN public.settings.use_eta_ranking IS
    'Global default: when true, dispatch uses the Distance Matrix API to rank candidates by real ETA. Requires Distance Matrix API enabled on the Maps key. Falls back to haversine when false or when the API call fails.';
