-- 114_rides_scheduled_dispatch_flags.sql
-- Add the two bookkeeping flags the scheduled-ride dispatcher relies on.
--
-- Background:
--   create_ride now parks future-dated bookings in status='scheduled', and
--   utils/scheduled_rides.py flips them to 'searching' at their pickup time
--   while sending a 10-minute reminder. Both the dispatch claim
--   (scheduled_dispatched) and the reminder de-dupe (reminder_sent) write
--   these columns, but they were never added to the schema — so PostgREST
--   would reject the UPDATE with PGRST204 ("column does not exist") and roll
--   the whole claim back, leaving scheduled rides stuck in 'scheduled'
--   forever once their pickup time arrived. This migration closes that gap.
--
-- Type choice:
--   BOOLEAN NOT NULL DEFAULT FALSE for both — every ride starts un-dispatched
--   and un-reminded. A constant default makes this an instant metadata-only
--   change in Postgres 11+ (no table rewrite), safe against in-flight traffic.
--
-- Query pattern:
--   The dispatcher reads candidates via idx_rides_scheduled
--   (is_scheduled, status) and filters scheduled_dispatched / reminder_sent in
--   application code on the already-narrow per-tick result set, so no
--   additional index is warranted.
--
-- Rollback:
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS scheduled_dispatched;
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS reminder_sent;

ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS scheduled_dispatched BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.rides.scheduled_dispatched IS
    'TRUE once the scheduled-ride dispatcher has flipped this ride from scheduled→searching. Set atomically with the status transition in utils/scheduled_rides.py.';

COMMENT ON COLUMN public.rides.reminder_sent IS
    'TRUE once the 10-minute pre-pickup reminder push has been sent for this scheduled ride. De-dupes the reminder across dispatcher ticks/replicas.';
