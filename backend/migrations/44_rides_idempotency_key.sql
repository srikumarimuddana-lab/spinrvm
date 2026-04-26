-- Migration 44: idempotency_key on rides
--
-- routes/rides.py reads Idempotency-Key from the request header and, if
-- present, looks up an existing ride with the same (rider_id, idempotency_key)
-- before creating a new one. The column was never added to the schema, so
-- every ride-create request errored out with
--   column rides.idempotency_key does not exist  (42703)
--
-- Add the column and a partial unique index so the lookup is cheap and the
-- (rider_id, idempotency_key) pair can't be duplicated.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_rides_rider_idempotency_key
    ON rides (rider_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

COMMENT ON COLUMN rides.idempotency_key IS
    'Client-supplied Idempotency-Key header for create-ride retries. Scoped per rider.';
