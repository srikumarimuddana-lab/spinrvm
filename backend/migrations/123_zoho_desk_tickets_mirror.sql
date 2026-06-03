-- 123_zoho_desk_tickets_mirror.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS zoho_desk_tickets;
--
-- Purpose: local mirror of Zoho Desk tickets so the admin Help Desk can serve
-- lists, dashboards, and trends from our own Postgres instead of hitting the
-- Zoho REST API on every page view (Zoho enforces a per-day API credit limit).
-- A background sync loop upserts changed tickets incrementally; reads come from
-- here. Writes (reply/assign/status/create) still go live to Zoho, then the
-- next sync reflects them.
--
-- Backend-only table (RLS enabled, no policies -> anon/authenticated denied;
-- the service-role key bypasses RLS). Never exposed to the frontend directly.
--
-- Forward-compatible & idempotent; safe against live traffic.

CREATE TABLE IF NOT EXISTS zoho_desk_tickets (
    zoho_id         TEXT        PRIMARY KEY,            -- Zoho ticket id
    ticket_number   TEXT,
    subject         TEXT,
    status          TEXT,
    status_type     TEXT,                                -- Open / Closed meta-status
    priority        TEXT,
    channel         TEXT,
    category        TEXT,
    classification  TEXT,
    department_id   TEXT,
    assignee_id     TEXT,
    assignee_name   TEXT,
    contact_email   TEXT,
    contact_name    TEXT,
    tags            JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_time    TIMESTAMPTZ,
    modified_time   TIMESTAMPTZ,
    closed_time     TIMESTAMPTZ,
    due_date        TIMESTAMPTZ,
    web_url         TEXT,
    raw             JSONB,                               -- full Zoho payload
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index the columns the dashboards/lists/trends filter & sort on.
CREATE INDEX IF NOT EXISTS idx_zdt_created      ON zoho_desk_tickets (created_time DESC);
CREATE INDEX IF NOT EXISTS idx_zdt_modified     ON zoho_desk_tickets (modified_time DESC);
CREATE INDEX IF NOT EXISTS idx_zdt_status       ON zoho_desk_tickets (status);
CREATE INDEX IF NOT EXISTS idx_zdt_assignee     ON zoho_desk_tickets (assignee_id);
CREATE INDEX IF NOT EXISTS idx_zdt_department   ON zoho_desk_tickets (department_id);

ALTER TABLE zoho_desk_tickets ENABLE ROW LEVEL SECURITY;

-- Sync cursor lives on the existing single-row config table.
ALTER TABLE zoho_desk_config ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
ALTER TABLE zoho_desk_config ADD COLUMN IF NOT EXISTS last_sync_count INTEGER;

NOTIFY pgrst, 'reload schema';
