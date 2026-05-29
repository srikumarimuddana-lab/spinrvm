-- 114_lost_and_found_add_item_category.sql
--
-- Production repair: adds the item_category column to lost_and_found on
-- environments where the table pre-dated the migration runner (or where
-- migration 69 / 69a ran as a no-op against the legacy schema).
--
-- Symptom: PGRST204 "Could not find the 'item_category' column of
-- 'lost_and_found' in the schema cache" — every POST /lost-and-found
-- returns 503 until this migration runs.
--
-- The CHECK constraint matches the valid values enforced in rides.py.
-- The NOTIFY forces PostgREST to reload its schema cache immediately.
--
-- Forward-compatible: ADD COLUMN IF NOT EXISTS is a no-op when the column
-- already exists (environments where 69a ran successfully).
--
-- Rollback:
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS item_category;
--   NOTIFY pgrst, 'reload schema';

ALTER TABLE lost_and_found
    ADD COLUMN IF NOT EXISTS item_category TEXT
        CHECK (item_category IN ('electronics', 'clothing', 'bag', 'document', 'keys', 'other'));

NOTIFY pgrst, 'reload schema';
