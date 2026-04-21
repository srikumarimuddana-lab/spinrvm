"""Rider-app Work Profile endpoints (`/rider/work-profile/**`)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

try:
    from ..db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_corporate_wallet_by_company,
        get_member_allowance,
        get_ride,
        get_rows,
        insert_allowance_request,
        list_active_memberships_for_user,
        list_company_allowance_requests,
        list_pending_allowance_requests_for_member,
    )
    from ..dependencies import get_current_user  # type: ignore
    from ..schemas.corporate import AllowanceRequestCreate  # type: ignore
    from ..services.corporate_allowance_service import apply_grant  # type: ignore
    from ..services.corporate_membership_service import (  # type: ignore
        InviteAlreadyConsumed,
        InviteNotFound,
        accept_invite,
        auto_match_by_email,
        join_via_domain,
    )
except ImportError:
    from db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_corporate_wallet_by_company,
        get_member_allowance,
        get_ride,
        get_rows,
        insert_allowance_request,
        list_active_memberships_for_user,
        list_company_allowance_requests,
        list_pending_allowance_requests_for_member,
    )
    from dependencies import get_current_user  # type: ignore
    from schemas.corporate import AllowanceRequestCreate  # type: ignore
    from services.corporate_allowance_service import apply_grant  # type: ignore
    from services.corporate_membership_service import (  # type: ignore
        InviteAlreadyConsumed,
        InviteNotFound,
        accept_invite,
        auto_match_by_email,
        join_via_domain,
    )


router = APIRouter(prefix="/rider/work-profile", tags=["Corporate Rider"])


class AcceptInviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str


class JoinDomainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str
    email: str


def _compute_remaining(allowance: dict) -> Optional[float]:
    """v1 convention: remaining = amount - max(used, 0).

    `used` goes negative after grants post (grants decrement used) and
    positive after ride debits post. Unlimited allowances return None.
    """
    if allowance.get("type") == "unlimited":
        return None
    amt = allowance.get("amount")
    used = allowance.get("used") or 0
    if amt is None:
        return None
    return float(amt) - max(float(used), 0.0)


async def _ensure_member(current_user: dict, company_id: str) -> dict:
    memberships = await list_active_memberships_for_user(current_user["id"])
    for m in memberships:
        if m.get("company_id") == company_id:
            return m
    raise HTTPException(status_code=403, detail="not a company member")


@router.get("")
async def list_work_profiles(current_user: dict = Depends(get_current_user)):
    memberships = await list_active_memberships_for_user(current_user["id"])
    out = []
    for m in memberships:
        company = await get_corporate_account_by_id(m["company_id"]) or {}
        out.append({
            "membership": m,
            "company": {"id": company.get("id"), "name": company.get("name")},
        })
    return out


@router.get("/auto-match")
async def auto_match(
    email: str,
    current_user: dict = Depends(get_current_user),
):
    return await auto_match_by_email(user_id=current_user["id"], email=email)


@router.post("/accept-invite")
async def do_accept_invite(
    body: AcceptInviteBody,
    current_user: dict = Depends(get_current_user),
):
    try:
        company, member = await accept_invite(token=body.token, user_id=current_user["id"])
    except InviteNotFound:
        raise HTTPException(status_code=404, detail="invite not found") from None
    except InviteAlreadyConsumed:
        raise HTTPException(status_code=409, detail="invite already used") from None
    return {"company": company, "member": member}


@router.post("/join-domain")
async def do_join_domain(
    body: JoinDomainBody,
    current_user: dict = Depends(get_current_user),
):
    member = await join_via_domain(
        company_id=body.company_id, user_id=current_user["id"], email=body.email,
    )
    company = await get_corporate_account_by_id(body.company_id) or {}
    return {"company": company, "member": member}


@router.get("/{company_id}/balance")
async def my_balance(
    company_id: str,
    current_user: dict = Depends(get_current_user),
):
    membership = await _ensure_member(current_user, company_id)
    allowance = await get_member_allowance(membership["id"]) or {}
    company = await get_corporate_account_by_id(company_id) or {}
    return {
        "company_name": company.get("name"),
        "type": allowance.get("type"),
        "amount": allowance.get("amount"),
        "used": allowance.get("used"),
        "remaining": _compute_remaining(allowance),
        "period_start": allowance.get("period_start"),
        "period_end": allowance.get("period_end"),
        "status": allowance.get("status"),
    }


@router.get("/{company_id}/rides")
async def my_rides(
    company_id: str,
    from_: Optional[str] = None,
    to: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Return the caller's Work rides for a company, joined from ride_payment_sources.

    Query params:
      from_  ISO-8601 date/datetime lower bound on ride_payment_sources.created_at (inclusive)
      to     ISO-8601 date/datetime upper bound (inclusive)

    Each item is the ride row augmented with payment source fields:
      allowance_debit_amount, master_fallback_amount, source_type
    """
    membership = await _ensure_member(current_user, company_id)

    payment_sources = await get_rows(
        "ride_payment_sources",
        {"member_id": membership["id"]},
        order="created_at",
        desc=True,
        limit=200,
    )

    if not payment_sources:
        return []

    # Apply optional date-range filter on the payment source created_at.
    if from_ or to:
        filtered = []
        for ps in payment_sources:
            created = ps.get("created_at") or ""
            if from_ and created < from_:
                continue
            if to and created > to:
                continue
            filtered.append(ps)
        payment_sources = filtered

    # Hydrate each payment source with the full ride row.
    result = []
    for ps in payment_sources:
        ride_id = ps.get("ride_id")
        if not ride_id:
            continue
        ride = await get_ride(ride_id) or {}
        result.append({
            **ride,
            "allowance_debit_amount": ps.get("allowance_debit_amount"),
            "master_fallback_amount": ps.get("master_fallback_amount"),
            "source_type": ps.get("source_type"),
            "payment_source": ps,
        })

    return result


