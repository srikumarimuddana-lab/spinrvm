-- 85_service_areas_fees_jsonb.sql
--
-- Purpose: Replace the scalar platform_fee / city_fee columns on
--          service_areas with a single fees JSONB column. Admins can
--          now define any number of named fees (platform_fee, city_fee,
--          environmental_levy, etc.) without a schema migration.
--
-- The fare engine reads service_areas.fees at booking time, sums all
-- values into the ride total, and snapshots them into rides.extra_fees.
--
-- Rollback (paper plan — no down-migration file):
--   ALTER TABLE service_areas ADD COLUMN platform_fee FLOAT NOT NULL DEFAULT 0;
--   ALTER TABLE service_areas ADD COLUMN city_fee FLOAT NOT NULL DEFAULT 0;
--   UPDATE service_areas
--     SET platform_fee = COALESCE((fees->>'platform_fee')::float, 0),
--         city_fee     = COALESCE((fees->>'city_fee')::float, 0);
--   ALTER TABLE service_areas DROP COLUMN fees;

BEGIN;

-- 1. Add the flexible fees column.
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS fees JSONB NOT NULL DEFAULT '{}';

-- 2. Backfill: migrate existing platform_fee / city_fee into the JSONB.
--    Only include a key when the value is non-zero so the UI doesn't
--    render $0.00 line items for every area.
UPDATE service_areas
SET fees =
      (CASE WHEN platform_fee > 0 THEN jsonb_build_object('platform_fee', platform_fee) ELSE '{}'::jsonb END)
   || (CASE WHEN city_fee     > 0 THEN jsonb_build_object('city_fee',     city_fee)     ELSE '{}'::jsonb END);

-- 3. Drop the old scalar columns now that data is in the JSONB.
ALTER TABLE service_areas DROP COLUMN IF EXISTS platform_fee;
ALTER TABLE service_areas DROP COLUMN IF EXISTS city_fee;

COMMIT;

COMMENT ON COLUMN service_areas.fees IS
    'Dynamic flat fees applied to every ride in this area. '
    'Keys are fee names (e.g. platform_fee, city_fee); values are NUMERIC. '
    'Admin dashboard writes/reads this object directly — no migration needed to add new fee types.';
