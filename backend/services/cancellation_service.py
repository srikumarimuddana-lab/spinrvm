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
    from ..features import calculate_all_fees
    from . import corporate_wallet_service
except ImportError:
    from features import calculate_all_fees  # type: ignore
    from services import corporate_wallet_service  # type: ignore

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
    pickup = parse_iso_utc(ride.get("scheduled_time")) if ride.get("is_scheduled") else None
    if pickup and datetime.now(timezone.utc) < pickup:
        return _d(0), _d(0)
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


async def compute_cancellation_fee_tax(
    fee: Decimal,
    settings: dict,
    area: dict | None,
) -> Tuple[Decimal, dict]:
    """Return (tax_amount, tax_breakdown) owed ON TOP OF a cancellation/no-show fee.

    POLICY DECISION ENCODED HERE — pending legal/tax confirmation
    (docs/change-log/2026-09-23-cancellation-fee-tax-receipt-corporate.md):
    this implements the tax-inclusive interpretation, i.e. that a
    cancellation/no-show fee is consideration for a taxable supply and
    attracts the same GST/PST/HST as the ride fare in that service area.
    Gated behind ``cancellation_fee_tax_enabled`` (default OFF) so it ships
    dark until that interpretation is confirmed.

    Reuses ``features.calculate_all_fees`` — the canonical fare-path tax
    computation (per-area GST/PST/HST enablement + rates, each tax quantized
    independently) — rather than a second tax-rate lookup. The already-
    resolved ``area`` is passed as ``_matched_area`` and ``_area_fees=[]`` so
    only tax is computed: area fees (airport/night/custom surcharges) belong
    to a trip and must never be added to a cancellation fee. Pure: no DB
    reads with those overrides.

    Returns (0, {}) when the flag is off, the fee is zero, or the ride has no
    service area (same as the fare path, which charges no tax without a
    matched area).
    """
    if fee <= 0 or not settings.get("cancellation_fee_tax_enabled", False) or not area:
        return _d(0), {}
    result = await calculate_all_fees(
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        _round(_d(fee)),
        _all_areas=[],
        _matched_area=area,
        _area_fees=[],
    )
    return _round(_d(result.get("tax_amount") or 0)), dict(result.get("tax_breakdown") or {})


