-- 275_drivers_regulator_removal_queue.sql
-- Purpose: Track that a departed driver still owes the regulator a removal
-- filing, and record when that filing was actually produced.
--
-- Why this exists:
--   When a driver deletes their account, Spinr stops dispatching to them, but
--   SGI (or whichever authority licensed them) still lists them as an active
--   passenger-for-hire driver until we file the D00032 "remove" row — and the
--   vehicle stays listed until the matching D00033 row. Nothing triggered or
--   recorded that filing: an admin had to remember, and there was no way to
--   tell an already-filed removal from one nobody had done, so the only safe
--   options were to re-file (duplicate submission) or hope.
--
--   The two forms are tracked separately on purpose. A driver needs BOTH, and
--   stamping a single "reported" column on whichever was generated first would
--   mark the job done with the vehicle still on SGI's books.
--
--   `regulator_removal_effective_date` is the date the driver actually stopped
--   driving, not the date the form was generated. SGI cares about the former --
--   the form filler previously always wrote today's date, which is wrong for a
--   removal filed days or weeks after the fact.
--
-- Rollback:
--   ALTER TABLE public.drivers
--     DROP COLUMN IF EXISTS regulator_removal_required,
--     DROP COLUMN IF EXISTS regulator_removal_effective_date,
--     DROP COLUMN IF EXISTS regulator_removal_driver_form_at,
--     DROP COLUMN IF EXISTS regulator_removal_vehicle_form_at,
--     DROP COLUMN IF EXISTS regulator_removal_reported_by;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_regulator_removal_pending;
--   (Dropping the columns discards the record of which removals were already
--    filed with the regulator. Export the queue first — the SGI submissions
--    themselves are external and a revert cannot undo them.)
--
-- Notes:
-- - The column additions are additive and nullable (bar the BOOLEAN default),
--   so they are safe to apply with production traffic in flight.
-- - This migration DOES backfill: existing soft-deleted, regulator-approved
--   drivers are queued (see the UPDATE below and its comment). The backfill is
--   idempotent — guarded by `regulator_removal_required = FALSE` — so a re-run
--   cannot double-apply.
-- - The index is built CONCURRENTLY because `drivers` is a hot table (location
--   updates, is_online/is_available toggles). A plain CREATE INDEX takes a
--   SHARE lock that blocks every write to `drivers` for the build duration.
--   Because the file contains the literal "CONCURRENTLY", scripts/migrate.py
--   runs the WHOLE file statement-by-statement under autocommit rather than in
--   one transaction (see apply_migration / _apply_migration_autocommit). That
--   is safe here only because every DDL statement is IF NOT EXISTS-guarded and
--   the backfill UPDATE is self-idempotent — a partially-applied run can simply
--   be re-run. Do not add a non-idempotent statement to this file.

ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS regulator_removal_required BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS regulator_removal_effective_date DATE,
  ADD COLUMN IF NOT EXISTS regulator_removal_driver_form_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS regulator_removal_vehicle_form_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS regulator_removal_reported_by TEXT;

-- Backfill drivers who already deleted their account and were approved by the
-- regulator at the time. These are exactly the rows SGI still lists but Spinr
-- no longer dispatches to — the gap this migration exists to close — so
-- leaving them out would mean the queue starts empty while the real backlog
-- sits invisible. An applicant who never got approved was never filed with the
-- regulator and so needs no removal.
--
-- Deliberately unbatched. The predicate is narrow (soft-deleted AND
-- regulator-approved AND not already queued) and the whole drivers table is in
-- the low hundreds of rows today, so this touches a handful. That is a
-- statement about today's data, not a durable safety property — preview the
-- affected count before applying (no trailing semicolon on purpose: migrate.py
-- splits this file on semicolons, so one inside a comment creates a needless
-- chunk boundary):
--     SELECT count(*) FROM public.drivers
--      WHERE deleted_at IS NOT NULL AND regulator_removal_required = FALSE
--        AND (is_verified = TRUE OR regulatory_authority_approved = TRUE)
-- If that ever returns a large number, batch it rather than running as-is.
UPDATE public.drivers
   SET regulator_removal_required = TRUE,
       regulator_removal_effective_date = COALESCE(regulator_removal_effective_date, deleted_at::date)
 WHERE deleted_at IS NOT NULL
   AND regulator_removal_required = FALSE
   AND (is_verified = TRUE OR regulatory_authority_approved = TRUE);

-- The queue query. `sgi_removal_queue` (routes/admin/sgi_forms.py) filters on
-- `regulator_removal_required = TRUE` and orders by the effective date. Whether
-- each form is still outstanding is decided in Python afterwards, because
-- `include_filed=true` deliberately wants the already-filed rows too.
--
-- So the index predicate is exactly that one column and no more. An earlier
-- draft also required "at least one form still unfiled" — that predicate is not
-- implied by the query's WHERE clause, which means Postgres could never use the
-- index and the endpoint would sequential-scan `drivers`. A partial index is
-- only usable when the query's WHERE implies the index's.
--
-- CONCURRENTLY: `drivers` is a hot table, and a plain build blocks every write to it
-- for the duration. See migration 55 for the same treatment.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_regulator_removal_pending
  ON public.drivers (regulator_removal_effective_date)
  WHERE regulator_removal_required = TRUE;

COMMENT ON COLUMN public.drivers.regulator_removal_required IS
  'Driver left (account deletion) while filed with a regulator, so a D00032/D00033 removal is owed. Set by the account-deletion path.';
COMMENT ON COLUMN public.drivers.regulator_removal_effective_date IS
  'Date the driver actually stopped driving. Used as the removal row''s effective_date — NOT the date the form was generated.';
COMMENT ON COLUMN public.drivers.regulator_removal_driver_form_at IS
  'When the D00032 (Driver Details) remove row was generated for this driver. NULL = still owed.';
COMMENT ON COLUMN public.drivers.regulator_removal_vehicle_form_at IS
  'When the D00033 (Vehicle Details) remove row was generated for this driver. NULL = still owed. Tracked separately from the driver form because both are required.';
COMMENT ON COLUMN public.drivers.regulator_removal_reported_by IS
  'admin id (users.id) of whoever last generated a removal form for this driver.';
