"""Portal funding endpoints (Phase 5 / M5.4).

The money-movement surface of the departmental funding hierarchy:

    POST /company/{id}/wallet/allocate                      admin: master ↔ section
    POST /company/{id}/wallet/topup-session                 admin: fund the MASTER wallet (Stripe Checkout)
    GET  /company/{id}/sections/{sid}/wallet                head:  section wallet + recent ledger
    POST /company/{id}/sections/{sid}/wallet/topup-session  head:  fund the SECTION wallet (Stripe Checkout)

Payments land via the EXISTING corporate_topup webhook (payment_intent.succeeded
→ apply_topup keyed on metadata.wallet_id, idempotent on the PI id) — Checkout
sessions therefore set metadata on payment_intent_data so it reaches the PI.

Money rules: Decimal only at the boundary (amounts validated as Decimal-safe
strings by pydantic condecimal), transfers via the migration-227 RPC (floors
derived server-side: sections never below 0 — Q9; master down to its credit
floor — Q10), head Stripe top-ups capped at $5,000/day (Q14) on top of the
$100–$10,000 per-transaction bounds mirroring the staff top-up.

NOTE: no `from __future__ import annotations` — string annotations break
FastAPI body resolution through slowapi's wrapper (see corporate_signup.py).
"""

import asyncio
import logging
import time
from decimal import Decimal
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, condecimal

try:
    from ..core.config import settings as core_settings  # type: ignore
    from ..db_supabase import (  # type: ignore
        ensure_section_wallet,
        get_corporate_account_by_id,
        get_master_wallet,
        get_rows,
        get_section_wallet,
        list_wallet_transactions,
        sum_autotopups_today,
    )
    from ..dependencies.company_guard import require_company_admin, require_section_head  # type: ignore
    from ..services.corporate_wallet_service import (  # type: ignore
        TransferBelowFloor,
        transfer_between_wallets,
    )
    from ..settings_loader import get_app_settings  # type: ignore
    from ..utils.money import dollars_to_cents  # type: ignore
    from ..utils.rate_limiter import default_limiter as limiter  # type: ignore
except ImportError:
    from core.config import settings as core_settings  # type: ignore
    from db_supabase import (  # type: ignore
        ensure_section_wallet,
        get_corporate_account_by_id,
        get_master_wallet,
        get_rows,
        get_section_wallet,
        list_wallet_transactions,
        sum_autotopups_today,
    )
    from dependencies.company_guard import require_company_admin, require_section_head  # type: ignore
    from services.corporate_wallet_service import (  # type: ignore
        TransferBelowFloor,
        transfer_between_wallets,
    )
    from settings_loader import get_app_settings  # type: ignore
    from utils.money import dollars_to_cents  # type: ignore
    from utils.rate_limiter import default_limiter as limiter  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company/{company_id}", tags=["Corporate Company Wallet"])

# Per-transaction Checkout bounds — mirror the staff top-up ($100–$10,000 CAD).
_TOPUP_MIN = Decimal("100")
_TOPUP_MAX = Decimal("10000")
# Q14: a department head may move at most this much per day per section wallet
# through Stripe (bounds the blast radius of a compromised head account).
_HEAD_DAILY_CAP = Decimal("5000")


class AllocateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    amount: condecimal(gt=Decimal("0"), le=Decimal("100000"))  # type: ignore[valid-type]
    # to_section: master → section (default). to_master: claw an allocation back.
    direction: str = Field("to_section", pattern="^(to_section|to_master)$")
    client_key: Optional[str] = Field(None, max_length=64)


class TopupSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: condecimal(ge=_TOPUP_MIN, le=_TOPUP_MAX)  # type: ignore[valid-type]
    # Client-tracked idempotency (a UUID the portal generates per click):
    # retries/double-submits collapse into ONE Checkout Session → one charge.
    client_key: Optional[str] = Field(None, max_length=64)


async def _active_company_or_409(company_id: str) -> dict:
    company = await get_corporate_account_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if company.get("status") != "active":
        raise HTTPException(status_code=409, detail="Company is not active")
    return company


