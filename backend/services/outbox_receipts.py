"""Automatic paid-ride receipt routing.

The Postgres trigger is the atomic producer. Application code must not insert
outbox rows after settlement. It only checks whether a row already exists and
falls back to the historical direct send when it does not.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from loguru import logger

try:
    from .outbox import is_auto_receipt_queued
except ImportError:
    from services.outbox import is_auto_receipt_queued  # type: ignore


async def auto_receipt_is_queued(ride_id: str) -> bool:
    """True when the atomic producer already wrote ride_receipt.v1 for this ride."""
    return await is_auto_receipt_queued(ride_id)


async def maybe_send_auto_receipt(
    ride: dict,
    rider_id: str,
    tip: Any,
    *,
    spawn: Optional[Callable] = None,
    send: Optional[Callable] = None,
) -> bool:
    """Skip the direct auto-receipt send when an outbox row already exists.

    Lookup failure is logged and treated as 'not queued' so the historical
    spawn/await path still runs. That favours delivery and can duplicate.
    Manual rider/admin resend must never call this helper.

    ``send`` lets a caller pass its locally-bound ``send_ride_receipt`` so
    existing tests that patch that name keep working.
    """
    ride_id = ride.get("id") if isinstance(ride, dict) else None
    queued = False
    if ride_id:
        try:
            queued = await auto_receipt_is_queued(str(ride_id))
        except Exception:
            logger.opt(exception=True).error(
                "outbox auto-receipt lookup failed ride_id={} — falling back to direct send",
                ride_id,
            )
            queued = False
    if queued:
        logger.info("auto receipt already queued in outbox ride_id={}", ride_id)
        return True

    send_fn = send
    if send_fn is None:
        try:
            from .payment_service import send_ride_receipt as send_fn
        except ImportError:
            from services.payment_service import send_ride_receipt as send_fn  # type: ignore

    if spawn is not None:
        spawn(send_fn(ride, rider_id, tip))
        return True
    return bool(await send_fn(ride, rider_id, tip))
