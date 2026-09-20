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

# Spread into a ``rides`` money query next to ``EXCLUDE_LEGACY_RIDES``
# (``utils/legacy_rides.py``) — same shape, same purpose: a filter that
# governs money math only. Never apply it to activity counts (trip totals),
# which should keep showing every completed ride.
#
# Prefer ``payable_ride_filter()`` for anything that feeds a driver-visible
# balance or a payout: applying this unconditionally retroactively changes
# balances for drivers already paid out under the old behaviour. See that
# function's docstring.
ONLY_COLLECTED_RIDES: dict[str, Any] = {"payment_status": {"$in": list(COLLECTED_PAYMENT_STATUSES)}}

# app_settings flag gating the payable-money filter. Default OFF: the filter
# is correct going forward but is NOT safe to switch on blind — see
# ``payable_ride_filter``.
PAYABLE_FILTER_FLAG = "uncollected_rides_excluded_from_payable"


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


async def payable_ride_filter(app_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """``ONLY_COLLECTED_RIDES`` when the flag is on, ``{}`` (no filter) otherwise.

    **Why this is flagged rather than always on.** ``payable_balance`` is a
    live recompute — ``total_earnings - total_payouts`` (``routes/drivers/
    earnings.py``, ``utils/auto_payout._balance_from_rows``) — with no floor.
    Filtering uncollected rides shrinks ``total_earnings``, but a payout
    already sent for such a ride stays in ``total_payouts``. Every driver paid
    out under the old behaviour would go negative the moment this switched on,
    and each future collected fare would silently net against that past
    overpayment until they climbed back to zero.

    That is an automatic clawback with no ledger row and no human decision —
    exactly what ``scripts/reconcile_uncollected_ride_payouts.sql`` refuses to
    build ("a driver still drove the trip, and whether Spinr recovers an
    uncollected fare from the rider or absorbs it is a per-case business
    decision"), and the same argument this module makes for keeping
    ``disputed`` rides payable.

    So the mechanism ships dark. Before flipping the flag on: run that report
    against a read replica to size the affected cohort, decide how the
    existing overpayment is handled, and note that with the flag ON
    ``/drivers/balance`` and ``/drivers/earnings`` report different totals for
    the same period (ACTION_ITEMS.md A28 decided they should agree — revisit
    it, or carry the filter to ``get_driver_earnings`` at the same time).

    Pass ``app_settings`` when the caller already loaded it; otherwise it is
    fetched through the 60s-cached ``settings_loader.get_app_settings``.
    Fails CLOSED (no filter, i.e. today's behaviour) if the settings read
    fails — a flag read must never be the reason a driver's balance moves.
    """
    if app_settings is None:
        try:
            try:
                from ..settings_loader import get_app_settings
            except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
                from settings_loader import get_app_settings  # type: ignore

            app_settings = await get_app_settings()
        except Exception:
            return {}
    return dict(ONLY_COLLECTED_RIDES) if (app_settings or {}).get(PAYABLE_FILTER_FLAG, False) else {}
