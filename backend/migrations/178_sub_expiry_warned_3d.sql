-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_driver_subscriptions_expiry_warn_3d;
--   ALTER TABLE driver_subscriptions DROP COLUMN IF EXISTS expiry_warned_3d;
--
-- Adds a second claim-flag for the 3-day expiry warning push notification.
-- The existing `expiry_warned` column gates the 24-hour push; this new column
-- gates an earlier reminder at ~72 hours before expiry so drivers have enough
-- time to renew before they are forced offline.
--
-- Both flags follow the claim-flag replay-safety pattern: the background loop
-- performs an UPDATE … WHERE expiry_warned_3d = false and only sends the push
-- when the update returns a row, preventing duplicate notifications on
-- multi-replica deploys.

ALTER TABLE driver_subscriptions
  ADD COLUMN IF NOT EXISTS expiry_warned_3d BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill: any subscription expiring in the next 3 days that was already
-- warned at 24h has already been active — mark the 3d flag true so they
-- don't get a belated "3 days left" message on the next loop tick.
UPDATE driver_subscriptions
SET    expiry_warned_3d = TRUE
WHERE  expiry_warned = TRUE;

-- Index for the background loop query so the 6-hour sweep is fast even with
-- many rows. The partial index only covers active subscriptions because those
-- are the only ones the loop checks.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_driver_subscriptions_expiry_warn_3d
  ON driver_subscriptions (expires_at, expiry_warned_3d)
  WHERE status = 'active';
