-- 84_drop_service_areas_fee_columns.sql
--
-- Purpose: Drop the scalar platform_fee / city_fee columns from
--          service_areas now that all named fees are managed through
--          the area_fees table (dynamic, supports flat/percentage/per-km,
--          conditions, etc.). Nothing in the backend reads these columns
--          any longer.
--
-- Rollback:
--   ALTER TABLE service_areas ADD COLUMN platform_fee FLOAT NOT NULL DEFAULT 0;
--   ALTER TABLE service_areas ADD COLUMN city_fee FLOAT NOT NULL DEFAULT 0;

ALTER TABLE service_areas DROP COLUMN IF EXISTS platform_fee;
ALTER TABLE service_areas DROP COLUMN IF EXISTS city_fee;
