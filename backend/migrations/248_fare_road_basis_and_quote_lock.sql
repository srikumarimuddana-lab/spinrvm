-- 248_fare_road_basis_and_quote_lock.sql
-- Bill on the road distance quoted before the ride, and collect exactly that.
--
-- Product decision (owner-confirmed): the rider is quoted the actual road
-- (driving) distance before the ride starts and is charged that amount. There
-- is no straight-line/haversine billing basis and no post-ride GPS re-pricing.
--
--   1. fare_distance_basis = 'road'  -> the estimate prices on the Google
--      Directions road distance (haversine stays only as a flagged safety
--      fallback when Directions returns nothing / a physically impossible
--      distance). Made an explicit, admin-overridable settings column.
--   2. fare_lock_enabled  = TRUE     -> settlement keeps the booking-time fare;
--      it never recalculates on the post-ride GPS-measured distance. "Collect
--      what we showed at first."
--
-- Forward-compatible: single-row settings table, metadata-only column add +
-- one-row UPDATE; safe against in-flight traffic. Old backend tolerates the new
-- column (it only reads it).
--
-- Rollback:
--   UPDATE public.settings SET fare_lock_enabled = FALSE, fare_distance_basis = 'shadow' WHERE id = 'app_settings';
--   ALTER TABLE public.settings ALTER COLUMN fare_lock_enabled SET DEFAULT FALSE;
--   ALTER TABLE public.settings ALTER COLUMN fare_distance_basis SET DEFAULT 'shadow';
--   -- (dropping the fare_distance_basis column is optional; code defaults to 'road' when absent.)

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS fare_distance_basis TEXT NOT NULL DEFAULT 'road';

-- Bill on road distance and collect the quoted fare for the live settings row.
UPDATE public.settings
    SET fare_distance_basis = 'road',
        fare_lock_enabled = TRUE
    WHERE id = 'app_settings';

-- New installs inherit the same behaviour.
ALTER TABLE public.settings ALTER COLUMN fare_lock_enabled SET DEFAULT TRUE;

NOTIFY pgrst, 'reload schema';
