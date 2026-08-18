-- 332_backfill_legacy_ride_insurance_periods.sql
-- CR-4081 / ACTION_ITEMS.md A34 — "reconstruct-and-flag" remediation for the
-- 186 legacy-imported rides that have zero driver_insurance_periods rows.
--
-- Decision recorded in issue #4081 (2026-08-18): reconstruct-and-flag,
-- approved by this session's user, confirmed to hold the SGI-facing
-- legal/regulatory authority CLAUDE.md requires for this specific call.
-- CLAUDE.md is explicit: "engineering must NOT fabricate period rows" —
-- this migration does not fabricate anything undisclosed. Every row it
-- writes is marked `is_reconstructed = true`, derived only from timestamps
-- the legacy importer actually preserved on the ride itself, and the 4
-- rides where those timestamps don't support even an approximate
-- reconstruction are explicitly excluded and documented below, not
-- silently skipped.
--
-- Scope, verified directly against production data before writing this file
-- (read-only queries, via Supabase MCP — see PR body for the exact queries
-- and results):
--   * 186 rides total have legacy_import_metadata and zero existing
--     driver_insurance_periods rows (all status = 'completed').
--   * driver_notified_at / driver_accepted_at / assigned_at are NULL for
--     ALL 186 — the old app / importer never captured a driver-assignment
--     timestamp. This is not new information: migration 65's own Period-2
--     backfill already falls back through
--     COALESCE(driver_arrived_at, driver_accepted_at, created_at, now())
--     for exactly this reason. This migration follows that same
--     established precedent and the same disclosed limitation: Period 2
--     is reconstructed starting from driver_arrived_at, not the true
--     (unrecorded) assignment moment, so the true Period-1->2 boundary is
--     understated for these rides. Per spinr-insurance-period-auditor's
--     own rule ("Period 2 starts on driver_assigned, not driver_accepted")
--     this is a known, disclosed gap inherited from the source data, not
--     an error in this migration.
--   * 182 of the 186 rides have driver_id + driver_arrived_at + started_at
--     (== ride_started_at, verified identical for all 186) + ride_completed_at
--     all present, and driver_id resolves to a real row in `drivers` (all
--     182, verified). These get a clean two-row reconstruction: Period 2
--     (driver_arrived_at -> started_at) and Period 3 (started_at ->
--     ride_completed_at, carrying ride_id per the period_3_requires_ride
--     check).
--   * 4 rides are explicitly excluded from reconstruction — no row is
--     written for them, by design, not omission:
--       - 3 rides have driver_id IS NULL (driver_insurance_periods.driver_id
--         is NOT NULL with a drivers(id) FK — there is no driver to
--         attribute a period to):
--           bda2a258-7987-4344-882e-ca202df17d43
--           ab5c5f5b-4c3e-4989-90a8-8163b69b08b5
--           ab0acdfc-46fd-430e-a6e2-502c1a2c7642
--       - 1 ride (e8c7f1b5-84f4-4a64-9f98-1b8ca70ba251) has a driver_id but
--         driver_arrived_at/started_at/ride_started_at are ALL NULL; only
--         created_at (2026-04-13 19:46) and ride_completed_at (2026-04-14
--         10:34, ~14.8 hours later) exist. That gap is far too long to be
--         a real single trip duration and is almost certainly an import
--         data artifact, not a recoverable Period-2/3 boundary. Writing a
--         ~14.8-hour Period-3 ("passenger aboard") row from those two
--         timestamps would be exactly the fabrication CLAUDE.md prohibits,
--         not a reconstruction — excluded, flagged for manual review if
--         it's ever specifically needed.
--
-- Rollback:
--   DELETE FROM driver_insurance_periods WHERE is_reconstructed = true;
--   ALTER TABLE driver_insurance_periods DROP COLUMN is_reconstructed;
--   -- (also revert the immutability-trigger replacement below, or accept
--   -- the trigger's is_reconstructed column-lock becoming a no-op once the
--   -- column is gone)
--   -- Safe any time before a downstream consumer (SGI export, admin
--   -- tooling, spinr-insurance-period-auditor-driven code) relies on the
--   -- reconstructed rows; each row's own append-only contract (see
--   -- migration 64) still applies once written, same as any other row.
--
-- Forward-compatible: adds one column to an existing table, no other
-- table touched. `rides` is not written by this migration.

