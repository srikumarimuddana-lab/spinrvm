-- 387_admin_referred_user_count_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of ALL 10k users rows in
--   admin_get_referral_analytics (routes/admin/drivers.py) just to count
--   how many have a referral code matching the requested source (driver/rider).
--   The old path fetched every user row to iterate and count in Python.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_referred_user_count(text, text, text);

CREATE OR REPLACE FUNCTION public.admin_referred_user_count(
    p_kind  text,
    p_start text DEFAULT NULL,
    p_end   text DEFAULT NULL
)
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT COUNT(*)
    FROM users
    WHERE referred_by IS NOT NULL
      AND referral_code_used IS NOT NULL
      AND CASE
            WHEN p_kind = 'rider'
            THEN upper(referral_code_used) LIKE 'RIDE%'
            ELSE upper(referral_code_used) NOT LIKE 'RIDE%'
          END
      AND (p_start IS NULL OR created_at >= p_start::timestamptz)
      AND (p_end IS NULL OR created_at <= p_end::timestamptz);
$$;

COMMENT ON FUNCTION public.admin_referred_user_count(text, text, text) IS
    'Count referred users by source kind (rider/driver) with optional date '
    'range. Replaces fetching ALL 10k users for a single count.';

REVOKE EXECUTE ON FUNCTION public.admin_referred_user_count(text, text, text)
FROM anon, authenticated;
