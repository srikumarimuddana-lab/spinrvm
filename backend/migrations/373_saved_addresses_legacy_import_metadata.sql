-- 373_saved_addresses_legacy_import_metadata.sql
--
-- Purpose:
--   Adds a `legacy_import_metadata` provenance column to `saved_addresses`,
--   matching the shape every other importer in this migration effort already
--   uses on `users`/`drivers`/`rides` (JSONB NOT NULL DEFAULT '{}'). Needed
--   for Phase 4 of docs/migration/2026-08-27-legacy-data-full-migration-
--   approach.md: importing riders' saved addresses from the legacy Mongo
--   `customer_addresses.csv` export into this existing, live, self-serve
--   table (routes/addresses.py) rather than inventing a new destination.
--
-- Why additive, not a new table:
--   `saved_addresses` already exists and is already exactly the right
--   destination (id/user_id/name/address/lat/lng/icon/place_id/created_at)
--   -- it just has no provenance column yet, same gap
--   `rider_import_service.py` had before 2026-08-17's backfill. This is a
--   single nullable-with-default column addition; every existing row's
--   current data is untouched (defaults to '{}', identical to "not
--   imported").
--
-- Forward-compatible: nullable-with-default ALTER TABLE ADD COLUMN is a
-- metadata-only change on Postgres 11+, no table rewrite, safe against
-- live traffic (routes/addresses.py's INSERT/SELECT are unaffected -- the
-- new column is never referenced by existing code paths).
--
-- RLS: no new policy needed by this migration. saved_addresses has RLS
-- ENABLED but, checked directly against production, currently carries ZERO
-- policies (RLS-enabled-with-no-policy denies all access to anon/
-- authenticated roles; the service-role backend bypasses RLS entirely,
-- which is how routes/addresses.py already reads/writes this table today).
-- That posture is unrelated to this column addition and pre-dates it --
-- flagged separately as its own finding, not silently fixed here.
--
-- Rollback:
--   ALTER TABLE saved_addresses DROP COLUMN IF EXISTS legacy_import_metadata;
--   Safe any time before a backfilled row's provenance is relied on by a
--   downstream consumer (none exist yet -- this migration is purely
--   additive with no other code change in the same commit).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'saved_addresses' AND column_name = 'legacy_import_metadata'
    ) THEN
        ALTER TABLE saved_addresses
            ADD COLUMN legacy_import_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
    END IF;
END $$;