@router.post("/wallet/allocate")
@limiter.limit("30/hour")
async def allocate(
    request: Request,
    company_id: str,
    body: AllocateRequest,
    guard=Depends(require_company_admin),
):
    """Move funds master ↔ section. The RPC enforces floors server-side:
    sections never go below 0 (Q9); the master may draw down into its credit
    floor — allocating extended credit is deliberate (Q10)."""
    await _active_company_or_409(company_id)

    # Tenancy: the section must belong to THIS company (money-auditor
    # warning — a foreign section_id would mislabel the wallet's attribution).
    sections = await get_rows("corporate_sections", {"id": body.section_id, "company_id": company_id}, limit=1)
    if not sections:
        raise HTTPException(status_code=404, detail="Section not found")

    master = await get_master_wallet(company_id)
    if not master:
        raise HTTPException(status_code=500, detail="Master wallet not provisioned")
    section_wallet = await ensure_section_wallet(company_id=company_id, section_id=body.section_id)
    if not section_wallet.get("id"):
        raise HTTPException(status_code=503, detail="Could not provision the section wallet")

    if body.direction == "to_section":
        from_id, to_id = master["id"], section_wallet["id"]
    else:
        from_id, to_id = section_wallet["id"], master["id"]

    amount = Decimal(str(body.amount))
    # Idempotency: client key wins; else a 1-minute bucket keyed on the exact
    # movement (same scheme as the staff top-up PI key).
    idem = body.client_key or f"alloc-{from_id}-{to_id}-{dollars_to_cents(amount)}-{int(time.time() // 60)}"

    try:
        result = await transfer_between_wallets(
            from_wallet_id=from_id,
            to_wallet_id=to_id,
            amount=amount,
            actor_user_id=str(guard["user"]["id"]),
            idempotency_key=idem,
            notes=f"portal allocation {body.direction} by member {guard.get('member_id')}",
        )
    except TransferBelowFloor:
        raise HTTPException(
            status_code=409,
            detail=(
                "Insufficient section balance for this claw-back."
                if body.direction == "to_master"
                else "This allocation would exceed the company's available balance and credit."
            ),
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        logger.error("wallet allocate failed for company %s", company_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Allocation failed. Please try again.") from e

    return {
        "success": True,
        "from_balance": result.get("from_balance"),
        "to_balance": result.get("to_balance"),
        "idempotency_key": idem,
    }


@router.get("/sections/{section_id}/wallet")
async def section_wallet(
    company_id: str,
    section_id: str,
    guard=Depends(require_section_head),
):
    """Section wallet + recent ledger. Admin (any section) or that section's head."""
    wallet = await get_section_wallet(company_id=company_id, section_id=section_id)
    if not wallet:
        return {"wallet": None, "transactions": []}
    txns = await list_wallet_transactions(wallet_id=wallet["id"], limit=50)
    return {"wallet": wallet, "transactions": txns}


async def _create_checkout_session(
    *,
    company: dict,
    wallet_id: str,
    amount: Decimal,
    initiated_by: str,
    label: str,
    return_path: str,
    client_key: Optional[str] = None,
) -> dict:
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=503, detail="Payments are not configured")
    if not company.get("stripe_customer_id"):
        raise HTTPException(status_code=409, detail="Company has no Stripe customer")

    base = core_settings.PORTAL_BASE_URL.rstrip("/")
    metadata = {
        "scope": "corporate_topup",  # existing webhook credits metadata.wallet_id
        "company_id": company["id"],
        "wallet_id": wallet_id,
        "initiated_by": initiated_by,
    }

    def _create():
        return stripe.checkout.Session.create(
            mode="payment",
            customer=company["stripe_customer_id"],
            line_items=[
                {
                    "price_data": {
                        "currency": "cad",
                        "unit_amount": dollars_to_cents(amount),
                        "product_data": {"name": label},
                    },
                    "quantity": 1,
                }
            ],
            # Metadata must live on the PAYMENT INTENT — the webhook handles
            # payment_intent.succeeded, and Checkout does NOT copy session
            # metadata onto the PI automatically.
            payment_intent_data={"metadata": metadata},
            metadata=metadata,
            success_url=f"{base}{return_path}?topup=success",
            cancel_url=f"{base}{return_path}?topup=cancelled",
            # Unpaid sessions expire fast so abandoned/duplicated checkouts
            # can't linger as parallel charge paths.
            expires_at=int(time.time()) + 30 * 60,
            api_key=stripe_secret,
            # DETERMINISTIC key (money-auditor blocker): a retry/double-submit
            # of the same top-up must return the SAME Checkout Session, never
            # mint a second real charge. Client key wins; else the same
            # 1-minute wallet+amount bucket scheme as allocate()/staff top-up.
            idempotency_key=client_key
            or f"corp-checkout-{wallet_id}-{dollars_to_cents(amount)}-{int(time.time() // 60)}",
        )

    session = await asyncio.to_thread(_create)
    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/wallet/topup-session")
@limiter.limit("20/hour")
async def master_topup_session(
    request: Request,
    company_id: str,
    body: TopupSessionRequest,
    guard=Depends(require_company_admin),
):
    """Company admin funds the MASTER wallet — portal self-funding."""
    company = await _active_company_or_409(company_id)
    master = await get_master_wallet(company_id)
    if not master:
        raise HTTPException(status_code=500, detail="Master wallet not provisioned")
    return await _create_checkout_session(
        company=company,
        wallet_id=master["id"],
        amount=Decimal(str(body.amount)),
        initiated_by=str(guard["user"]["id"]),
        label=f"Spinr for Business wallet top-up — {company.get('name', '')}",
        return_path=f"/company-portal/{company_id}/billing",
        client_key=body.client_key,
    )


@router.post("/sections/{section_id}/wallet/topup-session")
@limiter.limit("4/hour")
async def section_topup_session(
    request: Request,
    company_id: str,
    section_id: str,
    body: TopupSessionRequest,
    guard=Depends(require_section_head),
):
    """Department head (or admin) funds the SECTION wallet via Stripe Checkout.

    Q14: on top of the per-transaction bounds, completed Stripe top-ups on a
    section wallet are capped at $5,000/day. The check counts LANDED top-ups
    (webhook-credited ledger rows) — a soft guardrail against a compromised
    head account, not an accounting invariant."""
    company = await _active_company_or_409(company_id)
    wallet = await ensure_section_wallet(company_id=company_id, section_id=section_id)
    if not wallet.get("id"):
        raise HTTPException(status_code=503, detail="Could not provision the section wallet")

    amount = Decimal(str(body.amount))
    landed_today = await sum_autotopups_today(wallet["id"])
    if landed_today + amount > _HEAD_DAILY_CAP:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily section top-up limit reached (${_HEAD_DAILY_CAP}/day). "
                "Ask a company admin to allocate from the master wallet instead."
            ),
        )

    section_name = (guard.get("section") or {}).get("name") or "section"
    return await _create_checkout_session(
        company=company,
        wallet_id=wallet["id"],
        amount=amount,
        initiated_by=str(guard["user"]["id"]),
        label=f"Spinr for Business — {section_name} wallet top-up",
        return_path=f"/company-portal/{company_id}/sections",
        client_key=body.client_key,
    )
