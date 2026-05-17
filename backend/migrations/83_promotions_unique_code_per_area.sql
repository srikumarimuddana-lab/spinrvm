-- 83_promotions_unique_code_per_area.sql
--
-- Purpose: Change the promotions uniqueness constraint from code-only to
--          (code, service_area_id) so the same code can exist in multiple
--          service areas but not twice within the same area.
--
--          NULL service_area_id = global promo. We use COALESCE(service_area_id, '')
--          in the index so two global promos with the same code are still blocked
--          (Postgres treats two NULLs as distinct in a plain unique index).
--
-- Rollback:
--   DROP INDEX IF EXISTS uq_promotions_code_area;
--   CREATE UNIQUE INDEX uq_promotions_code ON promotions (code);

-- Drop the old code-only unique index
DROP INDEX IF EXISTS uq_promotions_code;

-- New composite unique index: same code allowed in different areas,
-- but not twice in the same area (including global/NULL).
CREATE UNIQUE INDEX uq_promotions_code_area
    ON promotions (code, COALESCE(service_area_id, ''));
