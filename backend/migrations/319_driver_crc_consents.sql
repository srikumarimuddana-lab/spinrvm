-- 319_driver_crc_consents.sql
--
-- Purpose-built PIPEDA consent tracking for the Criminal Record Check (CRC)
-- and Vulnerable Sector Check (VSC) collected at driver onboarding and
-- renewed at least annually (CLAUDE.md "Driver eligibility"). Criminal-record
-- information is a sensitive category under PIPEDA; relying on the general
-- Privacy Policy's blanket consent for this specific collection is weaker
-- practice than capturing an explicit, separate, versioned consent at the
-- moment the check is authorized — see docs/legal/background-check-consent.md
-- for the actual consent text (served through legal_documents,
-- audience='driver', doc_type='background-check-consent').
--
-- Mirrors migration 190 (marketing_consents)'s two-table shape:
--
--   • driver_crc_consents — current consent state, one row per driver.
--   • driver_crc_consent_events — append-only audit of every consent given,
--     with the legal_documents version the driver actually saw and agreed
--     to. This is the evidentiary record if a background-check consent is
--     ever challenged.
--
-- No FOREIGN KEY on driver_id (deliberate, same reasoning as migration 190):
-- driver_crc_consent_events is a PIPEDA evidentiary record that must SURVIVE
-- a driver account deletion — a deleted driver's prior consent may still
-- need to be produced. Since the only driver-linked column is driver_id (no
-- other PII), retaining orphaned rows is PIPEDA-safe.
--
-- RLS: both tables hold driver-linked data → RLS ENABLED with NO policies,
-- denying all anon/authenticated access; only the backend service role
-- (which bypasses RLS by design) reads/writes them.
--
-- Forward-compatible: pure CREATE TABLE / CREATE INDEX, safe under live
-- traffic. No money columns.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.driver_crc_consent_events;
--   DROP TABLE IF EXISTS public.driver_crc_consents;

-- ── Current consent state ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.driver_crc_consents (
    driver_id        UUID        PRIMARY KEY,
    consented        BOOLEAN     NOT NULL DEFAULT false,
    -- Version of the consent language (legal_documents.version for
    -- audience='driver', doc_type='background-check-consent') the driver
    -- most recently agreed to.
    consent_version  INTEGER,
    consented_at     TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.driver_crc_consents ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.driver_crc_consents IS
    'Current CRC/VSC background-check consent state per driver. One row per driver_id. Consent must be renewed (re-confirmed) whenever the consent_version served differs from the version on file — see services/driver_crc_consent.py. Service-role-only (RLS enabled, no policies).';

-- ── Append-only consent audit (PIPEDA proof-of-consent) ────────────────────
CREATE TABLE IF NOT EXISTS public.driver_crc_consent_events (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id        UUID        NOT NULL,
    action           TEXT        NOT NULL CHECK (action IN ('consent', 'renew')),
    consent_version  INTEGER,
    source           TEXT        NOT NULL CHECK (source IN ('driver_app', 'admin')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "Show me this driver's consent history, newest first" — the PIPEDA
-- evidence query, same shape as marketing_consent_events_user_idx.
CREATE INDEX IF NOT EXISTS driver_crc_consent_events_driver_idx
    ON public.driver_crc_consent_events (driver_id, created_at DESC);

ALTER TABLE public.driver_crc_consent_events ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.driver_crc_consent_events IS
    'Append-only CRC/VSC consent audit: every consent/renew action with the legal_documents consent_version in effect at the time. Never UPDATE/DELETE. Service-role-only (RLS enabled, no policies).';
