-- 168_users_updated_at_trigger.sql
--
-- Purpose:
--   migration 167 added users.updated_at but it was nullable with no default
--   and is only written by the admin endpoints — so legacy rows are NULL and
--   non-admin writes (profile/phone updates in routes/users.py) never touch it,
--   making `updated_at` an unreliable freshness signal (migration-reviewer W2).
--
--   This makes updated_at universal:
--     1. DEFAULT now() so every new row gets a value.
--     2. A BEFORE UPDATE trigger sets updated_at = now() on every row change,
--        regardless of which code path made it.
--     3. Backfill existing NULLs to created_at.
--
--   No CONCURRENTLY here, so the whole file runs in one transaction (atomic).
--   The backfill is a single UPDATE over NULL rows only; at Spinr's user scale
--   this is fast. If the users table is ever very large, run the backfill
--   out-of-band in batches before applying the rest.
--
-- Rollback:
--   DROP TRIGGER IF EXISTS trg_users_set_updated_at ON users;
--   DROP FUNCTION IF EXISTS public.set_users_updated_at();
--   ALTER TABLE users ALTER COLUMN updated_at DROP DEFAULT;

ALTER TABLE users ALTER COLUMN updated_at SET DEFAULT now();

UPDATE users SET updated_at = COALESCE(created_at, now()) WHERE updated_at IS NULL;

CREATE OR REPLACE FUNCTION public.set_users_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_users_set_updated_at ON users;
CREATE TRIGGER trg_users_set_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION public.set_users_updated_at();

COMMENT ON FUNCTION public.set_users_updated_at() IS
    'BEFORE UPDATE trigger fn: stamps users.updated_at = now() on every row change.';
