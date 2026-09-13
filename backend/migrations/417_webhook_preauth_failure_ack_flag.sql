-- 417: kill switch for acking an unlinkable booking-stage pre-auth failure
-- on POST /api/v1/webhooks/stripe.
--
-- Context: the booking-time hold PaymentIntent is created BEFORE the ride row
-- is inserted (routes/rides/booking.py pre-authorizes, then inserts), so its
-- payment_intent.payment_failed can arrive when no row exists — and on a
-- genuine decline the booking raises 402 and the row never exists at all, so
-- that event used to 500 on every Stripe retry for three days. Stripe disables
-- endpoints that fail persistently. routes/webhooks.py now acks such an event
-- instead, gated on BOTH metadata.source == 'ride_booking_authorization' AND
-- the ride row being absent.
--
-- Fixed-flat-column `settings` table (migration 313's own header: "there is no
-- JSON catch-all") — a new AppSettings/admin-PATCH field is not safe to add
-- without a matching migration, or PUT /api/admin/settings 500s on the unknown
-- column. See migrations 313/353/415 for the prior incidents.
--
-- DEFAULT true, a DELIBERATE deviation from the ship-dark rule that 353/415
-- follow. Justification, because the deviation should not pass unexplained:
--   * this is a kill switch for a FIX, not a gate on a new feature — shipping
--     it dark would leave an active production defect running (one 500 per
--     booking, and a 3-day Stripe retry loop per declined pre-auth);
--   * the guard only ever STOPS writes, so there is no data to unwind if it is
--     flipped off, and flipping it off restores exactly the prior behaviour;
--   * the one behaviour it changes for an existing ride is suppressing a
--     "Payment Failed" push for a booking about to succeed on the fallback
--     hold. It cannot suppress a real settlement failure: the handler requires
--     the ride row to be ABSENT, because Stripe metadata is stamped once at
--     PaymentIntent creation and never updated, so capture_ride carries the
--     same source on the same PI at settlement (see the reviewer finding
--     recorded in docs/change-log/2026-09-12-live-ride-triage-gps-webhook-health.md).
--
-- Rollback:
--   UPDATE public.settings SET webhook_preauth_failure_ack_enabled = false
--     WHERE id = 'app_settings';          -- no redeploy, takes effect within
--                                         -- settings_loader's 60s TTL
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS webhook_preauth_failure_ack_enabled;
--
-- Forward-compatible: additive defaulted column; older backends ignore it.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS webhook_preauth_failure_ack_enabled BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.settings.webhook_preauth_failure_ack_enabled IS
    'Kill switch for POST /api/v1/webhooks/stripe acking a '
    'payment_intent.payment_failed whose metadata.source is '
    '''ride_booking_authorization'' AND whose ride row does not exist. On '
    '(default) = ack it, because a booking-stage hold failure for a '
    'non-existent ride is unlinkable, moved no money, and was already '
    'surfaced to the rider synchronously as the 402 — while 500ing it made '
    'Stripe retry for three days. Off = restore the prior unclaim+500, which '
    'is the documented rollback. Never suppresses a settlement failure: the '
    'ride row must be ABSENT, so a capture declined at settlement (which '
    'carries the same metadata.source on the same PaymentIntent) still '
    'records and still notifies the rider and driver.';
