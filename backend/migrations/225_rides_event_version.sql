-- 225_rides_event_version.sql
--
-- Purpose:
--   Add a monotonic per-ride event version so WebSocket clients can reject stale
--   / out-of-order ride events (a delayed driver_arrived arriving after
--   in_progress, or a reconnect-replay duplicate) instead of applying them and
--   moving the UI backwards. Ride-status writes are scattered across many call
--   sites with no central transition helper, so the only way to guarantee the
--   counter bumps on EVERY change (including admin/manual edits) is a database
--   trigger — no per-site plumbing can be missed.
--
--   1. version INTEGER NOT NULL DEFAULT 0 — a constant default, so this is a
--      metadata-only ADD COLUMN (no table rewrite / no long lock) in PG11+, and
--      every existing row reads as 0 without a backfill.
--   2. A BEFORE UPDATE trigger sets version = OLD.version + 1 on every row
--      change, regardless of which code path made it. Monotonic-increasing is
--      the only property clients need; gaps (updates that emit no WS event) are
--      fine. The trigger owns the column — callers never set it.
--
--   No CONCURRENTLY, so the whole file runs in one transaction (atomic). The
--   trigger only mutates NEW (no privileged reads), so no SECURITY DEFINER.
--   Coexists with any other BEFORE UPDATE trigger on rides (distinct column;
--   triggers fire in name order).
--
-- Rollback:
--   DROP TRIGGER IF EXISTS trg_rides_bump_version ON rides;
--   DROP FUNCTION IF EXISTS public.bump_ride_version();
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS version;

ALTER TABLE public.rides
  ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION public.bump_ride_version()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS $$
BEGIN
    NEW.version := OLD.version + 1;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_rides_bump_version ON public.rides;
CREATE TRIGGER trg_rides_bump_version
    BEFORE UPDATE ON public.rides
    FOR EACH ROW
    EXECUTE FUNCTION public.bump_ride_version();

COMMENT ON FUNCTION public.bump_ride_version() IS
    'BEFORE UPDATE trigger fn: bumps rides.version = OLD.version + 1 on every row '
    'change, giving clients a monotonic per-ride event version to drop stale/'
    'out-of-order WebSocket events (issue #11).';