async def _record_corporate_fee_writeoff(
    *,
    ride_id: str,
    company_id: Optional[str],
    amount: Decimal,
    fee_driver: Decimal,
    reason: str,
    source: str,
    actor_user_id: str,
) -> None:
    """Queryable per-ride record that a corporate cancellation fee was NOT billed.

    ``audit_logs`` action ``corporate_cancellation_fee_unbilled`` — finance
    can list every ride where the driver was paid a cancellation fee out of
    Spinr's own funds with no corporate debit behind it.
    """
    try:
        await db_supabase.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "action": "corporate_cancellation_fee_unbilled",
                "entity_type": "rides",
                "entity_id": ride_id,
                "actor_id": actor_user_id,
                "details": {
                    "company_id": company_id,
                    "amount": str(_round(_d(amount))),
                    "fee_driver": str(_round(_d(fee_driver))),
                    "reason": reason,
                    "source": source,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.opt(exception=True).error(
            "[CANCEL] corporate fee write-off audit row failed ride={} company={} reason={} amount={}",
            ride_id,
            company_id,
            reason,
            _round(_d(amount)),
        )


async def bill_corporate_cancellation_fee(
    *,
    ride: dict,
    ride_id: str,
    amount: Decimal,
    fee_driver: Decimal,
    settings: dict,
    actor_user_id: str,
    source: str,
) -> str:
    """Bill a company_allowance ride's cancellation/no-show fee to the company.

    Previously nothing billed anyone for a corporate ride's cancellation fee
    while ``pay_driver_cancellation_fee`` still paid the driver — an
    untracked Spinr-funded write-off per ride.

    Debits the company MASTER wallet via
    ``corporate_wallet_service.apply_adjustment`` — the same wrapper and
    ``corporate_wallet_apply_delta`` row-locking RPC ``settle_corporate``'s
    master-fallback debit uses, with ``ride_id`` set so the RPC's ride-scoped
    idempotency (migration 297: wallet + ride_id + type + scope) makes a
    replayed cancellation a no-op. A cancelled ride never reaches
    settle_corporate, so this row can never collide with a settlement debit
    for the same ride. ``floor=0`` matches settle_corporate. The member's
    allowance is deliberately NOT consumed (a fee is not a trip; see the
    change log's open questions).

    Gated by ``corporate_cancellation_fee_billing_enabled`` (default OFF —
    a new debit on a company's ledger is corporate-admin-visible) and by the
    ``corporate_billing_enabled`` incident kill switch. Whenever the fee is
    not billed — flag off, kill switch, no wallet, or the debit failing — a
    ``corporate_cancellation_fee_unbilled`` audit row is written instead.

    Returns "billed" | "deduped" | "unbilled". Never raises: the cancel is
    already persisted and the driver must still be released.
    """
    company_id = ride.get("corporate_account_id")
    amount = _round(_d(amount))
    if amount <= 0:
        return "unbilled"

    async def _writeoff(reason: str) -> str:
        await _record_corporate_fee_writeoff(
            ride_id=ride_id,
            company_id=company_id,
            amount=amount,
            fee_driver=fee_driver,
            reason=reason,
            source=source,
            actor_user_id=actor_user_id,
        )
        return "unbilled"

    if not company_id:
        logger.error("[CANCEL] company_allowance ride {} has no corporate_account_id — fee unbilled", ride_id)
        return await _writeoff("no_corporate_account")
    if not settings.get("corporate_cancellation_fee_billing_enabled", False):
        logger.info("[CANCEL] corporate cancellation fee billing flag off ride={} amount={}", ride_id, amount)
        return await _writeoff("billing_flag_off")
    if not settings.get("corporate_billing_enabled", True):
        logger.error("[CANCEL] corporate billing kill switch on — fee unbilled ride={} amount={}", ride_id, amount)
        return await _writeoff("corporate_billing_disabled")

    try:
        corp_wallet = await db_supabase.get_corporate_wallet_by_company(company_id) or {}
        if not corp_wallet.get("id"):
            logger.error("[CANCEL] company {} has no wallet — cancellation fee unbilled ride={}", company_id, ride_id)
            return await _writeoff("no_wallet")
        result = await corporate_wallet_service.apply_adjustment(
            wallet_id=corp_wallet["id"],
            amount=-amount,
            notes=f"ride:{ride_id}:{source}",
            actor_user_id=actor_user_id,
            floor=Decimal("0"),
            ride_id=ride_id,
        )
    except Exception as exc:
        details = getattr(exc, "details", None)
        logger.opt(exception=True).error(
            "[CANCEL] corporate cancellation fee debit failed ride={} company={} amount={}: {}",
            ride_id,
            company_id,
            amount,
            details.get("original", exc) if isinstance(details, dict) else exc,
        )
        return await _writeoff("debit_failed")

    if isinstance(result, dict) and result.get("deduped"):
        logger.info("[CANCEL] corporate cancellation fee already billed ride={} (idempotent replay)", ride_id)
        return "deduped"
    logger.info("[CANCEL] corporate cancellation fee billed ride={} company={} amount={}", ride_id, company_id, amount)
    return "billed"


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
        # WS-6 atomic locked credit (#4604 finding 1) -- this previously read
        # the balance, computed balance + fee in Python, and wrote it back
        # filtered on {id} only, same pre-WS-6 pattern the sibling RIDER-side
        # debits at cancellation.py/ride_cancel.py were already migrated off
        # of. Two near-simultaneous credits to the same driver (two rides, or
        # racing an admin debit/payout) could silently lose one; with no
        # reference_id dedup, a retry would double-credit with no backstop.
        # reference_id=ride_id makes a replayed payout idempotent instead.
        await db_supabase.wallet_apply_delta(
            wallet_id=wallet["id"],
            user_id=driver_user_id,
            type_="cancellation_fee",
            delta=fee_dec,
            reference_id=ride_id,
            description=f"Cancellation fee for ride {ride_id}",
            metadata={
                "ride_id": ride_id,
                "status_at_cancel": ride_status_at_cancel,
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
