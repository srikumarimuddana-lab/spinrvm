-- 320_driver_appeals.sql
--
-- Backs the process docs/legal/driver-deactivation-appeals-policy.md
-- describes: a driver whose account is on hold or deactivated can submit
-- an appeal, and a different admin reviewer than the one who made the
-- original decision reviews it. Today this process only existed as a
-- policy document — this table is what a driver's appeal is stored in and
-- what the new admin review queue (routes/admin/driver_appeals.py) reads.
--
-- Deliberately NOT modeled on the older migration 10 (disputes) RLS
-- pattern — that table originally shipped a broad `FOR ALL TO
-- authenticated` admin policy that migration 142 later had to lock down to
-- SELECT-only after the fact (see that migration's own history, and
-- CLAUDE.md's "B2" note). This table is accessed only through backend
-- routes (driver submits via routes/drivers/appeals.py, admin reviews via
-- routes/admin/driver_appeals.py) — there is no direct frontend Supabase
-- read/write path — so it follows the simpler, already-correct
-- service-role-only pattern from migrations 190/319 instead: RLS enabled,
-- no policies, denying all anon/authenticated access from day one.
--
-- No FOREIGN KEY on driver_id (deliberate, same reasoning as migration
-- 319): an appeal record should remain producible even if the driver
-- account it concerns is later deleted — an FK with cascade delete would
-- erase exactly the record a dispute over the deletion itself might need.
--
-- Forward-compatible: pure CREATE TABLE / CREATE INDEX, safe under live
-- traffic. No money columns.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.driver_appeals;

CREATE TABLE IF NOT EXISTS public.driver_appeals (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id       UUID        NOT NULL,
    -- What's being appealed. Mirrors the drivers.status values that can
    -- represent a hold (migration 12: suspended/banned/needs_review) plus
    -- a catch-all for anything else a driver believes needs review.
    appeal_type     TEXT        NOT NULL CHECK (appeal_type IN ('suspension', 'ban', 'needs_review', 'other')),
    driver_message  TEXT        NOT NULL,
    -- Snapshot of the reason shown to the driver at submission time
    -- (drivers.suspension_reason / ban_reason at that moment) so the
    -- appeal record still makes sense even if that column later changes.
    original_reason TEXT,
    status          TEXT        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
    admin_note      TEXT,
    resolved_by     TEXT,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Admin queue's default view: open appeals, oldest first.
CREATE INDEX IF NOT EXISTS driver_appeals_status_idx
    ON public.driver_appeals (status, created_at);

-- "Show me this driver's appeal history" — used to prevent a driver from
-- submitting a duplicate appeal while one is already pending.
CREATE INDEX IF NOT EXISTS driver_appeals_driver_idx
    ON public.driver_appeals (driver_id, created_at DESC);

-- services/driver_appeals.py's create_appeal() enforces "one pending appeal
-- per driver" with a check-then-insert (get_pending_appeal, then insert) —
-- on its own that's a TOCTOU race: two concurrent submissions (e.g. a
-- driver double-tapping submit) could both pass the check before either
-- insert lands, producing two 'pending' rows. This partial unique index is
-- the actual correctness guarantee; the application-level check just gives
-- a friendlier error before hitting it. A second concurrent insert fails
-- this constraint and the route surfaces it as the same 409 "already have
-- a pending appeal" the application-level check produces.
CREATE UNIQUE INDEX IF NOT EXISTS driver_appeals_one_pending_per_driver
    ON public.driver_appeals (driver_id)
    WHERE status = 'pending';

ALTER TABLE public.driver_appeals ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.driver_appeals IS
    'Driver appeals of an account suspension/ban/hold, per docs/legal/driver-deactivation-appeals-policy.md. Submitted via routes/drivers/appeals.py, reviewed via routes/admin/driver_appeals.py. Service-role-only (RLS enabled, no policies).';
