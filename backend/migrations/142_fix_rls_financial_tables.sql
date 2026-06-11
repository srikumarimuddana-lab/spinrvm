-- Migration 142: RLS lockdown on disputes and corporate financial tables.
-- =============================================================================
-- Closes P0: "FOR ALL TO authenticated with no WITH CHECK" on disputes and
-- nine corporate tables (including corporate_wallets + corporate_wallet_transactions).
-- Any authenticated JWT could INSERT/UPDATE/DELETE these rows via PostgREST,
-- bypassing every backend guard.
--
-- Pattern follows migration 51 (audit_logs lockdown).
--
-- Companion: migration 143 builds the single-accepted-offer unique index
-- CONCURRENTLY (the runner executes CONCURRENTLY files in autocommit mode,
-- which cannot host the DO blocks below). The duplicate-accepted repair that
-- the index depends on runs HERE, before 143.
--
-- Note: backend mobile/admin traffic is always mediated by the service_role
-- client (db_supabase) — the apps never talk to PostgREST directly. The
-- member/rider SELECT policies below exist for convention compliance and
-- future direct-read tooling, not because any current code path needs them.
--
-- Deploy sequencing (user_name scrub): old replicas still write user_name
-- during the rolling deploy window; rows written between this migration and
-- the code rollout keep a name until the follow-up migration that drops the
-- column. Acceptable: the API stops returning the stored value immediately
-- (read-time enrichment overwrites it).
--
-- Rollback plan:
--   DROP the new SELECT-only policies, re-run the dynamic block from migration
--   27 to restore FOR ALL policies, and GRANT INSERT/UPDATE/DELETE on the
--   affected tables back to the authenticated role. Drop constraint
--   ride_offers_status_check and re-add migration 131's five-value version.
--   The user_name scrub and the accepted-offer repair are irreversible short
--   of PITR. Low blast radius throughout: the service_role bypass policies
--   (migrations 10 / 27) stay untouched, so the backend is unaffected.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. disputes: replace FOR ALL (no WITH CHECK) with enumerated SELECT.
--    Migration 10 created "Admin full access disputes" FOR ALL TO authenticated.
--    Legitimate writes on disputes go through service_role (backend); no
--    direct-from-browser INSERT/UPDATE/DELETE is ever needed.
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "Admin full access disputes" ON disputes;

CREATE POLICY "Admin read disputes"
    ON disputes FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = auth.uid()::text
              AND users.role IN ('admin', 'super_admin')
        )
    );

-- Convention: every user-data table enumerates SELECT for its owner.
CREATE POLICY "Rider read own disputes"
    ON disputes FOR SELECT
    TO authenticated
    USING (user_id = auth.uid()::text);

REVOKE ALL ON disputes FROM anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON disputes FROM authenticated;
GRANT  SELECT ON disputes TO authenticated;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Corporate financial tables: replace FOR ALL (no WITH CHECK) with SELECT.
--    Migration 27 dynamically created "Admin full access <table>" FOR ALL TO
--    authenticated on nine tables (with only role = 'admin', accidentally
--    excluding super_admin). corporate_wallets and corporate_wallet_transactions
--    are direct money-mutation vectors via PostgREST. All writes go through the
--    backend service layer and the corporate_wallet_apply_delta PG function.
-- ─────────────────────────────────────────────────────────────────────────────
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
        -- Drop the FOR ALL policy from migration 27
        EXECUTE format('DROP POLICY IF EXISTS "Admin full access %s" ON %I', t, t);

        -- Replace with SELECT only; admins and super_admins can read
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename  = t
              AND policyname = 'Admin read ' || t
        ) THEN
            EXECUTE format(
                'CREATE POLICY "Admin read %s" ON %I FOR SELECT TO authenticated '
                'USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text '
                'AND users.role IN (''admin'', ''super_admin'')))',
                t, t
            );
        END IF;

        -- Strip all anon access and write access from authenticated
        -- (backend uses service_role for every read and write today).
        EXECUTE format('REVOKE ALL ON %I FROM anon', t);
        EXECUTE format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON %I FROM authenticated', t);
        EXECUTE format('GRANT SELECT ON %I TO authenticated', t);
    END LOOP;
