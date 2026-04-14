-- Migration 22: Stripe webhook event dedup table
--
-- Context: production-readiness audit 2026-04, finding P0-B2.
-- Stripe retries webhooks on any non-2xx reply or response >20s. Previously
-- the webhook handler processed events inline with no dedup, so every retry
-- produced a second "payment confirmed" update, a second wallet credit, a
-- second subscription activation, and duplicate push notifications.
--
-- This table gives us an atomic "claim" primitive: INSERT with the Stripe
-- event.id as the PK will raise 23505 unique_violation on retry, which the
-- handler treats as "already processed" and returns 200 OK.
--
-- processed_at is stamped after the business logic finishes. Rows with
-- received_at older than ~5 minutes but processed_at = NULL indicate events
-- that crashed mid-processing; a nightly reconciliation job should replay
-- them (Stripe also keeps event history for 30 days).

CREATE TABLE IF NOT EXISTS stripe_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    payload      JSONB NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

-- Reconciliation: "unprocessed and older than X" scan.
CREATE INDEX IF NOT EXISTS idx_stripe_events_unprocessed
    ON stripe_events (received_at)
    WHERE processed_at IS NULL;

-- Type rollup for observability dashboards.
CREATE INDEX IF NOT EXISTS idx_stripe_events_type_received
    ON stripe_events (event_type, received_at DESC);

-- RLS: only the service role (used by the backend) ever reads/writes this
-- table. Keep RLS on as a defense-in-depth measure — no policies means no
-- access for anon / authenticated roles.
ALTER TABLE stripe_events ENABLE ROW LEVEL SECURITY;
