"""Which ``rides.payment_status`` values mean the rider's fare was actually collected.

Driver-payable money (``/drivers/balance``, the weekly ``auto_payout`` batch,
driver statements, the T4A slip) must only be computed over completed rides
whose fare Spinr has collected — or deliberately waived. A completed ride
whose card charge failed (``utils/payment_retry.py`` parks it at
``payment_status='failed'`` after ``MAX_RETRIES``) still carries its
``driver_earnings``, and before this filter existed that number flowed straight
into ``payable_balance`` and out through a real Stripe Transfer. Spinr takes no
commission, so an uncollected fare paid out is 100% platform loss with nothing
to net it against.

The set is every status a ride can hold *after* the charge succeeded:

- ``paid`` — every settlement path's success write (card, hold capture,
  wallet RPC, corporate).
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
  this filter must not auto-resolve them against the driver.

Everything else is "not collected (yet)": ``pending`` (settlement never ran),
``processing`` / ``requires_action`` / ``retrying`` (in flight), ``failed``
(terminal, gave up), ``held_for_review`` (GPS-spoof gate, never charged).
A ride that later settles re-enters the sums on its own once its status flips;
nothing here needs a backfill. Mirrors ``routes/webhooks.py``'s
``_SETTLED_PAYMENT_STATUSES`` — change both together if the policy changes.

``rides.payment_status`` is ``NOT NULL DEFAULT 'pending'`` (supabase_schema.sql),
so an inclusion list is safe: there is no NULL to accidentally drop.
"""

from __future__ import annotations

from typing import Any

COLLECTED_PAYMENT_STATUSES: tuple[str, ...] = (
    "paid",
    "waived_admin",
    "refunded",
    "partially_refunded",
    "disputed",
    "dispute_lost",
)

# Spread into a ``rides`` money query next to ``EXCLUDE_LEGACY_RIDES``
# (``utils/legacy_rides.py``) — same shape, same purpose: a filter that
# governs money math only. Never apply it to activity counts (trip totals),
# which should keep showing every completed ride.
ONLY_COLLECTED_RIDES: dict[str, Any] = {"payment_status": {"$in": list(COLLECTED_PAYMENT_STATUSES)}}


def is_collected(ride: dict[str, Any]) -> bool:
    """True when this ride's fare has been collected (or waived) and is payable."""
    return str(ride.get("payment_status") or "").lower() in COLLECTED_PAYMENT_STATUSES


def drop_uncollected_rides(rides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Post-fetch companion to ``ONLY_COLLECTED_RIDES``.

    For callers that cannot add a filter to their query (repository helpers
    with a fixed signature such as ``get_rides_for_driver``, or rows already
    in hand) — same shape as ``legacy_rides.drop_legacy_rides``.
    """
    return [r for r in rides if is_collected(r)]
