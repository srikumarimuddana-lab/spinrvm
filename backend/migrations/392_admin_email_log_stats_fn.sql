-- 392_admin_email_log_stats_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of 5k email_send_log rows + three Counter()
--   aggregations in get_email_deliverability (routes/admin/monitoring.py)
--   with server-side GROUP BY.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_email_log_stats(timestamptz);

CREATE OR REPLACE FUNCTION public.admin_email_log_stats(
    p_since timestamptz
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
    WITH base AS (
        SELECT
            COALESCE(status, 'unknown')    AS status,
            COALESCE(provider, 'none')     AS provider,
            COALESCE(email_type, 'unknown') AS email_type
        FROM email_send_log
        WHERE created_at >= p_since
    )
    SELECT jsonb_build_object(
        'total', (SELECT COUNT(*) FROM base),
        'by_status', COALESCE(
            (SELECT jsonb_object_agg(status, cnt)
             FROM (SELECT status, COUNT(*) cnt FROM base GROUP BY 1) t),
            '{}'::jsonb),
        'by_provider', COALESCE(
            (SELECT jsonb_object_agg(provider, cnt)
             FROM (SELECT provider, COUNT(*) cnt FROM base GROUP BY 1) t),
            '{}'::jsonb),
        'by_type', COALESCE(
            (SELECT jsonb_object_agg(email_type, cnt)
             FROM (SELECT email_type, COUNT(*) cnt FROM base GROUP BY 1) t),
            '{}'::jsonb)
    ) INTO v_result;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_email_log_stats(timestamptz) IS
    'Email send log grouped counts by status/provider/type. '
    'Replaces 5k row fetch + three Python Counter() calls.';

REVOKE EXECUTE ON FUNCTION public.admin_email_log_stats(timestamptz)
FROM anon, authenticated;
