-- 381_admin_cloud_message_stats_rollup_fn.sql
--
-- Purpose:
--   Replace Python-side fetch-all + loop in admin_get_cloud_message_stats
--   (routes/admin/messaging.py) with a single server-side GROUP BY.
--   The old path fetched up to 10,000 cloud_messages rows and iterated
--   5 times counting by status + summing successful/total_recipients.
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_cloud_message_stats_rollup();

CREATE OR REPLACE FUNCTION public.admin_cloud_message_stats_rollup()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT jsonb_build_object(
        'total',            COUNT(*),
        'sent',             COALESCE(SUM(CASE WHEN status = 'sent'      THEN 1 ELSE 0 END), 0),
        'scheduled',        COALESCE(SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END), 0),
        'failed',           COALESCE(SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END), 0),
        'total_reached',    COALESCE(SUM(COALESCE(successful, 0)), 0),
        'total_recipients', COALESCE(SUM(COALESCE(total_recipients, 0)), 0)
    )
    FROM cloud_messages;
$$;

COMMENT ON FUNCTION public.admin_cloud_message_stats_rollup() IS
    'Cloud message counts by status + delivery totals. '
    'Replaces fetch-all + Python loop in admin_get_cloud_message_stats.';

REVOKE EXECUTE ON FUNCTION public.admin_cloud_message_stats_rollup()
    FROM anon, authenticated;
