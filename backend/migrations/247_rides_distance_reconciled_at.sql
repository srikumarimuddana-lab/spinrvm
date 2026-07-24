-- 247_rides_distance_reconciled_at.sql
-- Claim-flag column for the nightly distance-reconciliation loop: once a
-- completed ride's quoted-vs-measured distance has been compared, its
-- distance_reconciled_at is stamped so the loop (which runs on every replica)
-- never re-processes it — the atomic UPDATE ... WHERE distance_reconciled_at IS
-- NULL RETURNING is the per-ride claim, and duplicate metric/event emission is
-- thereby prevented.
--
-- Forward-compatible: nullable column with no default, so the ALTER is a
-- metadata-only change (no table rewrite / no row locks on the live rides
-- table). Existing rows read NULL = "not yet reconciled".
--
-- Rollback:
-- Stop the reconciliation loop, then
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_rides_distance_unreconciled;
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS distance_reconciled_at;
-- Nothing else references either; dropping loses only the "already reconciled"
-- marker (the loop would re-scan historical rides once, harmless aside from
-- re-emitting aggregate metrics).

ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS distance_reconciled_at timestamptz;

-- Partial index for the loop's hot query: unreconciled completed rides only, so
-- the index stays small (drains toward empty as the backlog is processed) and
-- the scan never walks the full rides history. CONCURRENTLY because rides is a
-- high-traffic table — a plain CREATE INDEX would SHARE-lock it for the whole
-- build, blocking ride status transitions / settlement / dispatch writes. The
-- migration runner (backend/scripts/migrate.py) detects CONCURRENTLY and runs
-- the file in autocommit, statement-by-statement (same as migration 239).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_distance_unreconciled
    ON public.rides(ride_completed_at)
    WHERE distance_reconciled_at IS NULL AND status = 'completed';

COMMENT ON COLUMN public.rides.distance_reconciled_at IS
    'When the distance-reconciliation loop compared quoted vs measured distance. NULL = pending. Claim flag; never affects fare.';

NOTIFY pgrst, 'reload schema';
