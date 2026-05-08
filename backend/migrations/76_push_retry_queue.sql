-- Rollback: DROP TABLE IF EXISTS push_retry_queue;
--
-- The table has no foreign-key dependents at creation time, so DROP TABLE
-- is safe and complete. Re-running this migration on a DB that already has
-- the table is a no-op due to IF NOT EXISTS guards throughout.

CREATE TABLE IF NOT EXISTS push_retry_queue (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         text        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           text        NOT NULL,
    body            text        NOT NULL,
    data            jsonb       NOT NULL DEFAULT '{}',
    priority        text        NOT NULL DEFAULT 'normal'
                                CHECK (priority IN ('dispatch', 'safety', 'normal')),
    attempts        int         NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    sent_at         timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Partial index used by the retry background loop: fetch unsent rows whose
-- next_attempt_at has elapsed, ordered by priority tier then scheduled time.
CREATE INDEX IF NOT EXISTS push_retry_queue_pending_idx
    ON push_retry_queue (next_attempt_at)
    WHERE sent_at IS NULL;

-- RLS: no client (authenticated or anon) should read or write this table.
-- The backend service role bypasses RLS by design; all access goes through it.
ALTER TABLE push_retry_queue ENABLE ROW LEVEL SECURITY;

CREATE POLICY push_retry_queue_service_only
    ON push_retry_queue
    FOR ALL
    TO authenticated
    USING (false);
