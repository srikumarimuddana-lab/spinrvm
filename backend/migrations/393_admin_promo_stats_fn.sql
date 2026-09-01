-- 393_admin_promo_stats_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of 2k promotions + 5k promo_applications
--   in admin_get_promo_stats (routes/admin/promotions.py) with
--   server-side aggregation. Promo counts, usage stats, and daily
--   chart data (split by public/private type) are computed in one call.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_promo_stats(timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.admin_promo_stats(
    p_range_start timestamptz,
    p_now         timestamptz
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_result jsonb;
BEGIN
    WITH promo_counts AS (
        SELECT
            COUNT(*) FILTER (WHERE promo_type IS DISTINCT FROM 'private')                        AS total_codes,
            COUNT(*) FILTER (WHERE promo_type IS DISTINCT FROM 'private' AND is_active = TRUE)    AS active_codes,
            COUNT(*) FILTER (WHERE promo_type IS DISTINCT FROM 'private'
                             AND is_active IS NOT TRUE
                             AND expiry_date IS NOT NULL
                             AND expiry_date < p_now)                                             AS expired_codes,
            COUNT(*) FILTER (WHERE promo_type = 'private')                                       AS total_private,
            COUNT(*) FILTER (WHERE promo_type = 'private' AND is_active = TRUE)                  AS active_private
        FROM promotions
    ),
    usage_agg AS (
        SELECT
            COUNT(*)                            AS total_redemptions,
            COALESCE(SUM(pa.discount_applied), 0) AS total_discount
        FROM promo_applications pa
        WHERE pa.created_at >= p_range_start
    ),
    daily_raw AS (
        SELECT
            (pa.created_at AT TIME ZONE 'UTC')::date AS day,
            COALESCE(p.promo_type, 'public')          AS ptype,
            COUNT(*)                                   AS cnt,
            COALESCE(SUM(pa.discount_applied), 0)      AS amt
        FROM promo_applications pa
        LEFT JOIN promotions p ON p.id = pa.promo_id
        WHERE pa.created_at >= p_range_start
        GROUP BY 1, 2
    ),
    daily_pivot AS (
        SELECT
            day,
            SUM(cnt)                                          AS count,
            SUM(amt)                                          AS amount,
            COALESCE(SUM(cnt) FILTER (WHERE ptype != 'private'), 0)  AS public_count,
            COALESCE(SUM(amt) FILTER (WHERE ptype != 'private'), 0)  AS public_amount,
            COALESCE(SUM(cnt) FILTER (WHERE ptype = 'private'), 0)   AS private_count,
            COALESCE(SUM(amt) FILTER (WHERE ptype = 'private'), 0)   AS private_amount
        FROM daily_raw
        GROUP BY day
    )
    SELECT jsonb_build_object(
        'total_codes',      (SELECT total_codes FROM promo_counts),
        'active_codes',     (SELECT active_codes FROM promo_counts),
        'expired_codes',    (SELECT expired_codes FROM promo_counts),
        'total_private',    (SELECT total_private FROM promo_counts),
        'active_private',   (SELECT active_private FROM promo_counts),
        'total_redemptions',(SELECT total_redemptions FROM usage_agg),
        'total_discount',   (SELECT total_discount FROM usage_agg),
        'daily_usage',      COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'date',           day,
                'count',          count,
                'amount',         amount,
                'public_count',   public_count,
                'public_amount',  public_amount,
                'private_count',  private_count,
                'private_amount', private_amount
            ) ORDER BY day)
            FROM daily_pivot
        ), '[]'::jsonb)
    ) INTO v_result;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_promo_stats(timestamptz, timestamptz) IS
    'Promotion overview + usage stats + daily chart data. '
    'Replaces 2k promotions + 5k promo_applications fetch + Python iteration.';

REVOKE EXECUTE ON FUNCTION public.admin_promo_stats(timestamptz, timestamptz)
FROM anon, authenticated;
