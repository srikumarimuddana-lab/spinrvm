-- Migration 40: human-readable ride_code
--
-- rides.id is a UUID (good for joins and WS channels, terrible when
-- operators or riders need to read it over the phone). Add a short
-- ride_code users can actually reference: "SPR-XXXXXX" with 6 chars
-- from a Crockford-ish base32 alphabet (no 0/O/1/I/L so the code
-- can't be misread aloud).
--
-- UUID stays as the primary key — no foreign keys, WS channel names,
-- or app routes change. ride_code is TEXT UNIQUE, so a duplicate
-- insert (astronomically unlikely at 32^6 ≈ 1e9 combinations) would
-- bubble up as a constraint error and the backend retries with a
-- fresh code.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS ride_code TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_rides_ride_code
    ON rides (ride_code)
    WHERE ride_code IS NOT NULL;

-- Backfill existing rides with a generated code. Alphabet excludes
-- 0/O/1/I/L to avoid read-aloud ambiguity. Done with a plpgsql DO
-- block so we can loop one ride at a time and catch the unique-index
-- collision on the (very unlikely) duplicate.
DO $$
DECLARE
    r_id TEXT;
    alphabet TEXT := '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
    code TEXT;
    retries INT;
BEGIN
    FOR r_id IN SELECT id FROM rides WHERE ride_code IS NULL LOOP
        retries := 0;
        LOOP
            code := 'SPR-' ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1) ||
                substr(alphabet, 1 + floor(random()*32)::int, 1);
            BEGIN
                UPDATE rides SET ride_code = code WHERE id = r_id;
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

COMMENT ON COLUMN rides.ride_code IS
    'Short human-readable identifier (SPR-XXXXXX). Safe to quote over phone. UUID stays primary key.';
