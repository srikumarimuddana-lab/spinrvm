-- 432: flag gating whether uncollected fares are excluded from driver-payable money.
--
-- Backs utils/payment_collection.payable_ride_filter, which is read by
-- /drivers/balance, the weekly auto_payout batch, and driver statements.
--
-- DEFAULT FALSE, and it must stay false until the pre-enable checklist below
-- is done. The filter itself is correct going forward — a completed ride whose
-- card charge failed should never fund a Stripe Transfer — but payable_balance
-- is a live recompute (total_earnings - total_payouts) with no floor, so
-- switching it on retroactively drives every driver already paid out for such
-- a ride negative, and their future collected fares then net silently against
-- that overpayment. See docs/change-log/2026-09-20-uncollected-rides-not-payable.md
-- section 11.1.
--
-- Before setting this true:
--   1. Run backend/scripts/reconcile_uncollected_ride_payouts.sql against a
--      read replica to size the affected cohort (per-driver overpaid_exposure).
--   2. Decide how that existing overpayment is handled (absorb vs. recover
--      per case) — a finance decision, not a query-filter side effect.
--   3. Decide whether to clamp the reported balance at 0 and surface the
--      shortfall as its own field.
--   4. Resolve the /balance vs /earnings divergence (ACTION_ITEMS.md A28):
--      with this flag ON the two report different totals for the same period.
--
-- Rollback (no redeploy; the settings cache expires within 60 seconds):
--   UPDATE public.settings
--      SET uncollected_rides_excluded_from_payable = false
--    WHERE id = 'app_settings';
-- Schema rollback, after retiring the code readers:
--   ALTER TABLE public.settings
--       DROP COLUMN IF EXISTS uncollected_rides_excluded_from_payable;
--
-- Existing table permissions/RLS remain unchanged. No new query or index.

SET lock_timeout = '5s';

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS uncollected_rides_excluded_from_payable BOOLEAN NOT NULL DEFAULT FALSE;

RESET lock_timeout;

COMMENT ON COLUMN public.settings.uncollected_rides_excluded_from_payable IS
    'When true, completed rides whose fare was never collected (failed card charge) '
    'are excluded from driver-payable money: /drivers/balance, the weekly auto_payout '
    'batch, and driver statements. Default false — enabling it retroactively drives '
    'already-paid-out drivers to a negative payable_balance. Run '
    'backend/scripts/reconcile_uncollected_ride_payouts.sql first.';
