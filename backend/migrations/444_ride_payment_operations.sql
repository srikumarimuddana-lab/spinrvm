-- Durable provider operation state for refunds, authorization releases, and
-- scheduled notice fees.
-- rollback: drop ride_payment_operations and the
-- additive ride summary columns below; first retain/export unresolved rows.
ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS refund_id text,
    ADD COLUMN IF NOT EXISTS refund_status text,
    ADD COLUMN IF NOT EXISTS scheduled_notice_fee_amount numeric(12,2),
    ADD COLUMN IF NOT EXISTS scheduled_notice_fee_status text,
    ADD COLUMN IF NOT EXISTS scheduled_notice_fee_payment_intent_id text;

CREATE TABLE IF NOT EXISTS public.ride_payment_operations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_type text NOT NULL CHECK (operation_type IN ('refund', 'authorization_release', 'scheduled_notice_fee')),
    ride_id uuid NOT NULL REFERENCES public.rides(id) ON DELETE RESTRICT,
    idempotency_key text NOT NULL UNIQUE,
    payment_intent_id text,
    provider_object_id text UNIQUE,
    amount_cents bigint NOT NULL DEFAULT 0 CHECK (amount_cents >= 0),
    collected_cents bigint NOT NULL DEFAULT 0 CHECK (collected_cents >= 0),
    payment_method text,
    status text NOT NULL CHECK (status IN ('requested', 'pending', 'succeeded', 'failed', 'canceled', 'requires_action', 'processing', 'exhausted')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz,
    last_error text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.ride_payment_operations ENABLE ROW LEVEL SECURITY;
-- service-role-only: intentionally no client policies; backend service_role bypasses RLS.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ride_payment_operations TO service_role;
CREATE INDEX IF NOT EXISTS idx_ride_payment_operations_due
    ON public.ride_payment_operations (next_attempt_at, created_at)
    WHERE status IN ('requested', 'pending', 'failed', 'processing');
CREATE INDEX IF NOT EXISTS idx_ride_payment_operations_ride
    ON public.ride_payment_operations (ride_id, created_at DESC);

COMMENT ON TABLE public.ride_payment_operations IS
    'Backend-only durable Stripe/wallet operation lifecycle; unresolved work is recoverable by the payment reconciler.';
