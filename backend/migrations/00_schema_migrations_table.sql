-- Migration tracking table (OPS-002)
-- Run this FIRST before any other migrations.
-- Idempotent — safe to run multiple times.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT        PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE schema_migrations IS
    'Tracks which migration files have been applied. Managed by backend/scripts/migrate.py.';
