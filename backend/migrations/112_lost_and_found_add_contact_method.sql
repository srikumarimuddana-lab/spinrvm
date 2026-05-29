-- 112_lost_and_found_add_contact_method.sql
--
-- Production repair: adds the contact_method column to lost_and_found on
-- environments where migration 69a_lost_and_found_repair_schema.sql was
-- either never applied (e.g. the table pre-dated the migration runner) or
-- ran before the ADD COLUMN statement was present.
--
-- PGRST204 "Could not find the 'contact_method' column of 'lost_and_found'
-- in the schema cache" is the symptom — every POST /lost-and-found returns
-- 503 until this migration runs.
--
-- The NOTIFY at the end forces PostgREST to reload its schema cache so the
-- column is visible immediately without a server restart.
--
-- Forward-compatible: ADD COLUMN IF NOT EXISTS is a no-op when the column
-- already exists (environments where 69a ran successfully).
--
-- Rollback:
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS contact_method;
--   NOTIFY pgrst, 'reload schema';

ALTER TABLE lost_and_found
    ADD COLUMN IF NOT EXISTS contact_method TEXT;

NOTIFY pgrst, 'reload schema';
