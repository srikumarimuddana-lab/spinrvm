-- ============================================================
-- Disputes table
-- Run this in the Supabase SQL Editor
-- ============================================================

CREATE TABLE IF NOT EXISTS disputes (
    id                  TEXT PRIMARY KEY,
    ride_id             TEXT,
    user_id             TEXT,
    user_name           TEXT DEFAULT '',
    user_type           TEXT DEFAULT 'rider',
    reason              TEXT DEFAULT 'other',
    description         TEXT DEFAULT '',
    status              TEXT DEFAULT 'pending',
    refund_amount       NUMERIC(8,2) DEFAULT 0,
    resolution_status   TEXT,
    resolution_notes    TEXT,
    resolved_at         TIMESTAMPTZ,
    resolved_by         TEXT,
    admin_note          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_disputes_status ON disputes(status);
CREATE INDEX IF NOT EXISTS idx_disputes_created ON disputes(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_disputes_ride ON disputes(ride_id);

-- RLS
ALTER TABLE disputes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role bypass disputes"
ON disputes FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "Admin full access disputes"
ON disputes FOR ALL TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM users
    WHERE users.id = auth.uid()::text
    AND users.role IN ('admin', 'super_admin')
  )
);
