-- Migration 35: Back-fill refresh_tokens columns from migration 08 → 25 gap
--
-- Migration 08 created refresh_tokens with a `revoked BOOLEAN` column.
-- Migration 25 used CREATE TABLE IF NOT EXISTS (silently a no-op when the
-- table already exists) and then tried to CREATE INDEX on `revoked_at` —
-- which does not exist on a table created by migration 08 alone. This
-- migration adds all missing columns idempotently and recreates the indices.

ALTER TABLE refresh_tokens
    ADD COLUMN IF NOT EXISTS revoked_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS audience      TEXT NOT NULL DEFAULT 'rider',
    ADD COLUMN IF NOT EXISTS issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS replaced_by   UUID REFERENCES refresh_tokens(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS user_agent    TEXT,
    ADD COLUMN IF NOT EXISTS ip            TEXT;

-- Back-fill revoked_at from old boolean flag for rows revoked before this migration.
UPDATE refresh_tokens
SET    revoked_at = COALESCE(created_at, now())
WHERE  revoked = true
  AND  revoked_at IS NULL;

-- Recreate indices that reference revoked_at (idempotent).
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user
    ON refresh_tokens (user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires
    ON refresh_tokens (expires_at)
    WHERE revoked_at IS NULL;