END $$;

-- Members can read their own membership and allowance rows (convention;
-- no current code path reads these via PostgREST — see header note).
-- NOTE: corporate_members.user_id is UUID (migration 27); auth.uid() is also
-- UUID — compare directly. auth.uid()::text would produce a uuid=text mismatch
-- that aborts the policy creation.
CREATE POLICY "Member read own corporate_members"
    ON corporate_members FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY "Member read own corporate_member_allowances"
    ON corporate_member_allowances FOR SELECT
    TO authenticated
    USING (
        member_id IN (
            SELECT id FROM corporate_members
            WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "Member read own corporate_allowance_requests"
    ON corporate_allowance_requests FOR SELECT
    TO authenticated
    USING (
        member_id IN (
            SELECT id FROM corporate_members
            WHERE user_id = auth.uid()
        )
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. PIPEDA data minimization: scrub persisted full legal names from disputes.
--    user_name duplicated PII already derivable by joining users on user_id;
--    the admin list endpoint enriches at read time. The backend no longer
--    writes this column. The column itself stays (dropping it would break
--    in-flight inserts from old replicas during the deploy window) — drop in
--    a follow-up migration once this code is fully rolled out.
--    Single UPDATE: DO blocks in Postgres run inside the current transaction
--    and cannot COMMIT between iterations, so the prior batched loop held row
--    locks for the entire migration anyway. disputes is a low-volume support
--    table; a single UPDATE finishes in < 100 ms even at several thousand rows.
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE disputes SET user_name = '' WHERE COALESCE(user_name, '') <> '';

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Latent bug fix discovered while hardening ride_offers: rides.py
--    rider-cancel writes status='cancelled' on pending offers, but the CHECK
--    constraint (migrations 100/131) never allowed that value — the write
--    fails and is swallowed by the caller's try/except, leaving drivers with
--    stale offer panels. Widen the allowed set. NOT VALID avoids the
--    full-table validation scan under ACCESS EXCLUSIVE; the VALIDATE step
--    takes only SHARE UPDATE EXCLUSIVE (does not block dispatch traffic).
--    Additive change — no existing row can violate it.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE ride_offers DROP CONSTRAINT IF EXISTS ride_offers_status_check;
ALTER TABLE ride_offers
    ADD CONSTRAINT ride_offers_status_check
    CHECK (status IN ('pending', 'accepted', 'declined', 'expired', 'preempted', 'cancelled'))
    NOT VALID;
ALTER TABLE ride_offers VALIDATE CONSTRAINT ride_offers_status_check;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Repair the divergence the double-accept race already produced: where
--    multiple offers are 'accepted' for one ride, keep exactly one winner —
--    preferring the offer matching rides.driver_id (the row-level truth),
--    falling back to the earliest response when driver_id is NULL — and
--    demote the rest to 'preempted'. Without this, migration 143's unique
--    index fails to build on affected production data.
-- ─────────────────────────────────────────────────────────────────────────────
WITH ranked AS (
    SELECT o.id,
           row_number() OVER (
               PARTITION BY o.ride_id
               ORDER BY (o.driver_id = r.driver_id) DESC NULLS LAST,
                        o.responded_at NULLS LAST,
                        o.offered_at
           ) AS rn
    FROM ride_offers o
    LEFT JOIN rides r ON r.id = o.ride_id
    WHERE o.status = 'accepted'
)
UPDATE ride_offers
SET status = 'preempted'
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- Booking idempotency note: the partial unique index on
-- rides(rider_id, idempotency_key) already exists — migration 44 created
-- idx_rides_rider_idempotency_key. routes/rides.py now catches its
-- violation and returns the original ride. No new index here.

NOTIFY pgrst, 'reload schema';
