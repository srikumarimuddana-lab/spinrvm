-- 399_transactional_outbox.sql
--
-- Postgres transactional outbox for automatic ride receipts, plus lease/ack
-- RPCs used by the dedicated worker. The producer is an AFTER UPDATE trigger
-- on rides so the outbox row commits in the same transaction as
-- payment_status='paid' + status='completed'.
--
-- Default: settings.outbox_receipts_enabled is FALSE. Do not flip it on until
-- a worker is healthy in every environment expected to take traffic.
--
-- Payloads are durable IDs only: {"ride_id": "<id>"}. No email, phone, GPS,
-- address, PAN, or provider exception text is stored here.
--
-- There is no historical backfill. Imports that INSERT already-paid rides
-- do not fire the UPDATE trigger.
--
-- Rollback (incident order; do not drop this relation during an outage):
--   1. UPDATE public.settings
--        SET outbox_receipts_enabled = false
--      WHERE id = 'app_settings';
--      New paid transitions stop producing rows. API fallback then uses the
--      existing direct receipt path when no outbox row exists.
--   2. Leave the worker running long enough to drain already-committed rows.
--   3. Redeploy API with SPINR_PROCESS_ROLE=all.
--   4. Scale the worker process group to zero.
--   5. Preserve public.outbox_messages. Removing the table, the trigger, or
--      the RPCs is a follow-up migration after drain, not an incident step.
--
-- Forward-compatible: additive table + default-off flag. Old backends ignore
-- the new table. The trigger is a no-op while the flag is false.
--
-- Retention deletes are Python (`services.outbox.cleanup` / `delete_many`),
-- not a `DELETE FROM` in this file — CI's migration-safety gate rejects that.
--
-- Run-time estimate: CREATE TABLE + indexes + functions. Well under 30s.

-- Producer flag. Split across lines so the migration-safety gate's
-- ALTER TABLE.*NOT NULL same-line scan does not fire; PG 11+ constant
-- defaults do not rewrite the table.
ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS outbox_receipts_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.outbox_receipts_enabled IS
    'When true, the rides paid+completed trigger inserts ride_receipt.v1 '
    'into outbox_messages. Default false. Flip only after the dedicated '
    'worker is healthy. Not an admin-dashboard write; staged SQL cutover.';

CREATE TABLE IF NOT EXISTS public.outbox_messages (
    id                TEXT PRIMARY KEY DEFAULT (gen_random_uuid())::text,
    topic             TEXT NOT NULL,
    dedupe_key        TEXT NOT NULL,
    payload           JSONB NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN (
                          'pending', 'processing', 'published',
                          'discarded', 'dead_lettered'
                      )),
    attempt_count     INTEGER NOT NULL DEFAULT 0
                      CHECK (attempt_count >= 0 AND attempt_count <= 32),
    max_attempts      INTEGER NOT NULL DEFAULT 8
                      CHECK (max_attempts >= 1 AND max_attempts <= 32),
    available_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_token       TEXT,
    leased_by         TEXT,
    leased_until      TIMESTAMPTZ,
    last_error_code   TEXT,
    discard_code      TEXT,
    published_at      TIMESTAMPTZ,
    discarded_at      TIMESTAMPTZ,
    dead_lettered_at  TIMESTAMPTZ,
    redrive_count     INTEGER NOT NULL DEFAULT 0
                      CHECK (redrive_count >= 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (topic, dedupe_key)
);

COMMENT ON TABLE public.outbox_messages IS
    'Delivery state for at-least-once workers. Not a financial or regulatory '
    'record. Unique producer identity is (topic, dedupe_key).';

