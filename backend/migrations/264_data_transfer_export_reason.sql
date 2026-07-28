-- 264_data_transfer_export_reason.sql
-- Adds a short business-justification field to Data Transfer export jobs
-- (PIA recommendation R-C, docs/privacy/2026-07-28-pia-data-transfer-export.md,
-- ACTION_ITEMS.md B11). Closes the accountability gap: previously an export
-- request had no required field capturing *why* a given export happened, so
-- there was no contemporaneous record to check a later compliance question
-- against.
--
-- Nullable at the schema level (forward-compatible — existing completed/
-- historical job rows have no reason and must not break on this migration);
-- the backend route enforces "required" at the application layer for all
-- NEW export requests going forward (Pydantic Field(..., min_length=...) on
-- ExportRequest.reason), not via a NOT NULL constraint that would need a
-- backfill for old rows.
--
-- Rollback: `ALTER TABLE data_transfer_export_jobs DROP COLUMN IF EXISTS reason;`
-- Safe — no other migration or view references this column, and dropping a
-- nullable column with no dependents is a pure schema shrink, no data-shape
-- coordination needed with any other table.

BEGIN;

ALTER TABLE data_transfer_export_jobs
    ADD COLUMN IF NOT EXISTS reason TEXT;

COMMENT ON COLUMN data_transfer_export_jobs.reason IS
    'Short admin-supplied business justification for this export, e.g. '
    '"seeding staging with 20 realistic driver profiles". Required by the '
    'route for new exports (backend-enforced, not a DB constraint, to avoid '
    'a NOT NULL backfill for pre-existing rows). Displayed in the (super_admin-'
    'only) Jobs & History tab for accountability/audit purposes.';

COMMIT;