-- ---------------------------------------------------------------------
-- Step 1: additive marker column. NOT NULL DEFAULT FALSE means every
-- existing (contemporaneously-logged) row is correctly labelled false —
-- Postgres 11+ applies a constant DEFAULT as a metadata-only operation,
-- no table rewrite, no lock escalation beyond the brief ACCESS EXCLUSIVE
-- needed for the catalog change itself.
-- ---------------------------------------------------------------------
ALTER TABLE driver_insurance_periods
    ADD COLUMN IF NOT EXISTS is_reconstructed boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN driver_insurance_periods.is_reconstructed IS
    'CR-4081. true = this period row was reconstructed after the fact from '
    'ride-level timestamps (legacy-imported ride whose driver-side state '
    'transitions were never captured), not written contemporaneously by '
    'record_period_transition(). Structurally distinguishes reconstructed '
    'rows for SGI audit exports, admin tooling, and '
    'spinr-insurance-period-auditor — never conflate with a live-logged row.';

-- ---------------------------------------------------------------------
-- Step 2: extend the append-only immutability trigger (migration 64) so
-- is_reconstructed is protected the same way every other column already
-- is. Without this, the trigger's explicit column-by-column comparison
-- would silently allow is_reconstructed to be flipped post-insert on an
-- otherwise-still-open row's close UPDATE -- a gap the original trigger
-- couldn't have anticipated (this column didn't exist yet). Re-creating
-- with CREATE OR REPLACE FUNCTION; the trigger itself (already attached
-- to the table since migration 64) picks up the new function body
-- automatically, no need to re-create the trigger.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _driver_insurance_periods_immutable()
RETURNS trigger LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS
$$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'driver_insurance_periods rows are append-only and cannot be deleted';
    END IF;

    -- UPDATE: allow only the close transition (ended_at NULL -> non-NULL).
    -- Every other column must be unchanged.
    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION
            'driver_insurance_periods row % is already closed and cannot be modified', OLD.id;
    END IF;

    IF NEW.ended_at IS NULL THEN
        RAISE EXCEPTION
            'driver_insurance_periods UPDATE must set ended_at to a non-NULL timestamp';
    END IF;

    IF NEW.id          IS DISTINCT FROM OLD.id
       OR NEW.driver_id IS DISTINCT FROM OLD.driver_id
       OR NEW.period    IS DISTINCT FROM OLD.period
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.ride_id   IS DISTINCT FROM OLD.ride_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.is_reconstructed IS DISTINCT FROM OLD.is_reconstructed THEN
        RAISE EXCEPTION
            'driver_insurance_periods UPDATE may only set ended_at; other columns are immutable';
    END IF;

    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------
-- Step 3: backfill. Scoped tightly to the 186-row legacy-import set via
-- legacy_import_metadata, with the same NOT EXISTS idempotency guard
-- migration 65 uses (re-running this migration is a no-op the second
-- time -- also enforced anyway by the migration runner's
-- schema_migrations tracking, same belt-and-suspenders approach as 65).
-- Every row this backfill writes is CLOSED (ended_at set) -- these are
-- all historical, completed rides, never an "open" row -- so none of
-- this ever touches the driver_insurance_periods_open partial unique
-- index (one-open-row-per-driver), regardless of how many legacy rides
-- a given driver appears in.
-- ---------------------------------------------------------------------

-- Period 2 (en route to pickup): driver_arrived_at -> started_at.
-- Excludes the 4 documented rides above via the NULL/anomaly guards
-- (driver_id IS NOT NULL, driver_arrived_at/started_at both present).
INSERT INTO driver_insurance_periods
    (driver_id, period, ride_id, started_at, ended_at, is_reconstructed)
SELECT
    r.driver_id,
    2,
    r.id,
    r.driver_arrived_at,
    r.started_at,
    true
FROM rides r
WHERE r.legacy_import_metadata IS NOT NULL
  AND r.legacy_import_metadata != '{}'::jsonb
  AND r.driver_id IS NOT NULL
  AND r.driver_arrived_at IS NOT NULL
  AND r.started_at IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM driver_insurance_periods dip
      WHERE dip.ride_id = r.id AND dip.period = 2
  );

-- Period 3 (passenger aboard): started_at -> ride_completed_at.
INSERT INTO driver_insurance_periods
    (driver_id, period, ride_id, started_at, ended_at, is_reconstructed)
SELECT
    r.driver_id,
    3,
    r.id,
    r.started_at,
    r.ride_completed_at,
    true
FROM rides r
WHERE r.legacy_import_metadata IS NOT NULL
  AND r.legacy_import_metadata != '{}'::jsonb
  AND r.driver_id IS NOT NULL
  AND r.started_at IS NOT NULL
  AND r.ride_completed_at IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM driver_insurance_periods dip
      WHERE dip.ride_id = r.id AND dip.period = 3
  );
