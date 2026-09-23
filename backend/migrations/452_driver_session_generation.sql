-- Rollback: set settings.driver_single_session_enabled=false. Keep generation
-- bindings and revoked rows: restoring credentials would undo a security action.
-- After retiring all readers, DROP FUNCTION begin_driver_session(text,text);
-- ALTER TABLE refresh_tokens DROP COLUMN token_version; drop the settings flag.
-- Enable only after EVERY API replica preserves the refresh parent's generation.
-- Driver sign-in displaces all mobile sessions for the account; admin is separate.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';
ALTER TABLE public.refresh_tokens ADD COLUMN IF NOT EXISTS token_version integer;
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS driver_single_session_enabled boolean NOT NULL DEFAULT false;
COMMIT;

-- This is the new revocation query pattern. CONCURRENTLY also ensures the runner
-- executes transaction blocks separately, releasing DDL locks before backfill.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_refresh_mobile_generation
ON public.refresh_tokens (user_id, token_version)
WHERE revoked_at IS NULL AND audience IN ('rider', 'driver');

-- Existing credentials retain their currently valid generation. New inserts from
-- older code remain NULL; new refresh code treats NULL as legacy generation zero.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';
UPDATE public.refresh_tokens AS t
SET token_version = COALESCE(u.token_version, 0)
FROM public.users AS u
WHERE t.user_id = u.id AND t.audience IN ('rider', 'driver')
  AND t.token_version IS NULL AND u.token_version > 0
  AND t.revoked_at IS NULL AND t.expires_at > now();
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';
CREATE OR REPLACE FUNCTION public.begin_driver_session(p_user_id text, p_session_id text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_version integer;
    v_previous_session text;
BEGIN
    IF NOT COALESCE((SELECT driver_single_session_enabled FROM settings WHERE id='app_settings'), false) THEN
        RETURN jsonb_build_object('enabled', false);
    END IF;
    IF p_session_id IS NULL OR p_session_id = '' THEN
        RAISE EXCEPTION 'session identity is required';
    END IF;
    SELECT COALESCE(token_version, 0), current_session_id
      INTO v_version, v_previous_session FROM users WHERE id=p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'session user missing';
    END IF;
    v_version := v_version + 1;
    UPDATE users SET token_version=v_version, current_session_id=p_session_id,
                     sessions_invalid_before=now()
      WHERE id=p_user_id;
    UPDATE refresh_tokens
       SET revoked_at=COALESCE(revoked_at, now()), revocation_reason='session_superseded'
     WHERE user_id=p_user_id AND audience IN ('rider', 'driver')
       AND revoked_at IS NULL
       AND COALESCE(token_version, 0) < v_version;
    RETURN jsonb_build_object('enabled', true, 'token_version', v_version,
                             'previous_session_id', v_previous_session);
END;
$$;
REVOKE ALL ON FUNCTION public.begin_driver_session(text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.begin_driver_session(text,text) TO service_role;
COMMIT;
-- Existing refresh_tokens RLS remains unchanged.
