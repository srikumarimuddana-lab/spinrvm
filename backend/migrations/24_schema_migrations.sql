-- Migration 24: schema_migrations tracking table
--
-- Context: production-readiness audit 2026-04, finding P0-B4.
-- Until now the backend shipped a directory of .sql files with no
-- tracking table, no up/down scripts, and duplicate ordering prefixes
-- (`10_disputes_table.sql` and `10_service_area_driver_matching.sql`
-- both started with "10_", so filesystem `ls` order was
-- non-deterministic across platforms). The duplicate was resolved by
-- renaming the second to `10b_service_area_driver_matching.sql` so
-- lexicographic sort is now total.
--
-- This migration introduces the provenance table that the new
-- `backend/scripts/run_migrations.py` runner uses to decide what to
-- apply. Each row records one migration filename with the checksum of
-- the file at the time it was applied, so a later edit to an already-
-- applied migration is detected and rejected (migrations are
-- append-only; never edit an applied file).
--
-- Full Alembic adoption is tracked as the P1 follow-up to this P0 fix
-- (audit 09_ROADMAP_CHECKLIST.md item 0.6). This table gives us
-- deterministic, observable migration state today without introducing
-- a new framework.

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename      TEXT PRIMARY KEY,
    checksum      TEXT NOT NULL,
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by    TEXT NOT NULL DEFAULT current_user
);

CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
    ON schema_migrations (applied_at DESC);

-- Defence-in-depth: the backend uses the service role which bypasses
-- RLS, but any other role that gets handed the DB URL should not be
-- allowed to read or tamper with migration history.
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
