-- 342: Append-only-safe corrections for driver_period_distances.
--
-- WHY: the per-insurance-period distance audit is frozen at settlement — when
-- late GPS arrives (offline tail delivered after completion) the route
-- finalizer recomputes rides.* measured distances but the insurer-billed
-- audit rows can never be corrected: migration 249's immutability trigger
-- blocks UPDATE/DELETE and its partial unique index allows only ONE row per
-- (ride_id, period). Corrections therefore become NEW rows at a higher
-- revision; the trigger stays; readers use the _current view (latest revision
-- per ride/period, plus all ride-less Period-1 rows).
--
-- DEPLOY COUPLING: routes/admin/compliance.py switches to the view in the
-- same deploy — reading the base table after the first correction row lands
-- would double-count insurer billing.
--
-- Rollback (only if no revision>0 rows exist yet):
--   DROP VIEW IF EXISTS driver_period_distances_current;
--   CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_driver_period_distances_ride_period
--       ON driver_period_distances (ride_id, period) WHERE ride_id IS NOT NULL;
--   DROP INDEX CONCURRENTLY IF EXISTS uq_driver_period_distances_ride_period_rev;
--   ALTER TABLE driver_period_distances
--     DROP COLUMN IF EXISTS supersedes_id, DROP COLUMN IF EXISTS revision;

ALTER TABLE driver_period_distances
    ADD COLUMN IF NOT EXISTS revision smallint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS supersedes_id uuid REFERENCES driver_period_distances(id);

-- CONCURRENTLY (runner auto-commits each statement): record_ride_period_
-- distances is awaited synchronously inside ride completion / fare settlement
-- — a transactional index swap would hold an ACCESS EXCLUSIVE lock across the
-- build and stall trip-end receipts. CREATE the new index BEFORE dropping the
-- old one so the (ride, period[, revision]) invariant is never unenforced;
-- with revision defaulted to 0 everywhere, existing rows satisfy both.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_driver_period_distances_ride_period_rev
    ON driver_period_distances (ride_id, period, revision)
    WHERE ride_id IS NOT NULL;

DROP INDEX CONCURRENTLY IF EXISTS uq_driver_period_distances_ride_period;

-- Latest revision per (ride_id, period) + every ride-less row (Period 1
-- deadhead spans have no ride and no revisions).
CREATE OR REPLACE VIEW driver_period_distances_current AS
SELECT dpd.*
FROM driver_period_distances dpd
WHERE dpd.ride_id IS NULL
   OR dpd.revision = (
        SELECT MAX(inner_dpd.revision)
        FROM driver_period_distances inner_dpd
        WHERE inner_dpd.ride_id = dpd.ride_id
          AND inner_dpd.period = dpd.period
   );

-- A view executes with its OWNER's privileges for RLS purposes (unless created
-- WITH security_invoker), and Supabase grants anon/authenticated default CRUD
-- on new public-schema objects — so without this REVOKE the view would expose
-- every driver's per-period audit rows through the anon key, bypassing the
-- migration-249 RLS policy on the base table. Same lockdown pattern as
-- migration 286's financial_event_entries_unbalanced view.
REVOKE ALL ON driver_period_distances_current FROM anon, authenticated;

NOTIFY pgrst, 'reload schema';
