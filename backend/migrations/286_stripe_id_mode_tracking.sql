-- 286_stripe_id_mode_tracking.sql
-- Purpose: record WHICH Stripe mode (test vs live) each stored Stripe identity
--          belongs to, and preserve any identity we supersede.
--
--          Stripe object IDs are mode-scoped: a `cus_…` minted with an
--          `sk_test_…` key does not exist under `sk_live_…`, and vice versa.
--          The prefix is identical in both modes, so an ID alone carries no
--          evidence of its mode. Until now nothing recorded it, so flipping
--          app_settings.stripe_secret_key from test to live left every stored
--          users.stripe_customer_id / drivers.stripe_account_id /
--          corporate_accounts.stripe_customer_id pointing at an object the new
--          key cannot see. Every downstream Stripe call then fails
--          `resource_missing` — riders' saved cards 502, driver payout
--          onboarding cannot start, corporate auto-topup errors.
--
--          These columns let the backend detect the mismatch WITHOUT a Stripe
--          round-trip on the hot path, and let admin enumerate exactly who is
--          affected.
--
-- Additive only. No backfill: the mode of a pre-existing ID is genuinely
-- unknowable from the value, so existing rows keep mode = NULL, which the
-- application treats as "unverified" (verify against Stripe on use, then
-- stamp). Never guess a mode for an existing row.
--
-- Rollback (safe — nothing reads these columns when the feature flag
-- `stripe_reprovision_stale_ids` is off, and no existing column is modified):
--   ALTER TABLE public.users
--     DROP COLUMN IF EXISTS stripe_customer_id_mode,
--     DROP COLUMN IF EXISTS stripe_customer_id_superseded,
--     DROP COLUMN IF EXISTS stripe_customer_id_superseded_at;
--   ALTER TABLE public.drivers
--     DROP COLUMN IF EXISTS stripe_account_id_mode,
--     DROP COLUMN IF EXISTS stripe_account_id_superseded,
--     DROP COLUMN IF EXISTS stripe_account_id_superseded_at;
--   ALTER TABLE public.corporate_accounts
--     DROP COLUMN IF EXISTS stripe_customer_id_mode,
--     DROP COLUMN IF EXISTS stripe_customer_id_superseded,
--     DROP COLUMN IF EXISTS stripe_customer_id_superseded_at;
--   DROP INDEX IF EXISTS users_stripe_customer_mode_idx;
--   DROP INDEX IF EXISTS drivers_stripe_account_mode_idx;
--   DROP INDEX IF EXISTS corporate_accounts_stripe_customer_mode_idx;
--
-- Notes:
-- - The CHECK constraints are added NOT VALID and validated separately so the
--   ALTER takes only a brief ACCESS EXCLUSIVE lock for the catalog change and
--   the scan runs under SHARE UPDATE EXCLUSIVE — safe with traffic in flight
--   (Performance SLA: migration apply < 30 s).
-- - `*_superseded` is provenance for reconciliation against the old Stripe
--   account. It is deliberately NOT unique: it is a historical record, and the
--   live uniqueness guarantee stays on the active column (migration 257).
-- - No new RLS policies: these columns live on existing tables and inherit
--   their policies. They hold Stripe operational identifiers, the same data
--   class as the `stripe_customer_id` / `stripe_account_id` columns beside
--   them — no new PII category is introduced.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS stripe_customer_id_mode          TEXT,
    ADD COLUMN IF NOT EXISTS stripe_customer_id_superseded    TEXT,
    ADD COLUMN IF NOT EXISTS stripe_customer_id_superseded_at TIMESTAMPTZ;

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS stripe_account_id_mode          TEXT,
    ADD COLUMN IF NOT EXISTS stripe_account_id_superseded    TEXT,
    ADD COLUMN IF NOT EXISTS stripe_account_id_superseded_at TIMESTAMPTZ;

ALTER TABLE public.corporate_accounts
    ADD COLUMN IF NOT EXISTS stripe_customer_id_mode          TEXT,
    ADD COLUMN IF NOT EXISTS stripe_customer_id_superseded    TEXT,
    ADD COLUMN IF NOT EXISTS stripe_customer_id_superseded_at TIMESTAMPTZ;

-- Mode is a closed set. NULL is legal and means "not yet verified against a
-- key" — the state every pre-existing row starts in.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_stripe_customer_id_mode_chk'
    ) THEN
        ALTER TABLE public.users
            ADD CONSTRAINT users_stripe_customer_id_mode_chk
            CHECK (stripe_customer_id_mode IN ('live', 'test')) NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'drivers_stripe_account_id_mode_chk'
    ) THEN
        ALTER TABLE public.drivers
            ADD CONSTRAINT drivers_stripe_account_id_mode_chk
            CHECK (stripe_account_id_mode IN ('live', 'test')) NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'corporate_accounts_stripe_customer_id_mode_chk'
    ) THEN
        ALTER TABLE public.corporate_accounts
            ADD CONSTRAINT corporate_accounts_stripe_customer_id_mode_chk
            CHECK (stripe_customer_id_mode IN ('live', 'test')) NOT VALID;
    END IF;
END $$;

ALTER TABLE public.users             VALIDATE CONSTRAINT users_stripe_customer_id_mode_chk;
ALTER TABLE public.drivers           VALIDATE CONSTRAINT drivers_stripe_account_id_mode_chk;
ALTER TABLE public.corporate_accounts VALIDATE CONSTRAINT corporate_accounts_stripe_customer_id_mode_chk;

-- Query pattern (admin Stripe-mode audit): "every row carrying a Stripe
-- identity that is not confirmed to match the running key's mode", i.e.
--   WHERE stripe_customer_id IS NOT NULL
--     AND stripe_customer_id_mode IS DISTINCT FROM '<current mode>'
-- The partial index keeps the scan to rows that actually hold an identity.
CREATE INDEX IF NOT EXISTS users_stripe_customer_mode_idx
    ON public.users (stripe_customer_id_mode)
    WHERE stripe_customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS drivers_stripe_account_mode_idx
    ON public.drivers (stripe_account_id_mode)
    WHERE stripe_account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS corporate_accounts_stripe_customer_mode_idx
    ON public.corporate_accounts (stripe_customer_id_mode)
    WHERE stripe_customer_id IS NOT NULL;

COMMENT ON COLUMN public.users.stripe_customer_id_mode IS
    'Stripe mode (live|test) the stripe_customer_id belongs to. NULL = not yet verified against a key; verified lazily on first use. Never guessed.';
COMMENT ON COLUMN public.users.stripe_customer_id_superseded IS
    'Previous stripe_customer_id, retained when the active one was re-provisioned because it did not resolve under the running key (e.g. test to live cutover). Provenance/reconciliation only — never charged.';
COMMENT ON COLUMN public.drivers.stripe_account_id_mode IS
    'Stripe mode (live|test) the stripe_account_id belongs to. NULL = not yet verified against a key.';
COMMENT ON COLUMN public.drivers.stripe_account_id_superseded IS
    'Previous stripe_account_id, retained when the active one was cleared because it did not resolve under the running key. Never a payout destination.';
COMMENT ON COLUMN public.corporate_accounts.stripe_customer_id_mode IS
    'Stripe mode (live|test) the stripe_customer_id belongs to. NULL = not yet verified against a key.';
COMMENT ON COLUMN public.corporate_accounts.stripe_customer_id_superseded IS
    'Previous stripe_customer_id, retained when the active one was re-provisioned. Provenance/reconciliation only — never charged.';
