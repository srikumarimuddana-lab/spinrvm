-- ============================================================
-- Migration 27: Corporate Accounts B2B v1
--
-- Spec: docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md
--
-- Extends the existing corporate_accounts table (migration 05) with
-- B2B v1 fields, and adds tables for:
--   * master wallet + append-only ledger
--   * per-employee allowances
--   * allowance top-up requests ("ask for more")
--   * company-wide policies (geofence/time/fare/payment-source)
--   * allowed email domains (for auto-match onboarding)
--   * ride → payment source link
--   * policy evaluation audit log
--
-- SAFE TO RE-RUN. Every ALTER/CREATE is guarded by
-- IF NOT EXISTS / existence checks.
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- 1. Extend corporate_accounts
-- ─────────────────────────────────────────────────────────────
ALTER TABLE corporate_accounts
    ADD COLUMN IF NOT EXISTS legal_name          TEXT,
    ADD COLUMN IF NOT EXISTS business_number     TEXT,
    ADD COLUMN IF NOT EXISTS country_code        TEXT DEFAULT 'CA',
    ADD COLUMN IF NOT EXISTS currency            TEXT DEFAULT 'CAD',
    ADD COLUMN IF NOT EXISTS tax_region          TEXT,
    ADD COLUMN IF NOT EXISTS timezone            TEXT DEFAULT 'America/Toronto',
    ADD COLUMN IF NOT EXISTS locale              TEXT DEFAULT 'en-CA',
    ADD COLUMN IF NOT EXISTS billing_email       TEXT,
    ADD COLUMN IF NOT EXISTS stripe_customer_id  TEXT,
    ADD COLUMN IF NOT EXISTS status              TEXT DEFAULT 'pending_verification',
    ADD COLUMN IF NOT EXISTS size_tier           TEXT DEFAULT 'smb',
    ADD COLUMN IF NOT EXISTS kyb_document_url    TEXT,
    ADD COLUMN IF NOT EXISTS kyb_reviewed_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS kyb_reviewed_by     UUID;

