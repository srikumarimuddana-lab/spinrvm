-- 115_lost_and_found_chat.sql
--
-- Adds per-case chat to the lost_and_found feature.
-- Creates lost_and_found_messages and extends lost_and_found with
-- reporter_type so driver- and admin-initiated cases are distinguished.
--
-- Rollback:
--   DROP TABLE IF EXISTS lost_and_found_messages CASCADE;
--   ALTER TABLE lost_and_found DROP COLUMN IF EXISTS reporter_type;
--   ALTER TABLE lost_and_found DROP CONSTRAINT IF EXISTS lost_and_found_status_check;
--   ALTER TABLE lost_and_found ADD CONSTRAINT lost_and_found_status_check
--       CHECK (status IN ('reported','driver_notified','found','returned','unclaimed','resolved','unresolved'));
--   NOTIFY pgrst, 'reload schema';

-- 1. reporter_type: who opened the case
ALTER TABLE lost_and_found
    ADD COLUMN IF NOT EXISTS reporter_type TEXT DEFAULT 'rider'
        CHECK (reporter_type IN ('rider', 'driver', 'admin'));

-- 2. Extend status values to cover driver-initiated and full lifecycle
ALTER TABLE lost_and_found DROP CONSTRAINT IF EXISTS lost_and_found_status_check;

ALTER TABLE lost_and_found
    ADD CONSTRAINT lost_and_found_status_check
        CHECK (status IN (
            'reported',        -- rider filed; driver not yet responded
            'driver_found',    -- driver filed; item in hand
            'admin_created',   -- admin opened on behalf of either party
            'driver_notified', -- push sent to driver after rider report
            'found',           -- driver confirmed item is in vehicle
            'not_found',       -- driver confirmed item not in vehicle
            'returned',        -- item physically returned to rider
            'unclaimed',       -- driver gave up waiting; handed to admin
            'resolved',        -- generic closed-happy
            'unresolved',      -- kept from prior constraint; admin resolve endpoint writes this
            'closed'           -- admin closed without resolution
        ));

-- 3. Per-case chat thread
CREATE TABLE IF NOT EXISTS lost_and_found_messages (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    lost_and_found_id TEXT        NOT NULL,
    sender_id         TEXT,
    sender_role       TEXT        NOT NULL
                                      CHECK (sender_role IN ('rider', 'driver', 'admin', 'system')),
    message           TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lfm_case_time_idx
    ON lost_and_found_messages (lost_and_found_id, created_at);

ALTER TABLE lost_and_found_messages ENABLE ROW LEVEL SECURITY;

-- Rider or driver on the case can read messages
DROP POLICY IF EXISTS lfm_select ON lost_and_found_messages;
CREATE POLICY lfm_select ON lost_and_found_messages
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM lost_and_found lf
            WHERE lf.id = lost_and_found_id
              AND (
                  auth.uid()::text = lf.reporter_id::text
               OR auth.uid()::text = lf.driver_id::text
              )
        )
    );

-- Participants can insert their own messages (not system messages)
DROP POLICY IF EXISTS lfm_insert ON lost_and_found_messages;
CREATE POLICY lfm_insert ON lost_and_found_messages
    FOR INSERT WITH CHECK (
        sender_id IS NOT NULL
        AND auth.uid()::text = sender_id::text
        AND sender_role != 'system'
        AND EXISTS (
            SELECT 1 FROM lost_and_found lf
            WHERE lf.id = lost_and_found_id
              AND (
                  auth.uid()::text = lf.reporter_id::text
               OR auth.uid()::text = lf.driver_id::text
              )
        )
    );

-- Messages are append-only; only service role (backend) may mutate or delete
DROP POLICY IF EXISTS lfm_update ON lost_and_found_messages;
CREATE POLICY lfm_update ON lost_and_found_messages
    FOR UPDATE USING (false);

DROP POLICY IF EXISTS lfm_delete ON lost_and_found_messages;
CREATE POLICY lfm_delete ON lost_and_found_messages
    FOR DELETE USING (false);

NOTIFY pgrst, 'reload schema';
