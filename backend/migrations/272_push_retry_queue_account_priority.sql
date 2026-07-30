-- 272_push_retry_queue_account_priority.sql
-- Rollback:
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

ALTER TABLE public.push_retry_queue
    DROP CONSTRAINT IF EXISTS push_retry_queue_priority_check;

ALTER TABLE public.push_retry_queue
    ADD CONSTRAINT push_retry_queue_priority_check
    CHECK (priority IN ('dispatch', 'safety', 'account', 'normal'));

COMMENT ON COLUMN public.push_retry_queue.priority IS
    'Delivery tier. dispatch/safety = latency-critical; account = driver '
    'account-state notice (guaranteed delivery, bypasses push opt-out); '
    'normal = informational, honours the opt-out.';

NOTIFY pgrst, 'reload schema';
