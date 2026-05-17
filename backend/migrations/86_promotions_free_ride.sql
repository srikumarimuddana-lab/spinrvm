-- 86_promotions_free_ride.sql
-- Adds free_ride flag to promotions table.
-- Rollback: ALTER TABLE promotions DROP COLUMN free_ride;

ALTER TABLE promotions
    ADD COLUMN IF NOT EXISTS free_ride BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN promotions.free_ride IS
    'When true, the promo covers the entire grand total (rider pays $0). '
    'discount_type and discount_value are ignored for free_ride promos.';
