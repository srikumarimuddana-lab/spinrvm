-- Migration 186: Tax breakdown columns on subscription_payments
--
-- The plan price is the pre-tax (subtotal) amount.  GST, PST and HST are
-- computed at checkout from the driver's service-area tax config and stored
-- here so every invoice can show the authoritative breakdown without
-- re-deriving it.
--
-- Columns are nullable so legacy rows (backfilled by migration 151) remain
-- valid — the payments endpoint back-computes from the area config for old rows.
--
-- Rollback:
--   ALTER TABLE public.subscription_payments
--       DROP COLUMN IF EXISTS subtotal,
--       DROP COLUMN IF EXISTS gst_amount,
--       DROP COLUMN IF EXISTS pst_amount,
--       DROP COLUMN IF EXISTS hst_amount,
--       DROP COLUMN IF EXISTS tax_total,
--       DROP COLUMN IF EXISTS province;

ALTER TABLE public.subscription_payments
    ADD COLUMN IF NOT EXISTS subtotal    NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS gst_amount  NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS pst_amount  NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS hst_amount  NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS tax_total   NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS province    TEXT;

COMMENT ON COLUMN public.subscription_payments.subtotal   IS 'Pre-tax plan price charged to driver.';
COMMENT ON COLUMN public.subscription_payments.gst_amount IS 'Federal GST collected (5% for most CA provinces).';
COMMENT ON COLUMN public.subscription_payments.pst_amount IS 'Provincial PST collected (SK 6%, AB 0%, QC uses QST).';
COMMENT ON COLUMN public.subscription_payments.hst_amount IS 'Harmonised HST collected (ON/NS/NB/NL/PEI 13-15%).';
COMMENT ON COLUMN public.subscription_payments.tax_total  IS 'gst_amount + pst_amount + hst_amount.';
COMMENT ON COLUMN public.subscription_payments.province   IS 'Two-letter province code from service_area at time of charge.';
