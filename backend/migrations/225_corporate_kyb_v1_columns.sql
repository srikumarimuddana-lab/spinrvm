-- 225_corporate_kyb_v1_columns.sql
--
-- KYB v1 columns (self-serve onboarding, Phase 2) — fixes one latent crash
-- and adds the review-lifecycle metadata the portal/staff UIs need:
--
--   * kyb_review_note   — FIXES a latent bug: repositories/corporate_repo.py::
--     record_kyb_decision has always written this column when a reviewer
--     passed a note ("column added in a follow-up migration if desired"), but
--     no migration ever created it → any approve/reject WITH a note failed at
--     the DB layer. The column now exists; the note is also what the portal
--     shows a rejected company so they know what to fix.
--   * kyb_submitted_at  — when the company last submitted a document for
--     review (drives the portal's under_review state + staff queue ordering).
--   * kyb_last_decision — 'approved' | 'rejected' | NULL (never reviewed).
--     Rejection flips status to 'suspended' (existing lifecycle, unchanged);
--     this column lets the portal distinguish KYB-rejected (can resubmit)
--     from staff-suspended (cannot self-unsuspend) without a second state
--     machine. A future automated KYB provider records through the same
--     record_kyb_decision path.
--
-- Forward-compatible: nullable metadata-only ADD COLUMNs, guarded CHECK —
-- safe against in-flight traffic. No new table → RLS posture unchanged
-- (corporate_accounts RLS from migration 17).
--
-- Rollback (on paper):
--   ALTER TABLE corporate_accounts DROP CONSTRAINT IF EXISTS corporate_accounts_kyb_last_decision_check;
--   ALTER TABLE corporate_accounts
--     DROP COLUMN IF EXISTS kyb_review_note,
--     DROP COLUMN IF EXISTS kyb_submitted_at,
--     DROP COLUMN IF EXISTS kyb_last_decision;

ALTER TABLE corporate_accounts
    ADD COLUMN IF NOT EXISTS kyb_review_note   TEXT,
    ADD COLUMN IF NOT EXISTS kyb_submitted_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS kyb_last_decision TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'corporate_accounts_kyb_last_decision_check'
    ) THEN
        ALTER TABLE corporate_accounts ADD CONSTRAINT corporate_accounts_kyb_last_decision_check
            CHECK (kyb_last_decision IN ('approved', 'rejected') OR kyb_last_decision IS NULL);
    END IF;
END $$;
