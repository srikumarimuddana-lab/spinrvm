-- 82_rides_discount_columns.sql
--
-- Adds discount/promo bookkeeping columns to `rides` so admin-created rides
-- (and any rider-side promo redemption that wants on-ride storage) can record
-- the subtotal, the promo code applied, the discount amount, and the
-- corresponding `promo_applications` row id without joining a second table.
--
-- Why:
-- ── backend/routes/admin/rides.py:admin_create_ride now accepts a promo code
--    on behalf of the rider and may override the final fare. Receipts and
--    audit need: subtotal_fare, discount_amount, total_fare, promo_code.
-- ── Previously, `rides` stored only `total_fare`. Discount lived solely on
--    `promo_applications` and was invisible to the receipt renderer.
--
-- Forward-compatible: all columns are nullable; existing rides get NULL,
-- which the application reads as "no discount applied." No backfill.
--
-- Rollback:
--   ALTER TABLE rides
--     DROP COLUMN IF EXISTS subtotal_fare,
--     DROP COLUMN IF EXISTS discount_amount,
--     DROP COLUMN IF EXISTS promo_code,
--     DROP COLUMN IF EXISTS promo_application_id,
--     DROP COLUMN IF EXISTS fare_overridden_by_admin;
--
-- RLS unchanged — `rides` policies operate on the row, not the column set;
-- service role (backend) already bypasses RLS and writes these fields.

DO $$ BEGIN
    -- Pre-discount fare (what the breakdown sums to before promo/override).
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'rides' AND column_name = 'subtotal_fare'
    ) THEN
        ALTER TABLE rides ADD COLUMN subtotal_fare NUMERIC(10, 2);
    END IF;

    -- Discount applied (>= 0). NULL = no promo on this ride.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'rides' AND column_name = 'discount_amount'
    ) THEN
        ALTER TABLE rides ADD COLUMN discount_amount NUMERIC(10, 2) DEFAULT 0;
    END IF;

    -- Promo code (uppercased) for receipt + audit. Denormalised from promotions.code.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'rides' AND column_name = 'promo_code'
    ) THEN
        ALTER TABLE rides ADD COLUMN promo_code TEXT;
    END IF;

    -- FK-by-convention to promo_applications.id (joinable for audit trail).
    -- Not enforced as a hard FK to keep this migration forward-compatible
    -- against environments where promo_applications was created out-of-band.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'rides' AND column_name = 'promo_application_id'
    ) THEN
        ALTER TABLE rides ADD COLUMN promo_application_id UUID;
    END IF;

    -- Flag set when admin manually edited the final fare AFTER promo applied.
    -- Helps a finance auditor distinguish "computed" from "override" rides.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'rides' AND column_name = 'fare_overridden_by_admin'
    ) THEN
        ALTER TABLE rides ADD COLUMN fare_overridden_by_admin BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
END $$;

-- Help the admin "rides with promo applied" view + per-promo redemption audit.
CREATE INDEX IF NOT EXISTS idx_rides_promo_application_id
    ON rides (promo_application_id)
    WHERE promo_application_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
