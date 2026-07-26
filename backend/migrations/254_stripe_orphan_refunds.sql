-- Rollback: DROP TABLE IF EXISTS stripe_orphan_refunds;
-- Captures charge.refunded events that cannot be linked to a ride — either
-- the charge has no payment_intent (wallet top-up refunds, manual Stripe
-- Dashboard refunds) or the payment_intent doesn't match any rides row.
-- Without this table, those refunds silently vanish from the books.

CREATE TABLE IF NOT EXISTS stripe_orphan_refunds (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stripe_charge_id  text NOT NULL,
    payment_intent_id text,
    amount_refunded_cents integer NOT NULL DEFAULT 0 CHECK (amount_refunded_cents >= 0),
    currency          text NOT NULL DEFAULT 'cad',
    reason            text NOT NULL DEFAULT 'unlinked',
    stripe_event_id   text,
    raw_metadata      jsonb,
    resolved_at       timestamptz,
    resolved_by       text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stripe_orphan_refunds_unresolved
    ON stripe_orphan_refunds (created_at)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_stripe_orphan_refunds_pi
    ON stripe_orphan_refunds (payment_intent_id)
    WHERE payment_intent_id IS NOT NULL;

-- RLS: admin-only table; service role bypasses.
ALTER TABLE stripe_orphan_refunds ENABLE ROW LEVEL SECURITY;

CREATE POLICY stripe_orphan_refunds_admin_read ON stripe_orphan_refunds
    FOR SELECT USING (
        (current_setting('request.jwt.claims', true)::json->>'role') IN
        ('admin', 'super_admin', 'finance')
    );
