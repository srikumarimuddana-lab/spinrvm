-- Live coordinates have an independent sensor timestamp. Apply before backend rollout.
-- Rollback: disable rider fanout in settings, roll back backend, then DROP FUNCTION
-- public.update_live_driver_marker(uuid,timestamptz,jsonb); retain timestamp for audit.
ALTER TABLE public.drivers ADD COLUMN IF NOT EXISTS location_captured_at timestamptz;

CREATE OR REPLACE FUNCTION public.update_live_driver_marker(
    p_driver_id uuid, p_captured_at timestamptz, p_values jsonb
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
REVOKE ALL ON FUNCTION public.update_live_driver_marker(uuid,timestamptz,jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_live_driver_marker(uuid,timestamptz,jsonb) TO service_role;
