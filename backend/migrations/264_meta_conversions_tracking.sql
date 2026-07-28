-- Meta (Facebook) Conversions API tracking support.
--
-- Two additions, both purely additive — no existing column changes meaning,
-- no existing row is rewritten, and nothing here is read by any pre-existing
-- code path. Safe to apply against live traffic.
--
--  1. users.first_ride_completed_at — the single source of truth for the
--     "has this rider ever completed a paid ride?" question. The FirstRide
--     conversion event must fire exactly once per user, ever. Client state
--     cannot guarantee that (reinstall, multi-device, retry-after-timeout all
--     double-fire), so the gate is an atomic conditional UPDATE:
--
--         UPDATE users SET first_ride_completed_at = now()
--          WHERE id = :user_id AND first_ride_completed_at IS NULL
--
--     Exactly one concurrent caller gets a row back; everyone else gets zero
--     rows and does not fire. NULL means "no paid ride yet", which is the
--     correct reading for every pre-existing row — riders who completed rides
--     before this migration are treated as not-yet-first-ride and will fire a
--     FirstRide on their next paid ride. That is intentional and accepted:
--     backfilling from the rides table would emit a burst of historical
--     conversions into Meta with today's timestamps, which is worse (it
--     corrupts attribution windows and campaign optimisation) than a small
--     one-time over-count on genuinely-active riders.
--
--  2. meta_capi_events — append-only send log. Backend-only events
--     (DriverApproved, DriverActivated) have no client counterpart to inherit
--     an event_id from, so the id is generated here and persisted BEFORE the
--     send. A retry re-reads the stored event_id instead of minting a new one,
--     so Meta de-duplicates the retry against the original attempt rather than
--     counting it twice. dedup_key is the idempotency key.
--
-- rollback:
--   ALTER TABLE users DROP COLUMN IF EXISTS first_ride_completed_at;
--   DROP TABLE IF EXISTS public.meta_capi_events;
-- Rolling back is safe at any time: nothing else reads either object, and the
-- send path treats a missing table as "tracking disabled" (logged, non-fatal).

-- ── 1. First-ride marker ────────────────────────────────────────────────────

ALTER TABLE users ADD COLUMN IF NOT EXISTS first_ride_completed_at TIMESTAMPTZ;

-- Partial index on the un-fired set only. The FirstRide claim always filters
-- `first_ride_completed_at IS NULL`, and that set shrinks to near-nothing as
-- riders convert, so a partial index stays tiny forever where a full index
-- would grow with the whole users table.
CREATE INDEX IF NOT EXISTS idx_users_first_ride_pending
    ON users (id)
    WHERE first_ride_completed_at IS NULL;

-- ── 2. Append-only CAPI send log ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.meta_capi_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Idempotency key. Shape: "<EventName>:<subject_id>" e.g.
    -- "DriverApproved:3f2a...". UNIQUE — a second insert attempt for the same
    -- logical event conflicts, which is how the send path detects "already
    -- handled" without a read-then-write race.
    dedup_key     TEXT        NOT NULL UNIQUE,
    -- The event_id sent to Meta. Meta de-duplicates on (event_name, event_id),
    -- so a retry MUST reuse this value rather than generating a fresh one.
    event_id      UUID        NOT NULL,
    event_name    TEXT        NOT NULL,
    -- Which app dataset this went to. Not a FK — dataset ids are Meta-side
    -- identifiers configured in app_settings, not rows we own.
    dataset_id    TEXT,
    -- 'pending' | 'sent' | 'failed'. Never deleted; a failed row is retried in
    -- place by bumping attempts, so the event_id above is preserved.
    status        TEXT        NOT NULL DEFAULT 'pending',
    attempts      INTEGER     NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at       TIMESTAMPTZ
);

-- Operator query: "what failed and needs a retry sweep?"
CREATE INDEX IF NOT EXISTS idx_meta_capi_events_status
    ON public.meta_capi_events (status, created_at)
    WHERE status <> 'sent';

ALTER TABLE public.meta_capi_events ENABLE ROW LEVEL SECURITY;

-- Service role (backend) only. This table is written exclusively by the
-- server-side conversions sender and read only by operators through the
-- backend. No rider, driver, or admin JWT has any reason to touch it, so no
-- `authenticated` policy is granted — with RLS on and no matching policy,
-- every non-service-role request returns zero rows by default.
CREATE POLICY "Service role bypass meta_capi_events"
    ON public.meta_capi_events FOR ALL TO service_role
    USING (true)
    WITH CHECK (true);
