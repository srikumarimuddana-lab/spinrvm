-- 423_retention_cohorts.sql
--
-- Purpose:
--   admin_retention_cohorts(p_cohort_start, p_cohort_end, p_service_area_id)
--   -- W1/W4/W12 rider and driver retention curves, bucketed by signup week.
--
--   CLAUDE.md's KPI table names "Weekly active driver retention (week-over-
--   week) >= 80%" as a target, but nothing in the codebase computes it --
--   confirmed by direct grep (zero hits for retention/cohort/repeat_rider/
--   churn/weekly_active across backend/ and admin-dashboard/, other than
--   unrelated PII-retention-purge code of the same name) and by direct
--   inspection of analytics.py's `_KPI_TARGETS` dict, which only tracks
--   match_rate/rider_cancel_rate/driver_cancel_rate/utilization_pct. This is
--   the first cohort-retention concept anywhere in the schema -- there is no
--   existing pattern to extend, only 351's aggregation style to follow.
--
-- Definition (explicit product decision, not assumed):
--   A user from signup cohort week W is "retained" at horizon H (weeks)
--   if they completed >= 1 ride in week (W + H) -- same rule for riders
--   (rides.rider_id) and drivers (rides.driver_id). No distinction between
--   "opened the app" and "booked a ride" -- app-open telemetry was not
--   confirmed queryable, so this intentionally uses the stricter, unambiguous
--   signal already durably stored.
--
--   Horizons reported: 1, 4, 12 weeks after the signup week. A horizon is
--   only reported once it has actually, fully elapsed (cohort_week + H weeks
--   < the current week -- strictly less-than, since the current week itself
--   is still in progress and its data is incomplete) -- a cohort that signed
--   up 2 weeks ago cannot yet have
--   a W4 or W12 answer, and reporting one would either have to fabricate a
--   number from an incomplete window or silently coerce "not yet knowable"
--   into "0% retained", both worse than omitting the row. Rows are dropped,
--   not zero-filled, matching 351/422's precedent for "no data" cases.
--
-- Population:
--   Riders: users.is_rider = TRUE, bucketed by users.created_at (signup).
--            NOT users.role = 'rider' -- migration 101 retired role for
--            rider/driver discrimination in favour of is_rider/is_driver
--            precisely because role is written once at signup and never
--            resynced, so a driver-first dual-role user keeps role='driver'
--            forever even while actively riding. routes/admin/users.py
--            already treats is_rider as the source of truth for this same
--            reason -- role='rider' here would have silently undercounted
--            the cohort.
--   Drivers: all rows in drivers, bucketed by drivers.created_at (the date
--            they became a driver, not users.created_at -- a user can sign
--            up as a rider long before onboarding as a driver, and the
--            retention question here is "since becoming a driver", not
--            "since creating an account").
--   p_service_area_id filters the ACTIVITY (rides), not cohort membership --
--   "of riders who signed up in this window, how many kept riding in zone X"
--   is the useful question when an operator is looking at one zone; signup
--   itself has no service-area column on users/drivers to filter by.
--
-- Same conventions as 351/422: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from PUBLIC/anon/authenticated, granted to
-- service_role only. Regina business weeks (extends 350's day-bucketing
-- precedent to week granularity). Excludes legacy-imported rides
-- (legacy_import_metadata = '{}'::jsonb, 349) -- a backfilled historical
-- ride is not a real retention signal.
--
-- Index:
--   public.users and public.drivers have NO index on created_at today
--   (confirmed via pg_indexes against production -- the one created_at index
--   found was on auth.users, an unrelated Supabase-internal table). The
--   cohort-bucketing scan (WHERE created_at BETWEEN p_cohort_start AND
--   p_cohort_end) needs one on each. The ride-side scan is already covered
--   by idx_rides_rider_created(rider_id, created_at DESC) / idx_rides_driver_
--   created(driver_id, created_at DESC) (migration 34, pre-existing) -- the
--   join predicate here is rider_id/driver_id against an already date-bounded
--   cohort set, not a ride_completed_at range scan, so those (not a
--   ride_completed_at index) are what actually serve this query -- no new
--   rides index needed, same reasoning as 351's own header for its own
--   predicate.
--   CONCURRENTLY so neither index build locks a live-write table
--   (users: OTP/profile writes; drivers: location/status writes every few
--   seconds). run_migrations.py splits CONCURRENTLY statements out of the
--   transaction.
--
-- Forward-compatible: one new function, two new indexes. No table, column,
-- constraint, or existing function is altered. Nothing is written or migrated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_retention_cohorts(timestamptz, timestamptz, text);
--   DROP INDEX CONCURRENTLY IF EXISTS idx_users_rider_created_at;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_created_at;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_rider_created_at
    ON users (created_at)
    WHERE is_rider;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_created_at
    ON drivers (created_at);

