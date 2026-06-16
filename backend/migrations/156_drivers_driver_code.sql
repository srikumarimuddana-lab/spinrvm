-- Migration 156: human-readable driver_code
--
-- drivers.id is a UUID (good for joins/FKs, terrible to quote). Add a short
-- app-wide driver_code operators, riders, and support can reference and search
-- by: "DRV-XXXXXX" with 6 chars from a Crockford-ish base32 alphabet (no
-- 0/O/1/I/L so it can't be misread aloud). Mirrors rides.ride_code
-- (migration 40).
--
-- The UUID stays the primary key — no foreign keys, app routes, or WS keys
-- change. driver_code is TEXT UNIQUE; a duplicate insert (astronomically
-- unlikely at 32^6 ≈ 1e9) bubbles up as a constraint error and the backend
-- retries with a fresh code.
--
-- Forward-compatible: ADD COLUMN (nullable) + partial unique index + a batched
-- backfill loop. Safe under live traffic.
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_drivers_driver_code;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS driver_code;

ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS driver_code TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_drivers_driver_code
    ON drivers (driver_code)
    WHERE driver_code IS NOT NULL;

-- Backfill existing drivers, one row at a time, retrying on the (very unlikely)
-- unique collision. Alphabet excludes 0/O/1/I/L.
DO $$
DECLARE
    d_id TEXT;
    alphabet TEXT := '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
    code TEXT;
    retries INT;
BEGIN
    FOR d_id IN SELECT id FROM drivers WHERE driver_code IS NULL LOOP
        retries := 0;
        LOOP
            code := 'DRV-' ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1);
            BEGIN
                UPDATE drivers SET driver_code = code WHERE id = d_id;
                EXIT;
            EXCEPTION WHEN unique_violation THEN
                retries := retries + 1;
                IF retries > 5 THEN
                    RAISE;
                END IF;
            END;
        END LOOP;
    END LOOP;
END $$;

COMMENT ON COLUMN drivers.driver_code IS
    'Short human-readable driver identifier (DRV-XXXXXX). Safe to quote/search. UUID stays primary key.';
