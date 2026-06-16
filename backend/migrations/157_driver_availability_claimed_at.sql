-- 157_driver_availability_claimed_at.sql
--
-- Orphaned driver-claim recovery (C3).
--
-- Dispatch claims a driver by atomically flipping is_available=false
-- (claim_driver_atomic) and only THEN inserts the ride_offers row + schedules
-- the in-process offer-timeout task. If the process crashes/restarts in that
-- window, the driver is left is_available=false with no offer row and no
-- timeout handler — silently dropped from dispatch (is_available ⇒ is_online
-- recovery contract violated, match-rate erodes with no obvious cause). The
-- stuck-ride sweeper recovers the ride, not the orphaned driver flag.
--
-- This adds a DEDICATED claim timestamp so a reaper loop can release such
-- orphans WITHOUT racing the sub-second claim→offer-insert window:
--
--   • availability_claimed_at timestamptz — set to now() by claim_driver_atomic
--     when it claims a driver; cleared (NULL) by set_driver_available(True) on
--     release. A reaper releases drivers that are is_online=true,
--     is_available=false, claimed > N seconds ago, AND have no pending offer and
--     no active ride. A dedicated column (NOT updated_at) is used precisely
--     because heartbeats/location writes bump updated_at and would mask the age.
--
-- Index: the reaper fetches drivers is_online=true, is_available=false (then
-- sieves the claim age in code). The partial index predicate matches THAT query
-- exactly so Postgres actually uses it, and indexes availability_claimed_at for
-- ordered access; the available majority is excluded.
--
-- RLS: no new table — drivers already has RLS; a nullable column inherits it.
--
-- Forward-compatible (drivers is a hot table): ADD COLUMN is metadata-only (no
-- rewrite), and the partial index is built CONCURRENTLY (the runner detects
-- CONCURRENTLY and applies the file in autocommit). Old code ignores the column;
-- new code sets it. Existing rows keep NULL (never reaped — conservative).
--
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_drivers_orphan_claim;
--   ALTER TABLE public.drivers DROP COLUMN IF EXISTS availability_claimed_at;

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS availability_claimed_at timestamptz;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_orphan_claim
    ON public.drivers (availability_claimed_at)
    WHERE is_online = true AND is_available = false;
