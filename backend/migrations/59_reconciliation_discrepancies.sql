-- 59_reconciliation_discrepancies.sql
-- 20-2 companion: stores daily Stripe ↔ DB reconciliation results so the
-- finance team can investigate and resolve open discrepancies via the admin
-- dashboard.
--
-- Rollback plan:
--   DROP TABLE IF EXISTS reconciliation_discrepancies;
--
-- Forward-compatible: new table, no changes to existing tables.

CREATE TABLE IF NOT EXISTS reconciliation_discrepancies (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    date                date        NOT NULL,
    stripe_total_cents  bigint      NOT NULL,
    db_total_cents      bigint      NOT NULL,
    discrepancy_cents   bigint      NOT NULL,
    -- 'open' → finance has not reviewed; 'resolved' → root cause documented
    status              text        NOT NULL DEFAULT 'open'
                                    CHECK (status IN ('open', 'resolved', 'dismissed')),
    resolution_notes    text,
    resolved_by         uuid        REFERENCES users(id),
    resolved_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- One row per calendar date (the cron only fires once per day via Redis lock).
CREATE UNIQUE INDEX IF NOT EXISTS reconciliation_discrepancies_date
    ON reconciliation_discrepancies (date);

-- Query pattern: "show all open discrepancies"
CREATE INDEX IF NOT EXISTS reconciliation_discrepancies_status
    ON reconciliation_discrepancies (status, date DESC)
    WHERE status = 'open';

-- Admins only — no rider/driver should see reconciliation data.
ALTER TABLE reconciliation_discrepancies ENABLE ROW LEVEL SECURITY;

CREATE POLICY recon_admin_only ON reconciliation_discrepancies
    FOR ALL USING (
        (SELECT role FROM users WHERE id = auth.uid()) = 'admin'
    );

COMMENT ON TABLE reconciliation_discrepancies IS
    'Daily Stripe ↔ financial_events reconciliation results. '
    'Populated by the reconciliation_loop background task (migration 59). '
    'Required by PCI-DSS, SOC2 CC9.1, and CRA record-keeping.';