@router.post("/{company_id}/allowance-requests")
async def submit_request(
    company_id: str,
    body: AllowanceRequestCreate,
    current_user: dict = Depends(get_current_user),
):
    membership = await _ensure_member(current_user, company_id)
    pending = await list_pending_allowance_requests_for_member(membership["id"])
    if pending:
        raise HTTPException(status_code=409, detail="a request is already pending")
    allowance = await get_member_allowance(membership["id"]) or {}
    auto_cap = allowance.get("auto_approve_topup_amount")
    auto_monthly = allowance.get("auto_approve_monthly_count")
    used_auto = allowance.get("auto_approved_this_period") or 0
    if (
        auto_cap is not None
        and body.amount <= float(auto_cap)
        and auto_monthly is not None
        and used_auto < int(auto_monthly)
    ):
        row = await insert_allowance_request(
            member_id=membership["id"], amount=body.amount,
            reason=body.reason, status="auto_approved",
        )
        wallet = await get_corporate_wallet_by_company(company_id)
        if wallet and allowance.get("id"):
            await apply_grant(
                wallet_id=wallet["id"],
                allowance_id=allowance["id"],
                member_id=membership["id"],
                amount=body.amount,
                actor_user_id=current_user["id"],
                notes=f"auto_approved request {row.get('id', '')}",
                floor=float(wallet.get("soft_negative_floor", -50)),
            )
        return row
    return await insert_allowance_request(
        member_id=membership["id"], amount=body.amount,
        reason=body.reason, status="pending",
    )


@router.get("/{company_id}/allowance-requests")
async def my_requests(
    company_id: str,
    current_user: dict = Depends(get_current_user),
):
    membership = await _ensure_member(current_user, company_id)
    rows = await list_company_allowance_requests(company_id, statuses=None)
    return [r for r in rows if r.get("member_id") == membership["id"]]
