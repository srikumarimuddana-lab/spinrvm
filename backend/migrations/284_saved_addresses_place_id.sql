-- 284_saved_addresses_place_id.sql
-- Adds a place_id column to saved_addresses (ACTION_ITEMS.md B9,
-- explicitly-deferred half). The write-time geocode-verify check
-- (utils/address_verification.py) is the actual safety net for the
-- address<->coordinate mismatch bug class this item tracks; storing the
-- Google place_id captured during that same verification call is an
-- enhancement on top, not required to close the core gap -- it lets a
-- future re-resolve-on-use pass confirm a saved address still points at
-- the same real-world place without re-geocoding free-text against a
-- coordinate again.
--
-- Nullable at the schema level (forward-compatible -- existing saved
-- addresses have no place_id and must not break on this migration, and
-- the verification call that captures it fails open in several cases --
-- no API key, budget exhausted, ambiguous geocode -- so a NULL place_id
-- on a NEW row is an expected, not exceptional, outcome).
--
-- Rollback: `ALTER TABLE saved_addresses DROP COLUMN IF EXISTS place_id;`
-- Safe -- no other migration or view references this column, and dropping
-- a nullable column with no dependents is a pure schema shrink.

BEGIN;

ALTER TABLE saved_addresses
    ADD COLUMN IF NOT EXISTS place_id TEXT;

COMMENT ON COLUMN saved_addresses.place_id IS
    'Google Places place_id captured from the write-time geocode-verify '
    'check (utils/address_verification.py) when a geocode result was '
    'returned, regardless of whether it was precise enough to confirm a '
    'match. NULL when verification failed open (no API key, budget '
    'exhausted, network/API error) or returned no result at all. '
    'Enhancement on top of the coordinate-mismatch write-time reject -- '
    'not itself relied on for any validation today.';

COMMIT;
