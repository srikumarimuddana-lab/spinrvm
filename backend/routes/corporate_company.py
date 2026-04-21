"""Company-admin endpoints (`/company/**`). Consumed by the company portal
and used by the rider app for read paths (balances).

Separation: writes requiring admin role use require_company_admin.
Reads available to any active member use require_company_member.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

try:
    from ..db_supabase import (  # type: ignore
        add_allowed_domain,
        delete_allowed_domain,
        get_allowance_request_by_id,
        get_corporate_member_by_id,
        get_corporate_policy,
        get_corporate_wallet_by_company,
        get_member_allowance,
        list_allowed_domains,
        list_company_allowance_requests,
        list_company_allowances,
        list_company_members,
        update_allowance_request,
        update_corporate_member,
        upsert_corporate_policy,
        upsert_member_allowance,
    )
    from ..dependencies.company_guard import (  # type: ignore
        require_company_admin,
        require_company_member,
    )
    from ..schemas.corporate import (  # type: ignore
        AllowanceCreate,
        AllowanceRequestDecision,
        AllowanceUpdate,
        AllowedDomainCreate,
        MemberInvite,
        MemberUpdate,
        PolicyCreate,
        PolicyUpdate,
    )
    from ..services.corporate_allowance_service import apply_grant  # type: ignore
    from ..services.corporate_membership_service import invite_member  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        add_allowed_domain,
        delete_allowed_domain,
        get_allowance_request_by_id,
        get_corporate_member_by_id,
        get_corporate_policy,
        get_corporate_wallet_by_company,
        get_member_allowance,
        list_allowed_domains,
        list_company_allowance_requests,
        list_company_allowances,
        list_company_members,
        update_allowance_request,
        update_corporate_member,
        upsert_corporate_policy,
        upsert_member_allowance,
    )
    from dependencies.company_guard import (  # type: ignore
        require_company_admin,
        require_company_member,
    )
    from schemas.corporate import (  # type: ignore
        AllowanceCreate,
        AllowanceRequestDecision,
        AllowanceUpdate,
        AllowedDomainCreate,
        MemberInvite,
        MemberUpdate,
        PolicyCreate,
        PolicyUpdate,
    )
    from services.corporate_allowance_service import apply_grant  # type: ignore
    from services.corporate_membership_service import invite_member  # type: ignore


router = APIRouter(prefix="/company/{company_id}", tags=["Corporate Company"])


# ---------- Members ----------
@router.get("/members")
async def list_members(
    company_id: str,
    status: Optional[str] = None,
    guard=Depends(require_company_admin),
):
    statuses = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    return await list_company_members(company_id=company_id, statuses=statuses)


@router.post("/members/invite")
async def invite(
    company_id: str,
    body: MemberInvite,
    guard=Depends(require_company_admin),
):
    member, url = await invite_member(
        company_id=company_id,
        email=body.email,
        role=body.role.value,
        invited_by=guard["user"]["id"],
        policy_override=body.policy_override,
    )
    return {"member": member, "invite_url": url}


@router.patch("/members/{member_id}")
async def update_member(
    company_id: str,
    member_id: str,
    body: MemberUpdate,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    patch = body.model_dump(exclude_none=True)
    if "role" in patch and hasattr(patch["role"], "value"):
        patch["role"] = patch["role"].value
    if "status" in patch and hasattr(patch["status"], "value"):
        patch["status"] = patch["status"].value
    return await update_corporate_member(member_id, patch) or existing


@router.delete("/members/{member_id}")
async def remove_member(
    company_id: str,
    member_id: str,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    return await update_corporate_member(member_id, {"status": "removed"}) or existing


# ---------- Allowances ----------
@router.get("/allowances")
async def list_allowances(company_id: str, guard=Depends(require_company_admin)):
    return await list_company_allowances(company_id)


@router.get("/members/{member_id}/allowance")
async def get_allowance(
    company_id: str,
    member_id: str,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    return await get_member_allowance(member_id) or {}


@router.put("/members/{member_id}/allowance")
async def set_allowance(
    company_id: str,
    member_id: str,
    body: AllowanceCreate,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    patch = body.model_dump()
    if hasattr(patch["type"], "value"):
        patch["type"] = patch["type"].value
    for k in ("period_start", "period_end"):
        if patch.get(k) is not None:
            patch[k] = patch[k].isoformat()
    return await upsert_member_allowance(member_id=member_id, patch=patch)


@router.patch("/members/{member_id}/allowance")
async def patch_allowance(
    company_id: str,
    member_id: str,
    body: AllowanceUpdate,
    guard=Depends(require_company_admin),
):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    patch = body.model_dump(exclude_none=True)
    if "status" in patch and hasattr(patch["status"], "value"):
        patch["status"] = patch["status"].value
    if not patch:
        return await get_member_allowance(member_id) or {}
    return await upsert_member_allowance(member_id=member_id, patch=patch)


# ---------- Allowance requests (admin side) ----------
@router.get("/allowance-requests")
async def list_requests(
    company_id: str,
    status: Optional[str] = "pending",
    guard=Depends(require_company_admin),
):
    statuses = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    return await list_company_allowance_requests(company_id, statuses=statuses)


# ---------- Allowed domains ----------
@router.get("/allowed-domains")
async def list_domains(company_id: str, guard=Depends(require_company_admin)):
    return await list_allowed_domains(company_id)


@router.post("/allowed-domains")
async def add_domain(
    company_id: str,
    body: AllowedDomainCreate,
    guard=Depends(require_company_admin),
):
    return await add_allowed_domain(company_id=company_id, domain=body.domain)


@router.delete("/allowed-domains/{domain}")
async def remove_domain(
    company_id: str,
    domain: str,
    guard=Depends(require_company_admin),
):
    await delete_allowed_domain(company_id=company_id, domain=domain.lower())
    return {"status": "ok"}


# ---------- Allowance request decision (approve/deny) ----------
@router.post("/allowance-requests/{request_id}/decide")
async def decide_allowance_request(
    company_id: str,
    request_id: str,
    body: AllowanceRequestDecision,
    guard=Depends(require_company_admin),
):
    request = await get_allowance_request_by_id(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    member = await get_corporate_member_by_id(request["member_id"])
    if not member or member.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    if request.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Request already decided")

    new_status = "approved" if body.approve else "denied"
    if body.approve:
        allowance = await get_member_allowance(request["member_id"])
        wallet = await get_corporate_wallet_by_company(company_id)
        if not allowance or not wallet:
            raise HTTPException(status_code=409, detail="missing allowance or wallet")
        await apply_grant(
            wallet_id=wallet["id"],
            allowance_id=allowance["id"],
            member_id=request["member_id"],
            amount=float(request["amount"]),
            actor_user_id=guard["user"]["id"],
            notes=f"approved request {request_id}",
            floor=float(wallet.get("soft_negative_floor", -50)),
        )
    return await update_allowance_request(
        request_id=request_id,
        status=new_status,
        reviewed_by=guard["user"]["id"],
        decision_notes=body.note,
    )


# ---------- Policy ----------

@router.get("/policy")
async def get_policy(
    company_id: str,
    guard=Depends(require_company_member),
):
    """Return the company's active policy row.

    Accessible to any active company member so the rider Work Profile
    screen can display the policy summary ("Max $80/ride, Mon–Fri 9am–7pm").
    Returns an empty dict when no policy has been configured yet.
    """
    return await get_corporate_policy(company_id) or {}


@router.put("/policy")
async def replace_policy(
    company_id: str,
    body: PolicyCreate,
    guard=Depends(require_company_admin),
):
    """Create or fully replace the company's policy.

    Idempotent — safe to call multiple times; always returns the current state.
    """
    patch = body.model_dump()
    patch["allowed_payment_source"] = patch["allowed_payment_source"].value \
        if hasattr(patch["allowed_payment_source"], "value") \
        else patch["allowed_payment_source"]

    # Serialise TimeWindow objects to plain dicts for JSON storage.
    if patch.get("allowed_time_windows") is not None:
        patch["allowed_time_windows"] = [
            w.model_dump() if hasattr(w, "model_dump") else w
            for w in patch["allowed_time_windows"]
        ]

    return await upsert_corporate_policy(company_id, patch)


@router.patch("/policy")
async def patch_policy(
    company_id: str,
    body: PolicyUpdate,
    guard=Depends(require_company_admin),
):
    """Partially update the company's policy.

    Only the fields present in the request body are changed; omitted
    fields retain their current values.
    """
    patch = body.model_dump(exclude_none=True)
    if not patch:
        return await get_corporate_policy(company_id) or {}

    if "allowed_payment_source" in patch and hasattr(
        patch["allowed_payment_source"], "value"
    ):
        patch["allowed_payment_source"] = patch["allowed_payment_source"].value

    if "allowed_time_windows" in patch and patch["allowed_time_windows"] is not None:
        patch["allowed_time_windows"] = [
            w.model_dump() if hasattr(w, "model_dump") else w
            for w in patch["allowed_time_windows"]
        ]

    return await upsert_corporate_policy(company_id, patch)
