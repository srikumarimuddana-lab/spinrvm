-- 110_drivers_device_attestation.sql
-- Add device attestation columns to drivers so _record_attestation()
-- in utils/device_attestation.py can store trust-tier results without
-- hitting PGRST204 (column not found).
--
-- Rollback:
--   ALTER TABLE public.drivers
--       DROP COLUMN IF EXISTS device_risk_tier,
--       DROP COLUMN IF EXISTS device_attested_at,
--       DROP COLUMN IF EXISTS device_risk_reason;
--   DROP INDEX IF EXISTS idx_drivers_device_risk_tier;

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS device_risk_tier    TEXT        DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS device_attested_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS device_risk_reason  TEXT;

-- Allows admin dashboard to filter/count by risk tier efficiently.
CREATE INDEX IF NOT EXISTS idx_drivers_device_risk_tier
    ON public.drivers (device_risk_tier)
    WHERE device_risk_tier IS NOT NULL;

COMMENT ON COLUMN public.drivers.device_risk_tier IS
    'Device trust level from attestation: trusted | elevated | untrusted | unknown';
COMMENT ON COLUMN public.drivers.device_attested_at IS
    'Timestamp of most recent successful device attestation check';
COMMENT ON COLUMN public.drivers.device_risk_reason IS
    'Human-readable reason code when risk_tier is elevated or untrusted';
