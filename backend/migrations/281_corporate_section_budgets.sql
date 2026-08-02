-- Migration 281: corporate_sections.monthly_budget_cap + corporate_section_spend
--
-- Rollback:
--   DROP TABLE IF EXISTS public.corporate_section_spend;
--   ALTER TABLE public.corporate_sections DROP COLUMN IF EXISTS monthly_budget_cap;
--
-- Corporate + admin portal review round 2, business decision: "department/
-- section budgets" — VISIBILITY ONLY (product decision, not full hard
-- enforcement). corporate_sections (migration 206) was explicitly
-- grouping/reporting-only with budgets staying per-member
-- (corporate_member_allowances); this adds an OPTIONAL monthly spend cap a
-- company can set on a section purely to see "$X of $Y used" — it never
-- blocks a booking. Hard enforcement would require a new atomic Postgres
-- function (mirroring corporate_allowance_apply_delta's SELECT...FOR UPDATE
-- ceiling check, migration 258) wired into BOTH independent corporate
-- booking code paths in routes/rides/booking.py — deliberately out of
-- scope this round per explicit product decision, given this codebase
-- already hit a real race-condition bug on the closely analogous
-- per-member allowance cap before that fix landed.
--
-- corporate_section_spend is a simple running-total ledger, one row per
-- (section_id, month). Recorded via a single atomic UPDATE ... SET used =
-- used + delta (or upsert) at ride SETTLEMENT time
-- (services/payment_service.py::settle_corporate) — an atomic increment
-- needs no row lock/RPC the way a compare-then-conditionally-write ceiling
-- check would, so this is safe under concurrency without the complexity a
-- hard cap would require.
--
-- Forward-compatible: additive nullable column + new table. Safe against
-- production traffic in flight.

ALTER TABLE public.corporate_sections
    ADD COLUMN IF NOT EXISTS monthly_budget_cap NUMERIC(10, 2) CHECK (monthly_budget_cap IS NULL OR monthly_budget_cap >= 0);

CREATE TABLE IF NOT EXISTS public.corporate_section_spend (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_id  UUID NOT NULL REFERENCES public.corporate_sections(id) ON DELETE CASCADE,
    month       TEXT NOT NULL,  -- 'YYYY-MM', UTC calendar month
    used        NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (used >= 0),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One running-total row per section per month; the upsert target for the
-- atomic increment at settlement time.
CREATE UNIQUE INDEX IF NOT EXISTS corp_section_spend_section_month_unique
    ON public.corporate_section_spend (section_id, month);

-- Service role (backend) bypasses RLS by design; the explicit policy below
-- matches corporate_sections' own (migration 206) so a direct authenticated
-- (non-service-role) admin session reads the same rows a service-role-backed
-- route would, rather than silently getting zero rows.
ALTER TABLE public.corporate_section_spend ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'corporate_section_spend'
          AND policyname = 'Admin full access corporate_section_spend'
    ) THEN
        EXECUTE 'CREATE POLICY "Admin full access corporate_section_spend" ON corporate_section_spend '
                'FOR ALL TO authenticated '
                'USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND users.role = ''admin''))';
    END IF;
END $$;

COMMENT ON COLUMN public.corporate_sections.monthly_budget_cap IS
    'Optional visibility-only monthly spend cap for this section (CAD). Never blocks a booking — see migration 281.';
COMMENT ON TABLE public.corporate_section_spend IS
    'Running month-to-date spend total per section, recorded at ride settlement. Visibility only, not an enforcement gate.';

-- Atomic upsert-increment: PostgREST's REST upsert cannot express "used =
-- used + delta" (it can only replace a row's values, not compute from the
-- existing one), so a single-statement INSERT ... ON CONFLICT DO UPDATE is
-- the simplest race-safe primitive here — no explicit row lock needed since
-- this is one atomic statement, not a read-then-conditionally-write ceiling
-- check (that harder case is corporate_allowance_apply_delta, deliberately
-- not replicated here per this round's visibility-only scope). Same
-- SECURITY DEFINER + pinned search_path convention as every other
-- money-adjacent RPC in this codebase (CLAUDE.md, migration 203).
CREATE OR REPLACE FUNCTION corporate_section_spend_add(
    p_section_id UUID,
    p_month      TEXT,
    p_delta      NUMERIC(10, 2)
)
RETURNS NUMERIC(10, 2)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_used NUMERIC(10, 2);
BEGIN
    INSERT INTO corporate_section_spend (section_id, month, used, updated_at)
    VALUES (p_section_id, p_month, GREATEST(p_delta, 0), now())
    ON CONFLICT (section_id, month)
    DO UPDATE SET
        used = corporate_section_spend.used + GREATEST(p_delta, 0),
        updated_at = now()
    RETURNING used INTO v_used;
    RETURN v_used;
END;
$$;

REVOKE ALL ON FUNCTION corporate_section_spend_add(UUID, TEXT, NUMERIC) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION corporate_section_spend_add(UUID, TEXT, NUMERIC) TO service_role;
