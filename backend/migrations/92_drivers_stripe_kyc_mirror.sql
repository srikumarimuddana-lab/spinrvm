-- 92_drivers_stripe_kyc_mirror.sql
-- Cache the verification + tax-identity status that Stripe Connect Express
-- captures during the driver-app onboarding flow, so the admin slideout's
-- Payouts tab can read it without an extra Stripe API round-trip on every
-- view. SIN itself is NEVER stored — Stripe Express holds it; we mirror
-- only `id_number_provided` (boolean) and `id_number_last4` (4 chars). The
-- legal-name / DOB / address fields likewise stay on Stripe's side.
--
-- Source of truth for these columns is Stripe. They get refreshed by:
--   1. `account.updated` webhook (routes/webhooks.py)
--   2. Admin "Refresh from Stripe" button (routes/admin/drivers.py)
--   3. Nightly reconciliation loop (future, not in this migration)
--
-- The existing `stripe_account_onboarded` boolean (set optimistically the
-- moment AccountLink.create() runs) is preserved but no longer the gate.
-- Real gate is now `stripe_details_submitted AND stripe_payouts_enabled`,
-- which only flips after Stripe confirms KYC is complete.
--
-- Rollback (manual):
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_details_submitted;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_charges_enabled;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_payouts_enabled;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_id_number_provided;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_id_number_last4;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_requirements_due;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_requirements_past_due;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_disabled_reason;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_verification_status;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_business_type;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS gst_hst_number;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_tos_accepted_at;
--   ALTER TABLE drivers DROP COLUMN IF EXISTS stripe_last_synced_at;

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS stripe_details_submitted    BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS stripe_charges_enabled      BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS stripe_payouts_enabled      BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS stripe_id_number_provided   BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS stripe_id_number_last4      TEXT,
    ADD COLUMN IF NOT EXISTS stripe_requirements_due     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS stripe_requirements_past_due JSONB      NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS stripe_disabled_reason      TEXT,
    ADD COLUMN IF NOT EXISTS stripe_verification_status  TEXT,
    ADD COLUMN IF NOT EXISTS stripe_business_type        TEXT,
    ADD COLUMN IF NOT EXISTS gst_hst_number              TEXT,
    ADD COLUMN IF NOT EXISTS stripe_tos_accepted_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS stripe_last_synced_at       TIMESTAMPTZ;

-- Constraint: SIN last-4 must be exactly 4 digits when present. Guards
-- against the wrong field being persisted (e.g. full SIN by mistake).
ALTER TABLE public.drivers
    DROP CONSTRAINT IF EXISTS drivers_stripe_id_number_last4_format,
    ADD  CONSTRAINT drivers_stripe_id_number_last4_format
        CHECK (stripe_id_number_last4 IS NULL OR stripe_id_number_last4 ~ '^\d{4}$');

-- Verification status enum-ish: keep it TEXT for forward-compatibility
-- with new Stripe states, but constrain to known values when set.
ALTER TABLE public.drivers
    DROP CONSTRAINT IF EXISTS drivers_stripe_verification_status_check,
    ADD  CONSTRAINT drivers_stripe_verification_status_check
        CHECK (stripe_verification_status IS NULL OR stripe_verification_status IN
            ('unverified', 'pending', 'verified', 'unrequested'));

-- Query patterns:
--   admin payouts dashboard: WHERE stripe_payouts_enabled = false (find
--                              drivers blocked from receiving payouts)
--   reconciliation loop:     WHERE stripe_last_synced_at < now() - 24h
-- Index the boolean filter only when payouts_enabled = false — the rest
-- of the fleet doesn't need the index pressure.
CREATE INDEX IF NOT EXISTS idx_drivers_stripe_payouts_blocked
    ON public.drivers (id)
    WHERE stripe_payouts_enabled = FALSE AND stripe_account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_drivers_stripe_last_synced_at
    ON public.drivers (stripe_last_synced_at)
    WHERE stripe_account_id IS NOT NULL;

COMMENT ON COLUMN public.drivers.stripe_details_submitted IS
    'Driver completed Stripe Express onboarding (details_submitted=true). True gate for payouts, not the legacy stripe_account_onboarded flag which was set optimistically.';
COMMENT ON COLUMN public.drivers.stripe_id_number_provided IS
    'Stripe has the driver SIN on file. Never store the SIN itself — Stripe Express holds it.';
COMMENT ON COLUMN public.drivers.stripe_id_number_last4 IS
    'Last 4 digits of SIN as reported by Stripe. Used for display + audit cross-reference. The full SIN is never persisted on our side.';
COMMENT ON COLUMN public.drivers.stripe_requirements_due IS
    'JSONB array of Stripe requirement keys still owed (e.g. ["individual.id_number", "tos_acceptance.date"]). Drives the "Action required" UI on the Payouts tab.';
COMMENT ON COLUMN public.drivers.gst_hst_number IS
    'Driver GST/HST registration number (Canadian sole-proprietor, format 123456789RT0001). Captured by Stripe via business_profile.tax_id and mirrored here for admin convenience.';
