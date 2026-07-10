-- 226_corporate_section_wallets.sql
--
-- Departmental funding hierarchy (Phase 5, owner-approved Q9/Q11):
-- departments (corporate_sections) gain their OWN wallets, funded by admin
-- allocation from the master wallet (migration 227's transfer RPC) or by the
-- department head's Stripe top-up. This SUPERSEDES migration 206's "sections
-- are grouping only" note — sections now carry money and a head.
--
--   * corporate_wallets.section_id (nullable UUID → corporate_sections):
--     NULL  = the company's MASTER wallet (existing rows unchanged)
--     value = that section's wallet
--     ON DELETE RESTRICT (default NO ACTION): a section holding a wallet
--     cannot be hard-deleted — wallets hold money; sections are archived,
--     not deleted (206 convention).
--   * The inline UNIQUE on company_id (one-wallet-per-company) is REPLACED
--     by two partial unique indexes:
--       - exactly one MASTER wallet per company   (WHERE section_id IS NULL)
--       - at most one wallet per (company, section)
--     The old constraint is dropped by NAME LOOKUP (it was auto-named by the
--     inline UNIQUE in migration 27), guarded so re-runs are no-ops.
--   * corporate_sections.head_member_id (nullable UUID → corporate_members,
--     ON DELETE SET NULL): the section's single department head (Q11 — an
--     attribute, not a new role; guards check "company admin OR head").
--     One head per section by design.
--
-- Ledger impact: none — corporate_wallet_transactions.scope is free-form
-- TEXT ('master' / 'member:<uuid>'); section activity uses 'section:<uuid>'
-- with no schema change. Section wallets floor at 0 (Q9) — enforced by the
-- RPCs in migration 227, not a CHECK (the master wallet legitimately goes
-- negative to its credit floor and shares this table).
--
-- ⚠ SEQUENCING GATE (reviewer-flagged): this DDL alone changes nothing at
-- runtime (every company still has exactly one wallet row), but ~9 call sites
-- read the company wallet via .limit(1) with no section filter — they become
-- NON-DETERMINISTIC the moment the first section-wallet row exists. The
-- get_master_wallet(section_id IS NULL) repo fix MUST be merged before any
-- code that can insert a section wallet (transfer RPC callers / top-up
-- endpoints) goes live.
--
-- Forward-compatible: nullable ADD COLUMNs (metadata-only); the constraint
-- swap takes a brief ACCESS EXCLUSIVE on corporate_wallets — a small B2B
-- table, not a hot consumer table. No new table → RLS posture unchanged
-- (corporate_wallets has the admin-only baseline from migration 27; all
-- portal access goes through the service-role backend behind
-- require_company_* guards).
--
-- Rollback (on paper — only valid while no section wallets exist):
--   DROP INDEX IF EXISTS corp_wallets_master_unique;
--   DROP INDEX IF EXISTS corp_wallets_section_unique;
--   ALTER TABLE corporate_wallets ADD CONSTRAINT corporate_wallets_company_id_key UNIQUE (company_id);
--   ALTER TABLE corporate_wallets DROP COLUMN IF EXISTS section_id;
--   ALTER TABLE corporate_sections DROP COLUMN IF EXISTS head_member_id;

ALTER TABLE corporate_wallets
    ADD COLUMN IF NOT EXISTS section_id UUID REFERENCES corporate_sections(id);

ALTER TABLE corporate_sections
    ADD COLUMN IF NOT EXISTS head_member_id UUID REFERENCES corporate_members(id) ON DELETE SET NULL;

-- Drop the one-wallet-per-company UNIQUE (auto-named inline constraint from
-- migration 27) by looking it up: a single-column unique on company_id.
DO $$
DECLARE
    v_conname TEXT;
BEGIN
    SELECT c.conname INTO v_conname
    FROM pg_constraint c
    WHERE c.conrelid = 'corporate_wallets'::regclass
      AND c.contype = 'u'
      AND (
          SELECT array_agg(a.attname ORDER BY a.attname)
          FROM unnest(c.conkey) AS k(attnum)
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
      ) = ARRAY['company_id']::name[];
    IF v_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE corporate_wallets DROP CONSTRAINT %I', v_conname);
    ELSE
        -- Fail-safe: nothing matched (drift?). The old UNIQUE would then still
        -- block section-wallet inserts LOUDLY rather than permit duplicates.
        RAISE NOTICE 'corporate_wallets: no single-column UNIQUE(company_id) constraint found to drop';
    END IF;
END $$;

-- At most one master wallet per company; at most one wallet per section.
-- (Uniqueness, not existence — existence is ensure_corporate_wallet's job.)
CREATE UNIQUE INDEX IF NOT EXISTS corp_wallets_master_unique
    ON corporate_wallets (company_id)
    WHERE section_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS corp_wallets_section_unique
    ON corporate_wallets (company_id, section_id)
    WHERE section_id IS NOT NULL;
