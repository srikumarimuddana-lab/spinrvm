"""Rider-app Work Profile endpoints (`/rider/work-profile/**`)."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

try:
    from ..db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_corporate_wallet_by_company,
        get_member_allowance,
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


def _compute_remaining(allowance: dict) -> Optional[Decimal]:
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
    return Decimal(str(amt)) - max(Decimal(str(used)), Decimal(0))


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
        out.append(
            {
                "membership": m,
                # status (M2.4) drives the portal's verification gating: a
                # non-active company redirects to /verification instead of
                # the booking/overview pages.
                "company": {
                    "id": company.get("id"),
                    "name": company.get("name"),
                    "status": company.get("status"),
                },
            }
        )
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
    # Validate that the rider's JWT email domain is authorized for this company.
    # body.email is not trusted for this check — we use the JWT-sourced identity.
    user_email = (current_user.get("phone_or_email") or current_user.get("email") or "").lower()
    domain = user_email.split("@")[-1] if "@" in user_email else ""
    if not domain:
        raise HTTPException(
            status_code=400,
            detail="Account has no email address; cannot join via domain",
        )
    allowed = await get_rows(
        "corporate_allowed_domains",
        {"company_id": body.company_id, "domain": domain},
        limit=1,
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Your email domain is not authorized for this company",
        )

    member = await join_via_domain(
        company_id=body.company_id,
        user_id=current_user["id"],
        email=user_email,
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
    """Return this rider's work rides for the given company.

    Joins ride_payment_sources (written at process_payment time) back to
    the rides table so the rider app can show the Work tab ride history.
    """
    membership = await _ensure_member(current_user, company_id)
    member_id = membership["id"]

    filters: dict = {"company_id": company_id, "member_id": member_id}
    if from_:
        filters["created_at"] = {"$gte": from_}

    rps_rows = await get_rows("ride_payment_sources", filters, limit=100)

    # Apply `to` date ceiling in Python — db helper only supports one
    # comparison operator per key in a single filter dict.
    if to and rps_rows:
        rps_rows = [r for r in rps_rows if (r.get("created_at") or "") <= to]

    if not rps_rows:
        return []

    ride_ids = [r["ride_id"] for r in rps_rows]
    rides_list = await get_rows("rides", {"id": {"$in": ride_ids}}, limit=len(ride_ids))
    rides_by_id = {r["id"]: r for r in rides_list}

    rps_by_ride = {r["ride_id"]: r for r in rps_rows}
    return [{**rides_by_id[rid], "payment_source": rps_by_ride[rid]} for rid in ride_ids if rid in rides_by_id]


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
        and Decimal(str(body.amount)) <= Decimal(str(auto_cap))
        and auto_monthly is not None
        and used_auto < int(auto_monthly)
    ):
        row = await insert_allowance_request(
            member_id=membership["id"],
            amount=body.amount,
            reason=body.reason,
            status="auto_approved",
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
                floor=Decimal(str(wallet.get("soft_negative_floor", -50))),
            )
        return row
    return await insert_allowance_request(
        member_id=membership["id"],
        amount=body.amount,
        reason=body.reason,
        status="pending",
    )


@router.get("/{company_id}/allowance-requests")
async def my_requests(
    company_id: str,
    current_user: dict = Depends(get_current_user),
):
    membership = await _ensure_member(current_user, company_id)
    return await list_company_allowance_requests(company_id, statuses=None, member_id=membership["id"])
