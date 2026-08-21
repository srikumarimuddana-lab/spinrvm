-- 358_sos_contact_suppressions.sql
--
-- PIA finding R-002 (docs/audit/2026-08-21-emergency-contact-pia-memo.md):
-- SOS SMS is sent to a rider's emergency contacts — third parties who never
-- consented to receive it and had no opt-out. This migration adds the
-- suppression list backing a STOP-keyword opt-out for those contacts,
-- structurally mirroring the CASL marketing-suppression pattern
-- (marketing_suppressions, migration 191) but NOT reusing that table: it is
-- keyed on Spinr user_id, and an emergency contact is a third party with no
-- user_id at all. This table is keyed on phone only.
--
-- Unlike marketing_suppressions this is NOT a marketing-consent record — SOS
-- is a safety notification, not a CEM, so CASL express-consent rules don't
-- apply here. This is a plain opt-out/do-not-contact list satisfying the PIA
-- remediation, looked up on the SOS send path (safety-critical, low-latency —
-- see root CLAUDE.md Performance SLA notes). The service that reads this
-- table (backend/services/sos_contact_consent.py) is deliberately FAIL-OPEN:
-- a lookup error must never block a real emergency alert.
--
-- PIPEDA: phone is stored in PLAINTEXT for the same reason as
-- marketing_suppressions.target / email_suppressions.email — the suppression
-- check is on the hot path of every SOS send and needs an exact-match index;
-- vault encryption is non-deterministic and can't be indexed for equality.
--
-- RLS: ENABLED with NO policies — service-role-only (backend), matching every
-- other backend-only table in this repo (see marketing_suppressions,
-- migration 191, cited above as the template). Anon/authenticated locked out.
--
-- Un-suppression (STOP → later START/re-opt-in): matches
-- marketing_suppressions' actual behaviour, which is a hard DELETE of the row
-- (see backend/routes/admin/messaging.py admin_delete_marketing_suppression),
-- NOT an append-only audit table — this is a live "currently suppressed?"
-- list, not an evidentiary record, so removing the row on re-opt-in is
-- correct and matches the existing pattern exactly.
--
-- Forward-compatible: pure CREATE TABLE / CREATE INDEX / ALTER TABLE ADD
-- COLUMN (nullable, no default backfill needed), safe under live traffic. No
-- money columns.
--
-- Rollback:
--   ALTER TABLE public.emergency_contacts DROP COLUMN IF EXISTS consent_notice_sent_at;
--   DROP TABLE IF EXISTS public.sos_contact_suppressions;

CREATE TABLE IF NOT EXISTS public.sos_contact_suppressions (
    id             TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    -- Normalized E.164 phone. Plaintext by design (see note above). Callers
    -- MUST normalize (services.sos_contact_consent.normalize_phone) before
    -- insert/lookup.
    phone          TEXT        NOT NULL,
    suppressed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason         TEXT,
    source         TEXT
);

-- SOS send path looks this table up by phone on every emergency alert —
-- must be a fast exact-match lookup. Unique also enforces one active
-- suppression row per phone (idempotent STOP replays are safe: the service
-- checks-before-insert / upserts rather than relying on a DB error).
CREATE UNIQUE INDEX IF NOT EXISTS sos_contact_suppressions_phone_key
    ON public.sos_contact_suppressions (phone);

ALTER TABLE public.sos_contact_suppressions ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.sos_contact_suppressions IS
    'STOP-keyword opt-out list for SOS emergency-contact SMS (PIA finding R-002). Third-party phone numbers, not Spinr users — keyed on phone, not user_id. Looked up on every SOS send; the reading service (services/sos_contact_consent.py) is fail-open on error. Service-role-only (RLS enabled, no policies).';

-- Tracks whether the one-time opt-out notice SMS has been sent for this
-- specific emergency-contact row. Set on contact creation by a separate
-- subtask; nullable (existing rows have never had the notice sent).
ALTER TABLE public.emergency_contacts
    ADD COLUMN IF NOT EXISTS consent_notice_sent_at TIMESTAMPTZ;

COMMENT ON COLUMN public.emergency_contacts.consent_notice_sent_at IS
    'Timestamp the one-time SOS opt-out notice SMS was sent to this contact, or NULL if not yet sent (PIA finding R-002).';
