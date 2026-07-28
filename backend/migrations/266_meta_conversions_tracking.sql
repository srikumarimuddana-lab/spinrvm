-- Meta (Facebook) Conversions API tracking support.
--
-- Purely additive — no existing column changes meaning, no existing row is
-- rewritten, and nothing here is read by any pre-existing code path. Safe to
-- apply against live traffic.
--
-- rollback:
--   ALTER TABLE users DROP COLUMN IF EXISTS first_ride_completed_at;
--   DROP TABLE IF EXISTS public.meta_capi_deliveries;
--   ALTER TABLE settings DROP COLUMN IF EXISTS meta_rider_dataset_id;
--   ALTER TABLE settings DROP COLUMN IF EXISTS meta_driver_dataset_id;
--   ALTER TABLE settings DROP COLUMN IF EXISTS meta_capi_access_token;
--   ALTER TABLE settings DROP COLUMN IF EXISTS meta_test_event_code;
--   ALTER TABLE marketing_preferences DROP COLUMN IF EXISTS ad_attribution_opt_in;
--   (marketing_consent_events channel CHECK: restore the 3-value form)
-- Rolling back is safe at any time: nothing outside this feature reads any of
-- it, and the send path treats missing config as "tracking disabled".

-- ── 1. First-ride marker ────────────────────────────────────────────────────
--
-- Single source of truth for "has this rider ever completed a paid ride?".
-- FirstRide must fire exactly once per user, ever; client state cannot
-- guarantee that (reinstall, multi-device, retry-after-timeout all double-fire),
-- so the gate is an atomic conditional UPDATE:
--
--     UPDATE users SET first_ride_completed_at = now()
--      WHERE id = :user_id AND first_ride_completed_at IS NULL
--
-- Exactly one concurrent caller gets a row back. NULL means "no paid ride yet",
-- the correct reading for every pre-existing row: riders who completed rides
-- before this migration fire FirstRide on their next paid ride. Intentional —
-- backfilling from the rides table would emit a burst of historical
-- conversions stamped with today's timestamps, corrupting attribution windows.

ALTER TABLE users ADD COLUMN IF NOT EXISTS first_ride_completed_at TIMESTAMPTZ;

-- Partial index on the un-fired set only. The claim always filters
-- `first_ride_completed_at IS NULL`, and that set shrinks as riders convert, so
-- a partial index stays tiny where a full index would grow with the table.
CREATE INDEX IF NOT EXISTS idx_users_first_ride_pending
    ON users (id)
    WHERE first_ride_completed_at IS NULL;

-- ── 2. Meta credentials on the settings row ─────────────────────────────────
--
-- settings is a flat-column table (one row, id='app_settings'). The admin
-- settings endpoint persists these as real columns, so they MUST exist here —
-- without them every save raises PostgREST's missing-column error (PGRST204)
-- and Meta could never be configured at all. Same pattern as migrations 93/95.
--
-- Empty string = tracking disabled, which is the default and is fully
-- supported: no events are sent and no once-per-subject gate is consumed.

ALTER TABLE settings ADD COLUMN IF NOT EXISTS meta_rider_dataset_id  TEXT NOT NULL DEFAULT '';
ALTER TABLE settings ADD COLUMN IF NOT EXISTS meta_driver_dataset_id TEXT NOT NULL DEFAULT '';
ALTER TABLE settings ADD COLUMN IF NOT EXISTS meta_capi_access_token TEXT NOT NULL DEFAULT '';
ALTER TABLE settings ADD COLUMN IF NOT EXISTS meta_test_event_code   TEXT NOT NULL DEFAULT '';

-- ── 3. Ad-attribution consent (PIPEDA) ──────────────────────────────────────
--
-- Sending hashed identity to Meta for advertising attribution is a distinct
-- purpose from transactional messaging and from the marketing channels in
-- migration 190. It therefore needs its own recorded consent rather than
-- riding on an existing opt-in.
--
-- Default FALSE, matching migration 190's stance: silence is never consent.
-- Existing users are non-consenting until they actively opt in, so enabling
-- the access token cannot retroactively export the identity of anyone who
-- never accepted this purpose.

