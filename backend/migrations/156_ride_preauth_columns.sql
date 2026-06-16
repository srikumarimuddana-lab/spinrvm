-- 156_ride_preauth_columns.sql
--
-- Buffered pre-authorization support for card-paid rides.
--
-- Spinr historically had NO pre-authorization: the card was charged (immediate
-- off_session capture) only at trip completion, so a dead/declined card was
-- never discovered until AFTER the driver had completed the trip — an unpaid
-- ride the 0%-commission driver effectively eats. This migration adds the two
-- columns the new flow needs to (a) hold an authorization at booking time for
-- the estimated fare + a fixed buffer (RIDE_AUTH_BUFFER, $10), and (b) capture
-- fare + tip in a SINGLE transaction once the tip window closes, avoiding the
-- duplicate Stripe per-transaction fee on a separately-charged tip.
--
-- The held Stripe PaymentIntent id reuses the EXISTING rides.payment_intent_id
-- column — a manual-capture PI created at authorization is the same PI that is
-- later captured at settlement, so no new id column is needed. We add:
--
--   • authorized_amount  numeric(12,2) — the dollar amount actually HELD on the
--     card (estimated fare + buffer, OR fare-only when the buffered auth was
--     declined for insufficient funds and we fell back). Lets settlement know
--     the ceiling it can capture without a second charge.
--
--   • auth_status text — lifecycle of the hold, one of:
--       'authorized'       buffered hold placed (fare + buffer)
--       'fare_only'        buffered hold declined; fell back to fare-only hold
--       'captured'         hold captured at settlement (terminal)
--       'released'         hold canceled (ride cancelled pre-trip / expired)
--     NULL  → no pre-auth on this ride (wallet/corporate, or a scheduled ride
--             not yet dispatched). Settlement falls back to the create-charge
--             path for these, so the column is nullable and unconstrained-NULL.
--
-- Money columns are numeric(12,2) — never float — consistent with the rest of
-- the rides fare columns; all writes go through the Decimal helpers in
-- backend (_d/_round) before hitting this table.
--
-- Index: the tip-window sweeper (a replay-safe background loop) polls for rides
-- still holding an uncaptured authorization past the tip window, i.e.
-- WHERE auth_status IN ('authorized','fare_only'). A partial index on the open
-- states keeps that scan cheap and excludes the terminal-state majority.
--
-- RLS: no new table — rides already has RLS enabled with its existing policies.
-- Adding nullable columns inherits that policy set; no policy change needed.
--
-- Forward-compatible (rides is a HOT table — every dispatch, location, and
-- settlement write touches it, so every step here avoids a long write-blocking
-- lock):
--   • ADD COLUMN ... (nullable, no default) is metadata-only, no table rewrite.
--   • The CHECK constraint is added NOT VALID then VALIDATE'd separately, so the
--     full-table validation runs under SHARE UPDATE EXCLUSIVE (concurrent writes
--     keep flowing) instead of the ACCESS EXCLUSIVE a plain ADD CONSTRAINT takes.
--   • The partial index is built CONCURRENTLY (no SHARE lock on rides). The
--     runner (backend/scripts/migrate.py) detects "CONCURRENTLY" and applies the
--     whole file in autocommit, executing each statement individually.
-- New code writes the columns, old code ignores them, and existing rides keep
-- NULL (no pre-auth) which the settlement path already handles.
--
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_rides_open_authorization;
--   ALTER TABLE public.rides DROP CONSTRAINT IF EXISTS rides_auth_status_check;
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS auth_status;
--   ALTER TABLE public.rides DROP COLUMN IF EXISTS authorized_amount;

ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS authorized_amount numeric(12, 2),
    ADD COLUMN IF NOT EXISTS auth_status text;

-- Guard the enum-ish domain at the DB layer so a bad write surfaces loudly
-- instead of silently corrupting settlement logic. NULL is allowed (no pre-auth).
-- NOT VALID skips the blocking validation scan at ADD time; VALIDATE then checks
-- existing rows (all NULL today, so it passes) under a non-blocking lock.
ALTER TABLE public.rides
    DROP CONSTRAINT IF EXISTS rides_auth_status_check;
ALTER TABLE public.rides
    ADD CONSTRAINT rides_auth_status_check
    CHECK (auth_status IS NULL OR auth_status IN ('authorized', 'fare_only', 'captured', 'released'))
    NOT VALID;
ALTER TABLE public.rides
    VALIDATE CONSTRAINT rides_auth_status_check;

-- Partial index for the tip-window capture sweeper: only the open holds.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_open_authorization
    ON public.rides (auth_status)
    WHERE auth_status IN ('authorized', 'fare_only');
