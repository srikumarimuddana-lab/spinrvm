-- ============================================================
-- Migration 213: corporate_accounts.kyb_reviewed_by → TEXT
--
-- Bug: KYB review (POST /api/admin/corporate-accounts/{id}/kyb-review)
-- failed with Postgres 22P02 "invalid input syntax for type uuid:
-- 'admin-001'". The reviewer is a PLATFORM admin whose id is a string
-- (e.g. 'admin-001'), not a users.id UUID — platform-admin identity lives
-- in the JWT, not the users table (see CLAUDE.md "JWT trust model").
-- Migration 27 declared kyb_reviewed_by as UUID, so record_kyb_decision's
-- write of the admin string was rejected → 503 on approve/reject.
--
-- Fix: widen kyb_reviewed_by to TEXT, matching every other admin-attribution
-- column in the schema (disputes.resolved_by TEXT, safety_incidents.resolved_by
-- TEXT, service_area_assigned_by TEXT "-- admin id", rides.cancelled_by TEXT,
-- users.status_changed_by TEXT).
--
-- Safe under live traffic: kyb_reviewed_by has only ever held NULL (no review
-- ever succeeded — the write always errored), so the USING cast touches no rows.
--
-- Rollback: (only if no non-UUID id was written)
--   ALTER TABLE corporate_accounts
--       ALTER COLUMN kyb_reviewed_by TYPE UUID USING kyb_reviewed_by::uuid;
-- ============================================================

ALTER TABLE corporate_accounts
    ALTER COLUMN kyb_reviewed_by TYPE TEXT USING kyb_reviewed_by::text;
