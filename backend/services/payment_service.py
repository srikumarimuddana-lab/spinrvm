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
    from ..utils.stripe_charge import cancel_authorization, capture_ride, charge_ride
except ImportError:
    import db_supabase  # type: ignore
    from services import corporate_allowance_service, corporate_wallet_service  # type: ignore
    from services.corporate_policy_service import evaluate_policy  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.stripe_charge import cancel_authorization, capture_ride, charge_ride  # type: ignore

try:
    from ..core.config import settings as app_config
    from ..features import send_push_notification
    from ..utils.pii import area_only
except ImportError:
    from core.config import settings as app_config  # type: ignore
    from features import send_push_notification  # type: ignore
    from utils.pii import area_only  # type: ignore


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _round(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _f(v: Decimal) -> float:
    return float(_round(_d(v)))


def _money_str(v: Decimal) -> str:
    return f"{_round(v):.2f}"


async def _refuse_unconfigured_settlement(ride_id: str, context: str) -> "PaymentResult":
    """Production guard for the Stripe-unconfigured settlement paths.

    ``charge_ride``/``capture_ride`` return status ``unconfigured`` when
    ``stripe_secret_key`` is empty. In dev/test the callers mark the ride paid
    so flows don't wedge — but the key lives in the app_settings DB row (not
    env), so there is NO startup fail-fast: if the row is ever blanked in
    production (e.g. mid key-rotation), every settling ride would be marked
    paid for free. Refuse loudly instead and leave the ride collectible.
    """
    logger.error(
        "[PAYMENT] stripe_secret_key is EMPTY in production — refusing to mark "
        "ride {} paid without a real {}. Restore the key in app_settings; the "
        "retry loop / admin invoicing will collect this ride.",
        ride_id,
        context,
    )
    await db_supabase.update_ride(
        ride_id,
        {
            "payment_status": "failed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return PaymentResult(
        success=False,
        error_code="stripe_unconfigured",
        error="Payment processing is temporarily unavailable. Please try again shortly.",
        status_code=503,
    )


def _tip_ride_update(ride: dict, tip_amount: Decimal) -> dict:
    """Fields to merge into an update_ride call when a tip is being settled.

    Applies the delta (new tip - already-stored tip) to driver_earnings so
    the call is idempotent: re-running with the same tip amount leaves
    driver_earnings unchanged (delta = 0).
    """
    tip_d = _round(tip_amount)
    existing_tip = _round(_d(ride.get("tip_amount") or 0))
    tip_delta = tip_d - existing_tip
    fields: dict = {"tip_amount": _f(tip_d)}
    # Apply the delta in BOTH directions (C3): a downward tip correction must
    # claw the over-credit back out of driver_earnings, not just an increase —
    # otherwise a reduced/removed tip leaves the driver overpaid. Clamp at 0 so
    # a correction can never drive earnings negative.
    if tip_delta != 0:
        new_earnings = _round(_d(ride.get("driver_earnings") or 0) + tip_delta)
        fields["driver_earnings"] = _f(max(new_earnings, _round(Decimal("0"))))
    return fields


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
                # PIPEDA data minimization: financial_events is a 7-year
                # tax/audit ledger that outlives the account-deletion scrub, so
                # only the city-level area may be retained here — the exact
                # address stays on the ride row under its own retention policy.
                # Keys keep the legacy *_address names for reader compatibility.
                "pickup_address": area_only(ride.get("pickup_address")) or "",
                "dropoff_address": area_only(ride.get("dropoff_address")) or "",
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
        logger.opt(exception=True).error(
            "[PAYMENT] financial_events write failed for ride {} pi={}: {}", ride_id, payment_intent_id, ledger_err
        )


async def record_refund_event(
    ride_id: str,
    user_id: str,
    refund_cents: int,
    payment_intent_id: str | None = None,
    *,
    ride: dict | None = None,
) -> None:
    """Append a stripe_refund row to the financial_events ledger (C3).

    A refund previously left NO ledger entry, so the 7-year tax/audit ledger
    over-stated collected revenue + GST. This records the rider refund so
    remittance nets out. Per policy the driver KEEPS their pay on a refund
    (Spinr absorbs the payout), so ``driver_earnings`` is deliberately untouched
    — but we capture the reversed rider-side tax (proportional to the refund
    fraction) and note the retained driver amount for reconciliation.
    ``delta_cents`` is NEGATIVE (money leaving). Never raises.
    """
    meta: Dict[str, Any] = {"source": "charge.refunded", "driver_pay_absorbed_by_platform": True}
    if ride:
        total = _round(_d(ride.get("grand_total") or ride.get("total_fare") or 0))
        tax_total = _round(_d(ride.get("tax_amount") or 0))
        refund_d = _round(_d(refund_cents) / Decimal("100"))
        frac = min(refund_d / total, Decimal("1")) if total > Decimal("0") else Decimal("1")
        meta.update(
            {
                "refund_amount": str(refund_d),
                # Rider-side GST/PST reversed by this refund (remittance nets it out).
                "tax_reversed": str(_round(tax_total * frac)),
                "tax_breakdown": ride.get("tax_breakdown") or {},
                "driver_id": ride.get("driver_id") or "",
                # Driver keeps this — recorded so T4A/reconciliation can see the
                # platform absorbed it rather than clawing it back.
                "driver_earnings_retained": str(_round(_d(ride.get("driver_earnings") or 0))),
            }
        )
    try:
        await db_supabase.insert_one(
            "financial_events",
            {
                "event_type": "stripe_refund",
                "user_id": user_id,
                "ride_id": ride_id,
                "delta_cents": -abs(int(refund_cents)),
                "ref": payment_intent_id,
                "metadata": meta,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as ledger_err:
        logger.opt(exception=True).error(
            "[PAYMENT] refund financial_events write failed for ride {} pi={}: {}",
            ride_id,
            payment_intent_id,
            ledger_err,
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
        logger.opt(exception=True).error("settle_wallet: wallet_pay_for_ride failed for ride {}: {}", ride_id, exc)
        return PaymentResult(success=False, error="Wallet payment failed", status_code=400)

    # None means the RPC fired its idempotent no-op (ride already paid in a
    # previous attempt). No money moved, so we must NOT append a ledger row.
    if new_balance is None:
        logger.info("settle_wallet: ride {} already paid — idempotent no-op, skipping ledger write", ride_id)
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
                # City-level only (PIPEDA): wallet_transactions metadata is
                # financial-ledger data retained past ride-row anonymization.
                "pickup_address": area_only(ride.get("pickup_address")) or "",
                "dropoff_address": area_only(ride.get("dropoff_address")) or "",
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

    # The payer is the member whose allowance is debited. Prefer the member
    # stamped on the ride (rides.corporate_member_id, migration 36): for
    # guest bookings that is the BOOKING EMPLOYEE — the rider (the company's
    # customer) has no membership at all — and for self-booked work rides it
    # is the rider's own membership, saving the list read. Legacy rides
    # booked before the stamp fall back to deriving from the rider.
    membership = None
    stamped_member_id = ride.get("corporate_member_id")
    if stamped_member_id:
        candidate = await db_supabase.get_corporate_member_by_id(stamped_member_id)
        if candidate and candidate.get("company_id") == company_id and candidate.get("status") == "active":
            membership = candidate
        else:
            # A stamped member that doesn't belong to the ride's company (or
            # is no longer active) is a contract violation — surface loudly
            # and leave the ride pending rather than guessing a payer.
            logger.error(
                "[PAYMENT] ride {} corporate_member_id {} invalid for company {} (found={} status={} company={})",
                ride_id,
                stamped_member_id,
                company_id,
                bool(candidate),
                (candidate or {}).get("status"),
                (candidate or {}).get("company_id"),
            )
            await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
            return PaymentResult(
                success=False,
                error="Corporate membership not found",
                status_code=400,
            )
    else:
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

    if not corp_wallet.get("id"):
        # No wallet exists yet (e.g. a self-serve-signed-up company that never
        # completed KYB — a wallet row is only created on KYB approval).
        # Neither the allowance-debit nor master-fallback branches below can
        # execute without a wallet id; letting the ride silently reach
        # payment_status="paid" with zero money moved is the free-ride gap
        # closed at booking time by the require_company_bookable guard (see
        # corporate + admin portal review, Critical #1). This is a
        # defense-in-depth backstop for that gap, not the primary fix — fail
        # loudly instead of settling against a wallet that doesn't exist.
        logger.error(
            "[PAYMENT] company {} has no wallet — cannot settle ride {} against it",
            company_id,
            ride_id,
        )
        await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
        return PaymentResult(
            success=False,
            error="Corporate wallet not found",
            status_code=503,
        )

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
    # flag_violation is computed AFTER the allowance debit resolves — the cap
    # fallback below can flip master_debit from 0 to the full fare, which is
    # itself an allowance_only policy breach that must be flagged.

    allowance_applied = False
    if allowance_debit > 0 and allowance.get("id") and corp_wallet.get("id"):
        # ride_debit: master -amount, used +amount. This previously called
        # apply_rollback, whose master delta is POSITIVE — so every
        # allowance-covered corporate ride CREDITED the company's master wallet
        # instead of charging it (migration 248).
        try:
            await corporate_allowance_service.apply_ride_debit(
                wallet_id=corp_wallet["id"],
                allowance_id=allowance["id"],
                member_id=membership["id"],
                amount=_f(allowance_debit),
                actor_user_id=membership.get("user_id") or ride.get("rider_id"),
                notes=f"ride:{ride_id}:allowance",
                # Corporate + admin portal review, Critical #2: this call
                # previously passed no floor at all, so the master-wallet
                # floor check never engaged for the allowance-funded debit
                # path — only the master-fallback debit three lines below
                # was floor-protected. The per-member allowance ceiling
                # (migration 258) bounds one member's spend per period; it
                # does nothing to protect the shared master wallet balance
                # across the whole company, so without this floor an
                # allowance-funded ride could push the master wallet
                # unboundedly negative. Matches the master-fallback call's
                # floor below.
                floor=0.0,
            )
            allowance_applied = True
        except Exception as _cap_err:
            # The RPC enforces the per-member ceiling under its row lock
            # (migration 258). Our allowance_debit was computed from a
            # non-locking read, so a concurrent settle for the same member can
            # push `used` past `amount` between our read and the lock. When that
            # happens the RPC raises allowance_cap_exceeded — the allowance is
            # genuinely full, so route the whole fare to the company master
            # wallet (the existing fallback) instead of over-spending the cap.
            _detail = ""
            _details_attr = getattr(_cap_err, "details", None)
            if isinstance(_details_attr, dict):
                _detail = str(_details_attr.get("original") or "")
            _cap_err_text = f"{_cap_err} {_detail}"
            if "allowance_cap_exceeded" in _cap_err_text:
                logger.warning(
                    "corporate allowance cap hit under contention for member {} ride {} — "
                    "routing fare to master wallet",
                    membership["id"],
                    ride_id,
                )
                master_debit = total
                allowance_debit = _round(Decimal("0"))
            elif "wallet_below_floor" in _cap_err_text:
                # The master wallet itself would go below its floor even for
                # this allowance-covered ride (Critical #2's new floor check).
                # Unlike the allowance_cap_exceeded case, rerouting to the
                # master-fallback debit below would just hit the identical
                # floor again — there is genuinely no money available for
                # this ride within policy. Fail the same way the
                # master-fallback debit's own floor breach already does.
                logger.error(
                    "[PAYMENT] company {} master wallet at floor — cannot cover allowance debit for ride {}: {}",
                    company_id,
                    ride_id,
                    _cap_err,
                )
                await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
                return PaymentResult(
                    success=False,
                    error="Corporate payment failed — please retry.",
                    status_code=503,
                )
            else:
                raise

    # Final master_debit is known now (the cap fallback may have raised it).
    flag_violation = master_debit > 0 and corp_policy.get("allowed_payment_source") == "allowance_only"

    if master_debit > 0 and corp_wallet.get("id"):
        try:
            await corporate_wallet_service.apply_adjustment(
                wallet_id=corp_wallet["id"],
                amount=-_f(master_debit),
                notes=f"Ride fallback debit {ride_id}",
                # Ledger actor = the PAYING member's user. For guest bookings
                # the rider is the company's customer, not the payer.
                actor_user_id=membership.get("user_id") or ride.get("rider_id", "system"),
                floor=0.0,
            )
        except Exception as master_err:
            if allowance_applied:
                try:
                    # Exact inverse of the ride_debit above. apply_grant would
                    # debit master a second time (its master delta is negative),
                    # compounding the failure instead of compensating it.
                    await corporate_allowance_service.apply_ride_debit_reversal(
                        wallet_id=corp_wallet["id"],
                        allowance_id=allowance["id"],
                        member_id=membership["id"],
                        amount=_f(allowance_debit),
                        notes=f"ride:{ride_id}:allowance_compensation",
                    )
                except Exception as comp_err:
                    logger.opt(exception=True).error(
                        "[PAYMENT] Allowance compensation failed for ride {} — "
                        "allowance {:.2f} was debited but master wallet was NOT; "
                        "manual ledger fix required. comp_err={}",
                        ride_id,
                        allowance_debit,
                        comp_err,
                    )
            await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
            logger.opt(exception=True).error(
                "[PAYMENT] Master wallet debit failed for ride {}: {}", ride_id, master_err
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

    # Visibility-only section spend tracking (corporate + admin portal
    # review round 2, business decision: "department/section budgets" —
    # track and display, never block). Both independent corporate booking
    # paths in routes/rides/booking.py converge here at settlement, so
    # this one hook covers both without touching either. Never affects
    # the settlement that already happened above — a failure here is
    # logged and swallowed, exactly like the audit-only blocks below.
    if membership.get("section_id"):
        try:
            await db_supabase.record_section_spend(
                section_id=membership["section_id"],
                month=datetime.now(timezone.utc).strftime("%Y-%m"),
                amount=allowance_debit + master_debit,
            )
        except Exception:
            logger.opt(exception=True).error(
                "[PAYMENT] Failed to record section spend for ride={} section={}", ride_id, membership["section_id"]
            )

    # Audit-only: a company suspended/closed mid-ride is grandfathered — the
    # ride state machine forbids cancelling after trip start, so billing
    # proceeds normally above. This just makes the fact visible in
    # corporate_policy_evaluations for ops/finance review; it never blocks
    # or alters the settlement that already happened.
    company_status = None
    try:
        company_row = await db_supabase.get_corporate_account_by_id(validated_id=company_id) or {}
        company_status = company_row.get("status")
    except Exception as _status_exc:
        logger.opt(exception=True).error(
            "[PAYMENT] could not read company status for audit flag ride={} company={}: {}",
            ride_id,
            company_id,
            _status_exc,
        )

    # Audit-only, same pattern as company_inactive_during_ride above: a policy
    # edited mid-ride (tighter fare cap, allowed_payment_source narrowed, etc.)
    # is never re-checked against rides already in flight — evaluate_policy_for_ride
    # only runs at booking time, and this completion-phase evaluate_policy call
    # already re-fetches the CURRENT policy, so a tightened rule silently governs
    # settlement without anyone knowing the rider booked under a looser one.
    # This does not cancel or re-price anything — it only makes the drift visible
    # for ops/finance review. Detected without a schema change: corporate_policies
    # has a DB-trigger-maintained updated_at (migration 27); if the policy was
    # updated after this ride was created, it changed mid-flight. Proactive
    # cancellation of in-flight rides on policy edit is a separate, larger
    # product decision — deliberately out of scope here.
    policy_changed_since_booking = False
    try:
        _policy_updated_at = corp_policy.get("updated_at")
        _ride_created_at = ride.get("created_at")
        if _policy_updated_at and _ride_created_at:
            _policy_dt = datetime.fromisoformat(str(_policy_updated_at).replace("Z", "+00:00"))
            _ride_dt = datetime.fromisoformat(str(_ride_created_at).replace("Z", "+00:00"))
            if _policy_dt.tzinfo is None:
                _policy_dt = _policy_dt.replace(tzinfo=timezone.utc)
            if _ride_dt.tzinfo is None:
                _ride_dt = _ride_dt.replace(tzinfo=timezone.utc)
            policy_changed_since_booking = _policy_dt > _ride_dt
    except Exception as _policy_ts_exc:
        logger.opt(exception=True).error(
            "[PAYMENT] could not compare policy/ride timestamps for audit flag ride={} company={}: {}",
            ride_id,
            company_id,
            _policy_ts_exc,
        )

    completion_ctx = {
        "final_fare": _f(total),
        "phase": "completion",
        "allowance": allowance,
    }
    completion_eval = evaluate_policy(corp_policy, completion_ctx)
    if company_status in ("suspended", "closed"):
        completion_eval["failed_rules"] = [*completion_eval.get("failed_rules", []), "company_inactive_during_ride"]
        completion_eval["pass"] = False
    if policy_changed_since_booking:
        completion_eval["failed_rules"] = [*completion_eval.get("failed_rules", []), "policy_changed_since_booking"]
        completion_eval["pass"] = False
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
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **_tip_ride_update(ride, tip_amount),
        },
    )
    return PaymentResult(success=True, charged_amount=_money_str(total_charge))


async def auto_settle_guest_corporate(ride_id: str) -> Optional[PaymentResult]:
    """Server-side settlement for corporate GUEST rides.

    A guest customer has no app and never calls /process-payment, so the
    completion path (and the payment-retry sweep) drives settlement instead.
    Replay-safe: the conditional pending/failed → processing claim below is
    the same atomic gate process_payment uses — concurrent callers (driver
    completion hook + retry sweep on another replica) match zero rows and
    no-op. Guests cannot tip: tip is pinned to 0.
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        logger.error("[PAYMENT] auto-settle: ride {} not found", ride_id)
        return None
    if not ride.get("guest_booking") or ride.get("payment_method") != "company_allowance":
        return None
    if ride.get("status") != "completed":
        # Only completed rides settle — the caller hooks fire on completion,
        # but the retry sweep must never settle an in-flight ride.
        return None

    try:
        from ..utils.metrics import inc as _metric_inc
    except ImportError:
        from utils.metrics import inc as _metric_inc  # type: ignore

    claimed = None
    for _claim_status in ("pending", "failed"):
        claimed = await db_supabase.update_one(
            "rides",
            {"id": ride_id, "payment_status": _claim_status},
            {"payment_status": "processing", "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        if claimed:
            break
    if not claimed:
        return None  # another replica/settlement won the claim — done

    total = _round(_d(str(ride.get("grand_total") or ride.get("total_fare") or 0)))
    try:
        result = await settle_corporate(ride, ride_id, total_charge=total, tip_amount=Decimal("0"))
    except Exception:
        # settle_corporate resets payment_status itself on its known failure
        # paths; an unexpected raise would strand the ride in 'processing' —
        # release the claim so the retry sweep can pick it up again.
        logger.opt(exception=True).error("[PAYMENT] auto-settle crashed for guest ride {}", ride_id)
        await db_supabase.update_one(
            "rides",
            {"id": ride_id, "payment_status": "processing"},
            {"payment_status": "pending", "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        return None
    _metric_inc(
        "spinr_payment_settlement_total",
        {"outcome": "success" if result.success else "failed", "path": "corporate_guest_auto"},
    )
    if not result.success:
        logger.error(
            "[PAYMENT] auto-settle failed for guest ride {}: {} (status {})",
            ride_id,
            result.error,
            result.status_code,
        )
    elif not result.already_paid:
        # Meta Purchase for the corporate-guest path. This settlement is
        # server-driven and never touches a rider's device, so without this
        # hook these rides would be the one class of paid ride that produces
        # no conversion at all.
        await _fire_guest_purchase_conversion(ride, ride_id, result.charged_amount)
    return result


async def _fire_guest_purchase_conversion(ride: dict, ride_id: str, charged_amount) -> None:
    """Queue Meta Purchase for a guest corporate ride. Never raises.

    Delegates to the shared entry point used by every server-driven settlement
    path, so rider lookup and error containment stay in one place.
    """
    try:
        from .meta_conversions_service import send_ride_purchase_for_ride
    except ImportError:
        from services.meta_conversions_service import send_ride_purchase_for_ride  # type: ignore
    try:
        await send_ride_purchase_for_ride(ride, ride.get("rider_id"), charged_amount)
    except Exception:
        logger.opt(exception=True).error("meta: guest Purchase send failed for ride {}", ride_id)


# ── Card (Stripe) settlement ─────────────────────────────────────────


async def _settle_against_hold(
    ride: dict,
    ride_id: str,
    rider_id: str,
    total_charge: Decimal,
    tip_amount: Decimal,
    *,
    held_pi: str,
    authorized: Decimal,
    stripe_customer_id: Optional[str],
    payment_method_id: Optional[str],
) -> Optional[PaymentResult]:
    """Capture a booking-time hold for (fare + tip) in a single Stripe fee.

    Captures ``min(total_charge, authorized)`` against the manual-capture
    PaymentIntent placed at booking. When the tip pushes the total OVER the
    authorized hold (a tip beyond the buffer), the overflow is charged on a
    fresh PaymentIntent — Stripe forbids capturing more than was authorized.

    Returns:
        PaymentResult — terminal outcome (captured-and-paid, or capture
            declined by the issuer).
        ``None`` — the hold is unusable (expired / amount_too_large / Stripe
            ops error); the caller falls back to a fresh full charge.
    """
    capture_amount = _round(min(total_charge, authorized))
    cap = await capture_ride(ride_id=ride_id, payment_intent_id=held_pi, amount=capture_amount)

    if cap.status == "failed":
        # Hold lapsed or otherwise uncapturable — re-drive via a fresh charge so
        # the rider is still settled. error (not warning): a dropped hold is a
        # payment-path anomaly we must surface, per CLAUDE.md. No exc_info — we
        # are not inside an except block; the Stripe message is in error_message.
        logger.error(
            "[PAYMENT] capture of hold pi={} failed for ride {} ({}) — falling back to fresh charge",
            held_pi,
            ride_id,
            cap.error_message,
        )
        return None

    if cap.status == "declined":
        await db_supabase.update_ride(
            ride_id,
            {
                "payment_status": "failed",
                "payment_intent_id": held_pi,
                "auth_status": "released",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return PaymentResult(
            success=False,
            error_code="card_declined",
            decline_code=cap.decline_code,
            error=cap.error_message or "Your card was declined.",
            status_code=402,
            extra={"suggested_action": "change_card"},
        )

    if cap.status == "unconfigured":
        # DEV/TEST ONLY below the guard: reached only when stripe_secret_key is
        # unset. The key lives in the app_settings DB row (not env), so there is
        # no startup fail-fast — in production refuse to settle rather than
        # marking the ride paid for free. In dev/test, mark paid with no
        # financial_events row — mirroring the fresh-charge `unconfigured`
        # branch below — so flows don't wedge when Stripe isn't wired up.
        if app_config.ENV.lower() == "production":
            return await _refuse_unconfigured_settlement(ride_id, "capture")
        logger.error("Stripe unconfigured — marking ride {} paid (held) without real capture", ride_id)
        await db_supabase.update_ride(
            ride_id,
            {
                "payment_status": "paid",
                "auth_status": "captured",
                "tip_amount": _f(tip_amount),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return PaymentResult(success=True, charged_amount=_money_str(total_charge))

    # cap.status == "captured" — the hold is now real money.
    remainder = _round(total_charge - capture_amount)
    tip_collected = _round(tip_amount)
    extra_charged = Decimal("0")
    if remainder > 0:
        # Tip exceeded the buffer; charge the overflow on a fresh PaymentIntent.
        over = await charge_ride(
            ride=ride,
            total_amount=remainder,
            rider_id=rider_id,
            payment_method_id=payment_method_id,
            stripe_customer_id=stripe_customer_id,
        )
        if over.status == "succeeded":
            extra_charged = remainder
        else:
            # Fare + within-buffer tip are captured; only the EXCESS tip failed.
            # Settle what we actually collected rather than stranding a paid
            # ride. Log loudly — never silently drop the shortfall.
            fare = _round(total_charge - _round(tip_amount))
            tip_collected = _round(authorized - fare)
            if tip_collected < 0:
                tip_collected = Decimal("0")
            logger.error(
                "[PAYMENT] over-buffer tip charge failed for ride {} ({}); captured {}, "
                "tip collected {}, excess {} uncollected",
                ride_id,
                over.error_message,
                _money_str(capture_amount),
                _money_str(tip_collected),
                _money_str(remainder),
            )

    settled_amount = _round(capture_amount + extra_charged)
    await record_payment_event(
        ride_id=ride_id,
        user_id=rider_id,
        amount_cents=int(_round(settled_amount * Decimal("100"))),
        payment_intent_id=cap.payment_intent_id,
        ride=ride,
        tip_amount=tip_collected,
    )
    try:
        await db_supabase.update_ride(
            ride_id,
            {
                "payment_status": "paid",
                "payment_intent_id": cap.payment_intent_id,
                "auth_status": "captured",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **_tip_ride_update(ride, tip_collected),
            },
        )
    except Exception as db_err:
        logger.opt(exception=True).error(
            "[PAYMENT] Capture {} succeeded but ride {} DB update failed — "
            "ride stuck in 'processing'; financial_events written for recovery. err={}",
            cap.payment_intent_id,
            ride_id,
            db_err,
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
            "charged_amount": _money_str(settled_amount),
        },
        f"rider_{rider_id}",
    )
    return PaymentResult(success=True, charged_amount=_money_str(settled_amount))


async def settle_card(
    ride: dict,
    ride_id: str,
    rider_id: str,
    total_charge: Decimal,
    tip_amount: Decimal,
    payment_method_id_override: Optional[str] = None,
) -> PaymentResult:
    """Charge rider's card via Stripe.

    Prefers capturing a booking-time pre-authorization hold (one Stripe fee,
    lets a within-buffer tip ride on the same PaymentIntent). Falls back to a
    fresh charge when there is no usable hold or the hold could not be captured.

    ``payment_method_id_override`` is the in-app "Change Card" escape: when the
    booking-time card was declined (or none was on file), the rider picks a
    different card and we charge THAT card with a fresh PaymentIntent — never
    the booking-time hold, which sits on the rejected card.
    """
    rider_user = await db_supabase.get_user_by_id(rider_id)
    stripe_customer_id = (rider_user or {}).get("stripe_customer_id")

    override_hold_released = False
    override_hold_pi_to_release: Optional[str] = None
    if payment_method_id_override:
        # Rider explicitly chose a different card. Skip the hold path entirely
        # (the hold is on the old, rejected card) and always do a fresh charge.
        payment_method_id = payment_method_id_override
        confirm_pi = None
        # DEFER releasing the old card's hold until the new card actually charges
        # (Codex P1): cancelling the guaranteed authorization up front would lose
        # collectable fare if the new card then declines. We record the hold here
        # and release it in the success path only.
        _old_hold_pi = ride.get("payment_intent_id")
        _old_auth = (ride.get("auth_status") or "").lower()
        if _old_hold_pi and _old_auth in ("authorized", "fare_only"):
            override_hold_pi_to_release = _old_hold_pi
    else:
        payment_method_id = ride.get("payment_method_id") or (rider_user or {}).get("default_payment_method")

        # Settle against a pre-authorized hold when one is open. ``auth_status``
        # distinguishes a manual-capture hold from a prior 3DS auto-capture PI that
        # also lives in ``payment_intent_id`` — only the former is captured here.
        held_pi = ride.get("payment_intent_id")
        auth_status = (ride.get("auth_status") or "").lower()
        authorized = _round(_d(ride.get("authorized_amount") or 0))
        # Default fresh-charge confirm target: a stored PI is only reused for the
        # 3DS-retry case (no open hold). After an unusable hold we must NOT reconfirm
        # the dead hold PI — pass None so charge_ride creates a fresh PaymentIntent.
        confirm_pi = held_pi
        if held_pi and auth_status in ("authorized", "fare_only") and authorized > 0:
            held_result = await _settle_against_hold(
                ride,
                ride_id,
                rider_id,
                total_charge,
                tip_amount,
                held_pi=held_pi,
                authorized=authorized,
                stripe_customer_id=stripe_customer_id,
                payment_method_id=payment_method_id,
            )
            if held_result is not None:
                return held_result
            confirm_pi = None  # hold unusable → fresh charge, not a reconfirm

    if not payment_method_id:
        await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
        # Structured code (was a bare 400) so the rider app can surface the
        # Change Card / Add Card escape instead of a dead-end "Payment error".
        return PaymentResult(
            success=False,
            error_code="no_payment_method",
            error="No payment method on file. Please add a card.",
            status_code=402,
            extra={"suggested_action": "change_card"},
        )

    outcome = await charge_ride(
        ride=ride,
        # Pass the Decimal straight through — charge_ride does the cents
        # conversion via dollars_to_cents. Never coerce money to float.
        total_amount=total_charge,
        rider_id=rider_id,
        payment_method_id=payment_method_id,
        stripe_customer_id=stripe_customer_id,
        payment_intent_id=confirm_pi,
    )

    if outcome.status == "succeeded":
        # New card charged successfully — NOW it's safe to release the old card's
        # hold (deferred from the override branch so a decline couldn't have lost
        # the guaranteed authorization). Best-effort; only mark 'released' in the
        # DB if Stripe confirms the cancel.
        if override_hold_pi_to_release:
            override_hold_released = await cancel_authorization(
                ride_id=ride_id, payment_intent_id=override_hold_pi_to_release
            )
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
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    # Re-point the ride to the card actually charged so a later
                    # retry/receipt reflects the new card, not the rejected one.
                    # Also clear the cached card brand/last4 from the old (declined)
                    # card so the admin ride-detail resolver re-derives them from
                    # the new PaymentIntent instead of showing the rejected card.
                    **(
                        {"payment_method_id": payment_method_id, "card_brand": None, "card_last4": None}
                        if payment_method_id_override
                        else {}
                    ),
                    # Record the old hold as released only when Stripe confirmed the
                    # cancel, so auth_status reflects reality.
                    **({"auth_status": "released"} if override_hold_released else {}),
                    **_tip_ride_update(ride, tip_amount),
                },
            )
        except Exception as db_err:
            logger.opt(exception=True).error(
                "[PAYMENT] Stripe charge {} confirmed but ride {} DB update failed — "
                "ride stuck in 'processing'; financial_events written for recovery. err={}",
                outcome.payment_intent_id,
                ride_id,
                db_err,
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
        if app_config.ENV.lower() == "production":
            return await _refuse_unconfigured_settlement(ride_id, "charge")
        logger.error("Stripe unconfigured — marking ride {} paid without real charge", ride_id)
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


async def send_ride_receipt(
    ride: dict, rider_id: str, tip_amount: Decimal, recipient_email: Optional[str] = None
) -> bool:
    """Send receipt email. Returns True if email was sent.

    ``recipient_email`` overrides the destination address (admin can send the
    invoice to a different email). When omitted, it goes to the rider on file.
    """
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
                    # Carry the PIPEDA-safe driver reference + vehicle onto the
                    # receipt (the user record du has neither).
                    "driver_code": drv.get("driver_code", ""),
                    "driver_vehicle": f"{drv.get('vehicle_make', '')} {drv.get('vehicle_model', '')}".strip(),
                }
    try:
        from utils.email_receipt import send_receipt_email

        return await send_receipt_email(ride, rider or {}, driver_info, _f(tip_amount), recipient_email=recipient_email)
    except Exception as e:
        logger.opt(exception=True).error(f"Receipt email error: {e}")
        return False
