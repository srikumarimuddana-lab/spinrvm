-- ============================================================
-- Cloud Messaging Migration
-- Run this in the Supabase SQL Editor (Settings → SQL Editor)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. CLOUD_MESSAGES table
-- ============================================================
CREATE TABLE IF NOT EXISTS cloud_messages (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL,
    audience          TEXT NOT NULL DEFAULT 'customers',
    channel           TEXT NOT NULL DEFAULT 'push',
    particular_id     TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    scheduled_at      TIMESTAMPTZ,
    sent_at           TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_recipients  INTEGER NOT NULL DEFAULT 0,
    successful        INTEGER NOT NULL DEFAULT 0,
    failed_count      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cloud_messages_status ON cloud_messages (status);
CREATE INDEX IF NOT EXISTS idx_cloud_messages_audience ON cloud_messages (audience);
CREATE INDEX IF NOT EXISTS idx_cloud_messages_scheduled ON cloud_messages (scheduled_at)
    WHERE status = 'scheduled';
CREATE INDEX IF NOT EXISTS idx_cloud_messages_created ON cloud_messages (created_at DESC);

-- ============================================================
-- 2. AUDIT_LOGS table (previously referenced but not created)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id                TEXT PRIMARY KEY,
    action            TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    entity_id         TEXT NOT NULL,
    user_email        TEXT,
    details           TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs (entity_type, entity_id);

-- ============================================================
-- 3. PUSH_TOKENS table (previously referenced but not created)
-- ============================================================
CREATE TABLE IF NOT EXISTS push_tokens (
    id                TEXT PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id           TEXT NOT NULL,
    token             TEXT NOT NULL,
    platform          TEXT DEFAULT 'unknown',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_push_tokens_user_platform ON push_tokens (user_id, platform);
CREATE INDEX IF NOT EXISTS idx_push_tokens_token ON push_tokens (token);

-- ============================================================
-- 4. Enable RLS
-- ============================================================
ALTER TABLE cloud_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE push_tokens ENABLE ROW LEVEL SECURITY;

-- Admin full access policies
CREATE POLICY "Admin full access cloud_messages"
ON cloud_messages FOR ALL
TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM users
    WHERE users.id = auth.uid()::text
    AND users.role IN ('admin', 'super_admin')
  )
);

CREATE POLICY "Admin full access audit_logs"
ON audit_logs FOR ALL
TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM users
    WHERE users.id = auth.uid()::text
    AND users.role IN ('admin', 'super_admin')
  )
);

CREATE POLICY "Users manage own push tokens"
ON push_tokens FOR ALL
TO authenticated
USING (user_id = auth.uid()::text);

CREATE POLICY "Admin read push_tokens"
ON push_tokens FOR SELECT
TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM users
    WHERE users.id = auth.uid()::text
    AND users.role IN ('admin', 'super_admin')
  )
);

-- ============================================================
-- 5. Service role bypass (for backend API calls)
-- ============================================================
CREATE POLICY "Service role bypass cloud_messages"
ON cloud_messages FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "Service role bypass audit_logs"
ON audit_logs FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "Service role bypass push_tokens"
ON push_tokens FOR ALL TO service_role USING (true) WITH CHECK (true);