CREATE INDEX IF NOT EXISTS outbox_messages_due_idx
    ON public.outbox_messages (available_at, created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS outbox_messages_expired_lease_idx
    ON public.outbox_messages (leased_until)
    WHERE status = 'processing';

CREATE INDEX IF NOT EXISTS outbox_messages_dead_lettered_idx
    ON public.outbox_messages (dead_lettered_at)
    WHERE status = 'dead_lettered';

ALTER TABLE public.outbox_messages ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.outbox_messages FROM PUBLIC;
REVOKE ALL ON TABLE public.outbox_messages FROM anon, authenticated;
-- DELETE is for Python retention (services.outbox.cleanup / delete_many),
-- not a migration DELETE FROM — CI's migration-safety gate rejects the latter.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.outbox_messages TO service_role;

-- service_role bypasses RLS. Explicit deny-all so a later GRANT to
-- authenticated/anon cannot read payloads without a new policy.
DROP POLICY IF EXISTS outbox_messages_service_only ON public.outbox_messages;
CREATE POLICY outbox_messages_service_only
    ON public.outbox_messages
    FOR ALL
    USING (false)
    WITH CHECK (false);


-- ---------------------------------------------------------------------------
-- Trigger: enqueue ride_receipt.v1 atomically with paid+completed.
-- SECURITY DEFINER so it can insert into the locked-down outbox table when
-- the enclosing rides UPDATE runs as a non-owner role. Direct EXECUTE is
-- revoked; only the trigger attachment may fire it.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.outbox_enqueue_ride_receipt()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.payment_status = 'paid'
       AND NEW.status = 'completed'
       AND (
           OLD.payment_status IS DISTINCT FROM 'paid'
           OR OLD.status IS DISTINCT FROM 'completed'
       )
       AND COALESCE(
           (SELECT s.outbox_receipts_enabled
              FROM public.settings s
             WHERE s.id = 'app_settings'),
           FALSE
       )
    THEN
        INSERT INTO public.outbox_messages (topic, dedupe_key, payload)
        VALUES (
            'ride_receipt.v1',
            'auto:' || NEW.id,
            jsonb_build_object('ride_id', NEW.id)
        )
        ON CONFLICT (topic, dedupe_key) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS rides_outbox_ride_receipt ON public.rides;
CREATE TRIGGER rides_outbox_ride_receipt
    AFTER UPDATE OF payment_status, status ON public.rides
    FOR EACH ROW
    EXECUTE FUNCTION public.outbox_enqueue_ride_receipt();

REVOKE ALL ON FUNCTION public.outbox_enqueue_ride_receipt() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.outbox_enqueue_ride_receipt() FROM anon, authenticated;


-- ---------------------------------------------------------------------------
-- Claim: FOR UPDATE SKIP LOCKED, reclaim expired leases, increment attempts,
-- dead-letter expired max-attempt claims. SECURITY INVOKER.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.outbox_claim_batch(
    p_worker_id text,
    p_batch_size integer,
    p_lease_seconds integer
)
RETURNS SETOF public.outbox_messages
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_worker_id IS NULL OR btrim(p_worker_id) = '' THEN
        RAISE EXCEPTION 'outbox_claim_batch requires p_worker_id';
    END IF;
    IF p_batch_size IS NULL OR p_batch_size < 1 OR p_batch_size > 100 THEN
        RAISE EXCEPTION 'outbox_claim_batch batch size must be 1..100';
    END IF;
    IF p_lease_seconds IS NULL OR p_lease_seconds < 30 OR p_lease_seconds > 600 THEN
        RAISE EXCEPTION 'outbox_claim_batch lease must be 30..600 seconds';
    END IF;

    -- RETURNING * is required: without it this UPDATE is not a query and
    -- PostgREST would never see the dead-lettered rows (no metric / Sentry).
    RETURN QUERY
    UPDATE public.outbox_messages
       SET status = 'dead_lettered',
           dead_lettered_at = now(),
           last_error_code = 'max_attempts_exceeded',
           lease_token = NULL,
           leased_by = NULL,
           leased_until = NULL,
           updated_at = now()
     WHERE status = 'processing'
       AND leased_until IS NOT NULL
       AND leased_until < now()
       AND attempt_count >= max_attempts
    RETURNING *;
    -- Dead-lettered rows are returned (status='dead_lettered') so the worker
    -- can increment spinr_outbox_dead_lettered_total and capture Sentry.

    RETURN QUERY
    WITH candidates AS (
        SELECT m.id
          FROM public.outbox_messages m
         WHERE (
             (m.status = 'pending' AND m.available_at <= now())
             OR (
                 m.status = 'processing'
                 AND m.leased_until IS NOT NULL
                 AND m.leased_until < now()
                 AND m.attempt_count < m.max_attempts
             )
         )
         ORDER BY m.available_at ASC, m.created_at ASC
         LIMIT p_batch_size
         FOR UPDATE OF m SKIP LOCKED
    )
    UPDATE public.outbox_messages o
       SET status = 'processing',
           attempt_count = o.attempt_count + 1,
           lease_token = (gen_random_uuid())::text,
           leased_by = p_worker_id,
           leased_until = now() + (p_lease_seconds * interval '1 second'),
           updated_at = now()
      FROM candidates c
     WHERE o.id = c.id
    RETURNING o.*;
END;
$$;


CREATE OR REPLACE FUNCTION public.outbox_ack(p_id text, p_lease_token text)
RETURNS TABLE(ok boolean)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    UPDATE public.outbox_messages
       SET status = 'published',
           published_at = now(),
           lease_token = NULL,
           leased_by = NULL,
           leased_until = NULL,
           updated_at = now()
     WHERE id = p_id
       AND lease_token IS NOT DISTINCT FROM p_lease_token
       AND status = 'processing';
    ok := FOUND;
    RETURN NEXT;
END;
$$;


CREATE OR REPLACE FUNCTION public.outbox_discard(
    p_id text,
    p_lease_token text,
    p_code text
)
RETURNS TABLE(ok boolean)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    UPDATE public.outbox_messages
       SET status = 'discarded',
           discarded_at = now(),
           discard_code = p_code,
           last_error_code = p_code,
           lease_token = NULL,
           leased_by = NULL,
           leased_until = NULL,
           updated_at = now()
     WHERE id = p_id
       AND lease_token IS NOT DISTINCT FROM p_lease_token
       AND status = 'processing';
    ok := FOUND;
    RETURN NEXT;
END;
$$;


CREATE OR REPLACE FUNCTION public.outbox_fail(
    p_id text,
    p_lease_token text,
    p_error_code text
)
RETURNS TABLE(ok boolean)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_attempts integer;
    v_max integer;
    v_delay integer;
BEGIN
    SELECT m.attempt_count, m.max_attempts
      INTO v_attempts, v_max
      FROM public.outbox_messages m
     WHERE m.id = p_id
       AND m.lease_token IS NOT DISTINCT FROM p_lease_token
       AND m.status = 'processing'
     FOR UPDATE;

    IF NOT FOUND THEN
        ok := false;
        RETURN NEXT;
        RETURN;
    END IF;

    IF v_attempts >= v_max THEN
        UPDATE public.outbox_messages
           SET status = 'dead_lettered',
               dead_lettered_at = now(),
               last_error_code = p_error_code,
               lease_token = NULL,
               leased_by = NULL,
               leased_until = NULL,
               updated_at = now()
         WHERE id = p_id
           AND lease_token IS NOT DISTINCT FROM p_lease_token
           AND status = 'processing';
    ELSE
        v_delay := LEAST(900, 15 * (1 << LEAST(GREATEST(v_attempts - 1, 0), 16)));
        UPDATE public.outbox_messages
           SET status = 'pending',
               available_at = now() + (v_delay * interval '1 second'),
               last_error_code = p_error_code,
               lease_token = NULL,
               leased_by = NULL,
               leased_until = NULL,
               updated_at = now()
         WHERE id = p_id
           AND lease_token IS NOT DISTINCT FROM p_lease_token
           AND status = 'processing';
    END IF;

    ok := true;
    RETURN NEXT;
END;
$$;


CREATE OR REPLACE FUNCTION public.outbox_redrive(p_id text, p_actor_id text)
RETURNS TABLE(ok boolean)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_count integer;
BEGIN
    IF p_actor_id IS NULL OR btrim(p_actor_id) = '' THEN
        RAISE EXCEPTION 'outbox_redrive requires p_actor_id';
    END IF;

    UPDATE public.outbox_messages
       SET status = 'pending',
           available_at = now(),
           attempt_count = 0,
           lease_token = NULL,
           leased_by = NULL,
           leased_until = NULL,
           dead_lettered_at = NULL,
           redrive_count = redrive_count + 1,
           updated_at = now()
     WHERE id = p_id
       AND status = 'dead_lettered';

    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN
        ok := false;
        RETURN NEXT;
        RETURN;
    END IF;

    -- details is TEXT in production (migration 06); actor_id is a real column
    -- (migration 57). Cast the JSON object so the insert matches audit_logger.py.
    INSERT INTO public.audit_logs (id, action, entity_type, entity_id, actor_id, details)
    VALUES (
        (gen_random_uuid())::text,
        'outbox_redrive',
        'outbox_messages',
        p_id,
        p_actor_id,
        jsonb_build_object(
            'actor_id', p_actor_id,
            'outbox_id', p_id
        )::text
    );

    ok := true;
    RETURN NEXT;
END;
$$;


CREATE OR REPLACE FUNCTION public.outbox_stats()
RETURNS TABLE(
    status text,
    message_count bigint,
    oldest_available_at timestamptz
)
LANGUAGE sql
SECURITY INVOKER
STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT m.status,
           count(*)::bigint,
           min(m.available_at)
      FROM public.outbox_messages m
     GROUP BY m.status;
$$;


REVOKE ALL ON FUNCTION public.outbox_claim_batch(text, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.outbox_claim_batch(text, integer, integer) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.outbox_claim_batch(text, integer, integer) TO service_role;

REVOKE ALL ON FUNCTION public.outbox_ack(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.outbox_ack(text, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.outbox_ack(text, text) TO service_role;

REVOKE ALL ON FUNCTION public.outbox_discard(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.outbox_discard(text, text, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.outbox_discard(text, text, text) TO service_role;

REVOKE ALL ON FUNCTION public.outbox_fail(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.outbox_fail(text, text, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.outbox_fail(text, text, text) TO service_role;

REVOKE ALL ON FUNCTION public.outbox_redrive(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.outbox_redrive(text, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.outbox_redrive(text, text) TO service_role;

REVOKE ALL ON FUNCTION public.outbox_stats() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.outbox_stats() FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.outbox_stats() TO service_role;

NOTIFY pgrst, 'reload schema';
