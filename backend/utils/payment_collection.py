"""Which ``rides.payment_status`` values mean the rider's fare was actually collected.

Single source of truth for "money came in" on a ride. ``routes/webhooks.py``
imports it as its already-settled guard; the driver-payable surfaces
(``/drivers/balance``, the weekly ``auto_payout`` batch, driver statements)
use it to keep uncollected fares out of payable money.

A completed ride whose card charge failed (``utils/payment_retry.py`` parks it
at ``payment_status='failed'`` after ``MAX_RETRIES``) still carries its
``driver_earnings``, and without this filter that number flows into
``payable_balance`` and out through a real Stripe Transfer. Spinr takes no
commission, so an uncollected fare paid out is 100% platform loss.

The set is every status a ride can hold *after* the charge succeeded:

- ``paid`` — the canonical success write on every settlement path (card, hold
  capture, wallet RPC, corporate).
- ``succeeded`` — the raw Stripe ``PaymentIntent.status``. ``routes/payments.py``
  used to write it verbatim; that path now maps to ``paid``, but the value is
  kept here so any row written before that fix still reads as collected.
- ``waived_admin`` — an admin deliberately forgave the fare; the driver is
  still owed for the trip.
- ``refunded`` / ``partially_refunded`` — money came in and (some of it) went
  back to the rider as a goodwill/dispute outcome against the platform. The
  driver keeps their pay and the platform absorbs it — the policy
  ``services/ledger_service.py`` and ``routes/webhooks.py``'s
  ``charge.refunded`` handler already state.
- ``disputed`` / ``dispute_lost`` — a chargeback opened (or lost) on a charge
  that DID succeed, typically after the driver was already paid out. Same
  rule as refunds: the trip was driven and the money was collected, so the
  ride stays payable. Excluding these would silently pull an already-paid
  ride out of ``total_earnings`` while its payout stays in ``total_payouts``
  — a live-recomputed balance would drop mid-dispute with no ledger row and
  no human decision. Disputes are triaged by support via ``stripe_disputes``;
  this set must not auto-resolve them against the driver.

Everything else is "not collected (yet)": ``pending`` (settlement never ran),
``processing`` / ``requires_action`` / ``retrying`` (in flight), ``failed``
(terminal, gave up), ``held_for_review`` (GPS-spoof gate, never charged).
A ride that later settles re-enters the sums on its own once its status flips;
nothing here needs a backfill.

``rides.payment_status`` is ``NOT NULL DEFAULT 'pending'`` (supabase_schema.sql),
so an inclusion list is safe: there is no NULL to accidentally drop.
"""

from __future__ import annotations

from typing import Any

COLLECTED_PAYMENT_STATUSES: tuple[str, ...] = (
    "paid",
    "succeeded",
    "waived_admin",
    "refunded",
    "partially_refunded",
    "disputed",
    "dispute_lost",
)

# ``routes/webhooks.py``'s name for the same set: a ride in any of these has
# already settled, so an incoming ``payment_intent.payment_failed`` is a
# redelivery of a superseded attempt and must not relabel it. That guard used
# to carry its own three-value tuple, which left ``partially_refunded``,
# ``disputed`` and ``dispute_lost`` falling through to the CAS that writes
# ``failed`` — turning a collected ride into an uncollected one.
SETTLED_PAYMENT_STATUSES = COLLECTED_PAYMENT_STATUSES


def is_collected(ride: dict[str, Any]) -> bool:
    """True when this ride's fare has been collected (or waived) and is payable."""
    return str(ride.get("payment_status") or "").lower() in COLLECTED_PAYMENT_STATUSES


def drop_uncollected_rides(rides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Post-fetch filter on ``COLLECTED_PAYMENT_STATUSES``.

    For callers that cannot add a filter to their query (repository helpers
    with a fixed signature such as ``get_rides_for_driver``, or rows already
    in hand) — same shape as ``legacy_rides.drop_legacy_rides``.
    """
    return [r for r in rides if is_collected(r)]
