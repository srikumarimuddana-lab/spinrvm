-- 116_lost_and_found_rider_user_id.sql
--
-- Adds rider_user_id to lost_and_found so the rider on a driver-initiated
-- case can be identified without a JOIN through rides. This allows the list
-- endpoint to return all cases a user is party to with simple single-column
-- filters (reporter_id for rider-reported, rider_user_id for driver-reported,
-- driver_id for driver-side view).
--
-- Rollback:
--   DROP INDEX IF EXISTS lost_and_found_rider_user_id_idx;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS rider_user_id;
--   NOTIFY pgrst, 'reload schema';

ALTER TABLE lost_and_found
    ADD COLUMN IF NOT EXISTS rider_user_id TEXT;

CREATE INDEX IF NOT EXISTS lost_and_found_rider_user_id_idx
    ON lost_and_found (rider_user_id);

NOTIFY pgrst, 'reload schema';
