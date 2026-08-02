"""Wallet wind-down for corporate accounts on close (offboarding).

`closed` is a terminal status (see routes/corporate_accounts.py — a closed
account can never reopen), so any master-wallet balance left behind at close
time has no other path back to the company. This service refunds that
balance to the company's original Stripe payment method(s) and debits the
ledger to match, so nothing is silently stranded or silently zeroed.

Only funds that came in via a trackable Stripe top-up PaymentIntent can be
refunded through Stripe. Any remainder (e.g. balance built from manual
support adjustments with no underlying charge) is left in the wallet and
reported back for finance to handle manually — it is never written off
automatically.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

import stripe

try:
    from .. import db_supabase  # type: ignore
except ImportError:
    import db_supabase  # type: ignore

try:
    from .corporate_wallet_service import apply_adjustment
except ImportError:
    from services.corporate_wallet_service import apply_adjustment  # type: ignore

try:
    from ..settings_loader import get_app_settings  # type: ignore
except ImportError:
    from settings_loader import get_app_settings  # type: ignore

logger = logging.getLogger(__name__)

_CENTS = Decimal("0.01")


def _to_cents(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def refund_wallet_balance_on_close(
    *,
    company_id: str,
    stripe_customer_id: Optional[str],
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Refund a closed company's remaining master-wallet balance via Stripe.

    Returns a summary dict — never raises for a partial/failed refund past
    the first Stripe error, so a flaky refund can't roll back the account
    closure that already committed. Callers must log the returned dict
    loudly (not swallow it) since it can carry `stripe_error` or
    `unrefundable_amount`.
    """
    result: Dict[str, Any] = {
        "attempted": False,
        "refunded_total": "0.00",
        "unrefundable_amount": "0.00",
        "stripe_refund_ids": [],
        "stripe_error": None,
        "skipped_reason": None,
        "ledger_write_failed": False,
    }

    wallet = await db_supabase.get_corporate_wallet_by_company(company_id)
    if not wallet:
        result["skipped_reason"] = "no_wallet"
        return result

    balance = Decimal(str(wallet.get("balance") or 0))
    if balance <= 0:
        result["skipped_reason"] = "zero_or_negative_balance"
        return result

    if not stripe_customer_id:
        result["skipped_reason"] = "no_stripe_customer"
        result["unrefundable_amount"] = str(balance.quantize(_CENTS))
        return result

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        result["skipped_reason"] = "stripe_not_configured"
        result["unrefundable_amount"] = str(balance.quantize(_CENTS))
        return result

    result["attempted"] = True
    wallet_id = wallet["id"]

    # Refund the most recent top-ups first — LIFO is the simplest well-defined
    # order and matches how the wallet balance was most recently funded.
    topups: List[Dict[str, Any]] = await db_supabase.get_rows(
        "corporate_wallet_transactions",
        filters={"wallet_id": wallet_id, "type": "topup"},
        order="created_at",
        desc=True,
    )

    remaining = balance
    refunded_total = Decimal("0")
    stripe_refund_ids: List[str] = []

    for topup in topups:
        if remaining <= 0:
            break
        pi = topup.get("stripe_payment_intent_id")
        topup_amount = Decimal(str(topup.get("amount") or 0))
        if not pi or topup_amount <= 0:
            continue

        refund_amount = min(topup_amount, remaining)
        try:
            refund = await asyncio.to_thread(
                lambda pi=pi, refund_amount=refund_amount, topup=topup: stripe.Refund.create(
                    payment_intent=pi,
                    amount=_to_cents(refund_amount),
                    metadata={
                        "scope": "corporate_close_winddown",
                        "corporate_account_id": company_id,
                        "wallet_id": wallet_id,
                        "source_transaction_id": str(topup.get("id")),
                    },
                    api_key=stripe_secret,
                    idempotency_key=f"corp-close-refund-{wallet_id}-{topup.get('id')}",
                )
            )
        except stripe.error.StripeError as exc:
            logger.error(
                "Corporate wallet close refund failed company=%s wallet=%s payment_intent=%s: %s",
                company_id,
                wallet_id,
                pi,
                exc,
                exc_info=True,
            )
            result["stripe_error"] = str(exc)
            break

        stripe_refund_ids.append(refund.id)
        refunded_total += refund_amount
        remaining -= refund_amount

    if refunded_total > 0:
        # Corporate + admin portal review, High #3: this write was previously
        # unwrapped — if it raised after one or more Stripe refunds already
        # succeeded above, the exception propagated out of this function
        # entirely, and the refunded_total/stripe_refund_ids computed so far
        # were lost from the return value. The caller's generic exception
        # handler then recorded only "unhandled_exception," with no trace of
        # money that had already left the platform's Stripe balance. On a
        # terminal (un-reopenable) company that's a silent, unrecoverable
        # ledger/Stripe divergence. Now the real Stripe outcome is always
        # returned, with ledger_write_failed flagging the divergence for
        # finance/ops follow-up instead of losing it to an uncaught raise.
        try:
            await apply_adjustment(
                wallet_id=wallet_id,
                amount=-refunded_total,
                notes=(
                    f"Corporate account closed — wallet balance refunded to Stripe "
                    f"customer {stripe_customer_id} ({len(stripe_refund_ids)} refund(s))"
                ),
                actor_user_id=actor_user_id or "system",
                floor=Decimal("0"),
            )
        except Exception as ledger_exc:
            logger.error(
                "Corporate wallet close: %s in Stripe refunds succeeded (ids=%s) but the "
                "ledger debit failed for company=%s wallet=%s — wallet balance no longer "
                "matches what Stripe actually refunded; manual reconciliation required: %s",
                refunded_total.quantize(_CENTS),
                stripe_refund_ids,
                company_id,
                wallet_id,
                ledger_exc,
                exc_info=True,
            )
            result["ledger_write_failed"] = True

    result["refunded_total"] = str(refunded_total.quantize(_CENTS))
    result["unrefundable_amount"] = str((balance - refunded_total).quantize(_CENTS))
    result["stripe_refund_ids"] = stripe_refund_ids
    if remaining > 0 and not result["stripe_error"]:
        result["skipped_reason"] = "topups_exhausted_balance_remains"
    return result
