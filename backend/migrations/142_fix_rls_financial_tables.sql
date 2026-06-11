-- Migration 142: RLS lockdown on disputes and corporate financial tables.
-- =============================================================================
-- Closes P0: "FOR ALL TO authenticated with no WITH CHECK" on disputes and
-- nine corporate tables (including corporate_wallets + corporate_wallet_transactions).
-- Any authenticated JWT could INSERT/UPDATE/DELETE these rows via PostgREST,
-- bypassing every backend guard.
--
-- Pattern follows migration 51 (audit_logs lockdown).
--
-- Rollback plan:
--   DROP the new SELECT-only policies, re-run the dynamic block from migration
--   27 to restore FOR ALL policies, and GRANT INSERT/UPDATE/DELETE on the
--   affected tables back to the authenticated role. Low blast radius: the
--   service_role bypass policy (written by migration 10 / migration 27) stays
--   untouched throughout, so the backend is unaffected at all times.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. disputes: replace FOR ALL (no WITH CHECK) with SELECT only.
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

        -- Strip write access from the authenticated role (backend uses service_role)
        EXECUTE format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON %I FROM authenticated', t);
        EXECUTE format('GRANT SELECT ON %I TO authenticated', t);
    END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. PIPEDA data minimization: scrub persisted full legal names from disputes.
--    user_name duplicated PII already derivable by joining users on user_id;
--    the admin list endpoint enriches at read time. The backend no longer
--    writes this column. The column itself stays (dropping it would break
--    in-flight inserts from old replicas during the deploy window) — drop in
--    a follow-up migration once this code is fully rolled out.
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE disputes SET user_name = '' WHERE COALESCE(user_name, '') <> '';

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Rides idempotency: DB-enforced unique constraint.
--    Closes P1: "read-then-act" on idempotency_key; a flaky-network double-tap
--    can create two rides (and two charges). The partial unique index converts
--    the race into a constraint violation the backend can detect and handle.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS rides_rider_idempotency_key
    ON rides (rider_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Double-dispatch guard: at most one accepted offer per ride.
--    Defense-in-depth behind the atomic accept filter in routes/drivers.py
--    (status=searching AND driver_id IS NULL). Safe because a ride never
--    returns to 'searching' after an accept: the offer-timeout revert only
--    fires pre-accept (offer still 'pending'), and a post-accept driver
--    cancel terminates the ride ('cancelled') rather than re-dispatching.
-- ─────────────────────────────────────────────────────────────────────────────
-- Latent bug fix discovered while adding the index: rides.py rider-cancel
-- writes status='cancelled' on pending offers, but the CHECK constraint
-- (migrations 100/131) never allowed that value — the write fails and is
-- swallowed by the caller's try/except, leaving drivers with stale offer
-- panels. Widen the allowed set (additive, no existing row violates it).
ALTER TABLE ride_offers DROP CONSTRAINT IF EXISTS ride_offers_status_check;
ALTER TABLE ride_offers
    ADD CONSTRAINT ride_offers_status_check
    CHECK (status IN ('pending', 'accepted', 'declined', 'expired', 'preempted', 'cancelled'));

-- Repair any divergence the race already produced: where multiple offers are
-- 'accepted' for one ride, keep the one matching rides.driver_id (the actual
-- winner on the ride row) and demote the rest to 'preempted'. Without this
-- the unique index below fails to build on affected production data.
UPDATE ride_offers o
SET status = 'preempted'
WHERE o.status = 'accepted'
  AND EXISTS (
      SELECT 1 FROM ride_offers o2
      WHERE o2.ride_id = o.ride_id
        AND o2.status = 'accepted'
        AND o2.id <> o.id
  )
  AND o.driver_id IS DISTINCT FROM (SELECT r.driver_id FROM rides r WHERE r.id = o.ride_id);

CREATE UNIQUE INDEX IF NOT EXISTS ride_offers_one_accepted_per_ride
    ON ride_offers (ride_id)
    WHERE status = 'accepted';

NOTIFY pgrst, 'reload schema';
