"""Canonical release of a booking-time card hold.

``routes/rides/booking.py`` places a manual-capture PaymentIntent hold for
``grand_total + RIDE_AUTH_BUFFER_CAD`` **before** dispatch, so any ride that never
reaches settlement has live funds reserved on the rider's card. Three places have to
let that money go, and this module is the single definition of how:

  1. ``routes/rides/cancellation.py`` — rider/driver cancels (has its own inline
     implementation, predating this module; see the note below).
  2. ``utils/stuck_ride_sweeper.py`` — the 5-minute no-drivers auto-cancel.
  3. ``utils/orphaned_hold_reconciler.py`` — heals holds the other two missed.

It exists because the *absence* of a shared definition is what caused the bug it
fixes. The release was implemented in the interactive cancel path ("WS-8 finding 11")
and simply never carried over to the sweeper, so auto-cancelled rides stranded the
rider's money for ~7 days. A second copy in the reconciler would be a third chance
for the same divergence.

The failure semantics are the subtle part and are the reason this is shared rather
than reimplemented:

* **Never cancel a hold that is not open.** ``auth_status`` must be in
  ``_OPEN_AUTH_STATES``. Calling ``PaymentIntent.cancel`` on a ``captured`` intent
  attempts to reverse money the rider actually owes — worse than the leak.
* **Never mark ``released`` unless Stripe confirmed it.** A false ``released`` moves
  the row out of the open-hold index and hides live funds from every future
  reconciler, converting a 7-day leak into a permanent one.
* **Distinguish "money still held" from "bookkeeping drifted."** They need different
  operator responses, so they are different outcomes rather than one generic failure.

``routes/rides/cancellation.py`` is deliberately NOT refactored onto this. It does
considerably more in the same transaction (cancellation fees, a second PaymentIntent
for the fee, driver release, insurance-period transitions), and rewiring a live
interactive money path to prove a point about de-duplication is a worse trade than
leaving it. It is listed above so the next person knows a third implementation
exists.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

try:
    from .. import db_supabase
    from .metrics import inc as _metric_inc
    from .stripe_charge import cancel_authorization
except ImportError:  # pragma: no cover - dual import
    import db_supabase  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.stripe_charge import cancel_authorization  # type: ignore

logger = logging.getLogger(__name__)

# Hold states that are still open and therefore releasable. Terminal states
# ('captured', 'released') and NULL (no pre-auth: wallet/corporate) are skipped.
# Source of truth for the lifecycle: migrations/156_ride_preauth_columns.sql, which
# also enforces it as a CHECK constraint.
OPEN_AUTH_STATES = ("authorized", "fare_only")

# Outcomes. Distinct because each implies a different operator response.
SKIPPED = "skipped"  # nothing to release
RELEASED = "released"  # money freed, row marked
FAILED = "failed"  # money may STILL BE HELD — investigate
RELEASED_UNMARKED = "released_unmarked"  # money freed, row not marked — no rider impact
ERROR = "error"  # unexpected exception


def has_open_hold(ride: dict) -> bool:
    """True when this ride has a releasable booking-time hold."""
    return bool(ride.get("payment_intent_id")) and ride.get("auth_status") in OPEN_AUTH_STATES


async def release_open_hold(ride: dict, *, source: str) -> str:
    """Cancel the ride's open card hold and mark it released. Returns an outcome.

    Best-effort by construction: ``cancel_authorization`` never raises and carries its
    own Stripe idempotency key, so a duplicate call from a racing replica is safe.
    Never raises — callers are background loops mid-iteration over other rides, and
    one rider's Stripe failure must not strand the next rider's funds.

    ``source`` is a metric label identifying the caller (e.g. ``sweeper``,
    ``reconciler``) so the two paths can be told apart on a dashboard.
    """
    ride_id = ride.get("id")

    if not has_open_hold(ride):
        return SKIPPED

    payment_intent_id = ride.get("payment_intent_id")

    try:
        released = await cancel_authorization(ride_id=str(ride_id), payment_intent_id=str(payment_intent_id))
    except Exception as exc:  # defence-in-depth; cancel_authorization is no-raise
        logger.error(
            f"[card_hold_release] pre-auth release raised for ride {ride_id} (source={source}): {exc}",
            exc_info=True,
        )
        _metric_inc("spinr_card_hold_release_total", {"outcome": ERROR, "source": source})
        return ERROR

    if not released:
        # Do NOT mark auth_status='released' — the hold may still be live, and a
        # false 'released' would hide it from every future reconciler.
        logger.error(
            f"[card_hold_release] could not release pre-auth hold for ride {ride_id} "
            f"(source={source}) — rider funds may stay reserved until Stripe auth expiry"
        )
        _metric_inc("spinr_card_hold_release_total", {"outcome": FAILED, "source": source})
        return FAILED

    # Mark the hold released so reconcilers and the tip-window capture sweeper never
    # treat it as an open authorization. Filtered on the open states so a concurrent
    # writer that already moved it cannot be overwritten.
    try:
        await db_supabase.update_one(
            "rides",
            {"id": ride_id, "auth_status": {"$in": list(OPEN_AUTH_STATES)}},
            {"auth_status": "released", "updated_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception as exc:
        # The money is already free — this is bookkeeping drift, not a stranded
        # hold, so it must not be reported as a release failure.
        logger.error(
            f"[card_hold_release] hold released but auth_status write failed for ride {ride_id} "
            f"(source={source}): {exc}",
            exc_info=True,
        )
        _metric_inc("spinr_card_hold_release_total", {"outcome": RELEASED_UNMARKED, "source": source})
        return RELEASED_UNMARKED

    _metric_inc("spinr_card_hold_release_total", {"outcome": RELEASED, "source": source})
    return RELEASED
