-- ============================================================
-- Corporate Account Foreign Key Constraints (safe on any DB state)
-- Run this in the Supabase SQL Editor (Settings → SQL Editor)
-- ============================================================
--
-- Adds the FK constraints linking `users.corporate_account_id` and
-- `rides.corporate_account_id` to `corporate_accounts.id`. These
-- constraints were previously inlined in migration 03, but the
-- referenced `corporate_accounts` table is created by migration 05,
-- so the FK has to be added afterwards.
--
-- This migration is SAFE TO RE-RUN on a database in any state:
--
--   * It never creates, drops, or renames tables.
--   * Every write is guarded by a pg_constraint / pg_policies
--     existence check so re-running is a no-op.
--   * The FK additions only fire when the column data types of
--     users.corporate_account_id / rides.corporate_account_id /
--     corporate_accounts.id actually match. If an older deploy
--     has TEXT columns (from the original migration 03 shape),
--     the migration logs a NOTICE and skips the ALTER rather than
--     failing with a type-mismatch error. In that case you need
--     to reconcile the column types manually — see the
--     "Known drift" section in READINESS_REPORT.md § A.
--
-- Pre-requisites (verify before running):
--
--   1. backend/migrations/05_corporate_accounts.sql has been run
--      (table exists with the current shape).
--   2. backend/migrations/03_corporate_accounts_heatmap.sql has been
--      run at its current (post-2026-04-12) version — not the
--      original shape that included a conflicting CREATE TABLE.

-- ─────────────────────────────────────────────────────────────
-- 1. Type-guard helper: returns true iff the column `<table>.<col>`
--    resolves to the same PostgreSQL data type as
--    `corporate_accounts.id`. Pure query, no writes.
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION pg_temp._fk_types_match(tbl text, col text)
RETURNS boolean LANGUAGE plpgsql AS $fn$
DECLARE
    col_type  text;
    pk_type   text;
BEGIN
    SELECT data_type INTO col_type
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = tbl AND column_name = col;

    SELECT data_type INTO pk_type
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'corporate_accounts' AND column_name = 'id';

    IF col_type IS NULL OR pk_type IS NULL THEN
        RETURN false;
    END IF;

    RETURN col_type = pk_type;
END
$fn$;

-- ─────────────────────────────────────────────────────────────
-- 2. users.corporate_account_id → corporate_accounts.id
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_corporate_account_id_fkey') THEN
        RAISE NOTICE 'Constraint users_corporate_account_id_fkey already exists — skipping';
    ELSIF NOT pg_temp._fk_types_match('users', 'corporate_account_id') THEN
        RAISE NOTICE
            'Skipping users_corporate_account_id_fkey: column type mismatch between '
            'users.corporate_account_id and corporate_accounts.id. Reconcile the '
            'column types manually before re-running this migration.';
    ELSE
        ALTER TABLE users
            ADD CONSTRAINT users_corporate_account_id_fkey
            FOREIGN KEY (corporate_account_id)
            REFERENCES corporate_accounts(id)
            ON DELETE SET NULL;
        RAISE NOTICE 'Added constraint users_corporate_account_id_fkey';
    END IF;
END
$$;

-- ─────────────────────────────────────────────────────────────
-- 3. rides.corporate_account_id → corporate_accounts.id
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rides_corporate_account_id_fkey') THEN
        RAISE NOTICE 'Constraint rides_corporate_account_id_fkey already exists — skipping';
    ELSIF NOT pg_temp._fk_types_match('rides', 'corporate_account_id') THEN
        RAISE NOTICE
            'Skipping rides_corporate_account_id_fkey: column type mismatch between '
            'rides.corporate_account_id and corporate_accounts.id. Reconcile the '
            'column types manually before re-running this migration.';
    ELSE
        ALTER TABLE rides
            ADD CONSTRAINT rides_corporate_account_id_fkey
            FOREIGN KEY (corporate_account_id)
            REFERENCES corporate_accounts(id)
            ON DELETE SET NULL;
        RAISE NOTICE 'Added constraint rides_corporate_account_id_fkey';
    END IF;
END
$$;

-- ─────────────────────────────────────────────────────────────
-- 4. RLS on corporate_accounts + admin-access policy
-- ─────────────────────────────────────────────────────────────
-- `ENABLE ROW LEVEL SECURITY` is a no-op when RLS is already enabled,
-- and the policy creation is guarded by a pg_policies existence check.
ALTER TABLE corporate_accounts ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'corporate_accounts'
          AND policyname = 'Admin full access corporate_accounts'
    ) THEN
        CREATE POLICY "Admin full access corporate_accounts"
        ON corporate_accounts FOR ALL
        TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM users
                WHERE users.id = auth.uid()::text
                  AND users.role = 'admin'
            )
        );
        RAISE NOTICE 'Added RLS policy "Admin full access corporate_accounts"';
    END IF;
END
$$;
