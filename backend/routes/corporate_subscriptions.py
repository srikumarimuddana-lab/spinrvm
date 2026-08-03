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
    "plan_not_found_or_inactive": 404,
    "plan_missing_stripe_price": 422,
    "subscription_already_active": 409,
    "no_payment_method_on_file": 422,
    "stripe_not_configured": 503,
    "no_active_subscription": 404,
}


def _http_error(exc: CorporateSubscriptionError) -> HTTPException:
    reason = str(exc)
    return HTTPException(status_code=_ERROR_STATUS.get(reason, 400), detail=reason)


class AssignSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str


class CancelSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_period_end: bool = True


@router.get("/subscription-plans")
async def list_subscription_plans(current_admin: dict = Depends(get_admin_user)):
    plans = await db_supabase.list_corporate_subscription_plans(active_only=True)
    return {"plans": plans}


@router.get("/{company_id}/subscription")
async def get_company_subscription(company_id: str, current_admin: dict = Depends(get_admin_user)):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    current = await db_supabase.get_active_corporate_subscription(normalized_id)
    history = await db_supabase.list_corporate_subscriptions_for_company(normalized_id)
    return {"current": current, "history": history}


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
            detail="Corporate subscription billing is not yet enabled — turn on "
            "corporate_subscription_billing_enabled in Settings once verified in staging.",
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
