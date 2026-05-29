"""Payment settlement service — wallet, corporate, and Stripe card paths.

Extracted from routes/rides.py (Phase 4 of god-object decomposition).
Each settlement function handles one payment method and returns a result
dict; the route handler maps results to HTTP responses.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

from loguru import logger

try:
    from .. import db_supabase
    from ..services import corporate_allowance_service, corporate_wallet_service
    from ..services.corporate_policy_service import evaluate_policy
    from ..socket_manager import manager
    from ..utils.stripe_charge import charge_ride
except ImportError:
    import db_supabase  # type: ignore
    from services import corporate_allowance_service, corporate_wallet_service  # type: ignore
    from services.corporate_policy_service import evaluate_policy  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.stripe_charge import charge_ride  # type: ignore

try:
    from ..features import send_push_notification
except ImportError:
    from features import send_push_notification  # type: ignore


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _round(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _f(v: Decimal) -> float:
    return float(_round(_d(v)))


def _money_str(v: Decimal) -> str:
    return f"{_round(v):.2f}"


# ── Result types ─────────────────────────────────────────────────────


@dataclass
class PaymentResult:
    success: bool
    charged_amount: str = "0.00"
    already_paid: bool = False
    email_sent: bool = False
    error: Optional[str] = None
    error_code: Optional[str] = None
    status_code: int = 200
    decline_code: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ── Ledger ───────────────────────────────────────────────────────────


async def record_payment_event(
    ride_id: str,
    user_id: str,
    amount_cents: int,
    payment_intent_id: str | None = None,
    *,
    ride: dict | None = None,
    tip_amount: Decimal | None = None,
) -> None:
    """Append a stripe_charge row to the financial_events ledger.

    Called BEFORE the ride DB update so a recovery record always exists even
    if the ride row stays stuck in 'processing'. Never raises — logs and returns.
    """
    meta: Dict[str, Any] = {"source": "process_payment"}
    if ride:
        fare = _round(_d(ride.get("total_fare", 0)))
        meta.update(
            {
                "fare_amount": str(fare),
                "tip_amount": str(_round(_d(tip_amount))) if tip_amount else "0.00",
                "driver_id": ride.get("driver_id") or "",
                "surge_multiplier": str(ride.get("surge_multiplier") or "1.0"),
                "payment_method": ride.get("payment_method") or "card",
                "pickup_address": (ride.get("pickup_address") or "")[:200],
                "dropoff_address": (ride.get("dropoff_address") or "")[:200],
            }
        )
    try:
        await db_supabase.insert_one(
            "financial_events",
            {
                "event_type": "stripe_charge",
                "user_id": user_id,
                "ride_id": ride_id,
                "delta_cents": amount_cents,
                "ref": payment_intent_id,
                "metadata": meta,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as ledger_err:
        logger.error(
            "[PAYMENT] financial_events write failed for ride %s pi=%s: %s",
            ride_id,
            payment_intent_id,
            ledger_err,
            exc_info=True,
        )


# ── Wallet settlement ───────────────────────────────────────────────


async def settle_wallet(
    ride: dict,
    ride_id: str,
    rider_id: str,
    total_charge: Decimal,
    tip_amount: Decimal,
    fare_breakdown: Optional[list] = None,
) -> PaymentResult:
    """Debit rider's wallet for fare + tip."""
    wallet = await db_supabase.find_one("wallets", {"user_id": rider_id})
    if not wallet:
        wallet = {
            "id": str(uuid.uuid4()),
            "user_id": rider_id,
            "balance": "0.00",
            "currency": "CAD",
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await db_supabase.insert_one("wallets", wallet)

    if not wallet.get("is_active", True):
        await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
        return PaymentResult(
            success=False,
            error="Wallet is suspended",
            status_code=403,
        )

    debit = _round(total_charge)
    # Atomic debit + mark-paid + tip credit via wallet_pay_for_ride RPC
    # (migration 110): locks wallet FOR UPDATE, checks balance, debits,
    # sets payment_status='paid', writes tip_amount and driver_earnings delta
    # — all in one Postgres transaction. A Python crash after this call
    # cannot leave tip money collected but missing from driver earnings.
    try:
        new_balance = await db_supabase.wallet_pay_for_ride(wallet["id"], ride_id, debit, tip_amount)
    except ValueError as exc:
        await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
        if "insufficient_funds" in str(exc):
            current = _round(_d((await db_supabase.find_one("wallets", {"id": wallet["id"]}) or {}).get("balance", 0)))
            return PaymentResult(
                success=False,
                error=f"Insufficient wallet balance. Need ${debit}, have ${current}",
                status_code=400,
            )
        logger.error("settle_wallet: wallet_pay_for_ride failed for ride %s: %s", ride_id, exc, exc_info=True)
        return PaymentResult(success=False, error="Wallet payment failed", status_code=400)

    # None means the RPC fired its idempotent no-op (ride already paid in a
    # previous attempt). No money moved, so we must NOT append a ledger row.
    if new_balance is None:
        logger.info("settle_wallet: ride %s already paid — idempotent no-op, skipping ledger write", ride_id)
        return PaymentResult(success=True, already_paid=True, charged_amount=_money_str(total_charge))

    grand_total = _round(_d(ride.get("grand_total") or ride.get("total_fare", 0) or 0))
    ride_fare = _round(
        _d(ride.get("base_fare") or 0) + _d(ride.get("distance_fare") or 0) + _d(ride.get("time_fare") or 0)
    )
    await db_supabase.insert_one(
        "wallet_transactions",
        {
            "id": str(uuid.uuid4()),
            "wallet_id": wallet["id"],
            "user_id": rider_id,
            "type": "ride_payment",
            "amount": -_f(debit),
            "balance_after": _f(new_balance),
            "reference_id": ride_id,
            "description": f"Ride payment ${_f(debit):.2f}",
            "metadata": {
                "ride_id": ride_id,
                "ride_code": ride.get("ride_code") or "",
                "fare_amount": str(grand_total),
                "ride_fare": str(ride_fare),
                "tip_amount": str(_round(_d(tip_amount))),
                "driver_id": ride.get("driver_id") or "",
                "surge_multiplier": str(ride.get("surge_multiplier") or "1.0"),
                "pickup_address": (ride.get("pickup_address") or "")[:200],
                "dropoff_address": (ride.get("dropoff_address") or "")[:200],
                "discount_amount": str(_round(_d(ride.get("discount_amount") or 0))),
                "promo_code": ride.get("promo_code") or "",
                "grand_total": str(grand_total),
                "fare_breakdown": fare_breakdown or [],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    # payment_status, tip_amount, and driver_earnings are already written
    # atomically by the RPC above — no separate update_ride needed here.
    return PaymentResult(success=True, charged_amount=_money_str(total_charge))


# ── Corporate settlement ─────────────────────────────────────────────


async def settle_corporate(
    ride: dict,
    ride_id: str,
    total_charge: Decimal,
    tip_amount: Decimal,
) -> PaymentResult:
    """Corporate allowance + master wallet saga."""
    company_id = ride.get("corporate_account_id")
    if not company_id:
        return PaymentResult(
            success=False,
            error="Corporate account not set on ride",
            status_code=400,
        )

    memberships = await db_supabase.list_active_memberships_for_user(ride["rider_id"])
    membership = next((m for m in memberships if m.get("company_id") == company_id), None)
    if not membership:
        await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
        return PaymentResult(
            success=False,
            error="Corporate membership not found",
            status_code=400,
        )

    allowance = await db_supabase.get_member_allowance(membership["id"]) or {}
    corp_wallet = await db_supabase.get_corporate_wallet_by_company(company_id) or {}

    total = _round(_d(str(total_charge)))
    if allowance.get("type") == "unlimited":
        allowance_debit = total
        master_debit = _round(Decimal("0"))
    else:
        remaining = _round(_d(str(allowance.get("amount") or 0)) - max(_d(str(allowance.get("used") or 0)), _d("0")))
        remaining = max(remaining, _round(Decimal("0")))
        allowance_debit = min(remaining, total)
        master_debit = total - allowance_debit

    corp_policy = await db_supabase.get_corporate_policy(company_id) or {}
    flag_violation = master_debit > 0 and corp_policy.get("allowed_payment_source") == "allowance_only"

    allowance_applied = False
    if allowance_debit > 0 and allowance.get("id") and corp_wallet.get("id"):
        await corporate_allowance_service.apply_rollback(
            wallet_id=corp_wallet["id"],
            allowance_id=allowance["id"],
            member_id=membership["id"],
            amount=_f(allowance_debit),
            notes=f"ride:{ride_id}:allowance",
        )
        allowance_applied = True

    if master_debit > 0 and corp_wallet.get("id"):
        try:
            await corporate_wallet_service.apply_adjustment(
                wallet_id=corp_wallet["id"],
                amount=-_f(master_debit),
                notes=f"Ride fallback debit {ride_id}",
                actor_user_id=ride.get("rider_id", "system"),
                floor=0.0,
            )
        except Exception as master_err:
            if allowance_applied:
                try:
                    await corporate_allowance_service.apply_grant(
                        wallet_id=corp_wallet["id"],
                        allowance_id=allowance["id"],
                        member_id=membership["id"],
                        amount=_f(allowance_debit),
                        notes=f"ride:{ride_id}:allowance_compensation",
                    )
                except Exception as comp_err:
                    logger.error(
                        "[PAYMENT] Allowance compensation failed for ride %s — "
                        "allowance %.2f was debited but master wallet was NOT; "
                        "manual ledger fix required. comp_err=%s",
                        ride_id,
                        allowance_debit,
                        comp_err,
                        exc_info=True,
                    )
            await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
            logger.error(
                "[PAYMENT] Master wallet debit failed for ride %s: %s",
                ride_id,
                master_err,
                exc_info=True,
            )
            return PaymentResult(
                success=False,
                error="Corporate payment failed — please retry.",
                status_code=503,
            )

    await db_supabase.insert_one(
        "ride_payment_sources",
        {
            "ride_id": ride_id,
            "source_type": "company_allowance",
            "allowance_debit_amount": _f(allowance_debit),
            "master_fallback_amount": _f(master_debit),
            "member_id": membership["id"],
            "company_id": company_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    completion_ctx = {
        "final_fare": _f(total),
        "phase": "completion",
        "allowance": allowance,
    }
    completion_eval = evaluate_policy(corp_policy, completion_ctx)
    if not completion_eval["pass"] or flag_violation:
        await db_supabase.insert_one(
            "corporate_policy_evaluations",
            {
                "ride_id": ride_id,
                "member_id": membership["id"],
                "company_id": company_id,
                "phase": "completion",
                "result": "violation",
                "failed_rules": completion_eval.get("failed_rules", []),
                "bypassed_rules": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    await db_supabase.update_ride(
        ride_id,
        {
            "payment_status": "paid",
            "tip_amount": _f(tip_amount),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return PaymentResult(success=True, charged_amount=_money_str(total_charge))


# ── Card (Stripe) settlement ─────────────────────────────────────────


async def settle_card(
    ride: dict,
    ride_id: str,
    rider_id: str,
    total_charge: Decimal,
    tip_amount: Decimal,
) -> PaymentResult:
    """Charge rider's card via Stripe."""
    rider_user = await db_supabase.get_user_by_id(rider_id)
    stripe_customer_id = (rider_user or {}).get("stripe_customer_id")
    payment_method_id = ride.get("payment_method_id") or (rider_user or {}).get("default_payment_method")

    if not payment_method_id:
        await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
        return PaymentResult(
            success=False,
            error="No payment method on file. Please add a card.",
            status_code=400,
        )

    outcome = await charge_ride(
        ride=ride,
        # Pass the Decimal straight through — charge_ride does the cents
        # conversion via dollars_to_cents. Never coerce money to float.
        total_amount=total_charge,
        rider_id=rider_id,
        payment_method_id=payment_method_id,
        stripe_customer_id=stripe_customer_id,
        payment_intent_id=ride.get("payment_intent_id"),
    )

    if outcome.status == "succeeded":
        await record_payment_event(
            ride_id=ride_id,
            user_id=rider_id,
            amount_cents=int(_round(total_charge * Decimal("100"))),
            payment_intent_id=outcome.payment_intent_id,
            ride=ride,
            tip_amount=tip_amount,
        )
        try:
            await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "paid",
                    "payment_intent_id": outcome.payment_intent_id,
                    "tip_amount": _f(tip_amount),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as db_err:
            logger.error(
                "[PAYMENT] Stripe charge %s confirmed but ride %s DB update failed — "
                "ride stuck in 'processing'; financial_events written for recovery. err=%s",
                outcome.payment_intent_id,
                ride_id,
                db_err,
                exc_info=True,
            )
            return PaymentResult(
                success=False,
                error="Payment was captured but confirmation failed. Do not retry — our team has been notified.",
                status_code=503,
            )
        await manager.send_personal_message(
            {
                "type": "payment_completed",
                "ride_id": ride_id,
                "charged_amount": _money_str(total_charge),
            },
            f"rider_{rider_id}",
        )
        return PaymentResult(success=True, charged_amount=_money_str(total_charge))

    if outcome.status == "requires_action":
        await db_supabase.update_ride(
            ride_id,
            {
                "payment_status": "failed",
                "payment_intent_id": outcome.payment_intent_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return PaymentResult(
            success=False,
            error_code="authentication_required",
            error="Card requires authentication. Please update your payment method.",
            status_code=402,
        )

    if outcome.status == "declined":
        await db_supabase.update_ride(
            ride_id,
            {
                "payment_status": "failed",
                "payment_intent_id": outcome.payment_intent_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        rider_id_for_push = ride.get("rider_id")
        if rider_id_for_push:
            try:
                await send_push_notification(
                    rider_id_for_push,
                    "Payment failed",
                    "Your payment method was declined. Please update your payment method in the app.",
                    data={
                        "type": "payment_failed",
                        "ride_id": ride_id,
                        "deeplink": "/wallet",
                    },
                )
            except Exception as _push_err:
                logger.debug(f"Payment failure push to rider failed: {_push_err}")
        return PaymentResult(
            success=False,
            error_code="card_declined",
            decline_code=outcome.decline_code,
            error=outcome.error_message or "Your card was declined.",
            status_code=402,
            extra={"suggested_action": "change_card"},
        )

    if outcome.status == "unconfigured":
        logger.error("Stripe unconfigured — marking ride %s paid without real charge", ride_id)
        await db_supabase.update_ride(
            ride_id,
            {
                "payment_status": "paid",
                "tip_amount": _f(tip_amount),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return PaymentResult(success=True, charged_amount=_money_str(total_charge))

    # Generic failure
    await db_supabase.update_ride(
        ride_id,
        {
            "payment_status": "failed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    rider_id_for_push = ride.get("rider_id")
    if rider_id_for_push:
        try:
            await send_push_notification(
                rider_id_for_push,
                "Payment failed",
                "Your payment method was declined. Please update your payment method in the app.",
                data={
                    "type": "payment_failed",
                    "ride_id": ride_id,
                    "deeplink": "/wallet",
                },
            )
        except Exception as _push_err:
            logger.debug(f"Payment failure push to rider failed: {_push_err}")
    return PaymentResult(
        success=False,
        error_code="payment_error",
        error=outcome.error_message or "Payment could not be processed.",
        status_code=402,
    )


# ── Receipt ──────────────────────────────────────────────────────────


async def send_ride_receipt(ride: dict, rider_id: str, tip_amount: Decimal) -> bool:
    """Send receipt email. Returns True if email was sent."""
    rider = await db_supabase.get_user_by_id(rider_id)
    driver_info = None
    if ride.get("driver_id"):
        drv = await db_supabase.get_driver_by_id(ride["driver_id"])
        if drv:
            du = await db_supabase.get_user_by_id(drv.get("user_id"))
            if du:
                driver_info = {
                    **du,
                    "name": f"{du.get('first_name', '')} {du.get('last_name', '')}".strip(),
                }
    try:
        from utils.email_receipt import send_receipt_email

        return await send_receipt_email(ride, rider or {}, driver_info, _f(tip_amount))
    except Exception as e:
        logger.error(f"Receipt email error: {e}", exc_info=True)
        return False
