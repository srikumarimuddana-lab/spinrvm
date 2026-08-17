-- 321_tip_collection.sql
--
-- Purpose: record, per authorization, whether a post-trip tip can be added to
-- the booking hold instead of charged separately.
--
-- Background. The booking hold used to be oversized on purpose
-- (`grand_total + RIDE_AUTH_BUFFER_CAD`, a flat $10) so a tip had headroom to
-- ride along on the same PaymentIntent. On a $5 ride that showed the rider a $15
-- pending charge for a $5 trip, which reads as an overcharge. The buffer is now
-- $0.00 — the hold equals the quoted fare — and a tip is folded in by RAISING
-- the authorization (`PaymentIntent.increment_authorization`) before capture,
-- which needs no pre-reserved headroom at all.
--
-- Incremental-authorization support is a property of the CARD, not of our
-- account: Visa/Mastercard grant it broadly, some Amex issuers refuse, and
-- Discover restricts by merchant category. Stripe reports what it actually
-- granted on the charge at authorization time. Settlement happens in a LATER
-- request and cannot cheaply re-ask, so the answer is persisted here.
--
-- Scope note: an earlier version of this migration also created a `pending_tips`
-- table for tips arriving AFTER capture (which can never be incremented — Stripe
-- forbids incrementing a captured PaymentIntent). That is deliberately NOT here:
-- `main` already solved that case in migration 319 + `charge_late_tip` /
-- `charge_late_wallet_tip` / `charge_late_corporate_tip`, which debit the ride's
-- NATIVE payment source (card, personal wallet, or corporate allowance) rather
-- than assuming a personal card. This migration stays narrowly about the
-- pre-capture path so the two do not overlap.
--
-- Rollback:
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS auth_incrementable;
--   Safe at any time. The column is read only by settlement, and its absence is
--   read as "not incrementable" — which degrades to the pre-existing behaviour
--   of charging a tip on its own PaymentIntent. No data migration, no backfill.

ALTER TABLE public.rides
  ADD COLUMN IF NOT EXISTS auth_incrementable boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN public.rides.auth_incrementable IS
  'True when Stripe granted incremental-authorization support on this ride''s '
  'booking-time hold, meaning a post-trip tip can be added to that same '
  'PaymentIntent instead of charged separately. Read back from the charge at '
  'authorization time -- it varies by card brand and issuer, so it is never '
  'assumed. Default false is the safe direction: it only costs one extra Stripe '
  'fixed fee, whereas a wrong true fails the increment at settlement.';
