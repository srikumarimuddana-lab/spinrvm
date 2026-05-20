-- Rollback: DROP TABLE IF EXISTS stripe_disputes;
-- Tracks Stripe chargebacks/disputes linked to rides for admin review.

CREATE TABLE IF NOT EXISTS stripe_disputes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stripe_dispute_id text NOT NULL,
    payment_intent_id text,
    ride_id         text REFERENCES rides(id),
    amount_cents    integer NOT NULL DEFAULT 0 CHECK (amount_cents >= 0),
    reason          text NOT NULL DEFAULT 'unknown',
    status          text NOT NULL DEFAULT 'needs_response',
    stripe_event_id text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stripe_disputes_dispute_id
    ON stripe_disputes (stripe_dispute_id);

CREATE INDEX IF NOT EXISTS idx_stripe_disputes_ride_id
    ON stripe_disputes (ride_id) WHERE ride_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_stripe_disputes_status
    ON stripe_disputes (status) WHERE status NOT IN ('won', 'lost');

-- RLS: admin-only table; service role bypasses.
ALTER TABLE stripe_disputes ENABLE ROW LEVEL SECURITY;

CREATE POLICY stripe_disputes_admin_read ON stripe_disputes
    FOR SELECT USING (
        (current_setting('request.jwt.claims', true)::json->>'role') = 'admin'
    );
