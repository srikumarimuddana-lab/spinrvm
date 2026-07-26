-- 257_stripe_id_unique_indexes.sql
-- Purpose: DB-level backstop for Stripe identity mappings. The legacy Stripe
--          mapping import (and any future writer) must never leave two driver
--          rows pointing at one Connect account (payouts landing in another
--          driver's bank) or two users sharing one Stripe customer. The
--          importer's NULL-only guard protects the target ROW; these partial
--          unique indexes protect the target VALUE against overlapping
--          commits — the race application-level checks cannot close.
-- Rollback:
--   DROP INDEX IF EXISTS drivers_stripe_account_id_unique;
--   DROP INDEX IF EXISTS users_stripe_customer_id_unique;
--
-- Notes:
-- - Same pattern as corporate_accounts_stripe_customer_unique (migration 27).
-- - Partial (IS NOT NULL) so unmapped rows are unconstrained.
-- - If this migration fails with a duplicate-key error, production data
--   already contains a double-mapping — STOP and resolve it manually before
--   re-running; do not drop the WHERE clause or the uniqueness.

CREATE UNIQUE INDEX IF NOT EXISTS drivers_stripe_account_id_unique
    ON public.drivers (stripe_account_id)
    WHERE stripe_account_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS users_stripe_customer_id_unique
    ON public.users (stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;