ALTER TABLE marketing_preferences
    ADD COLUMN IF NOT EXISTS ad_attribution_opt_in BOOLEAN NOT NULL DEFAULT FALSE;

-- Extend the consent-audit channel CHECK so opt-ins for this purpose are
-- provable in the same append-only evidentiary record as the CASL channels.
ALTER TABLE marketing_consent_events
    DROP CONSTRAINT IF EXISTS marketing_consent_events_channel_check;
ALTER TABLE marketing_consent_events
    ADD CONSTRAINT marketing_consent_events_channel_check
    CHECK (channel IN ('email', 'sms', 'push', 'ad_attribution'));

-- Index for the per-user consent read on the conversion send path.
CREATE INDEX IF NOT EXISTS idx_marketing_prefs_ad_attribution
    ON marketing_preferences (user_id)
    WHERE ad_attribution_opt_in = TRUE;

-- ── 4. Delivery state for backend-only events ───────────────────────────────
--
-- NOT named *_events on purpose. Repo convention reserves `<entity>_events`
-- for append-only audit tables, and this table is deliberately MUTABLE: a row
-- is claimed 'pending', then updated in place to 'sent'/'failed' as delivery
-- resolves. Calling it an events table would promise history it does not keep.
--
-- Backend-only events (DriverApproved, DriverActivated) have no client
-- counterpart to inherit an event_id from, so the id is generated and persisted
-- BEFORE the send. A retry re-reads the stored event_id instead of minting a
-- new one, so Meta de-duplicates the retry rather than counting it twice.

CREATE TABLE IF NOT EXISTS public.meta_capi_deliveries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Idempotency key, shape "<EventName>:<sha256(subject_id)>". The subject id
    -- is HASHED, never raw: a raw driver UUID here would survive that driver's
    -- PIPEDA deletion and leave marketing approval/activation metadata linkable
    -- to a retained identifier, for data with no Transportation Act retention
    -- purpose. Hashing keeps idempotency exact (same input, same key) while
    -- making the row non-linkable once the driver row is gone.
    dedup_key     TEXT        NOT NULL UNIQUE,
    -- The event_id sent to Meta. Meta de-duplicates on (event_name, event_id),
    -- so a retry MUST reuse this value.
    event_id      UUID        NOT NULL,
    event_name    TEXT        NOT NULL,
    -- Meta-side identifier configured in app_settings, not a row we own.
    dataset_id    TEXT,
    status        TEXT        NOT NULL DEFAULT 'pending',
    attempts      INTEGER     NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at       TIMESTAMPTZ
);

COMMENT ON TABLE public.meta_capi_deliveries IS
    'Mutable delivery state for backend-only Meta conversion events. Rows are updated in place as delivery resolves — this is NOT an append-only audit table. dedup_key holds a hashed subject id so no row is linkable to a deleted driver.';

-- Operator query: "what failed and needs a retry sweep?"
CREATE INDEX IF NOT EXISTS idx_meta_capi_deliveries_status
    ON public.meta_capi_deliveries (status, created_at)
    WHERE status <> 'sent';

ALTER TABLE public.meta_capi_deliveries ENABLE ROW LEVEL SECURITY;

-- Service role (backend) only. Written exclusively by the server-side
-- conversions sender and read only by operators through the backend. No rider,
-- driver, or admin JWT has any reason to touch it, so no `authenticated` policy
-- is granted — with RLS on and no matching policy, every non-service-role
-- request returns zero rows by default.
CREATE POLICY "Service role bypass meta_capi_deliveries"
    ON public.meta_capi_deliveries FOR ALL TO service_role
    USING (true)
    WITH CHECK (true);
