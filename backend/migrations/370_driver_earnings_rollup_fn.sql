-- 370_driver_earnings_rollup_fn.sql
--
-- Purpose:
--   Replace the fleet-wide driver-earnings scan behind GET /admin/drivers/stats.
--   That handler fetched every completed, non-legacy ride for every driver in
--   scope (limit=50000, select *) and summed `driver_earnings` per driver in
--   Python, purely to fill one stat card and a per-service-area column. The
--   50,000-row cap was also a silent correctness ceiling: past it the stat card
--   under-reported with no signal.
--
--   This function does the same grouping in SQL and returns one row per driver,
--   so the handler transfers N driver rows instead of N-thousand ride rows.
--
-- Semantics mirror the previous Python exactly:
--   - status = 'completed' only.
--   - Legacy-imported rides excluded, matching admin_ride_money_rollup (302)
--     and admin_payouts_overview_aggregates (303): previous-app money the
--     driver already received, not new Spinr income.
--   - LIFETIME, not date-windowed. The stats handler pairs this with
--     total_rides, which is also a lifetime counter; windowing one and not the
--     other would make the card internally inconsistent.
--   - Drivers with no qualifying rides are simply absent from the result; the
--     caller defaults them to 0, exactly as the old defaultdict did.
--
-- Money-function safety:
--   `driver_earnings` is a FLOAT column. The ::text::numeric double cast is
--   mandatory, not stylistic — it reproduces Python's Decimal(str(float))
--   (float8::text uses the shortest round-trip representation), so totals match
--   the values every other earnings surface reports. A bare ::numeric would
--   carry the binary rounding error and drift from prior totals.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_driver_earnings_rollup(text[]);
--   ...and revert routes/admin/drivers.py to the in-Python sum. No column or
--   index is created or altered by this migration.

CREATE OR REPLACE FUNCTION public.admin_driver_earnings_rollup(
    p_driver_ids text[]
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH c AS (
        SELECT
            driver_id,
            COALESCE(driver_earnings, 0)::text::numeric AS driver_earnings
        FROM rides
        WHERE status = 'completed'
          AND driver_id = ANY (p_driver_ids)
          AND legacy_import_metadata = '{}'::jsonb
    ),
    per_driver AS (
        SELECT driver_id, SUM(driver_earnings) AS earnings
        FROM c
        GROUP BY driver_id
    )
    SELECT jsonb_build_object(
        'by_driver', COALESCE(
            (SELECT jsonb_object_agg(driver_id, earnings) FROM per_driver),
            '{}'::jsonb
        ),
        'total', COALESCE((SELECT SUM(earnings) FROM per_driver), 0)
    );
$$;

COMMENT ON FUNCTION public.admin_driver_earnings_rollup(text[]) IS
    'Lifetime completed-ride driver_earnings grouped by driver, for the given driver ids, '
    'excluding legacy-imported rides. Returns {by_driver: {driver_id: numeric}, total: numeric}. '
    'Replaces a 50000-row Python-side sum in GET /admin/drivers/stats (audit P1 #11, PR #4579). '
    'Money is summed as NUMERIC via ::text::numeric to stay cent-faithful.';

-- Called from the backend (service role) only. The service role bypasses these
-- REVOKEs by design; they exist so a leaked anon/authenticated key cannot read
-- fleet-wide earnings.
REVOKE EXECUTE ON FUNCTION public.admin_driver_earnings_rollup(text[]) FROM anon, authenticated;