CREATE OR REPLACE FUNCTION public.admin_retention_cohorts(
    p_cohort_start    timestamptz,
    p_cohort_end      timestamptz DEFAULT now(),
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH horizons(weeks) AS (VALUES (1), (4), (12)),
    current_week AS (
        SELECT date_trunc('week', now() AT TIME ZONE 'America/Regina')::date AS d
    ),

    -- ── Riders ──────────────────────────────────────────────────────────
    rider_cohorts AS (
        SELECT
            u.id,
            date_trunc('week', u.created_at AT TIME ZONE 'America/Regina')::date AS cohort_week
        FROM users u
        WHERE u.is_rider
          AND u.created_at >= p_cohort_start
          AND u.created_at <= p_cohort_end
    ),
    rider_active_weeks AS (
        SELECT DISTINCT
            r.rider_id AS id,
            date_trunc('week', r.ride_completed_at AT TIME ZONE 'America/Regina')::date AS active_week
        FROM rides r
        JOIN rider_cohorts rc ON rc.id = r.rider_id
        WHERE r.status = 'completed'
          AND r.ride_completed_at IS NOT NULL
          AND r.legacy_import_metadata = '{}'::jsonb
          AND (p_service_area_id IS NULL OR r.service_area_id::text = p_service_area_id)
    ),
    rider_retention AS (
        SELECT
            rc.cohort_week,
            h.weeks AS horizon_weeks,
            COUNT(*) AS cohort_size,
            COUNT(*) FILTER (
                WHERE EXISTS (
                    SELECT 1 FROM rider_active_weeks raw_
                    WHERE raw_.id = rc.id
                      AND raw_.active_week = rc.cohort_week + (h.weeks || ' weeks')::interval
                )
            ) AS retained
        FROM rider_cohorts rc
        CROSS JOIN horizons h
        -- Strictly less-than: the horizon week must have fully CLOSED, not
        -- merely arrived. `<=` would report the current, still-in-progress
        -- week as "elapsed" using only its partial data so far, and the
        -- number would then keep changing day-to-day for what should be a
        -- stable, closed data point once actually correct.
        WHERE rc.cohort_week + (h.weeks || ' weeks')::interval < (SELECT d FROM current_week)
        GROUP BY rc.cohort_week, h.weeks
    ),

    -- ── Drivers ─────────────────────────────────────────────────────────
    driver_cohorts AS (
        SELECT
            d.id,
            date_trunc('week', d.created_at AT TIME ZONE 'America/Regina')::date AS cohort_week
        FROM drivers d
        WHERE d.created_at >= p_cohort_start
          AND d.created_at <= p_cohort_end
    ),
    driver_active_weeks AS (
        SELECT DISTINCT
            r.driver_id AS id,
            date_trunc('week', r.ride_completed_at AT TIME ZONE 'America/Regina')::date AS active_week
        FROM rides r
        JOIN driver_cohorts dc ON dc.id = r.driver_id
        WHERE r.status = 'completed'
          AND r.ride_completed_at IS NOT NULL
          AND r.legacy_import_metadata = '{}'::jsonb
          AND (p_service_area_id IS NULL OR r.service_area_id::text = p_service_area_id)
    ),
    driver_retention AS (
        SELECT
            dc.cohort_week,
            h.weeks AS horizon_weeks,
            COUNT(*) AS cohort_size,
            COUNT(*) FILTER (
                WHERE EXISTS (
                    SELECT 1 FROM driver_active_weeks daw
                    WHERE daw.id = dc.id
                      AND daw.active_week = dc.cohort_week + (h.weeks || ' weeks')::interval
                )
            ) AS retained
        FROM driver_cohorts dc
        CROSS JOIN horizons h
        WHERE dc.cohort_week + (h.weeks || ' weeks')::interval < (SELECT d FROM current_week)
        GROUP BY dc.cohort_week, h.weeks
    )

    SELECT jsonb_build_object(
        'riders', (
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'cohort_week',   cohort_week::text,
                'horizon_weeks', horizon_weeks,
                'cohort_size',   cohort_size,
                'retained',      retained,
                'retained_pct',  ROUND((retained::numeric / cohort_size * 100), 1)
            ) ORDER BY cohort_week, horizon_weeks), '[]'::jsonb)
            FROM rider_retention
            WHERE cohort_size > 0
        ),
        'drivers', (
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'cohort_week',   cohort_week::text,
                'horizon_weeks', horizon_weeks,
                'cohort_size',   cohort_size,
                'retained',      retained,
                'retained_pct',  ROUND((retained::numeric / cohort_size * 100), 1)
            ) ORDER BY cohort_week, horizon_weeks), '[]'::jsonb)
            FROM driver_retention
            WHERE cohort_size > 0
        )
    );
$$;

COMMENT ON FUNCTION public.admin_retention_cohorts(timestamptz, timestamptz, text) IS
    'W1/W4/W12 rider and driver retention by signup cohort week -- retained means >=1 '
    'completed ride in that later week. Only reports a horizon once it has actually elapsed '
    '(a 2-week-old cohort has no W4/W12 answer yet, so that row is omitted, not zero-filled). '
    'Regina business weeks (extends 350). Excludes legacy imports (349). For '
    '/analytics/retention-cohorts.';

REVOKE EXECUTE ON FUNCTION public.admin_retention_cohorts(timestamptz, timestamptz, text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_retention_cohorts(timestamptz, timestamptz, text) TO service_role;
