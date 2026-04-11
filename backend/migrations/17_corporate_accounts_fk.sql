-- ============================================================
-- Corporate Account Foreign Key Constraints
-- Run this in the Supabase SQL Editor (Settings → SQL Editor)
-- ============================================================
--
-- Adds the FK constraints linking `users.corporate_account_id` and
-- `rides.corporate_account_id` to `corporate_accounts.id`. These
-- constraints were previously inlined in migration 03, but the
-- referenced `corporate_accounts` table is created by migration 05,
-- so the FK has to be added afterwards.
--
-- This migration must run AFTER:
--   - migrations/03_corporate_accounts_heatmap.sql (adds the columns)
--   - migrations/05_corporate_accounts.sql (creates the table)
--
-- DO $$ guards ensure idempotency: adding an FK with the same name
-- twice would raise an error.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'users_corporate_account_id_fkey'
  ) THEN
    ALTER TABLE users
      ADD CONSTRAINT users_corporate_account_id_fkey
      FOREIGN KEY (corporate_account_id)
      REFERENCES corporate_accounts(id)
      ON DELETE SET NULL;
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'rides_corporate_account_id_fkey'
  ) THEN
    ALTER TABLE rides
      ADD CONSTRAINT rides_corporate_account_id_fkey
      FOREIGN KEY (corporate_account_id)
      REFERENCES corporate_accounts(id)
      ON DELETE SET NULL;
  END IF;
END
$$;

-- ============================================================
-- Admin-access RLS policy for corporate_accounts
-- ============================================================
-- Migration 05 creates the table but doesn't define policies.
-- This was previously in migration 03; re-adding it here so RLS
-- enforcement lives next to the canonical definition.

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
  END IF;
END
$$;
