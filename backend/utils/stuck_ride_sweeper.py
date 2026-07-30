"""Sweeps rides stuck in 'searching' after the in-process timeout was lost (e.g., pod restart)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase
    from ..features import send_push_notification
    from ..socket_manager import manager
    from .metrics import inc as _metric_inc
    from .stripe_charge import cancel_authorization
except ImportError:
    import db_supabase  # type: ignore
    from features import send_push_notification  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.stripe_charge import cancel_authorization  # type: ignore

try:
    from ..supabase_client import supabase
except ImportError:
    from supabase_client import supabase  # type: ignore

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 60
_SEARCHING_TIMEOUT_MINUTES = 5

# Hold states that are still open and therefore releasable. Terminal states
# ('captured', 'released') and NULL (no pre-auth: wallet/corporate) are skipped.
# Source of truth for the lifecycle: migrations/156_ride_preauth_columns.sql.
_OPEN_AUTH_STATES = ("authorized", "fare_only")


async def _release_booking_hold(ride: dict) -> None:
    """Cancel the booking-time card hold on a ride this sweeper just cancelled.

    ``routes/rides/booking.py`` places a manual-capture PaymentIntent hold for
    ``grand_total + RIDE_AUTH_BUFFER_CAD`` **before** dispatch, so a ride sitting in
    ``searching`` has live funds reserved on the rider's card. The interactive cancel
    path releases that hold (``routes/rides/cancellation.py``, "WS-8 finding 11") —
    this sweeper cancelled the ride with a direct table UPDATE and never did, so the
    hold survived until Stripe's ~7-day authorization expiry. Nothing else cleaned it
    up: ``utils/preauth_capture`` only looks at ``status='completed'``, and
    ``utils/stripe_reconcile`` only heals stuck *processing* rides.

    Best-effort by construction: ``cancel_authorization`` never raises and carries its
    own Stripe idempotency key. A failure is logged at error (a stranded hold is a
    payment-path anomaly, and CLAUDE.md requires payment errors to surface loudly) and
    the sweep continues — the remaining rides still need cancelling.
    """
    ride_id = ride.get("id")
    payment_intent_id = ride.get("payment_intent_id")
    auth_status = ride.get("auth_status")

    if not payment_intent_id or auth_status not in _OPEN_AUTH_STATES:
        return

    try:
        released = await cancel_authorization(ride_id=str(ride_id), payment_intent_id=str(payment_intent_id))
    except Exception as exc:  # defence-in-depth; cancel_authorization is no-raise
        logger.error(
            f"[stuck_ride_sweeper] pre-auth release raised for ride {ride_id}: {exc}",
            exc_info=True,
        )
        _metric_inc("spinr_stuck_ride_hold_release_total", {"outcome": "error"})
        return

    if not released:
        # Do NOT mark auth_status='released' here — the hold may still be live, and
        # a false 'released' would hide it from any future reconciler.
        logger.error(
            f"[stuck_ride_sweeper] could not release pre-auth hold for ride {ride_id} "
            f"— rider funds may stay reserved until Stripe auth expiry"
        )
        _metric_inc("spinr_stuck_ride_hold_release_total", {"outcome": "failed"})
        return

    # Mirror the interactive path: mark the hold released so reconcilers and the
    # tip-window capture sweeper never treat it as an open authorization. Filtered
    # on the open states so a concurrent writer that already moved it cannot be
    # overwritten.
    try:
        await db_supabase.update_one(
            "rides",
            {"id": ride_id, "auth_status": {"$in": list(_OPEN_AUTH_STATES)}},
            {"auth_status": "released", "updated_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception as exc:
        # The money is already free — this is bookkeeping drift, not a stranded
        # hold, so it must not be reported as a release failure.
        logger.error(
            f"[stuck_ride_sweeper] hold released but auth_status write failed for ride {ride_id}: {exc}",
            exc_info=True,
        )
        _metric_inc("spinr_stuck_ride_hold_release_total", {"outcome": "released_unmarked"})
        return

    _metric_inc("spinr_stuck_ride_hold_release_total", {"outcome": "released"})


async def _sweep() -> None:
    if not supabase:
        return

    cutoff_iso = (datetime.now(timezone.utc) - timedelta(minutes=_SEARCHING_TIMEOUT_MINUTES)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    update_payload = {
        "status": "cancelled",
        "cancelled_at": now_iso,
        "cancellation_reason": "No nearby drivers found. Please try again.",
        "cancelled_by": "system",
        "cancellation_type": "no_drivers_found",
        "updated_at": now_iso,
    }

    def _claim():
        res = (
            supabase.table("rides")
            .update(update_payload)
            .eq("status", "searching")
            .lt("ride_requested_at", cutoff_iso)
            .execute()
        )
        return db_supabase._rows_from_res(res)

    try:
        claimed_rides = await db_supabase.run_sync(_claim, retry_policy="write")
    except Exception as exc:
        logger.error(f"[stuck_ride_sweeper] DB claim failed: {exc}", exc_info=True)
        return

    if not claimed_rides:
        return

    logger.info(f"[stuck_ride_sweeper] cancelling {len(claimed_rides)} stuck ride(s)")

    for ride in claimed_rides:
        ride_id = ride.get("id")
        rider_id = ride.get("rider_id")
        driver_id = ride.get("driver_id")

        # Release the rider's card hold FIRST. The WS and push calls below are
        # network round-trips that can block for seconds (push especially), and
        # money integrity should not queue behind a notification — CLAUDE.md's
        # anti-patterns list calls out awaiting Twilio/Stripe inline for this
        # reason. Both notify calls already tolerate failure independently, so
        # nothing downstream depends on this ordering.
        await _release_booking_hold(ride)

        if rider_id:
            try:
                await manager.send_personal_message(
                    {
                        "type": "ride_cancelled",
                        "ride_id": ride_id,
                        "reason": "no_drivers_found",
                        "message": "No nearby drivers found. Please try again.",
                    },
                    f"rider_{rider_id}",
                )
            except Exception as exc:
                logger.error(
                    f"[stuck_ride_sweeper] WS notify failed for ride {ride_id}: {exc}",
                    exc_info=True,
                )

            try:
                await send_push_notification(
                    rider_id,
                    "No drivers available",
                    "We couldn't find a driver nearby. Please try again.",
                    {"ride_id": str(ride_id), "type": "ride_cancelled"},
                )
            except Exception as exc:
                logger.error(
                    f"[stuck_ride_sweeper] push notify failed for ride {ride_id}: {exc}",
                    exc_info=True,
                )

        if driver_id:
            try:
                await db_supabase.set_driver_available(driver_id, True)
            except Exception as exc:
                logger.error(
                    f"[stuck_ride_sweeper] driver release failed for {driver_id}: {exc}",
                    exc_info=True,
                )

    _metric_inc("spinr_stuck_ride_sweeper_cancelled_total", {"count": str(len(claimed_rides))})


async def stuck_ride_sweeper_loop() -> None:
    await asyncio.sleep(random.uniform(0, _SWEEP_INTERVAL_SECONDS))
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await _sweep()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[stuck_ride_sweeper] tick failed: {exc}", exc_info=True)
            _had_error = True
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "stuck_ride_sweeper"})
        _record_heartbeat("stuck_ride_sweeper (60s)")
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
