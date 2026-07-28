-- 263_compliance_export_events.sql
-- Audit log for admin-generated compliance/regulatory reports (GST/PST
-- remittance summaries, insurance-period audit exports, DSAR lookups).
--
-- Purpose: every export of regulated financial or insurance data must be
-- attributable to an admin, timestamped, and immutable — the log itself is
-- evidence for a future SGI or CRA audit, not just an app convenience.
--
-- Append-only contract:
--   * INSERT allowed (service role only).
--   * UPDATE and DELETE blocked unconditionally.
--
-- Rollback:
--   DROP TABLE IF EXISTS compliance_export_events;
--
-- Forward-compatible: new table, no changes to existing tables.
-- Retention: 7 years (matches the underlying financial/regulatory records
-- this log describes access to).

CREATE TABLE IF NOT EXISTS compliance_export_events (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id text        NOT NULL REFERENCES users(id),
    -- 'gst_pst_remittance' | 'insurance_period_audit' | 'dsar_lookup'
    report_type   text        NOT NULL,
    -- Non-PII query params only (date range, driver/rider id if scoped) —
    -- never store raw GPS, full names, or addresses here.
    params        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    row_count     integer,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Query pattern: "all exports of a given report type, newest first" and
-- "all exports by a given admin" — both used by the compliance audit view.
CREATE INDEX IF NOT EXISTS compliance_export_events_type_created
    ON compliance_export_events (report_type, created_at DESC);

CREATE INDEX IF NOT EXISTS compliance_export_events_admin
    ON compliance_export_events (admin_user_id, created_at DESC);

ALTER TABLE compliance_export_events ENABLE ROW LEVEL SECURITY;

-- Writes are backend-only (service role bypasses RLS) — no INSERT policy
-- is granted to authenticated/anon clients.
-- SELECT is restricted to admins/super_admins; this log itself is
-- regulator-facing audit evidence, not something ops staff below admin
-- should read.
CREATE POLICY compliance_export_events_select ON compliance_export_events
    FOR SELECT USING (
        (SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin', 'super_admin')
    );

-- No INSERT, UPDATE, or DELETE policies → denied for authenticated/anon by
-- default. Only the service role can write.

-- Tamper-evidence trigger: this table is fully immutable once written.
CREATE OR REPLACE FUNCTION _compliance_export_events_immutable()
RETURNS trigger LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS
$$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'compliance_export_events rows are append-only and cannot be deleted';
    END IF;

    RAISE EXCEPTION
        'compliance_export_events rows are append-only and cannot be updated';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'compliance_export_events_no_mutate'
          AND tgrelid = 'compliance_export_events'::regclass
    ) THEN
        CREATE TRIGGER compliance_export_events_no_mutate
            BEFORE UPDATE OR DELETE ON compliance_export_events
            FOR EACH ROW EXECUTE FUNCTION _compliance_export_events_immutable();
    END IF;
END;
$$;

COMMENT ON TABLE compliance_export_events IS
    'Append-only audit log of admin-generated compliance/regulatory report '
    'exports (GST/PST remittance, insurance-period audit, DSAR lookup). '
    'INSERT only; UPDATE/DELETE blocked. 7-year retention. Created in '
    'migration 263.';
