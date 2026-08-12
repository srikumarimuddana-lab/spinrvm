"""Cancellation service — fee calculation, driver compensation, ride cleanup.

Extracted from routes/rides.py (Phase 5 of god-object decomposition).
"""

import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Tuple

from loguru import logger

try:
    from .. import db_supabase
    from ..features import send_push_notification
    from ..models.ride_status import RideStatus
except ImportError:
    import db_supabase  # type: ignore
    from features import send_push_notification  # type: ignore
    from models.ride_status import RideStatus  # type: ignore

try:
    from ..utils.datetime_utils import parse_iso_utc
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _round(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _f(v: Decimal) -> float:
    return float(_round(_d(v)))


def _resolve_cancel_fees(
    settings: dict,
    area: dict | None = None,
) -> Tuple[Decimal, Decimal, int]:
    """Resolve (admin_fee, driver_fee, free_cancel_window) from area → global fallback."""
    if area and area.get("cancel_fee_admin_share") is not None:
        fee_admin = _d(area["cancel_fee_admin_share"])
    else:
        fee_admin = _d(settings.get("cancellation_fee_admin", "0.50"))

    if area and area.get("cancel_fee_driver_share") is not None:
        fee_driver = _d(area["cancel_fee_driver_share"])
    else:
        fee_driver = _d(settings.get("cancellation_fee_driver", "4.00"))

    if area and area.get("free_cancel_window_seconds") is not None:
        window = int(area["free_cancel_window_seconds"])
    else:
        window = int(settings.get("free_cancel_window_seconds", 120))

    return fee_admin, fee_driver, window


def calculate_scheduled_cancel_notice_fee(ride: dict, settings: dict) -> Decimal:
    """Notice-window fee for a PRE-DISPATCH scheduled-ride cancellation
    (scheduled-rides gap review, Finding #01).

    Unlike ``calculate_cancellation_fee`` above, no driver is ever involved
    pre-dispatch — this is rider-only, nothing is disbursed to a driver.
    Flag-gated (``scheduled_ride_notice_window_fee_enabled``, default off).
    Returns 0 (free) when: the flag is off, ``scheduled_time`` is missing or
    unparseable, the pickup time has already passed (should be unreachable
    via the normal cancel flow — the dispatcher would have claimed the ride
    by then — but never charge against a stale timestamp), the ride is
    corporate-paid (``payment_method == "company_allowance"`` — that fee
    belongs on the corporate wallet ledger, intentionally not wired up here,
    mirroring the same exclusion in ``calculate_cancellation_fee``'s card
    branch), or the cancellation happened outside the notice window.
    """
    if not settings.get("scheduled_ride_notice_window_fee_enabled", False):
        return _d(0)
    if (ride.get("payment_method") or "").lower() == "company_allowance":
        return _d(0)
    scheduled_time_str = ride.get("scheduled_time")
    if not scheduled_time_str:
        return _d(0)
    scheduled_time = parse_iso_utc(scheduled_time_str)
    if scheduled_time is None:
        return _d(0)

    window_minutes = int(settings.get("scheduled_ride_notice_window_minutes", 60))
    seconds_to_pickup = (scheduled_time - datetime.now(timezone.utc)).total_seconds()
    if seconds_to_pickup < 0 or seconds_to_pickup > window_minutes * 60:
        return _d(0)
    return _d(settings.get("scheduled_ride_notice_window_fee_amount", "3.00"))


def calculate_cancellation_fee(
    ride: dict,
    settings: dict,
    area: dict | None = None,
) -> Tuple[Decimal, Decimal]:
    """Return (admin_fee, driver_fee) based on ride state and timing.

    ``area`` is the service_area row for per-area fee overrides.
    Returns (0, 0) when no fee applies (early cancel, no driver yet).
    """
    driver_id = ride.get("driver_id")
    fee_admin, fee_driver, free_window = _resolve_cancel_fees(settings, area)

    if ride.get("status") == RideStatus.DRIVER_ARRIVED and driver_id:
        return fee_admin, fee_driver

    if driver_id and ride.get("driver_accepted_at"):
        accepted_at = parse_iso_utc(ride["driver_accepted_at"])
        time_diff = (datetime.now(timezone.utc) - accepted_at).total_seconds() if accepted_at else 0
        if time_diff > free_window:
            return fee_admin, fee_driver

    return _d(0), _d(0)


def calculate_noshow_fee(
    ride: dict,
    settings: dict,
    area: dict | None = None,
) -> Tuple[Decimal, Decimal]:
    """Return (admin_fee, driver_fee) for a no-show cancellation.

    Uses per-area overrides when available, falls back to global settings.
    """
    fee_admin, fee_driver, _ = _resolve_cancel_fees(settings, area)
    return fee_admin, fee_driver


async def pay_driver_cancellation_fee(
    ride_id: str,
    driver_id: str,
    fee: Decimal,
    actor_user_id: str,
    ride_status_at_cancel: Optional[str] = None,
) -> bool:
    """Credit the driver's wallet with the cancellation fee and push-notify.

    Returns True on success, False if the payout fails (logged, never raises).
    """
    try:
        driver = await db_supabase.get_driver_by_id(driver_id)
        driver_user_id = driver.get("user_id") if driver else None
        if not driver_user_id:
            return False

        wallet = await db_supabase.find_one("wallets", {"user_id": driver_user_id})
        if not wallet:
            return False

        fee_dec = _d(str(fee))
        new_balance = _round(_d(str(wallet.get("balance", 0))) + fee_dec)
        await db_supabase.update_one(
            "wallets",
            {"id": wallet["id"]},
            {
                "balance": _f(new_balance),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await db_supabase.insert_one(
            "wallet_transactions",
            {
                "id": str(uuid.uuid4()),
                "wallet_id": wallet["id"],
                "user_id": driver_user_id,
                "type": "cancellation_fee",
                "amount": _f(fee_dec),
                "balance_after": _f(new_balance),
                "reference_id": ride_id,
                "description": f"Cancellation fee for ride {ride_id}",
                "metadata": {
                    "ride_id": ride_id,
                    "status_at_cancel": ride_status_at_cancel,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        try:
            await db_supabase.insert_one(
                "audit_logs",
                {
                    "id": str(uuid.uuid4()),
                    "action": "cancellation_fee_charged",
                    "entity_type": "rides",
                    "entity_id": ride_id,
                    "actor_id": actor_user_id,
                    "details": {
                        "fee_amount": _f(fee_dec),
                        "driver_id": driver_id,
                        "ride_status_at_cancel": ride_status_at_cancel,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            logger.opt(exception=True).error(
                "[CANCEL] audit_log write failed for cancellation_fee_charged ride={} driver={}", ride_id, driver_id
            )

        await send_push_notification(
            driver_user_id,
            title="Cancellation fee earned",
            body=f"${fee_dec:.2f} cancellation fee added to your earnings.",
            data={"type": "cancellation_fee_paid", "ride_id": ride_id},
            target_app="driver",
        )
        return True
    except Exception as fee_err:
        logger.opt(exception=True).error(f"[CANCEL] cancellation fee payout failed for driver {driver_id}: {fee_err}")
        return False
