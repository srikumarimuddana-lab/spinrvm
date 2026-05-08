-- Migration 50: PII retention purge (B-P1-6).
-- =============================================================================
-- Closes audit B-P1-6: until now Spinr retained every ride's pickup/dropoff
-- GPS, every per-second driver location ping, every in-app chat message, and
-- every refresh-token row indefinitely. That violates two regulatory regimes:
--
--   1. Saskatchewan Transportation Act + SGI insurance audit — sets *maximum*
--      retention windows (trip records 7y, GPS pickup/dropoff 3y, insurance
--      period transitions 7y). Retaining beyond the maximum is a compliance
--      finding on first audit.
--   2. PIPEDA — requires that PII no longer needed for its stated purpose be
--      destroyed or anonymized. "We never built a purge job" is not a defence.
--
-- Approach: a single SECURITY DEFINER Postgres function `purge_pii_retention`
-- called daily by the backend (utils/retention_purge.py) at ~03:00 UTC. Each
-- step is replay-safe (the second invocation finds zero matching rows or
-- returns the same idempotent result), so running on every replica is fine
-- — the Redis leader lock in the application code is belt-and-braces.
--
-- Rollback plan: the function can be safely DROPped at any time without
-- affecting application code paths (the loop survives a missing function by
-- catching the error and logging). The new columns and indexes are additive.
-- DELETEd rows cannot be recovered short of PITR — that's the point.
--
-- This migration is append-only: future retention-policy adjustments (e.g.
-- adding audit_logs to the purge surface) go in a new migration that calls
-- CREATE OR REPLACE on this function.
--
-- Tables in scope:
--   rides                     — anonymize GPS at 3y, hard-delete at 7y
--   driver_location_history   — hard-delete at 90d
--   ride_messages             — hard-delete at 90d
--   refresh_tokens            — hard-delete at expires_at + 30d
--   stripe_events             — hard-delete at 90d
--
-- Out of scope (handled elsewhere or follow-ups):
--   audit_logs                — retain 7y by policy; separate migration
--   disputes                  — retain 7y by policy; separate migration
--   saved_addresses / emergency_contacts — cascaded with user soft-delete
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 0. Ensure ride_messages exists. In environments bootstrapped without
--    08_complete_schema.sql the table may be absent. IF NOT EXISTS makes this
--    a no-op when the table already exists.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ride_messages (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id     TEXT        NOT NULL,
    sender_id   TEXT        NOT NULL,
    sender_role TEXT        NOT NULL,
    message     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Add anonymization marker on rides so the 3y-step is idempotent.
--    `gps_anonymized_at IS NULL` is the gate; once stamped, the UPDATE is a no-op.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS gps_anonymized_at TIMESTAMPTZ;

COMMENT ON COLUMN rides.gps_anonymized_at IS
    'Set by purge_pii_retention() once pickup/dropoff GPS + polylines are scrubbed (3y). NULL means full GPS still present. Append-only — never reset.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Indexes that the purge function's WHERE clauses need.
--    Without them each daily run does a seq scan; cheap on small tables but
--    becomes a problem as the row count grows.
--
--    Note on CONCURRENTLY: backend/scripts/migrate.py runs each file inside
--    a single transaction, and CREATE INDEX CONCURRENTLY is rejected inside
--    a transaction block. At Spinr's current scale (low-thousands of rows
--    on ride_messages / refresh_tokens, low-tens-of-thousands on
--    stripe_events, partial index bounded on rides), plain CREATE INDEX
--    completes in well under a second per table — the ShareLock window
--    is acceptable. If a future table grows past ~1M rows before the next
--    retention-policy migration, build the index out-of-band via psql
--    `CREATE INDEX CONCURRENTLY` first, then ship the IF NOT EXISTS
--    statement here for replica catch-up.
-- ─────────────────────────────────────────────────────────────────────────────

-- ride_messages.created_at  — purge filter, no existing index covers it
CREATE INDEX IF NOT EXISTS idx_ride_messages_created_at
    ON ride_messages (created_at);

