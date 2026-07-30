-- 272_push_retry_queue_account_priority.sql
-- Rollback (in this order — the DELETE must precede the narrowed constraint):
--   ALTER TABLE public.push_retry_queue DROP CONSTRAINT IF EXISTS push_retry_queue_priority_check;
--   DELETE FROM public.push_retry_queue WHERE priority = 'account' AND sent_at IS NULL;
--   ALTER TABLE public.push_retry_queue ADD CONSTRAINT push_retry_queue_priority_check
--       CHECK (priority IN ('dispatch', 'safety', 'normal'));
--   (The DELETE is required: rows already enqueued at the new tier would fail
--    the narrowed constraint. They are undelivered account notices, so dropping
--    them loses a suspension/ban push — re-notify from the admin dashboard.)
--
-- Adds an 'account' priority tier for driver account-state notices (rejected,
-- suspended, banned). Like 'dispatch' and 'safety' these bypass the user's
-- push_enabled opt-out and fall back to the retry queue, because a driver who
-- can no longer earn must be told why. Unlike those two they are not
-- latency-critical — the tier exists for delivery guarantee, not speed.
--
-- Widening a CHECK constraint is forward-compatible: every existing row already
-- satisfies the new predicate, so this does not rewrite the table and is safe
-- with traffic in flight. Old replicas that don't know the tier keep writing
-- 'dispatch'/'safety'/'normal', which remain valid.

-- Drop by LOOKUP, not by assumed name. Migration 76 declares the CHECK inline
-- on the column, so Postgres auto-generates the name. Hardcoding
-- 'push_retry_queue_priority_check' and using DROP ... IF EXISTS would
-- silently no-op if the name differed in any environment, and the ADD below
-- would then create a SECOND constraint while the old narrow one kept
-- rejecting 'account' — a migration that reports success but does nothing.
-- Dropping every CHECK constraint that references the priority column is
-- name-independent and idempotent on re-run.
DO $$
DECLARE
    con_name text;
BEGIN
    FOR con_name IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public'
          AND t.relname = 'push_retry_queue'
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) ILIKE '%priority%'
    LOOP
        EXECUTE format('ALTER TABLE public.push_retry_queue DROP CONSTRAINT %I', con_name);
        RAISE NOTICE 'dropped priority CHECK constraint %', con_name;
    END LOOP;
END
$$;

ALTER TABLE public.push_retry_queue
    ADD CONSTRAINT push_retry_queue_priority_check
    CHECK (priority IN ('dispatch', 'safety', 'account', 'normal'));

COMMENT ON COLUMN public.push_retry_queue.priority IS
    'Delivery tier. dispatch/safety = latency-critical; account = driver '
    'account-state notice (guaranteed delivery, bypasses push opt-out); '
    'normal = informational, honours the opt-out.';

NOTIFY pgrst, 'reload schema';
