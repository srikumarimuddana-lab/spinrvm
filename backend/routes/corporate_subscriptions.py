"""Admin endpoints for flat SaaS corporate subscription billing.

Product decision (corporate + admin portal review round 2): companies pay
a flat recurring platform fee via real Stripe Subscriptions. This route is
deliberately thin — all validation, Stripe calls, and audit logging live
in services/corporate_subscription_service.py; this file only maps HTTP
in/out and the CorporateSubscriptionError -> status-code translation.

Mounted at the same "/admin/corporate-accounts" prefix as
routes/corporate_wallet.py, under the same require_module("corporate_accounts")
gate applied at server.py's include_router call (not per-endpoint here) —
matches this domain's existing convention exactly.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

try:
    from .. import db_supabase  # type: ignore
    from ..dependencies import get_admin_user  # type: ignore
    from ..services.corporate_subscription_service import (  # type: ignore
        CorporateSubscriptionError,
        assign_subscription,
        cancel_subscription,
    )
    from ..settings_loader import get_app_settings  # type: ignore
    from ..utils.audit_logger import log_admin_action  # type: ignore
    from ..validators import validate_id  # type: ignore
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_admin_user  # type: ignore
    from services.corporate_subscription_service import (  # type: ignore
        CorporateSubscriptionError,
        assign_subscription,
        cancel_subscription,
    )
    from settings_loader import get_app_settings  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore
    from validators import validate_id  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/corporate-accounts", tags=["Corporate Subscriptions"])

# Ships dark: assigning a plan starts a real recurring Stripe charge, so new
# assignments stay off until verified in staging — cancelling an existing
# subscription is never gated behind this, an admin must always be able to
# stop a live charge regardless of rollout state.
_DEFAULT_BILLING_ENABLED = False

_ERROR_STATUS = {
    "company_not_found": 404,
    "company_not_in_pilot": 403,
    "plan_not_found_or_inactive": 404,
    "plan_missing_stripe_price": 422,
    "subscription_already_active": 409,
    "no_payment_method_on_file": 422,
    "stripe_not_configured": 503,
    "no_active_subscription": 404,
}


# Sentences for the same keys. Without this, _http_error returned the raw
# token as the entire user-facing message, so an admin assigning a plan to a
# company with no card read "no_payment_method_on_file". The reader is Spinr
# staff (this whole router sits behind get_admin_user), so these name real
# concepts — company, plan, Stripe price — without naming the service's
# internal error constants.
_ERROR_MESSAGE = {
    "company_not_found": "We couldn't find that company account.",
    "company_not_in_pilot": "This company isn't in the subscription-billing pilot yet.",
    "plan_not_found_or_inactive": "That plan doesn't exist or is no longer active.",
    "plan_missing_stripe_price": "That plan has no Stripe price set. Add one before assigning it.",
    "subscription_already_active": "This company already has an active subscription.",
    "no_payment_method_on_file": ("This company has no payment method on file. Add a card before assigning a plan."),
    "stripe_not_configured": "Stripe isn't configured yet. Add the API keys in admin Settings.",
    "no_active_subscription": "This company doesn't have an active subscription to cancel.",
}


def _http_error(exc: CorporateSubscriptionError) -> HTTPException:
    reason = str(exc)
    return HTTPException(
        status_code=_ERROR_STATUS.get(reason, 400),
        detail=_ERROR_MESSAGE.get(reason, "That subscription change could not be completed."),
    )


class AssignSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str


class CancelSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_period_end: bool = True


class SetSubscriptionPilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.get("/subscription-plans")
async def list_subscription_plans(current_admin: dict = Depends(get_admin_user)):
    plans = await db_supabase.list_corporate_subscription_plans(active_only=True)
    return {"plans": plans}


@router.get("/{company_id}/subscription")
async def get_company_subscription(company_id: str, current_admin: dict = Depends(get_admin_user)):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    current = await db_supabase.get_active_corporate_subscription(normalized_id)
    history = await db_supabase.list_corporate_subscriptions_for_company(normalized_id)
    company = await db_supabase.get_corporate_account_by_id(normalized_id)
    pilot_enabled = bool(company and company.get("subscription_billing_pilot_enabled"))
    return {"current": current, "history": history, "pilot_enabled": pilot_enabled}


@router.post("/{company_id}/subscription")
async def assign_company_subscription(
    company_id: str,
    body: AssignSubscriptionRequest,
    current_admin: dict = Depends(get_admin_user),
):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)

    settings = await get_app_settings()
    billing_enabled = settings.get("corporate_subscription_billing_enabled")
    if billing_enabled is None:
        billing_enabled = _DEFAULT_BILLING_ENABLED
    if not billing_enabled:
        raise HTTPException(
            status_code=403,
            # This router is mounted behind require_module("corporate_accounts")
            # and every endpoint takes Depends(get_admin_user), so the reader is
            # Spinr staff — not a corporate customer. An earlier pass rewrote
            # this as "contact Spinr support", which told an operator to contact
            # their own support desk and dropped the one actionable fact. The
            # setting has no labelled control in the admin UI yet, so naming the
            # key is the useful thing to say here.
            detail=(
                "Corporate subscription billing is turned off. A super admin can enable "
                "the corporate_subscription_billing_enabled setting once it has been "
                "verified in staging."
            ),
        )

    try:
        row = await assign_subscription(company_id=normalized_id, plan_id=body.plan_id, admin_id=current_admin["id"])
    except CorporateSubscriptionError as exc:
        raise _http_error(exc) from exc
    return row


@router.post("/{company_id}/subscription/cancel")
async def cancel_company_subscription(
    company_id: str,
    body: CancelSubscriptionRequest,
    current_admin: dict = Depends(get_admin_user),
):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    try:
        row = await cancel_subscription(
            company_id=normalized_id, admin_id=current_admin["id"], at_period_end=body.at_period_end
        )
    except CorporateSubscriptionError as exc:
        raise _http_error(exc) from exc
    return row


@router.post("/{company_id}/subscription-pilot")
async def set_subscription_pilot(
    company_id: str,
    body: SetSubscriptionPilotRequest,
    current_admin: dict = Depends(get_admin_user),
):
    """Per-company gate on top of the global corporate_subscription_billing_enabled
    setting (migration 419) — lets billing be verified against one chosen company
    (e.g. Spinr's own internal account) without exposing POST .../subscription
    for every corporate account the moment the global flag is turned on. Never
    starts or cancels a Stripe subscription itself; assign_company_subscription
    still requires both this flag and the global setting to be true.
    """
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    company = await db_supabase.get_corporate_account_by_id(normalized_id)
    if not company:
        raise HTTPException(status_code=404, detail="We couldn't find that company account.")

    updated = await db_supabase.update_corporate_account(
        normalized_id, {"subscription_billing_pilot_enabled": body.enabled}
    )

    await log_admin_action(
        admin=current_admin,
        action="corporate_subscription_pilot_toggled",
        resource="corporate_accounts",
        resource_id=normalized_id,
        details={"company_id": normalized_id, "enabled": body.enabled},
    )
    logger.info(
        "Corporate subscription pilot flag set: company=%s enabled=%s",
        normalized_id,
        body.enabled,
        extra={"domain": "corporate"},
    )
    return {
        "company_id": normalized_id,
        "subscription_billing_pilot_enabled": (updated or {}).get("subscription_billing_pilot_enabled", body.enabled),
    }