-- refresh_tokens.expires_at — purge filter, also useful for the existing
-- expiry-cleanup paths in routes/auth.py
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at
    ON refresh_tokens (expires_at);

-- rides.gps_anonymized_at  — partial index; only NULL rows are candidates
-- for the 3y step. Once anonymized, the row leaves the index entirely.
CREATE INDEX IF NOT EXISTS idx_rides_gps_unanon
    ON rides (created_at)
    WHERE gps_anonymized_at IS NULL;

-- stripe_events purge uses received_at; the existing partial index
-- idx_stripe_events_unprocessed covers unprocessed rows only. Add a full
-- received_at index for the retention scan.
CREATE INDEX IF NOT EXISTS idx_stripe_events_received_at
    ON stripe_events (received_at);

-- driver_location_history.recorded_at index already exists (migration 08).

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. The purge function.
--    SECURITY DEFINER + pinned search_path so the function executes with
--    the migration owner's privileges and a known schema resolution order
--    (CLAUDE.md rule: "All money-touching functions must be SECURITY DEFINER
--    with explicit search_path pinning"; same logic applies to PII purge).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION purge_pii_retention(p_dry_run BOOLEAN DEFAULT false)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_started_at         TIMESTAMPTZ := now();
    v_rides_anonymized   INTEGER := 0;
    v_rides_deleted      INTEGER := 0;
    v_loc_deleted        INTEGER := 0;
    v_msgs_deleted       INTEGER := 0;
    v_tokens_deleted     INTEGER := 0;
    v_stripe_deleted     INTEGER := 0;
    v_result             JSONB;

    -- Cutoff thresholds — driven by Saskatchewan Transportation Act +
    -- PIPEDA + product policy. Changing these is a compliance event;
    -- run a new migration (CREATE OR REPLACE) and document in
    -- docs/runbooks/data-retention.md, do not edit live.
    c_gps_anon_age       INTERVAL := INTERVAL '3 years';
    c_ride_keep_age      INTERVAL := INTERVAL '7 years';
    c_loc_history_age    INTERVAL := INTERVAL '90 days';
    c_chat_age           INTERVAL := INTERVAL '90 days';
    c_token_grace_age    INTERVAL := INTERVAL '30 days';
    c_stripe_event_age   INTERVAL := INTERVAL '90 days';
BEGIN
    -- Step A: anonymize ride GPS once a ride is older than 3 years but
    -- still inside the 7-year financial-record retention window. We zero
    -- the four lat/lng columns, clear both polyline shapes, and null out
    -- the route_snapshot_url (Cloudinary PNG of pickup/dropoff markers
    -- + route overlay added in migration 41 — visual GPS surface on the
    -- same row). The rest of the row (fare, totals, driver_id, ride_id)
    -- stays intact for tax retention. Note: clearing route_snapshot_url
    -- nulls the DB pointer but does not delete the public Cloudinary
    -- asset itself; that requires a separate Cloudinary admin-API sweep
    -- documented in docs/runbooks/data-retention.md.
    IF NOT p_dry_run THEN
        UPDATE rides
        SET pickup_lat         = NULL,
            pickup_lng         = NULL,
            dropoff_lat        = NULL,
            dropoff_lng        = NULL,
            route_polyline     = '[]'::jsonb,
            phase_polylines    = '{}'::jsonb,
            route_snapshot_url = NULL,
            gps_anonymized_at  = v_started_at
        WHERE created_at < v_started_at - c_gps_anon_age
          AND gps_anonymized_at IS NULL;
        GET DIAGNOSTICS v_rides_anonymized = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_rides_anonymized
        FROM rides
        WHERE created_at < v_started_at - c_gps_anon_age
          AND gps_anonymized_at IS NULL;
    END IF;

    -- Step B: hard-delete rides past the 7-year retention ceiling. By this
    -- point GPS has been NULLed for 4 years and the row is purely a fare
    -- record; deletion is the lawful next step under PIPEDA's "no longer
    -- needed" principle.
    IF NOT p_dry_run THEN
        DELETE FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
        GET DIAGNOSTICS v_rides_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_rides_deleted
        FROM rides
        WHERE created_at < v_started_at - c_ride_keep_age;
    END IF;

    -- Step C: high-frequency driver location pings — these are the real
    -- privacy hazard (per-second GPS for hours of driving, every day). The
    -- 3-year regulatory window applies to *pickup/dropoff* GPS, not the
    -- entire route trace. Keep 90 days for safety/dispute reconciliation
    -- (chargeback windows, SOS investigation), then purge.
    IF NOT p_dry_run THEN
        DELETE FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
        GET DIAGNOSTICS v_loc_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_loc_deleted
        FROM driver_location_history
        WHERE recorded_at < v_started_at - c_loc_history_age;
    END IF;

    -- Step D: in-app ride chat. 90 days covers the chargeback window and
    -- typical dispute lifecycle. Beyond that the message content is rarely
    -- relevant and is pure PII surface.
    IF NOT p_dry_run THEN
        DELETE FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
        GET DIAGNOSTICS v_msgs_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_msgs_deleted
        FROM ride_messages
        WHERE created_at < v_started_at - c_chat_age;
    END IF;

    -- Step E: refresh tokens past their expiry + a 30-day grace window.
    -- Tokens are already invalidated at expires_at; the grace period lets
    -- /auth/logout-all forensics (audit B-P1-13) inspect recently-revoked
    -- sessions before the row vanishes.
    IF NOT p_dry_run THEN
        DELETE FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
        GET DIAGNOSTICS v_tokens_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_tokens_deleted
        FROM refresh_tokens
        WHERE expires_at < v_started_at - c_token_grace_age;
    END IF;

    -- Step F: Stripe webhook idempotency rows. Stripe retains its own
    -- event history for 30 days and never replays beyond that, so the
    -- dedup table only needs to outlive Stripe's replay window. 90 days
    -- gives a comfortable margin for late-investigated payment disputes.
    IF NOT p_dry_run THEN
        DELETE FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
        GET DIAGNOSTICS v_stripe_deleted = ROW_COUNT;
    ELSE
        SELECT COUNT(*) INTO v_stripe_deleted
        FROM stripe_events
        WHERE received_at < v_started_at - c_stripe_event_age;
    END IF;

    v_result := jsonb_build_object(
        'started_at',                 v_started_at,
        'completed_at',               now(),
        'dry_run',                    p_dry_run,
        'rides_anonymized',           v_rides_anonymized,
        'rides_deleted',              v_rides_deleted,
        'driver_location_deleted',    v_loc_deleted,
        'ride_messages_deleted',      v_msgs_deleted,
        'refresh_tokens_deleted',     v_tokens_deleted,
        'stripe_events_deleted',      v_stripe_deleted
    );

    -- Audit trail — one row per run (success or dry-run). Failures raise
    -- and are logged by the caller; the audit_log row is only written
    -- when every step above completed.
    IF NOT p_dry_run THEN
        INSERT INTO audit_logs (actor_id, actor_role, action, resource, details)
        VALUES (
            'system:retention_purge',
            'system',
            'pii_retention_purge',
            'system',
            v_result
        );
    END IF;

    RETURN v_result;
END;
$$;

-- Lock down execution. Only the service role (the backend) may invoke this;
-- anon / authenticated must never trigger a mass-purge.
REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role;

COMMENT ON FUNCTION purge_pii_retention(BOOLEAN) IS
    'B-P1-6 retention enforcement. Anonymizes ride GPS at 3y, deletes rides at 7y, deletes location history / ride chat / stripe events at 90d, deletes expired refresh tokens after a 30d grace period. Idempotent; safe to call from multiple replicas. p_dry_run=true returns counts without mutating. See docs/runbooks/data-retention.md.';

NOTIFY pgrst, 'reload schema';
