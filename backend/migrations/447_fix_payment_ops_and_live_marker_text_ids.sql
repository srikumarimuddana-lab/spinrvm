-- 447: fix-forward for 444 and 445, which typed ride/driver ids as uuid.
-- rides.id and drivers.id are TEXT in production.
--   * 444 declared ride_payment_operations.ride_id uuid REFERENCES rides(id):
--     CREATE TABLE fails with 42804 (uuid vs text) on any fresh database.
--   * 445 declared update_live_driver_marker(p_driver_id uuid, ...): it creates
--     cleanly but every call fails with "operator does not exist: text = uuid",
--     so every driver live-location write fails.
-- Both files are append-only, so they are registered in run_migrations.py's
-- NEVER_APPLY skip-list and their corrected content lives here.
--
-- Production was hot-fixed on 2026-09-23 (text-typed function installed, uuid
-- overload dropped; ride_payment_operations already existed with ride_id text).
-- Every statement below is idempotent, so on production this is a no-op.
--
-- rollback: none needed for the function (the uuid overload can never succeed).
-- ride_payment_operations: drop the table and the additive ride summary columns
-- only after exporting unresolved rows.

ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS refund_id text,
    ADD COLUMN IF NOT EXISTS refund_status text,
    ADD COLUMN IF NOT EXISTS scheduled_notice_fee_amount numeric(12,2),
    ADD COLUMN IF NOT EXISTS scheduled_notice_fee_status text,
    ADD COLUMN IF NOT EXISTS scheduled_notice_fee_payment_intent_id text;

CREATE TABLE IF NOT EXISTS public.ride_payment_operations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_type text NOT NULL CHECK (operation_type IN ('refund', 'authorization_release', 'scheduled_notice_fee')),
    ride_id text NOT NULL REFERENCES public.rides(id) ON DELETE RESTRICT,
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

ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS location_captured_at timestamptz;

-- Drop the broken uuid overload so PostgREST never sees two same-named
-- candidates (PGRST203).
DROP FUNCTION IF EXISTS public.update_live_driver_marker(uuid, timestamptz, jsonb);

CREATE OR REPLACE FUNCTION public.update_live_driver_marker(
    p_driver_id text, p_captured_at timestamptz, p_values jsonb
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    previous_capture timestamptz;
    accept_marker boolean;
BEGIN
    SELECT location_captured_at INTO previous_capture FROM public.drivers
      WHERE id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    accept_marker := p_captured_at IS NOT NULL
      AND p_captured_at BETWEEN clock_timestamp() - interval '60 seconds' AND clock_timestamp() + interval '5 seconds'
      AND (previous_capture IS NULL OR p_captured_at > previous_capture)
      AND p_values ? 'lat' AND p_values ? 'lng'
      AND (p_values->>'lat')::double precision BETWEEN -90 AND 90
      AND (p_values->>'lng')::double precision BETWEEN -180 AND 180
      AND NOT ((p_values->>'lat')::double precision = 0 AND (p_values->>'lng')::double precision = 0);
    -- Insurance accumulator writes are independent of live marker freshness.
    -- Only explicitly allowed fields can be changed by this backend-only RPC.
    UPDATE public.drivers SET
      lat = CASE WHEN accept_marker THEN (p_values->>'lat')::double precision ELSE lat END,
      lng = CASE WHEN accept_marker THEN (p_values->>'lng')::double precision ELSE lng END,
      heading = CASE WHEN accept_marker AND p_values ? 'heading' THEN (p_values->>'heading')::double precision ELSE heading END,
      location_captured_at = CASE WHEN accept_marker THEN p_captured_at ELSE location_captured_at END,
      updated_at = CASE WHEN accept_marker THEN clock_timestamp() ELSE updated_at END,
      period1_accum_km = CASE WHEN p_values ? 'period1_accum_km' THEN (p_values->>'period1_accum_km')::numeric ELSE period1_accum_km END,
      period1_accum_since = CASE WHEN p_values ? 'period1_accum_since' THEN (p_values->>'period1_accum_since')::timestamptz ELSE period1_accum_since END
    WHERE id = p_driver_id AND (accept_marker OR p_values ? 'period1_accum_km' OR p_values ? 'period1_accum_since');
    RETURN coalesce(accept_marker, false);
END;
$$;
REVOKE ALL ON FUNCTION public.update_live_driver_marker(text, timestamptz, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_live_driver_marker(text, timestamptz, jsonb) TO service_role;
