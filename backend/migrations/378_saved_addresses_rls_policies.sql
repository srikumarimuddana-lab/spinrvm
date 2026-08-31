-- 378_saved_addresses_rls_policies.sql
--
-- ACTION_ITEMS.md B40: saved_addresses (rider home/work address book) has
-- RLS ENABLED (backend/supabase_schema.sql line 384) but ZERO policies.
-- Confirmed directly against production (pg_policy returns zero rows for
-- saved_addresses, pg_class.relrowsecurity = true) while building the
-- Phase 4 legacy saved-address backfill (see migration 373's header and
-- docs/migration/2026-08-27-legacy-data-full-migration-approach.md §4).
--
-- Not a live vulnerability: RLS-enabled-with-no-policy denies all access
-- to anon/authenticated by default (fail-closed). backend/routes/
-- addresses.py reads/writes this table exclusively via the service-role
-- key (db_supabase.get_rows/insert_one/delete_one), which bypasses RLS
-- entirely and does its own `user_id = current_user["id"]` scoping
-- app-side (see get_saved_addresses/create_saved_address/
-- delete_saved_address in that file). This migration is defense-in-depth
-- so a future direct-PostgREST/anon-key read of this table fails closed
-- with an explicit owner-only policy instead of relying on "nothing calls
-- it directly yet".
--
-- Pattern match: saved_addresses.id/user_id are TEXT (backend/
-- supabase_schema.sql: "id TEXT PRIMARY KEY", "user_id TEXT NOT NULL
-- REFERENCES users(id)") -- NOT UUID. This follows the TEXT-user_id owner
-- pattern already used for emergency_contacts (migration 120) and
-- disputes (migration 142) -- auth.uid()::text = user_id -- not the
-- UUID-column pattern used for driver_bonuses (179) or corporate_members
-- (142), which compare auth.uid() directly with no cast.
--
-- Scope: SELECT/INSERT/DELETE only, matching B40's acceptance criteria
-- exactly and mirroring emergency_contacts (120), which also ships no
-- UPDATE policy. routes/addresses.py has no PUT/PATCH endpoint for this
-- table today (only GET / POST / DELETE) -- there is no "edit a saved
-- address" flow to cover, so no UPDATE policy is added speculatively.
--
-- Idempotent: guarded by pg_policies existence checks so this is safe to
-- re-run.
--
-- Rollback:
--   DROP POLICY IF EXISTS saved_addresses_owner_select ON saved_addresses;
--   DROP POLICY IF EXISTS saved_addresses_owner_insert ON saved_addresses;
--   DROP POLICY IF EXISTS saved_addresses_owner_delete ON saved_addresses;
--   Reverts to the current production state (RLS enabled, zero policies,
--   anon/authenticated fully denied). No data is touched by this
--   migration or its rollback -- service-role traffic (all current app
--   behavior) is unaffected either way.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'saved_addresses'
          AND policyname = 'saved_addresses_owner_select'
    ) THEN
        CREATE POLICY saved_addresses_owner_select
            ON saved_addresses FOR SELECT
            USING (auth.uid()::text = user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'saved_addresses'
          AND policyname = 'saved_addresses_owner_insert'
    ) THEN
        CREATE POLICY saved_addresses_owner_insert
            ON saved_addresses FOR INSERT
            WITH CHECK (auth.uid()::text = user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'saved_addresses'
          AND policyname = 'saved_addresses_owner_delete'
    ) THEN
        CREATE POLICY saved_addresses_owner_delete
            ON saved_addresses FOR DELETE
            USING (auth.uid()::text = user_id);
    END IF;
END
$$;

NOTIFY pgrst, 'reload schema';
