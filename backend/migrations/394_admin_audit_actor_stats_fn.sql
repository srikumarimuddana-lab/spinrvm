-- 394_admin_audit_actor_stats_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of 5k audit_logs + nested Counter()
--   aggregation in admin audit activity (routes/admin/maintenance.py)
--   with server-side GROUP BY.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_audit_actor_stats(timestamptz, int);

CREATE OR REPLACE FUNCTION public.admin_audit_actor_stats(
    p_since timestamptz,
    p_limit int DEFAULT 20
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
    WITH actor_totals AS (
        SELECT
            COALESCE(actor_id, 'unknown') AS actor_id,
            COUNT(*)                      AS action_count
        FROM audit_logs
        WHERE created_at >= p_since
        GROUP BY 1
        ORDER BY action_count DESC
        LIMIT p_limit
    ),
    actor_actions AS (
        SELECT
            COALESCE(al.actor_id, 'unknown') AS actor_id,
            COALESCE(al.action, 'unknown')   AS action,
            COUNT(*)                          AS cnt,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(al.actor_id, 'unknown')
                ORDER BY COUNT(*) DESC
            ) AS rn
        FROM audit_logs al
        WHERE al.created_at >= p_since
          AND COALESCE(al.actor_id, 'unknown') IN (SELECT actor_id FROM actor_totals)
        GROUP BY 1, 2
    )
    SELECT jsonb_build_object(
        'rows_scanned', (SELECT COUNT(*) FROM audit_logs WHERE created_at >= p_since),
        'actors', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'actor_id',     at.actor_id,
                'action_count', at.action_count,
                'top_actions',  COALESCE((
                    SELECT jsonb_agg(
                        jsonb_build_object('action', aa.action, 'count', aa.cnt)
                        ORDER BY aa.cnt DESC
                    )
                    FROM actor_actions aa
                    WHERE aa.actor_id = at.actor_id AND aa.rn <= 5
                ), '[]'::jsonb)
            ) ORDER BY at.action_count DESC)
            FROM actor_totals at
        ), '[]'::jsonb)
    ) INTO v_result;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_audit_actor_stats(timestamptz, int) IS
    'Audit log actor activity: top N actors with their top 5 actions. '
    'Replaces 5k audit_logs fetch + nested Python Counter() calls.';

REVOKE EXECUTE ON FUNCTION public.admin_audit_actor_stats(timestamptz, int)
FROM anon, authenticated;
