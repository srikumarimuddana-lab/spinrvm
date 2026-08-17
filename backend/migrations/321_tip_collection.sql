-- 321_tip_collection.sql
--
-- Purpose: make a tip collectable independently of the ride fare.
--
-- Background. Until now the only way a tip reached us was by riding along on the
-- booking-time card hold, which is why that hold was deliberately oversized
-- (`grand_total + RIDE_AUTH_BUFFER_CAD`, a flat $10). On a $5 ride that put a $15
-- pending charge on the rider's bank feed for a $5 trip, which reads as an
-- overcharge. The buffer is going to $0.00 so the hold equals the quoted fare,
-- and tips are collected one of two ways instead:
--
--   1. INCREMENT (preferred, one Stripe fixed fee): raise the existing
--      authorization by the tip amount and capture once. Only possible BEFORE
--      capture and only on cards whose issuer grants it -- Stripe reports the
--      capability per charge, hence `rides.auth_incrementable` below.
--   2. SEPARATE CHARGE (fallback): a row in `pending_tips`, collected by the
--      tip-batch loop. This is the ONLY option once the fare is captured,
--      because a captured PaymentIntent can never be incremented.
--
-- Why `auth_incrementable` is a boolean and not a `tip_capture_mode` enum:
-- the capability is a fact about the authorization, known at booking. Which
-- path settlement actually takes is derived at settlement time from that fact
-- PLUS whether the hold is still uncaptured -- state that already lives in
-- `auth_status`/`payment_status`. Storing a "mode" would duplicate that and let
-- the two disagree.
--
-- Why `pending_tips` is a table and not a column on `rides`:
-- a tip that could not be collected yet needs its own retry state (attempts,
-- last_error, the charging claim) and must be batchable ACROSS rides -- a rider
-- with three small owed tips is charged once, not three times. `rides` already
-- carries `tip_amount` and keeps doing so; that column means "the tip on this
-- ride", while a `pending_tips` row means "and we have not got the money yet".
--
-- MONEY SAFETY: a `pending_tips` row is an uncollected receivable. Nothing here
-- credits a driver. `rides.driver_earnings` is only incremented when a row
-- reaches status='charged', so a failed collection can never leave a driver
-- credited with money we never took, and `driver_earnings_snapshot` (which feeds
-- T4A) stays an exact sum of amounts actually collected.
--
-- Rollback:
--   DROP TRIGGER IF EXISTS pending_tips_updated_at ON public.pending_tips;
--   DROP FUNCTION IF EXISTS pending_tips_set_updated_at();
--   DROP POLICY IF EXISTS "Service role bypass pending_tips" ON public.pending_tips;
--   DROP POLICY IF EXISTS "Rider can read own pending_tips" ON public.pending_tips;
--   DROP POLICY IF EXISTS "Admin read pending_tips" ON public.pending_tips;
--   DROP TABLE IF EXISTS public.pending_tips;
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS auth_incrementable;
--   Safe while no uncollected rows exist. If any DO exist, they are money owed
--   to drivers -- export them before dropping:
--     SELECT * FROM public.pending_tips WHERE status IN ('owed','charging','failed');
--   Dropping the column alone is always safe: absence is read as "not
--   incrementable", which degrades to the separate-charge path.

-- ---------------------------------------------------------------------------
-- 1. Per-authorization increment capability
-- ---------------------------------------------------------------------------

ALTER TABLE public.rides
  ADD COLUMN IF NOT EXISTS auth_incrementable boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN public.rides.auth_incrementable IS
  'True when Stripe granted incremental-authorization support on this ride''s '
  'booking-time hold, meaning a post-trip tip can be added to that same '
  'PaymentIntent instead of charged separately. Read back from the charge at '
  'authorization time -- it varies by card brand and issuer, so it is never '
  'assumed. Default false is the safe direction: it only costs one extra Stripe '
  'fixed fee, whereas a wrong true fails the increment at settlement.';

