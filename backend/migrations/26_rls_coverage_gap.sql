-- Migration 26: RLS coverage on the last 6 tables that lacked it (audit P0-S4)
--
-- Inventory (pre-migration, after a full audit of supabase_schema.sql +
-- supabase_rls.sql + sql/*.sql + migrations/*.sql):
--
--   Tables with RLS enabled:  ~40
--   Tables with RLS disabled:   6   ← this migration
--
-- The six tables below are all written exclusively by the backend via
-- the Supabase service role (which bypasses RLS), so enabling RLS with
-- no policies is defence-in-depth: it makes the tables invisible to
-- the anon / authenticated roles if someone accidentally queries them
-- with a non-service key (for example, a frontend shipping the anon
-- key and calling supabase-js directly).
--
-- Why no CREATE POLICY statements: none of these tables are consumed
-- from a client via supabase-js today. All reads happen through the
-- FastAPI backend, which uses the SUPABASE_SERVICE_ROLE_KEY and hence
-- bypasses RLS. Introducing policies before we have a client that
-- actually needs them would give us policies we can't test and
-- couldn't confidently revise later. The audit's P1 follow-up is to
-- classify each table by intended client access and emit the tight
-- (auth.uid() = owner_id) policies where appropriate.
--
-- The audit finding quoted "20+ tables without RLS" — we verified that
-- most were already covered by ENABLE statements in supabase_schema.sql
-- and supabase_rls.sql (initial readings missed those). These six are
-- the actual uncovered set:
--
--   subscription_plans       — admin-curated list of Spinr Pass tiers
--   driver_subscriptions     — per-driver Spinr Pass enrolment state
--   driver_notes             — admin-written notes about a driver
--   driver_activity_log      — driver state-change audit trail
--   driver_daily_stats       — precomputed per-driver daily aggregates
--   driver_location_history  — GPS breadcrumbs for rides in progress
--
-- All six are backend-only today (no mobile client reads them through
-- supabase-js).  Enabling RLS here is the minimum P0 fix; policy
-- authorship waits on a per-table access-model review.

ALTER TABLE IF EXISTS public.subscription_plans       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.driver_subscriptions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.driver_notes             ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.driver_activity_log      ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.driver_daily_stats       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.driver_location_history  ENABLE ROW LEVEL SECURITY;

-- Belt-and-suspenders deny-all policies for the three most sensitive
-- tables added in recent migrations. Without policies RLS already
-- blocks anon/authenticated, but an explicit FOR ALL USING (false)
-- policy documents intent and survives an accidental bulk
-- `CREATE POLICY ... USING (true)` applied later. These are no-ops
-- for the backend's service role (BYPASSRLS).
DO $$ BEGIN
    CREATE POLICY refresh_tokens_deny_all ON public.refresh_tokens
        FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE POLICY stripe_events_deny_all ON public.stripe_events
        FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE POLICY schema_migrations_deny_all ON public.schema_migrations
        FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
