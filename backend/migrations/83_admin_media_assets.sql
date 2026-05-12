-- 83_admin_media_assets.sql
--
-- Adds two pieces of admin-configurable media plumbing:
--   1. `vehicle_types.illustration_url` — the Uber-style car image the rider
--      sees on the booking screen, uploaded from the admin dashboard and
--      stored in Supabase Storage bucket `vehicle-illustrations`.
--   2. `settings.ride_offer_sound_url` — the ping the driver-app plays when
--      a new offer arrives. Uploaded from admin → bucket `audio-assets`.
--      Null means "use the bundled placeholder mp3 in driver-app/assets/".
--
-- Note: the project stores app settings as a single row in the `settings`
-- table (id='app_settings') with flat columns matching backend/schemas.py
-- AppSettings — it is NOT a key/value table — so ride_offer_sound_url is
-- added as a column, not as a row.
--
-- Why:
-- ── The rider-app type already references `vehicle_type.image_url` and
--    renders it with a fallback, but the admin CRUD models in
--    backend/routes/admin/vehicle_fleet.py never persisted it, so in
--    practice no illustration ever reached the rider.
-- ── The driver-app's useRideOfferSound hook hardcodes a bundled require()
--    of a silent placeholder. Ops can't rotate the tone without a mobile
--    redeploy.
--
-- Forward-compatible: both columns are nullable; existing rows are
-- unaffected. Idempotent: IF NOT EXISTS on both ALTERs.
--
-- Rollback:
--   ALTER TABLE vehicle_types DROP COLUMN illustration_url;
--   ALTER TABLE settings DROP COLUMN ride_offer_sound_url;
--
-- Supabase Storage buckets (set up out-of-band in the Supabase dashboard
-- before the upload endpoints are exercised — the SQL alone is not enough):
--   • Bucket `vehicle-illustrations`: public read, service_role-only write.
--   • Bucket `audio-assets`:          public read, service_role-only write.
-- Both buckets store non-PII media, so public read is appropriate.

ALTER TABLE vehicle_types
    ADD COLUMN IF NOT EXISTS illustration_url TEXT;

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS ride_offer_sound_url TEXT;
