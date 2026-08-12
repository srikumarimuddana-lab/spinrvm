"""Super-admin corporate wallet endpoints."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

try:
    from ..db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_corporate_wallet_by_company,
        get_rows,
        list_wallet_risk_portfolio,
        list_wallet_transactions,
        update_corporate_wallet_config,
    )
    from ..dependencies import get_admin_user  # type: ignore
    from ..services.corporate_stripe_identity import with_corporate_customer_repair  # type: ignore
    from ..services.corporate_wallet_service import apply_adjustment  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..utils.audit_logger import log_admin_action  # type: ignore
    from ..utils.money import dollars_to_cents  # type: ignore
    from ..validators import validate_id  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_corporate_wallet_by_company,
        get_rows,
        list_wallet_risk_portfolio,
        list_wallet_transactions,
        update_corporate_wallet_config,
    )
    from dependencies import get_admin_user  # type: ignore
    from services.corporate_stripe_identity import with_corporate_customer_repair  # type: ignore
    from services.corporate_wallet_service import apply_adjustment  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore
    from utils.money import dollars_to_cents  # type: ignore
    from validators import validate_id  # type: ignore

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/admin/corporate-accounts", tags=["Corporate Wallet"])

# OWNERSHIP ASSUMPTION: All Spinr admins are currently global staff — there are
# no org-scoped admin roles. For this reason, these endpoints authenticate via
# get_admin_user but do NOT check that the admin "owns" the company_id in the
# path. If per-org admin roles are ever added, every endpoint in this file must
# gain an ownership check (e.g. fetched_account["admin_email"] == current_admin["email"])
# before returning or mutating data.

_MAX_TXN_PAGE = 200

# Corporate + admin portal review: "$100k/minute" finding. /wallet/adjust
# accepts up to $100,000 per call with no limit on repeated calls by the
# same admin -- a compromised or malicious admin session could move an
# unbounded amount in minutes. This is a daily *cumulative* cap per admin,
# not a second-approver workflow (out of scope for this fix); configurable
# via app_settings.corporate_wallet_admin_adjust_daily_cap without redeploy,
# falling back to this default when unset.
_DEFAULT_ADJUST_DAILY_CAP = Decimal("50000.00")


async def _check_daily_adjust_cap(admin_id: str, amount: Decimal) -> None:
    """Raise 429 if today's (UTC) adjustments by this admin, plus this one,
    would exceed the configured daily cap. Sums absolute values -- an admin
    maliciously draining OR inflating a wallet is equally the risk being
    capped, not just one direction."""
    settings = await get_app_settings()
    raw_cap = settings.get("corporate_wallet_admin_adjust_daily_cap")
    cap = Decimal(str(raw_cap)) if raw_cap not in (None, "") else _DEFAULT_ADJUST_DAILY_CAP

    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    todays_rows = await get_rows(
        "audit_logs",
        filters={
            "actor_id": admin_id,
            "action": "corporate_wallet_manual_adjust",
            "created_at": {"$gte": day_start.isoformat()},
        },
        limit=1000,
    )
    spent_today = sum((abs(Decimal(str(r["details"]["amount"]))) for r in todays_rows), Decimal("0"))

    if spent_today + abs(amount) > cap:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily admin wallet-adjustment cap of ${cap} exceeded "
                f"(${spent_today} already moved today, this call is ${abs(amount)}). "
                "Have a second admin process the remainder, or wait until tomorrow (UTC)."
            ),
        )


@router.get("/wallet-portfolio")
async def get_wallet_risk_portfolio(current_admin: dict = Depends(get_admin_user)):
    """Portfolio-level view across every company's wallet, for spotting
    risk without opening each account one at a time. Corporate + admin
    portal review, round 2: "no portfolio-level view of corporate wallet
    risk."

    Single static path, registered before the /{company_id}/wallet dynamic
    route below -- no collision since it's a different path shape (one
    segment vs two), but ordered first for clarity regardless.
    """
    wallets = await list_wallet_risk_portfolio()
    flagged = [w for w in wallets if w["risk_flags"]]
    return {
        "total_wallets": len(wallets),
        "flagged_count": len(flagged),
        "wallets": wallets,
    }


@router.get("/{company_id}/wallet")
async def get_wallet(
    company_id: str,
    skip: int = 0,
    limit: int = 50,
    current_admin: dict = Depends(get_admin_user),
):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    wallet = await get_corporate_wallet_by_company(normalized_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    txns = await list_wallet_transactions(
        wallet_id=wallet["id"],
        skip=max(skip, 0),
        limit=min(max(limit, 1), _MAX_TXN_PAGE),
    )
    return {**wallet, "transactions": txns}


class TopUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(..., ge=Decimal("100"), le=Decimal("10000"), description="CAD between 100 and 10000")
    payment_method_id: Optional[str] = None
    client_idempotency_key: Optional[str] = None


class AdjustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(..., ge=Decimal("-100000.00"), le=Decimal("100000.00"))
    notes: str = Field(..., min_length=1, max_length=500)


@router.post("/{company_id}/wallet/topup")
async def manual_topup(
    company_id: str,
    body: TopUpRequest,
    current_admin: dict = Depends(get_admin_user),
):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    company = await get_corporate_account_by_id(normalized_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if company.get("status") != "active":
        raise HTTPException(status_code=409, detail="Company is not active")
    # No 409 on a missing stripe_customer_id: with_corporate_customer_repair
    # below provisions one. That guard used to be harmless (a NULL customer
    # only happened when KYB approval's Stripe step had failed), but the
    # auto-topup loop now NULLs the column when it retires a customer stranded
    # by a key rotation — so returning 409 here would leave the company with
    # no in-product way to ever fund its wallet again.

    wallet = await get_corporate_wallet_by_company(normalized_id)
    if not wallet:
        raise HTTPException(status_code=500, detail="Wallet not provisioned")

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    def _intent_kwargs(customer_id: str) -> dict:
        kwargs = dict(
            amount=dollars_to_cents(body.amount),
            currency="cad",
            customer=customer_id,
            metadata={
                "scope": "corporate_topup",
                "company_id": normalized_id,
                "wallet_id": wallet["id"],
                "initiated_by": current_admin["id"],
            },
            api_key=stripe_secret,
        )
        if body.payment_method_id:
            kwargs.update(
                payment_method=body.payment_method_id,
                off_session=True,
                confirm=True,
                # Server-side off_session confirm: disable redirect-based payment
                # methods so Stripe doesn't demand a `return_url` (it has no browser
                # to return to) and reject the top-up with invalid_request_error.
                automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            )
        # Always set an idempotency key so retries after network timeouts don't
        # create duplicate PaymentIntents. Client may supply one (e.g. a UUID they
        # track); fall back to a 1-minute time-bucket keyed on wallet + amount so
        # a retry of the same top-up reuses the intent, while a different amount
        # in the same minute gets a fresh key (same scheme as ride-charge keys —
        # without the amount, the second top-up would silently return the first
        # PI with the wrong amount).
        kwargs["idempotency_key"] = (
            body.client_idempotency_key
            or f"corp-topup-{wallet['id']}-{customer_id}-{dollars_to_cents(body.amount)}-{int(time.time() // 60)}"
        )
        return kwargs

    async def _create_intent(customer_id: str):
        # Sync Stripe SDK: threadpool so the round-trip doesn't block the event
        # loop (idempotency key above makes retries safe).
        return await asyncio.to_thread(lambda: stripe.PaymentIntent.create(**_intent_kwargs(customer_id)))

    # A Spinr admin is present, so a customer stranded by a test→live key
    # rotation is replaced here rather than 500ing the top-up. The retry runs
    # against the new customer; if the company's card lived on the old one,
    # Stripe reports the missing payment method plainly instead of a confusing
    # "no such customer".
    _, intent = await with_corporate_customer_repair(company, stripe_secret, _create_intent)

    # Corporate module lifecycle audit Finding 9: this endpoint moves real
    # money (creates a Stripe PaymentIntent) but never wrote to the admin
    # audit trail — only the wallet ledger (which does record actor+notes,
    # but lives in a different table than every other admin action in this
    # module). Log here too so "what did admin X do" queries stay unified.
    try:
        await log_admin_action(
            admin=current_admin,
            action="corporate_wallet_manual_topup",
            resource="corporate_wallet",
            resource_id=str(wallet["id"]),
            details={
                "company_id": normalized_id,
                "amount": str(body.amount),
                "payment_intent_id": intent.id,
            },
        )
    except Exception:
        logger.error(
            "Audit log failed for corporate_wallet_manual_topup wallet=%s",
            wallet["id"],
            exc_info=True,
        )

    return {"payment_intent_id": intent.id, "client_secret": intent.client_secret}


@router.post("/{company_id}/wallet/adjust")
async def manual_adjust(
    company_id: str,
    body: AdjustRequest,
    current_admin: dict = Depends(get_admin_user),
):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    wallet = await get_corporate_wallet_by_company(normalized_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    await _check_daily_adjust_cap(current_admin["id"], body.amount)
    result = await apply_adjustment(
        wallet_id=wallet["id"],
        amount=body.amount,
        notes=body.notes,
        actor_user_id=current_admin["id"],
        floor=Decimal(str(wallet.get("soft_negative_floor", -50))),
    )

    # See manual_topup above — Finding 9: same audit-trail gap.
    try:
        await log_admin_action(
            admin=current_admin,
            action="corporate_wallet_manual_adjust",
            resource="corporate_wallet",
            resource_id=str(wallet["id"]),
            details={
                "company_id": normalized_id,
                "amount": str(body.amount),
                "notes": body.notes,
            },
        )
    except Exception:
        logger.error(
            "Audit log failed for corporate_wallet_manual_adjust wallet=%s",
            wallet["id"],
            exc_info=True,
        )

    return result


class WalletConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_topup_enabled: Optional[bool] = None
    # Money fields parse as Decimal (2 dp) so binary-float artifacts never
    # reach validation or the DB; serialized back at the write boundary below.
    auto_topup_threshold: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    auto_topup_amount: Optional[Decimal] = Field(None, gt=0, le=10000, decimal_places=2)
    auto_topup_daily_cap: Optional[Decimal] = Field(None, gt=0, le=50000, decimal_places=2)


@router.put("/{company_id}/wallet/config")
async def update_wallet_config(
    company_id: str,
    body: WalletConfigPatch,
    current_admin: dict = Depends(get_admin_user),
):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    wallet = await get_corporate_wallet_by_company(normalized_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    # Decimal → float only at the DB write boundary (values are 2-dp clean
    # by validation, so the conversion is exact) — supabase-py can't
    # serialize Decimal.
    patch = {k: float(v) if isinstance(v, Decimal) else v for k, v in body.model_dump(exclude_none=True).items()}
    if not patch:
        return wallet
    # Enabling auto-topup requires enough config to actually run a tick.
    new_enabled = patch.get("auto_topup_enabled", wallet.get("auto_topup_enabled"))
    if new_enabled:
        threshold = patch.get("auto_topup_threshold", wallet.get("auto_topup_threshold"))
        amount = patch.get("auto_topup_amount", wallet.get("auto_topup_amount"))
        if threshold is None or amount is None:
            raise HTTPException(
                status_code=422,
                detail="auto_topup_threshold and auto_topup_amount must be set before enabling",
            )
    updated = await update_corporate_wallet_config(wallet_id=wallet["id"], patch=patch)

    # See manual_topup above — Finding 9: same audit-trail gap. This one
    # controls whether the company can be auto-charged going forward, which
    # makes it as security-relevant as the other admin actions in this file.
    try:
        await log_admin_action(
            admin=current_admin,
            action="corporate_wallet_config_updated",
            resource="corporate_wallet",
            resource_id=str(wallet["id"]),
            details={"company_id": normalized_id, "patch": patch},
        )
    except Exception:
        logger.error(
            "Audit log failed for corporate_wallet_config_updated wallet=%s",
            wallet["id"],
            exc_info=True,
        )

    return updated or {**wallet, **patch}
