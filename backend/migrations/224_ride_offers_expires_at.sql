-- 224_ride_offers_expires_at.sql
-- Purpose: Persist the per-offer expiry deadline on ride_offers so a durable,
-- restart-safe reaper can find and expire offers whose in-process asyncio timer
-- was lost on a backend restart/deploy. Previously the deadline lived only in
-- the transient WS/FCM payload and the in-memory asyncio.sleep, so an offer that
-- was counting down at restart never fired its expire / miss-streak /
-- acceptance-rate / insurance-period / re-dispatch side-effects. The partial
-- index is the reaper's hot lookup ("pending offers past their deadline").
--
-- Forward-compatible: additive nullable column. The offer-active-payload path
-- (backend/routes/rides/queries.py:463-481) keeps DERIVING expiry from
-- driver_notified_at when expires_at IS NULL, so pre-migration rows and any
-- in-flight offers are unaffected. New offers persist expires_at (migration M2).
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_ride_offers_pending_expiry;
--   ALTER TABLE public.ride_offers DROP COLUMN IF EXISTS expires_at;

ALTER TABLE public.ride_offers
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- Backfill still-pending offers so the reaper doesn't ignore rows created before
-- this migration. offered_at + 1 minute is exactly the hard ceiling of the real
-- batch-dispatch deadline: batch offers expire at offered_at +
-- ride_offer_timeout_seconds with NO grace period (see _batch_offer_timeout_handler
-- in backend/routes/rides/matching.py), and ride_offer_timeout_seconds is clamped
-- to 5-60s by the CHECK in 106_settings_dispatch_globals.sql. So 60s can never
-- expire a genuinely live offer early, while any already-dead pending row (from a
-- crashed timer) gets an expires_at in the past and is reaped on the next tick.
-- Pending offers are a small, short-lived working set and this WHERE clause is
-- served by the partial index idx_ride_offers_pending (migration 100), so this is
-- a bounded single-pass UPDATE (no batching loop required).
UPDATE public.ride_offers
SET expires_at = offered_at + interval '1 minute'
WHERE status = 'pending' AND expires_at IS NULL;

-- Reaper hot query: WHERE status='pending' AND expires_at < now(). The partial
-- predicate keeps the index tiny (only live offers) and the lookup index-only.
-- CONCURRENTLY: a plain CREATE INDEX takes a whole-table SHARE lock, which would
-- block every dispatch INSERT/accept/decline/expire on ride_offers for the build.
-- migrate.py runs any file containing CONCURRENTLY in per-statement autocommit
-- (repo precedent: 69b_lost_and_found_concurrent_indexes.sql).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_offers_pending_expiry
  ON public.ride_offers (expires_at) WHERE status = 'pending';
