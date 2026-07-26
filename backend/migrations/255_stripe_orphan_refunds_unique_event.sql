-- Rollback: DROP INDEX IF EXISTS idx_stripe_orphan_refunds_event_uniq;
-- Idempotency key for stripe_orphan_refunds (created in 254).
--
-- One charge.refunded event produces at most one orphan row: the handler
-- calls _record_orphan_refund on exactly one of two mutually exclusive
-- branches (no matching ride, or no payment_intent at all). Today a
-- redelivery cannot reach the insert because claim_stripe_event dedupes
-- upstream, but payment_intent.succeeded already has an unclaim path that
-- deliberately releases the claim so Stripe's retry re-processes. If a
-- similar unclaim is ever added to charge.refunded, finance would start
-- seeing duplicate orphan rows for a single refund. This index makes that
-- impossible rather than relying on the upstream guard alone.
--
-- Partial: stripe_event_id is nullable (manual/backfilled rows carry no
-- event id) and those must not collide with each other.

CREATE UNIQUE INDEX IF NOT EXISTS idx_stripe_orphan_refunds_event_uniq
    ON stripe_orphan_refunds (stripe_event_id)
    WHERE stripe_event_id IS NOT NULL;
