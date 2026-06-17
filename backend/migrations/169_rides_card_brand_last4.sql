-- 169_rides_card_brand_last4.sql
--
-- Purpose:
--   Show the card used for a ride in the admin ride detail (and keep it as a
--   durable receipt record). Two nullable columns cache the masked card:
--     card_brand  e.g. 'Visa', 'Mastercard'
--     card_last4  the last 4 digits (NEVER the PAN — PCI)
--
--   admin_get_ride_details resolves these from Stripe on first view (from the
--   ride's payment_intent_id / payment_method_id) and backfills them here, so
--   later views are instant and the record survives the rider deleting the card.
--   New card rides can also be populated at payment time later if desired.
--
--   PCI: only the last-4 is ever stored, never the full card number.
--
-- RLS: rides already has RLS; adding columns does not change it. Backfilled
-- only by the backend service role.
--
-- Rollback:
--   ALTER TABLE rides DROP CONSTRAINT IF EXISTS rides_card_last4_check;
--   ALTER TABLE rides DROP COLUMN IF EXISTS card_brand, DROP COLUMN IF EXISTS card_last4;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS card_brand TEXT,
    ADD COLUMN IF NOT EXISTS card_last4 TEXT;

-- Belt-and-braces: last-4 must be exactly four digits (or NULL). Stops a
-- malformed value — or a stray full PAN from a direct write — from being
-- persisted. NOT VALID + VALIDATE keeps it non-blocking on a large table
-- (all existing rows are NULL, so validation is trivial).
ALTER TABLE rides DROP CONSTRAINT IF EXISTS rides_card_last4_check;
ALTER TABLE rides
    ADD CONSTRAINT rides_card_last4_check
    CHECK (card_last4 IS NULL OR card_last4 ~ '^[0-9]{4}$') NOT VALID;
ALTER TABLE rides VALIDATE CONSTRAINT rides_card_last4_check;

COMMENT ON COLUMN rides.card_brand IS 'Masked card brand used for the ride (e.g. Visa). Never the PAN.';
COMMENT ON COLUMN rides.card_last4 IS 'Last 4 digits of the card used for the ride. Never the full PAN (PCI).';
