-- 375_service_area_tax_history.sql
-- ACTION_ITEMS.md A29 (final open sub-item): dedicated append-only audit
-- table for service-area tax-rate changes, requested explicitly by the user
-- to override the item's prior "low priority, audit_logs already covers
-- this" deferral.
--
-- Context: `admin_update_service_area` (routes/admin/service_areas.py) has
-- required a written `tax_justification` and written a generic
-- `tax_config_updated` audit_logs row for every GST/PST/HST change since
-- the A29 fix (2026-08-12). That is preserved unchanged by this migration.
-- What's missing is a queryable, append-only, tax-specific history the way
-- `driver_insurance_periods` (migration 64) exists for insurance periods —
-- `audit_logs.details` is an unstructured JSON blob shared with every other
-- admin action type, which makes "show me every tax-rate change for area X
-- over time, with before/after values" an ad-hoc JSON query today instead
-- of a straightforward SELECT. If SK/CRA ever audits "when exactly did
-- Spinr start collecting PST in area X, at what rate, on whose authority,"
-- this table is the direct source of truth.
--
-- Append-only contract (stricter than driver_insurance_periods, which
-- allows one specific UPDATE to close a period): this table has no "open"
-- row concept at all — every row is a complete, immutable record of one
-- change. UPDATE and DELETE are both blocked unconditionally.
--
-- Rollback plan:
--   DROP TRIGGER IF EXISTS service_area_tax_history_no_mutate ON service_area_tax_history;
--   DROP FUNCTION IF EXISTS _service_area_tax_history_immutable();
--   DROP TABLE IF EXISTS service_area_tax_history;
--
-- Forward-compatible: new table, no changes to existing tables/columns.
-- Retention: no purge job — same 7-year regulatory retention rationale as
-- driver_insurance_periods (Saskatchewan Transportation Act tax/financial
-- record window, see CLAUDE.md's Saskatchewan Regulatory section). If a
-- retention purge is ever added for this table it must NOT run before that
-- window, and per CLAUDE.md's append-only rule it should archive+delete
-- rather than mutate.

CREATE TABLE IF NOT EXISTS service_area_tax_history (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    service_area_id   text        NOT NULL REFERENCES service_areas(id),

    old_gst_enabled   boolean,
    old_gst_rate      numeric,
    old_pst_enabled   boolean,
    old_pst_rate      numeric,
    old_hst_enabled   boolean,
    old_hst_rate      numeric,

    new_gst_enabled   boolean,
    new_gst_rate      numeric,
    new_pst_enabled   boolean,
    new_pst_rate      numeric,
    new_hst_enabled   boolean,
    new_hst_rate      numeric,

    changed_by        text        NOT NULL REFERENCES users(id),
    changed_by_role   text,
    justification     text        NOT NULL,

    -- The audit_logs row this history row corresponds to, for cross-referencing
    -- the two without duplicating the full actor/request-id context here.
    audit_log_id      uuid,

    changed_at        timestamptz NOT NULL DEFAULT now(),
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Query pattern: "every tax change for this area, newest first" — the
-- regulatory-audit / admin-history read this table exists for.
CREATE INDEX IF NOT EXISTS idx_service_area_tax_history_area_changed
    ON service_area_tax_history (service_area_id, changed_at DESC);

-- Query pattern: "everything a given admin changed" — investigation support.
CREATE INDEX IF NOT EXISTS idx_service_area_tax_history_changed_by
    ON service_area_tax_history (changed_by, changed_at DESC);

ALTER TABLE service_area_tax_history ENABLE ROW LEVEL SECURITY;

-- Writes are backend-only (service role bypasses RLS by design) — no
-- INSERT/UPDATE/DELETE policy is granted to authenticated/anon clients.
-- SELECT is admin-only; riders/drivers never see this table.
CREATE POLICY service_area_tax_history_select ON service_area_tax_history
    FOR SELECT USING (
        (SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin', 'super_admin')
    );

-- Tamper-evidence trigger: no UPDATE, no DELETE, ever. Every row is a
-- finished, immutable record from the moment it's inserted.
CREATE OR REPLACE FUNCTION _service_area_tax_history_immutable()
RETURNS trigger LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS
$$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'service_area_tax_history rows are append-only and cannot be deleted';
    END IF;

    RAISE EXCEPTION
        'service_area_tax_history rows are append-only and cannot be updated (row %)', OLD.id;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'service_area_tax_history_no_mutate'
          AND tgrelid = 'service_area_tax_history'::regclass
    ) THEN
        CREATE TRIGGER service_area_tax_history_no_mutate
            BEFORE UPDATE OR DELETE ON service_area_tax_history
            FOR EACH ROW EXECUTE FUNCTION _service_area_tax_history_immutable();
    END IF;
END;
$$;

COMMENT ON TABLE service_area_tax_history IS
    'Append-only audit of service_areas GST/PST/HST rate and enabled-flag '
    'changes: old value, new value, changed_by admin, justification. '
    'Complements (does not replace) the tax_config_updated audit_logs row '
    'written by the same admin_update_service_area call. UPDATE/DELETE '
    'blocked by trigger. Created in migration 375 (ACTION_ITEMS.md A29).';