CREATE UNIQUE INDEX IF NOT EXISTS corporate_accounts_stripe_customer_unique
    ON corporate_accounts(stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS corporate_accounts_status_idx
    ON corporate_accounts(status);

-- Constrain status / size_tier / locale to known values
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'corporate_accounts_status_check') THEN
        ALTER TABLE corporate_accounts ADD CONSTRAINT corporate_accounts_status_check
            CHECK (status IN ('pending_verification','active','suspended','closed'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'corporate_accounts_size_tier_check') THEN
        ALTER TABLE corporate_accounts ADD CONSTRAINT corporate_accounts_size_tier_check
            CHECK (size_tier IN ('smb','mid_market','enterprise'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'corporate_accounts_locale_check') THEN
        ALTER TABLE corporate_accounts ADD CONSTRAINT corporate_accounts_locale_check
            CHECK (locale IN ('en-CA','fr-CA'));
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────
-- 2. corporate_wallets (master wallet, one per company)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_wallets (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id                 UUID NOT NULL UNIQUE REFERENCES corporate_accounts(id) ON DELETE CASCADE,
    balance                    NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    currency                   TEXT NOT NULL DEFAULT 'CAD',
    auto_topup_enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    auto_topup_threshold       NUMERIC(12,2),
    auto_topup_amount          NUMERIC(12,2),
    auto_topup_daily_cap       NUMERIC(12,2) NOT NULL DEFAULT 5000.00,
    low_balance_notified_at    TIMESTAMPTZ,
    soft_negative_floor        NUMERIC(12,2) NOT NULL DEFAULT -50.00,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS corporate_wallets_company_idx
    ON corporate_wallets(company_id);

-- ─────────────────────────────────────────────────────────────
-- 3. corporate_wallet_transactions (ledger for master + allowances)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_wallet_transactions (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_id                  UUID NOT NULL REFERENCES corporate_wallets(id) ON DELETE CASCADE,
    scope                      TEXT NOT NULL,  -- 'master' or 'member:<uuid>'
    type                       TEXT NOT NULL CHECK (type IN (
                                   'topup','allowance_grant','allowance_reset','allowance_rollback',
                                   'ride_debit','refund','adjustment'
                               )),
    amount                     NUMERIC(12,2) NOT NULL,
    balance_after              NUMERIC(12,2) NOT NULL,
    ride_id                    UUID,
    member_id                  UUID,
    stripe_payment_intent_id   TEXT,
    actor_user_id              UUID,
    notes                      TEXT,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS corp_wtxn_wallet_created_idx
    ON corporate_wallet_transactions(wallet_id, created_at DESC);
CREATE INDEX IF NOT EXISTS corp_wtxn_member_created_idx
    ON corporate_wallet_transactions(member_id, created_at DESC)
    WHERE member_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS corp_wtxn_ride_idx
    ON corporate_wallet_transactions(ride_id)
    WHERE ride_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS corp_wtxn_stripe_pi_unique
    ON corporate_wallet_transactions(stripe_payment_intent_id)
    WHERE stripe_payment_intent_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────
-- 4. corporate_members (company ⇄ user join)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_members (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID NOT NULL REFERENCES corporate_accounts(id) ON DELETE CASCADE,
    user_id           UUID,  -- NULL while invited, filled on acceptance
    role              TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','member')),
    status            TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','active','suspended','removed')),
    invited_email     TEXT,
    invite_token      TEXT,
    invited_at        TIMESTAMPTZ,
    joined_at         TIMESTAMPTZ,
    invited_by        UUID,
    policy_override   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS corp_members_company_user_unique
    ON corporate_members(company_id, user_id)
    WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS corp_members_user_status_idx
    ON corporate_members(user_id, status)
    WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS corp_members_invite_token_idx
    ON corporate_members(invite_token)
    WHERE invite_token IS NOT NULL;

-- ─────────────────────────────────────────────────────────────
-- 5. corporate_member_allowances (one per member)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_member_allowances (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    member_id                     UUID NOT NULL UNIQUE REFERENCES corporate_members(id) ON DELETE CASCADE,
    type                          TEXT NOT NULL CHECK (type IN ('fixed_recurring','one_time','unlimited')),
    amount                        NUMERIC(12,2),        -- NULL for unlimited
    used                          NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    period_start                  DATE,
    period_end                    DATE,
    rollover                      BOOLEAN NOT NULL DEFAULT FALSE,
    auto_approve_topup_amount     NUMERIC(12,2),
    auto_approve_monthly_count    INTEGER,
    auto_approved_this_period     INTEGER NOT NULL DEFAULT 0,
    status                        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','expired')),
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────
-- 6. corporate_allowance_requests ("ask for more")
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_allowance_requests (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    member_id         UUID NOT NULL REFERENCES corporate_members(id) ON DELETE CASCADE,
    amount            NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    reason            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','approved','denied','auto_approved')),
    reviewed_by       UUID,
    reviewed_at       TIMESTAMPTZ,
    decision_notes    TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS corp_alloreq_member_status_idx
    ON corporate_allowance_requests(member_id, status, created_at DESC);

-- Only one pending request per member at a time
CREATE UNIQUE INDEX IF NOT EXISTS corp_alloreq_one_pending_per_member
    ON corporate_allowance_requests(member_id)
    WHERE status = 'pending';

-- ─────────────────────────────────────────────────────────────
-- 7. corporate_policies (one per company)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_policies (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id                UUID NOT NULL UNIQUE REFERENCES corporate_accounts(id) ON DELETE CASCADE,
    active                    BOOLEAN NOT NULL DEFAULT TRUE,
    max_fare_per_ride         NUMERIC(12,2),
    allowed_geofence          JSONB,  -- GeoJSON FeatureCollection of polygons
    allowed_time_windows      JSONB,  -- [{"day":"mon","start":"09:00","end":"19:00"}]
    allowed_payment_source    TEXT NOT NULL DEFAULT 'both'
                                  CHECK (allowed_payment_source IN ('allowance_only','master_only','both')),
    tip_billed_to             TEXT NOT NULL DEFAULT 'rider_card'
                                  CHECK (tip_billed_to IN ('rider_card','allowance')),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────
-- 8. corporate_allowed_domains
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_allowed_domains (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES corporate_accounts(id) ON DELETE CASCADE,
    domain      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, domain)
);

CREATE INDEX IF NOT EXISTS corp_allowed_domains_domain_idx
    ON corporate_allowed_domains(domain);

-- Domain stored lowercased, no leading '@'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'corp_allowed_domains_format_check') THEN
        ALTER TABLE corporate_allowed_domains
            ADD CONSTRAINT corp_allowed_domains_format_check
            CHECK (domain = lower(domain) AND domain NOT LIKE '@%');
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────
-- 9. ride_payment_sources (ride → payment source link)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ride_payment_sources (
    ride_id                   UUID PRIMARY KEY,
    source_type               TEXT NOT NULL
                                  CHECK (source_type IN ('rider_wallet','rider_card','company_allowance')),
    company_id                UUID REFERENCES corporate_accounts(id) ON DELETE SET NULL,
    member_id                 UUID REFERENCES corporate_members(id)   ON DELETE SET NULL,
    allowance_debit_amount    NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    master_fallback_amount    NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    policy_check_result       TEXT NOT NULL CHECK (policy_check_result IN ('pass','fail','override')),
    policy_failed_rules       JSONB,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rps_company_created_idx
    ON ride_payment_sources(company_id, created_at DESC)
    WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS rps_member_created_idx
    ON ride_payment_sources(member_id, created_at DESC)
    WHERE member_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────
-- 10. corporate_policy_evaluations (audit log)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corporate_policy_evaluations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id          UUID NOT NULL,
    company_id       UUID NOT NULL REFERENCES corporate_accounts(id) ON DELETE CASCADE,
    result           TEXT NOT NULL CHECK (result IN ('pass','fail')),
    failed_rules     JSONB,
    bypassed_rules   JSONB,
    phase            TEXT NOT NULL CHECK (phase IN ('booking','completion')),
    evaluated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS corp_policy_eval_ride_idx
    ON corporate_policy_evaluations(ride_id, phase);
CREATE INDEX IF NOT EXISTS corp_policy_eval_company_time_idx
    ON corporate_policy_evaluations(company_id, evaluated_at DESC);

-- ─────────────────────────────────────────────────────────────
-- 11. updated_at triggers (reuse existing function from migration 05)
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_corporate_wallets_updated_at') THEN
        CREATE TRIGGER update_corporate_wallets_updated_at
            BEFORE UPDATE ON corporate_wallets
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_corporate_members_updated_at') THEN
        CREATE TRIGGER update_corporate_members_updated_at
            BEFORE UPDATE ON corporate_members
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_corporate_member_allowances_updated_at') THEN
        CREATE TRIGGER update_corporate_member_allowances_updated_at
            BEFORE UPDATE ON corporate_member_allowances
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_corporate_policies_updated_at') THEN
        CREATE TRIGGER update_corporate_policies_updated_at
            BEFORE UPDATE ON corporate_policies
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────
-- 12. RLS (baseline — full company-scoped policies authored in app layer)
-- ─────────────────────────────────────────────────────────────
ALTER TABLE corporate_wallets                ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_wallet_transactions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_members                ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_member_allowances      ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_allowance_requests     ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_policies               ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_allowed_domains        ENABLE ROW LEVEL SECURITY;
ALTER TABLE ride_payment_sources             ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_policy_evaluations     ENABLE ROW LEVEL SECURITY;

-- Super-admin full access (pattern from migration 17)
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'corporate_wallets',
        'corporate_wallet_transactions',
        'corporate_members',
        'corporate_member_allowances',
        'corporate_allowance_requests',
        'corporate_policies',
        'corporate_allowed_domains',
        'ride_payment_sources',
        'corporate_policy_evaluations'
    ]
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename  = t
              AND policyname = 'Admin full access ' || t
        ) THEN
            EXECUTE format(
                'CREATE POLICY "Admin full access %I" ON %I FOR ALL TO authenticated '
                'USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND users.role = ''admin''))',
                t, t
            );
        END IF;
    END LOOP;
END $$;
