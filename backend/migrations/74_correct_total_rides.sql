-- Rollback: No rollback possible — this corrects a counter that was wrong.
--           The previous (wrong) values are not worth preserving.
--           If needed, set total_rides = 0 to reset rather than revert.

-- Recalculate total_rides for all drivers from the actual completed ride records.
-- The $inc increment in complete_ride() was silently dropped by the DB wrapper
-- (fixed in routes/drivers.py commit 48c547f). This migration corrects
-- all historical rows.
UPDATE drivers
SET total_rides = (
    SELECT count(*)
    FROM rides
    WHERE rides.driver_id = drivers.id
      AND rides.status = 'completed'
);
