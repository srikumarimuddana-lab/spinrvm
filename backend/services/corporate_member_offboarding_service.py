"""Cancels pre-pickup rides when a single corporate member is removed.

Member-level companion to ``corporate_suspension_service.py`` (which handles
company-wide suspend/close). A removed employee shouldn't keep dispatching
new drivers against the company account any more than a suspended company
should — this closes that gap at the member scope. Only rides that haven't
reached ``in_progress`` are touched; an in-progress ride is grandfathered
(billed normally at settlement per the ride state machine's "never cancel
after trip start" rule).
"""

from datetime import datetime, timezone
from typing import Any, Dict

from loguru import logger

try:
    from .. import db_supabase
    from ..features import send_push_notification
    from ..models.ride_status import RideStatus
    from ..utils.insurance_periods import record_period_transition
except ImportError:
    import db_supabase  # type: ignore
    from features import send_push_notification  # type: ignore
    from models.ride_status import RideStatus  # type: ignore
    from utils.insurance_periods import record_period_transition  # type: ignore

try:
    from ..socket_manager import manager
except ImportError:
    from socket_manager import manager  # type: ignore

# Same pre-trip set as corporate_suspension_service._PRE_PICKUP_STATUSES /
# routes/rides/cancellation.py::cancel_ride_rider, PLUS `scheduled`
# (scheduled-rides gap review, Finding #16) — a removed member's already-
# scheduled ride was previously excluded here, so it kept dispatching a
# driver and billing the company against a member who should no longer
# have access. Safe to include: driver_id is always None pre-dispatch (the
# release branch below is a no-op), and corporate rides carry no pre-auth
# hold to release pre-dispatch either.
_PRE_PICKUP_STATUSES = (
    RideStatus.SCHEDULED,
    RideStatus.SEARCHING,
    RideStatus.DRIVER_ASSIGNED,
    RideStatus.DRIVER_ACCEPTED,
    RideStatus.DRIVER_ARRIVED,
)

_CANCELLATION_REASON = "Your access to this company account was removed before this ride started."


async def cancel_pre_pickup_rides_for_member(company_id: str, member_id: str) -> int:
    """Auto-cancel every pre-pickup ride billed against ``member_id``.

    No cancellation fee is charged — this is a system/company-side event,
    not a rider or driver decision. Returns the number of rides cancelled.
    Best-effort per ride: one failure doesn't stop the rest.
    """
    candidates = await db_supabase.get_rows(
        "rides",
        {
            "corporate_account_id": company_id,
            "corporate_member_id": member_id,
            "status": {"$in": list(_PRE_PICKUP_STATUSES)},
        },
    )
    cancelled_count = 0
    for ride in candidates or []:
        try:
            if await _cancel_one_ride(ride):
                cancelled_count += 1
        except Exception as exc:
            logger.error(
                "[CORP-MEMBER-REMOVE] failed to cancel ride %s for member %s: %s",
                ride.get("id"),
                member_id,
                exc,
                exc_info=True,
            )
    return cancelled_count


async def _cancel_one_ride(ride: Dict[str, Any]) -> bool:
    ride_id = ride["id"]
    now = datetime.now(timezone.utc)

    # Atomic claim — mirrors corporate_suspension_service: if the ride left
    # the pre-trip states between our read and this write, the $in filter
    # matches zero rows and we skip it rather than overwrite an in_progress
    # ride.
    claim = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "status": {"$in": list(_PRE_PICKUP_STATUSES)}},
        {
            "status": RideStatus.CANCELLED,
            "cancelled_at": now,
            "cancelled_by": "system",
            "cancellation_type": "corporate_member_removed",
            "cancellation_reason": _CANCELLATION_REASON,
            "updated_at": now,
        },
    )
    if claim is None:
        logger.info("[CORP-MEMBER-REMOVE] claim skipped ride_id=%s — already left pre-trip state", ride_id)
        return False

    driver_id = ride.get("driver_id")
    if driver_id:
        await db_supabase.set_driver_available(driver_id, True)
        await record_period_transition(driver_id, 1)
        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            # Best-effort, like the push/SMS notifies below — the ride is
            # already durably cancelled in the DB at this point (the claim
            # above succeeded), so a WS blip here must not propagate up and
            # make the caller think the cancellation itself failed (which
            # previously under-counted the returned count and logged a
            # misleading "failed to cancel" message for an already-cancelled
            # ride).
            try:
                await manager.send_personal_message(
                    {"type": "ride_cancelled", "ride_id": ride_id, "reason": _CANCELLATION_REASON},
                    f"driver_{driver['user_id']}",
                )
            except Exception as exc:
                logger.error(
                    "[CORP-MEMBER-REMOVE] driver WS notify failed ride_id=%s: %s", ride_id, exc, exc_info=True
                )

    rider_id = ride.get("rider_id")
    if rider_id:
        try:
            await manager.send_personal_message(
                {"type": "ride_cancelled", "ride_id": ride_id, "reason": _CANCELLATION_REASON},
                f"rider_{rider_id}",
            )
        except Exception as exc:
            logger.error("[CORP-MEMBER-REMOVE] rider WS notify failed ride_id=%s: %s", ride_id, exc, exc_info=True)
        try:
            await send_push_notification(
                rider_id,
                "Ride Cancelled",
                _CANCELLATION_REASON,
                {"type": "ride_cancelled", "ride_id": ride_id, "is_auto": "true"},
            )
        except Exception as exc:
            logger.error("[CORP-MEMBER-REMOVE] push notify failed ride_id=%s: %s", ride_id, exc, exc_info=True)

    if ride.get("guest_booking"):
        try:
            from ..services.guest_notification_service import notify_guest_cancelled
        except ImportError:
            from services.guest_notification_service import notify_guest_cancelled  # type: ignore
        try:
            await notify_guest_cancelled(dict(ride))
        except Exception as exc:
            logger.error("[CORP-MEMBER-REMOVE] guest SMS notify failed ride_id=%s: %s", ride_id, exc, exc_info=True)

    logger.info("[CORP-MEMBER-REMOVE] cancelled pre-pickup ride %s for removed member", ride_id)
    return True
