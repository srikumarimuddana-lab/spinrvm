-- Migration 300: document the new driver_statements.status value.
-- (Originally authored as 299; renamed to 300 to resolve a same-number
-- collision with 299_rider_email_verification_otp.sql, which merged to
-- main first, per backend/migrations/CLAUDE.md's "second one renames"
-- convention.)
--
-- Rollback:
--   COMMENT ON COLUMN public.driver_statements.status IS
--       'claimed | sent | failed | skipped_no_email | skipped_inactive (terminal after the claiming tick; never auto-retried).';
--
-- ACTION_ITEMS.md N9: utils/driver_statement_job.py now honours
-- notification_preferences.earnings_summary — a driver who opted out gets a
-- row written (so the period converges and is never rescanned for them,
-- same as skipped_no_email/skipped_inactive) but no email. No schema change:
-- `status` has always been an unconstrained TEXT column (no CHECK), so the
-- new value needs no ALTER — this migration only keeps the column comment,
-- the single source of truth other engineers read for the status lifecycle,
-- in sync with the code. Comment-only migrations are still numbered and
-- applied through the normal runner so the idempotency-key / append-only
-- rules stay uniform.

COMMENT ON COLUMN public.driver_statements.status IS
    'claimed | sent | failed | skipped_no_email | skipped_inactive | skipped_opted_out (terminal after the claiming tick; never auto-retried).';
