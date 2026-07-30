-- Migration 273: driver_statements.driver_id → ON DELETE CASCADE
--
-- Rollback:
--   ALTER TABLE public.driver_statements
--       DROP CONSTRAINT IF EXISTS driver_statements_driver_id_fkey;
--   ALTER TABLE public.driver_statements
--       ADD CONSTRAINT driver_statements_driver_id_fkey
--       FOREIGN KEY (driver_id) REFERENCES public.drivers(id);
--
-- Migration 272 created driver_statements with a plain (NO ACTION) FK to
-- drivers. That silently breaks the PIPEDA right-to-delete purge:
--
--   backend/migrations/216_deletion_hard_delete_no_anonymize.sql Step H runs
--   `DELETE FROM drivers WHERE user_id = v_uid` for DSAR-deleted accounts whose
--   regulatory window has elapsed. Its eligibility check only excludes drivers
--   that still have driver_insurance_periods / payouts / bank_accounts rows —
--   it has no knowledge of driver_statements. A driver carrying a statement row
--   therefore hits foreign_key_violation, which Step H's EXCEPTION handler
--   catches and skips (logged, never aborting the purge). The account is then
--   permanently un-purgeable: retried and skipped on every future run.
--
-- This is reachable for essentially EVERY driver, because the statement job
-- writes a row per driver per period even when there was no activity
-- (status='skipped_inactive', see utils/driver_statement_job.py) — including
-- drivers who signed up, never drove, and later requested deletion. Those are
-- exactly the accounts with no payouts/insurance rows that Step H is meant to
-- be able to purge.
--
-- Statements are DERIVED data (regenerated on demand from rides/payouts; the
-- PDF is never stored), so cascading them with the driver loses nothing that
-- must be retained — the underlying trip/payout records keep their own
-- 7-year retention independently. This matches the precedent set by
-- 139_driver_onboarding_reminder_log.sql, the other "log of things we sent a
-- driver" table, which uses ON DELETE CASCADE. It deliberately does NOT match
-- 64_driver_insurance_periods.sql, whose plain FK is intentional (append-only
-- regulatory evidence that Step H explicitly checks for and refuses to purge).
--
-- Written as DROP + ADD (rather than editing 272) so it converges whether or
-- not 272 has already been applied to a given environment — a re-run of 272's
-- CREATE TABLE IF NOT EXISTS would no-op and silently leave the wrong FK in
-- place.

DO $$
DECLARE
    v_constraint_name text;
BEGIN
    -- Resolve the FK by column rather than assuming Postgres' default name,
    -- so this works even if 272 was applied under a different naming scheme.
    SELECT con.conname INTO v_constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE nsp.nspname = 'public'
      AND rel.relname = 'driver_statements'
      AND con.contype = 'f'
      AND con.conkey = ARRAY[
          (SELECT attnum FROM pg_attribute
           WHERE attrelid = rel.oid AND attname = 'driver_id')
      ]::smallint[]
    LIMIT 1;

    IF v_constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.driver_statements DROP CONSTRAINT %I',
            v_constraint_name
        );
    END IF;

    ALTER TABLE public.driver_statements
        ADD CONSTRAINT driver_statements_driver_id_fkey
        FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE CASCADE;
END $$;

COMMENT ON CONSTRAINT driver_statements_driver_id_fkey ON public.driver_statements IS
    'ON DELETE CASCADE: statements are derived data and must not block the PIPEDA hard-delete purge (migration 216 Step H).';
