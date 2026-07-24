-- Migration 251: cancel_fee_payment_intent_id — separate column for cancellation fee PI
--
-- rollback: ALTER TABLE rides DROP COLUMN IF EXISTS cancel_fee_payment_intent_id;
--
-- Finding 11 (WS-8): when a rider cancels and a cancellation fee is charged,
-- the fee's PaymentIntent id was written into payment_intent_id, clobbering
-- the booking-time pre-auth hold's PI. After that overwrite, the original hold
-- can never be released — the rider's card shows a phantom charge for up to 7
-- days until Stripe auto-expires it.
--
-- Fix: store the cancellation fee PI in its own column so the booking-time
-- payment_intent_id is preserved and can be explicitly released via
-- PaymentIntent.cancel.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancel_fee_payment_intent_id TEXT;

COMMENT ON COLUMN rides.cancel_fee_payment_intent_id IS
    'Stripe PaymentIntent id for the cancellation/no-show fee charge, kept separate from payment_intent_id (the booking-time pre-auth hold) so the hold can be explicitly released on cancel.';
