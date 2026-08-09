"""Flat SaaS subscription billing for corporate accounts.

Product decision (corporate + admin portal review round 2): companies pay a
flat recurring platform fee; ride fares to riders/drivers are completely
unaffected — this is deliberately NOT a per-ride markup or commission (see
CLAUDE.md "What Spinr Is NOT").

Stripe Subscription objects own the actual recurring-charge schedule; this
module only ever creates/cancels them and persists the resulting state.
Renewals, retries, and dunning happen inside Stripe and are mirrored back
into `corporate_subscriptions` by the webhook handlers in routes/webhooks.py
(`customer.subscription.updated/deleted`, `invoice.paid/payment_failed`) —
this module does not poll or reconcile that state itself, same division of
responsibility as `driver_subscriptions` (Spinr Pass).

At most one live (`active`/`past_due`) subscription may exist per company —
enforced both by a partial unique index (migration 281) and by
`assign_subscription` refusing to create a second one. Switching plans is a
deliberate two-step admin action (cancel, then assign) rather than an
implicit swap, so an admin can never accidentally trigger an unintended
proration.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import stripe

try:
    from .. import db_supabase  # type: ignore
except ImportError:
    import db_supabase  # type: ignore

try:
    from .corporate_stripe_identity import with_corporate_customer_repair
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    from services.corporate_stripe_identity import with_corporate_customer_repair  # type: ignore

try:
    from ..settings_loader import get_app_settings  # type: ignore
except ImportError:
    from settings_loader import get_app_settings  # type: ignore

try:
    from ..utils.audit_logger import log_admin_action  # type: ignore
except ImportError:
    from utils.audit_logger import log_admin_action  # type: ignore

logger = logging.getLogger(__name__)

_CENTS = Decimal("0.01")


class CorporateSubscriptionError(ValueError):
    """Raised for expected, caller-fixable failures (bad plan, no card on
    file, a live subscription already exists, ...). Callers map this to a
    4xx; anything else (StripeError, DatabaseError) is a real failure and
    must propagate loudly, never be caught here."""


def _period_end_iso(subscription: Any) -> Optional[str]:
    ts = getattr(subscription, "current_period_end", None) or (
        subscription.get("current_period_end") if isinstance(subscription, dict) else None
    )
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


async def assign_subscription(
    *,
    company_id: str,
    plan_id: str,
    admin_id: str,
) -> Dict[str, Any]:
    """Start a new flat-fee Stripe subscription for a company.

    Raises CorporateSubscriptionError for expected precondition failures
    (unknown/inactive company or plan, a live subscription already exists,
    no default payment method on file, plan has no Stripe price wired up).
    Raises stripe.error.StripeError unmodified for real Stripe failures —
    never swallowed, per CLAUDE.md's "don't silently swallow payment
    errors."
    """
    company = await db_supabase.get_corporate_account_by_id(company_id)
    if not company:
        raise CorporateSubscriptionError("company_not_found")

    plan = await db_supabase.get_corporate_subscription_plan(plan_id)
    if not plan or not plan.get("is_active"):
        raise CorporateSubscriptionError("plan_not_found_or_inactive")
    if not plan.get("stripe_price_id"):
        raise CorporateSubscriptionError("plan_missing_stripe_price")

    existing = await db_supabase.get_active_corporate_subscription(company_id)
    if existing:
        raise CorporateSubscriptionError("subscription_already_active")

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise CorporateSubscriptionError("stripe_not_configured")

    # Lazy-create (mirroring the KYB-approval path) now lives in the shared
    # corporate identity service, which also repairs a customer stranded by a
    # test→live key rotation. An admin is present, so re-provisioning is safe.
    #
    # Both the payment-method lookup and the Subscription.create address the
    # customer, so the whole sequence goes inside the repair wrapper: if the
    # stored customer turns out to be unreachable, the retry re-runs against
    # the replacement and surfaces the honest "no_payment_method_on_file"
    # (the company's card lived on the customer that is gone) rather than an
    # opaque Stripe error.
    async def _create_subscription(customer_id: str):
        payment_method_id = await db_supabase.get_default_payment_method(customer_id, stripe_secret)
        if not payment_method_id:
            raise CorporateSubscriptionError("no_payment_method_on_file")
        return await asyncio.to_thread(
            lambda: stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": plan["stripe_price_id"]}],
                default_payment_method=payment_method_id,
                metadata={"scope": "corporate_subscription", "corporate_account_id": company_id},
                api_key=stripe_secret,
                # Deterministic per-company (not per-call): a network-retried
                # request for the same company within Stripe's 24h idempotency
                # window returns the same subscription instead of creating a
                # second one, matching the customer-create key above and the
                # existing corporate_close_refund pattern.
                idempotency_key=f"corp-sub-create-{company_id}",
            )
        )

    stripe_customer_id, subscription = await with_corporate_customer_repair(
        company, stripe_secret, _create_subscription
    )

    row_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    price = Decimal(str(plan["monthly_price"])).quantize(_CENTS)
    row = await db_supabase.create_corporate_subscription_row(
        {
            "id": row_id,
            "company_id": company_id,
            "plan_id": plan_id,
            "plan_name": plan["name"],
            "price": str(price),
            "status": "active",
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": subscription.id,
            "current_period_end": _period_end_iso(subscription),
            "cancel_at_period_end": False,
            "started_at": now_iso,
            "created_by_admin_id": admin_id,
            "created_at": now_iso,
        }
    )

    await log_admin_action(
        {"id": admin_id},
        "corporate_subscription_assigned",
        "corporate_subscriptions",
        row_id,
        details={"company_id": company_id, "plan_id": plan_id, "plan_name": plan["name"], "price": str(price)},
    )
    logger.info(
        "Corporate subscription assigned: company=%s plan=%s sub=%s",
        company_id,
        plan_id,
        subscription.id,
        extra={"domain": "corporate"},
    )
    return row


async def cancel_subscription(
    *,
    company_id: str,
    admin_id: str,
    at_period_end: bool = True,
) -> Dict[str, Any]:
    """Cancel a company's live subscription.

    `at_period_end=True` (default) lets the company keep access through
    the already-paid-for period — Stripe fires customer.subscription.deleted
    at the period boundary and the webhook handler flips status to
    'cancelled' then. `at_period_end=False` cancels immediately in Stripe;
    the local row is flipped to 'cancelled' right away rather than waiting
    for the webhook, since there is no "already paid for" period left to
    honor and an admin who asked for immediate cancellation should see it
    reflected immediately.
    """
    existing = await db_supabase.get_active_corporate_subscription(company_id)
    if not existing:
        raise CorporateSubscriptionError("no_active_subscription")

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise CorporateSubscriptionError("stripe_not_configured")

    stripe_sub_id = existing.get("stripe_subscription_id")
    if stripe_sub_id:
        if at_period_end:
            await asyncio.to_thread(
                lambda: stripe.Subscription.modify(stripe_sub_id, cancel_at_period_end=True, api_key=stripe_secret)
            )
        else:
            await asyncio.to_thread(lambda: stripe.Subscription.delete(stripe_sub_id, api_key=stripe_secret))

    patch: Dict[str, Any] = {"cancel_at_period_end": at_period_end}
    if not at_period_end:
        patch["status"] = "cancelled"
        patch["cancelled_at"] = datetime.now(timezone.utc).isoformat()

    row = await db_supabase.update_corporate_subscription(existing["id"], patch)

    await log_admin_action(
        {"id": admin_id},
        "corporate_subscription_cancelled",
        "corporate_subscriptions",
        existing["id"],
        details={"company_id": company_id, "at_period_end": at_period_end},
    )
    logger.info(
        "Corporate subscription cancelled: company=%s sub=%s at_period_end=%s",
        company_id,
        stripe_sub_id,
        at_period_end,
        extra={"domain": "corporate"},
    )
    return row or existing


async def list_plans(active_only: bool = True) -> list[Dict[str, Any]]:
    return await db_supabase.list_corporate_subscription_plans(active_only=active_only)
