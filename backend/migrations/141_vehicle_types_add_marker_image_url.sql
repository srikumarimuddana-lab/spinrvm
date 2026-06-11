-- 141: vehicle_types.marker_image_url — admin-uploaded custom map marker
-- image (transparent PNG/WebP, car facing north, portrait crop). Uploaded
-- via POST /admin/vehicle-types/{id}/upload-marker into the public
-- vehicle-illustrations bucket under markers/{type_id}/.
--
-- Apps load it through GET /vehicle-types on open (vehicleTypeStore),
-- prefetch it into the native image cache, and fall back to the bundled
-- marker_variant (migration 140) when NULL or unloadable.
--
-- Safe under production traffic: nullable column, no default, no backfill.
--
-- Rollback plan:
--   ALTER TABLE vehicle_types DROP COLUMN marker_image_url;

ALTER TABLE vehicle_types
    ADD COLUMN IF NOT EXISTS marker_image_url TEXT;
