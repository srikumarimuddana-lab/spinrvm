-- Migration 45: doc_expiry_warned_at on drivers
--
-- utils/document_expiry.py reads and writes drivers.doc_expiry_warned_at to
-- throttle "your documents are expiring" push notifications (one warning per
-- driver per expiry window). The column was never added to the schema, so the
-- background loop errored on every tick with:
--   Could not find the 'doc_expiry_warned_at' column of 'drivers' (PGRST204)
-- Those failures also pumped the DB circuit breaker's failure counter, which
-- contributed to spurious 503s on unrelated endpoints.

ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS doc_expiry_warned_at TIMESTAMPTZ;

COMMENT ON COLUMN drivers.doc_expiry_warned_at IS
    'Timestamp of the last document-expiry warning push. Used to rate-limit notifications.';
