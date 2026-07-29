-- 268_admin_export_approvals.sql
-- Shared dual-approval gate for large admin-panel data exports (AI-3,
-- docs/threat-model/admin-panel.md; ACTION_ITEMS.md B10/B11). Today any
-- single admin can generate a full-fidelity, unredacted PII export (Data
-- Transfer: up to 100 entities incl. GPS + documents; Compliance:
-- gst-pst-remittance / insurance-period-audit up to 10,000 rows) with no
-- second set of eyes. This table backs a shared request/approve/deny flow
-- so a route can gate on "has a *different* admin approved this" instead of
-- every module inventing its own one-off check.
--
-- One row per gated export attempt. `route_key` identifies which export
-- endpoint the request is for (e.g. 'compliance.gst_pst_remittance',
-- 'compliance.insurance_period_audit', 'data_transfer.export'); `params`
-- captures the exact query/body params so approval binds to the specific
-- request, not a standing grant. Single-use: `status` moves
-- pending -> approved|denied|expired and the route consumes it once.
--
-- Dark-launched behind settings.dual_approval_exports_enabled (default
-- false, same pattern as other feature flags in this table — see migration
-- 266) so this ships with zero behavior change until explicitly enabled
-- after staging verification, per CLAUDE.md's pre-merge release gates.
--
-- Rollback: NOT a plain git revert once wired — the gated routes read this
-- table. Sequence:
--   1. Set settings.dual_approval_exports_enabled = false (immediate kill
--      switch, no deploy needed — routes fall back to ungated behavior).
--   2. If a full revert is needed: remove the gate checks in
--      compliance.py / data_transfer_export.py, deploy, then
--      DROP TABLE IF EXISTS admin_export_approval_requests;
--      ALTER TABLE settings DROP COLUMN IF EXISTS dual_approval_exports_enabled;

BEGIN;

CREATE TABLE IF NOT EXISTS admin_export_approval_requests (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- public.users.id is TEXT (see migration 29) — admins are users too.
    -- RESTRICT, not SET NULL: NOT NULL + SET NULL would conflict (deleting
    -- the referenced user would try to null this column and violate NOT
    -- NULL, erroring the delete). A request must always have a known
    -- requester — this table gates a security control, so an admin
    -- deletion should be blocked while their requests still exist rather
    -- than silently orphaning an approval record.
    requested_by      TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    route_key         TEXT NOT NULL,       -- e.g. 'data_transfer.export'
    params            JSONB NOT NULL,      -- exact request params this approval is bound to
    row_count         INTEGER,             -- estimated/actual row or entity count, for the approver's review
    reason            TEXT,                -- requester's stated justification, if the route collects one
    status            TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' | 'denied' | 'expired' | 'consumed'
    decided_by        TEXT REFERENCES users(id) ON DELETE SET NULL,
    decision_note     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at        TIMESTAMPTZ,
    consumed_at       TIMESTAMPTZ,         -- set when the approved export actually ran
    expires_at        TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '24 hours'),
    -- A requester can never be their own approver. Enforced again at the
    -- application layer (belt-and-suspenders — this is the property the
    -- whole feature exists for), but the constraint makes the invariant
    -- impossible to violate even by a future direct-SQL admin action.
    CONSTRAINT admin_export_approval_requests_no_self_approval
        CHECK (decided_by IS NULL OR decided_by <> requested_by),
    CONSTRAINT admin_export_approval_requests_status_check
        CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'consumed'))
);

-- Approval-queue UI: pending requests, oldest first.
CREATE INDEX IF NOT EXISTS idx_admin_export_approval_requests_pending
    ON admin_export_approval_requests (created_at)
    WHERE status = 'pending';

-- Gate check: "is there an approved, unconsumed, unexpired request for this
-- exact route_key + params" — the hot path every gated export route runs.
CREATE INDEX IF NOT EXISTS idx_admin_export_approval_requests_lookup
    ON admin_export_approval_requests (route_key, requested_by, status);

COMMENT ON TABLE admin_export_approval_requests IS
    'Shared dual-approval gate for large admin-panel PII exports (AI-3). '
    'A pending row must be approved by a DIFFERENT admin than the requester '
    'before the gated export route will generate the file. Backend/'
    'service-role only — never queried directly from the client.';

-- This table references admin identities and gates access to bulk PII
-- exports — backend/service-role only, same posture as
-- data_transfer_export_jobs (migration 262). No anon/authenticated policy
-- exists or should be added; RLS default-deny covers those roles.
ALTER TABLE admin_export_approval_requests ENABLE ROW LEVEL SECURITY;

CREATE POLICY admin_export_approval_requests_service_select
    ON admin_export_approval_requests FOR SELECT TO service_role USING (true);
CREATE POLICY admin_export_approval_requests_service_insert
    ON admin_export_approval_requests FOR INSERT TO service_role WITH CHECK (true);
CREATE POLICY admin_export_approval_requests_service_update
    ON admin_export_approval_requests FOR UPDATE TO service_role USING (true) WITH CHECK (true);
CREATE POLICY admin_export_approval_requests_service_delete
    ON admin_export_approval_requests FOR DELETE TO service_role USING (true);

-- Feature flag — same flat-column pattern as migration 266's Meta CAPI
-- config. Default false: ships with zero behavior change to any existing
-- export route until an admin explicitly enables it after staging
-- verification.
ALTER TABLE settings ADD COLUMN IF NOT EXISTS dual_approval_exports_enabled BOOLEAN NOT NULL DEFAULT false;

COMMIT;
