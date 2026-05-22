-- Migration: 98_create_ride_messages.sql
-- Rollback: DROP TABLE IF EXISTS ride_messages;
--
-- Creates the ride_messages table for in-ride and post-trip chat.
-- Column names match what routes/rides.py actually reads/writes:
--   `text` (not `message`), `sender` (not `sender_id`+`sender_role`),
--   `timestamp` (used by the ORDER BY in get_ride_messages).
--
-- Note: 08_complete_schema.sql contains an outdated definition with
-- different column names; that file was never applied to production for
-- this table. This migration is the authoritative create.

CREATE TABLE IF NOT EXISTS ride_messages (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id     TEXT        NOT NULL,
    text        TEXT        NOT NULL,
    sender      TEXT        NOT NULL CHECK (sender IN ('rider', 'driver')),
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for the common query pattern: all messages for a ride, oldest-first
CREATE INDEX IF NOT EXISTS idx_ride_messages_ride_timestamp
    ON ride_messages (ride_id, timestamp ASC);

ALTER TABLE ride_messages ENABLE ROW LEVEL SECURITY;

-- The backend service role bypasses RLS.
-- App clients never query this table directly — all access goes through
-- the authenticated REST endpoints (GET/POST /rides/{id}/messages).
CREATE POLICY "ride_messages_no_direct_client_access"
    ON ride_messages
    FOR ALL
    TO authenticated
    USING (false);
