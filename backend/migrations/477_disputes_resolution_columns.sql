-- 477: add the disputes columns routes/disputes.py writes but production lacks
-- (ROADMAP N23 follow-up).
--
-- Finding: production public.disputes has only the columns from migrations
-- 10 + 126 (id, ride_id, user_id, user_name, user_type, reason, description,
-- status, refund_amount, resolution_status, resolution_notes, resolved_at,
-- resolved_by, admin_note, created_at, updated_at, zoho_ticket_id; confirmed
-- by a read-only information_schema check on 2026-09-25). routes/disputes.py
-- writes four more:
--   create_dispute (POST /disputes)             -> requested_amount, original_fare
--   admin_resolve_dispute (PUT /admin/disputes/{id}/resolve) -> resolution, refund_result
-- PostgREST rejects an unknown column (PGRST204), so both handlers would 500 in
-- production. That explains the 0 rows in disputes to date. The resolve
-- handler was shadowed by routes/admin/support.py until N23, so its writes had
-- never run.
--
-- Types match what the code writes:
--   requested_amount NUMERIC(10,2) -- Decimal from the request or ride.driver_earnings
--   original_fare    NUMERIC(10,2) -- ride.total_fare snapshot
--   resolution       TEXT          -- approved | partial_refund | rejected
--   refund_result    JSONB         -- {"status", "refund_id" | "reason", "approved_amount"?}
-- All nullable, no default: additive, metadata-only, no table rewrite, safe
-- under live traffic. No new query pattern, so no index. RLS on disputes is
-- unchanged (column additions inherit the table's policies).
--
-- DEPLOY ORDER: apply this BEFORE turning admin_dispute_refunds_enabled on.
-- With the flag on, the resolve handler issues the Stripe refund and only then
-- writes resolution/refund_result, so a missing column would 500 after money
-- has moved.
--
-- Rollback (ONLY after reverting the backend code that writes these columns,
-- otherwise create/resolve 500 again; the data in them is lost):
--   ALTER TABLE public.disputes
--     DROP COLUMN IF EXISTS refund_result,
--     DROP COLUMN IF EXISTS resolution,
--     DROP COLUMN IF EXISTS original_fare,
--     DROP COLUMN IF EXISTS requested_amount;

BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

ALTER TABLE public.disputes
    ADD COLUMN IF NOT EXISTS requested_amount NUMERIC(10,2) NULL,
    ADD COLUMN IF NOT EXISTS original_fare NUMERIC(10,2) NULL,
    ADD COLUMN IF NOT EXISTS resolution TEXT NULL,
    ADD COLUMN IF NOT EXISTS refund_result JSONB NULL;

COMMENT ON COLUMN public.disputes.requested_amount IS
    'CAD refund the claimant asked for (create_dispute); defaults to the fare, or driver_earnings for a driver claim.';
COMMENT ON COLUMN public.disputes.original_fare IS
    'Snapshot of rides.total_fare when the dispute was filed; upper bound for an admin refund.';
COMMENT ON COLUMN public.disputes.resolution IS
    'Admin resolution from PUT /admin/disputes/{id}/resolve: approved | partial_refund | rejected.';
COMMENT ON COLUMN public.disputes.refund_result IS
    'Outcome of the admin refund: Stripe {status, refund_id}, or {status: manual_required|not_issued, reason, approved_amount?}.';

COMMIT;