-- ---------------------------------------------------------------------------
-- 2. Uncollected tips
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.pending_tips (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- UNIQUE: one outstanding tip per ride, matching the existing one-tip-per-ride
    -- rule in routes/rides/payments.py. Also makes the insert naturally idempotent
    -- against the rider-app offline replay queue, which can POST the same tip twice.
    ride_id                  text NOT NULL UNIQUE REFERENCES public.rides(id) ON DELETE CASCADE,
    rider_id                 text NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    -- SET NULL not CASCADE: if a driver record goes away we still owe the money
    -- and still need the audit trail of having collected it.
    driver_id                text REFERENCES public.drivers(id) ON DELETE SET NULL,
    amount                   numeric(12, 2) NOT NULL CHECK (amount > 0),
    status                   text NOT NULL DEFAULT 'owed'
                             CHECK (status IN ('owed', 'charging', 'charged', 'failed', 'cancelled')),
    -- The PaymentIntent that actually collected this tip. Shared across every row
    -- in the same batch, which is what lets support answer "what was this charge?"
    batch_payment_intent_id  text,
    attempts                 integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error               text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    charged_at               timestamptz,
    updated_at               timestamptz NOT NULL DEFAULT now()
);

-- Batch selection: "what does this rider owe, and is it over the threshold yet".
CREATE INDEX IF NOT EXISTS idx_pending_tips_rider_status
    ON public.pending_tips (rider_id, status);

-- Age sweep: "has anything been owed longer than the ceiling", so a driver never
-- waits indefinitely on a rider who tipped once and never came back.
CREATE INDEX IF NOT EXISTS idx_pending_tips_status_created
    ON public.pending_tips (status, created_at)
    WHERE status IN ('owed', 'failed');

-- Reconciliation by charge, for disputes and support lookups.
CREATE INDEX IF NOT EXISTS idx_pending_tips_batch_pi
    ON public.pending_tips (batch_payment_intent_id)
    WHERE batch_payment_intent_id IS NOT NULL;

COMMENT ON TABLE public.pending_tips IS
  'Tips recorded but not yet collected from the rider. A row is an uncollected '
  'receivable: it exists precisely because the money has NOT moved. Rows are '
  'created when a tip cannot ride on the booking hold (hold already captured, or '
  'the card does not support incremental authorization) and are cleared by the '
  'tip-batch loop. A driver is credited only on the transition to charged.';

COMMENT ON COLUMN public.pending_tips.status IS
  'owed -> charging -> charged | failed. charging is the atomic claim taken by '
  'the batch loop so concurrent replicas cannot double-charge; failed rows are '
  'retried. cancelled is for support write-offs, and is terminal.';

-- updated_at auto-maintained so the batch loop's retry timeline is accurate
-- without every caller having to remember to set it.
CREATE OR REPLACE FUNCTION pending_tips_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pending_tips_updated_at ON public.pending_tips;
CREATE TRIGGER pending_tips_updated_at
    BEFORE UPDATE ON public.pending_tips
    FOR EACH ROW
    EXECUTE FUNCTION pending_tips_set_updated_at();

-- ---------------------------------------------------------------------------
-- 3. RLS
-- ---------------------------------------------------------------------------
-- Read-only for everyone except the backend. Every mutation here moves money,
-- so writes are service-role only -- there is deliberately no INSERT/UPDATE
-- policy for `authenticated`, not even for the rider who owns the row.

ALTER TABLE public.pending_tips ENABLE ROW LEVEL SECURITY;

-- Service role (backend) writes + reads everything.
CREATE POLICY "Service role bypass pending_tips"
    ON public.pending_tips FOR ALL TO service_role
    USING (true)
    WITH CHECK (true);

-- Rider sees what they still owe, so the app can show "tip pending" honestly
-- rather than implying the driver has already been paid.
CREATE POLICY "Rider can read own pending_tips"
    ON public.pending_tips FOR SELECT TO authenticated
    USING (rider_id = auth.uid()::text);

-- Admin / super_admin: read for support ("why was I charged $7?").
CREATE POLICY "Admin read pending_tips"
    ON public.pending_tips FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.users
            WHERE public.users.id = auth.uid()::text
              AND public.users.role IN ('admin', 'super_admin')
        )
    );
