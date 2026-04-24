-- Migration 42: drivers.last_status_changed_at
--
-- The admin dashboard needs to display "Online since …" / "Offline
-- since …" next to the driver's online badge. Today the only
-- timestamp we touch on a status flip is drivers.updated_at, which
-- is also bumped for vehicle updates, profile edits, etc., so it
-- doesn't answer "when did this driver go offline".
--
-- Add a dedicated column that update_driver_status only touches
-- when is_online actually flips. Backfill existing rows with
-- updated_at as a best-effort starting value — correct for rows
-- that haven't had any non-status update since the last toggle,
-- and at worst it gives operators a recent timestamp instead of
-- NULL.

ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS last_status_changed_at TIMESTAMPTZ;

UPDATE drivers
   SET last_status_changed_at = COALESCE(updated_at, created_at)
 WHERE last_status_changed_at IS NULL;

COMMENT ON COLUMN drivers.last_status_changed_at IS
    'Timestamp of the most recent is_online flip. Only touched when the boolean actually changes, so admin UI can show "online since"/"offline since".';
