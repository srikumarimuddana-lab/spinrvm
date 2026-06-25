-- 195_ride_live_activities.sql
-- Live Ride Activity (iOS Live Activity / Android ongoing notification) push-token
-- registry. One row per (ride_id, platform): the rider app registers the
-- ActivityKit push token (iOS) / device token (Android) when it starts a live
-- activity at driver_accepted; the backend reads it on each ride-state transition
-- to push an update, and marks it ended at completed/cancelled.
--
-- See docs/live-ride-activity-plan.md §4. Phase 1 = storage + register endpoint +
-- log-only hooks; no push is sent yet (APNs .p8 path is Phase 3).
--
-- Backend (service role) performs all writes; riders get SELECT on their own rows
-- only (defense-in-depth for the anon key — frontend never writes this directly).
--
-- Rollback (DESTRUCTIVE — drops the token registry):
--   DROP TABLE IF EXISTS ride_live_activities;
-- Safe in Phase 1: rows are ephemeral push tokens, no push is sent yet and no
-- downstream reads depend on them. After Phase 3 (live pushes), back up rows
-- before dropping or in-flight activities silently stop updating.

CREATE TABLE IF NOT EXISTS ride_live_activities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- ride_id/rider_id intentionally carry NO FK to rides/users: matches the
    -- house pattern (ride_incentive_claims, migration 96) and avoids coupling to
    -- the 7-year-retention-anonymised rides table. Integrity is enforced by the
    -- backend (ownership-checked register endpoint), which is the only writer.
    ride_id     UUID NOT NULL,
    rider_id    UUID NOT NULL,                          -- owning rider (rides.rider_id)
    platform    TEXT NOT NULL CHECK (platform IN ('ios', 'android')),
    push_token  TEXT NOT NULL,                          -- ActivityKit token (iOS) / device token (Android)
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ,                            -- set on terminal state (completed/cancelled)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One live-activity token per ride per platform; re-registration upserts.
    CONSTRAINT ride_live_activities_ride_platform_uniq UNIQUE (ride_id, platform)
);

-- Transition hooks look up the active activity (or activities) for a ride.
CREATE INDEX IF NOT EXISTS idx_ride_live_activities_ride
    ON ride_live_activities(ride_id);

-- "Active" (not yet ended) lookups — the common dispatch path.
CREATE INDEX IF NOT EXISTS idx_ride_live_activities_active
    ON ride_live_activities(ride_id)
    WHERE ended_at IS NULL;

-- RLS: backend (service_role) does all writes; riders may read their own rows.
ALTER TABLE ride_live_activities ENABLE ROW LEVEL SECURITY;

-- Backend service role — performs register/update/end on behalf of the rider.
-- Explicit per-operation policies (house rule bans FOR ALL on user-data tables).
CREATE POLICY ride_live_activities_service_select
    ON ride_live_activities FOR SELECT TO service_role USING (true);
CREATE POLICY ride_live_activities_service_insert
    ON ride_live_activities FOR INSERT TO service_role WITH CHECK (true);
CREATE POLICY ride_live_activities_service_update
    ON ride_live_activities FOR UPDATE TO service_role USING (true) WITH CHECK (true);
CREATE POLICY ride_live_activities_service_delete
    ON ride_live_activities FOR DELETE TO service_role USING (true);

-- Rider may read only their own live-activity rows (anon/authenticated key).
-- Direct uuid comparison (both sides uuid) so the planner can use the rider_id predicate.
CREATE POLICY ride_live_activities_rider_select
    ON ride_live_activities FOR SELECT TO authenticated
    USING (auth.uid() = rider_id);
