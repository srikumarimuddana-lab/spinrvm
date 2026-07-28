-- 263_service_areas_city_backfill.sql
-- Purpose: Backfill public.service_areas.city so it can be passed as a HARD
-- Geocoding API `components=locality:<city>` filter (ACTION_ITEMS.md B7),
-- the strongest available fix for cross-city street-address mis-resolution
-- (`bounds` is only a soft hint the Geocoding API may ignore).
--
-- routes/admin/service_areas.py already reads/writes `city` on every create
-- and update (ServiceAreaCreateRequest.city, ServiceAreaUpdateRequest.city)
-- and the public GET /service-areas endpoint already whitelists it in
-- _PUBLIC_FIELDS — the column is expected by existing code, just never
-- populated for the areas seeded before that admin form existed. No new
-- column needed; this migration adds it defensively (IF NOT EXISTS, in case
-- it predates migration tracking) and backfills known rows by name.
--
-- Backfill is name-keyed and best-effort: any row whose `name` isn't
-- recognized is left NULL. A NULL city means _lookup_place_candidates simply
-- skips the components filter for that area (today's behavior) rather than
-- guessing wrong and returning ZERO_RESULTS — see ai/tools_booking.py.
--
-- Rollback:
--   ALTER TABLE public.service_areas DROP COLUMN IF EXISTS city;
--   (safe only if city was actually added by this migration and not already
--   relied upon elsewhere in production data — check row count with
--   non-null city before dropping.)

ALTER TABLE public.service_areas
  ADD COLUMN IF NOT EXISTS city TEXT;

UPDATE public.service_areas
SET city = 'Regina'
WHERE (city IS NULL OR city = '')
  AND name IN ('Regina', 'Regina Airport');

UPDATE public.service_areas
SET city = 'Saskatoon'
WHERE (city IS NULL OR city = '')
  AND name = 'Saskatoon';

UPDATE public.service_areas
SET city = 'Riyadh'
WHERE (city IS NULL OR city = '')
  AND name IN ('riyadh', 'riyadh airport');

COMMENT ON COLUMN public.service_areas.city IS
  'Municipality name for this service area (e.g. Regina, Saskatoon) — used as a hard Geocoding API components=locality filter in ai/tools_booking.py._lookup_place_candidates and shown in the admin service-area editor. NULL means the geocode locality filter is skipped for this area (safe degrade, not an error).';
