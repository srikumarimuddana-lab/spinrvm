-- 315_safety_incidents_idempotency_key.sql
--
-- Purpose: make POST /rides/{ride_id}/emergency idempotent.
--
-- Problem: the SOS endpoint had no idempotency guard at all, unlike the
-- Stripe webhook path which claims `stripe_events` before processing. The
-- client retries the alert (SOSButton: 3 attempts, 1s/2s backoff) precisely
-- because SOS must survive a flaky connection — but a request that lands
-- server-side and whose *response* is lost looks identical to a request that
-- never arrived. Each retry therefore inserted another safety_incidents row
-- AND re-sent the "URGENT" SMS to every emergency contact. One press could
-- produce several duplicate incidents in the admin safety queue and repeat
-- alarming messages to a rider's family.
--
-- (A companion commit removed a second, nested retry ladder in the rider app
-- that multiplied this to as many as 9 POSTs per press. This column closes
-- the remaining single-ladder window, which no client-side change can.)
--
-- Design: additive nullable column + partial UNIQUE index. Nullable so every
-- existing row and any older client that doesn't send a key still works
-- unchanged — the endpoint falls back to today's insert-always behavior when
-- the key is absent, so this is a pure widening with no dual-read window
-- needed. The index is partial (WHERE NOT NULL) so the legacy NULL rows do
-- not collide with each other.
--
-- Scope is per-reporter, not global: two different people on the same ride
-- pressing SOS must both be recorded, so uniqueness is on
-- (reported_by_user_id, sos_idempotency_key) rather than the key alone. The
-- client generates one key per *press*, not per attempt.
--
-- Rollback:
--   DROP INDEX IF EXISTS public.idx_safety_incidents_sos_idem;
--   ALTER TABLE public.safety_incidents DROP COLUMN IF EXISTS sos_idempotency_key;
--   Safe at any time: the column is nullable, nothing joins on it, and the
--   endpoint treats a missing key as "no dedup" (pre-migration behavior).

ALTER TABLE public.safety_incidents
    ADD COLUMN IF NOT EXISTS sos_idempotency_key TEXT;

-- Enforces the dedup at the DB level, not just in application code: two
-- replicas can process a client's retry concurrently, so the app-level
-- lookup below is best-effort and this index is the real guarantee.
CREATE UNIQUE INDEX IF NOT EXISTS idx_safety_incidents_sos_idem
    ON public.safety_incidents (reported_by_user_id, sos_idempotency_key)
    WHERE sos_idempotency_key IS NOT NULL;

COMMENT ON COLUMN public.safety_incidents.sos_idempotency_key IS
    'Client-generated key, one per SOS button press (not per retry attempt). '
    'Lets POST /rides/{id}/emergency return the original incident instead of '
    'inserting a duplicate and re-sending emergency-contact SMS when a retry '
    'follows a lost response. NULL for rows created before migration 315 and '
    'for clients that do not send one.';
