-- Phase 3b — field alignment cleanup
--
-- 1. drivers: split atom `name` → `first_name` + `last_name` (additive; old column kept)
-- 2. bank_accounts: rename `account_number_last4` → `account_last4` (matches API)
--
-- ROLLBACK PLAN (manual, on paper — no down-migration file):
--   1. ALTER TABLE bank_accounts RENAME COLUMN account_last4 TO account_number_last4;
--   2. ALTER TABLE drivers DROP COLUMN first_name, DROP COLUMN last_name;
--   The drivers `name` column is preserved by this migration, so rollback is non-destructive.
--
-- FORWARD COMPATIBILITY:
--   - Backend code in this PR writes BOTH `name` and `first_name`/`last_name` so a
--     pre-PR replica still serving traffic continues to work.
--   - The `account_number_last4` → `account_last4` rename is atomic. The single
--     read site (routes/drivers.py:1424) is updated in the same PR.

BEGIN;

-- 1. drivers: add split name columns
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS last_name TEXT;

-- Backfill: split existing `name` on the first space; everything before is first,
-- everything after is last. Single-word names get the whole thing as first_name.
UPDATE drivers
SET
  first_name = COALESCE(first_name, NULLIF(split_part(name, ' ', 1), '')),
  last_name = COALESCE(
    last_name,
    NULLIF(
      CASE
        WHEN position(' ' IN name) > 0 THEN trim(substring(name FROM position(' ' IN name) + 1))
        ELSE ''
      END,
      ''
    )
  )
WHERE name IS NOT NULL AND (first_name IS NULL OR last_name IS NULL);

-- 2. bank_accounts: rename column to match API key
ALTER TABLE bank_accounts RENAME COLUMN account_number_last4 TO account_last4;

COMMIT;
