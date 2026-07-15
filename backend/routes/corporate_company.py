"""Company-admin endpoints (`/company/**`). Consumed by the company portal
and used by the rider app for read paths (balances).

Separation: writes requiring admin role use require_company_admin.
Reads available to any active member use require_company_member.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from ..db_supabase import (  # type: ignore
        add_allowed_domain,
        delete_allowed_domain,
        get_allowance_request_by_id,
        get_corporate_account_by_id,
        get_corporate_member_by_id,
        get_corporate_policy,
        get_corporate_wallet_by_company,
        get_member_allowance,
        list_allowed_domains,
        list_company_allowance_requests,
        list_company_allowances,
        list_company_members,
        list_company_ride_payment_sources,
        list_wallet_transactions,
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
        get_corporate_account_by_id,
        get_corporate_member_by_id,
        get_corporate_policy,
        get_corporate_wallet_by_company,
        get_member_allowance,
        list_allowed_domains,
        list_company_allowance_requests,
        list_company_allowances,
        list_company_members,
        list_company_ride_payment_sources,
        list_wallet_transactions,
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


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company/{company_id}", tags=["Corporate Company"])


def _validate_geofence(geofence: Optional[dict]) -> None:
    """Raise 422 if geofence is not a valid minimal GeoJSON FeatureCollection."""
    if geofence is None:
        return
    if not isinstance(geofence, dict) or geofence.get("type") != "FeatureCollection":
        raise HTTPException(status_code=422, detail="geofence must be a GeoJSON FeatureCollection")
    features = geofence.get("features")
    if not isinstance(features, list):
        raise HTTPException(status_code=422, detail="geofence.features must be a list")
    for feat in features:
        if not isinstance(feat, dict):
            raise HTTPException(status_code=422, detail="each geofence feature must be an object")
        geom = feat.get("geometry")
        if not isinstance(geom, dict) or not geom.get("type") or "coordinates" not in geom:
            raise HTTPException(
                status_code=422,
                detail="each geofence feature must have a geometry with type and coordinates",
            )


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

    # M3.1: deliver the invite by email instead of relying on the admin to
    # copy-paste the link. The web link (not the app deep link) is what works
    # on a desk computer; /company-login accepts ?invite_token= and claims the
    # membership right after OTP. Failure is surfaced (email_sent=false → the
    # UI offers the copy-link fallback), never swallowed into a fake success.
    try:
        from ..core.config import settings as _settings  # type: ignore
    except ImportError:
        from core.config import settings as _settings  # type: ignore

    token = member.get("invite_token")
    web_invite_url = f"{_settings.PORTAL_BASE_URL}/company-login?invite_token={token}" if token else None

    email_sent = False
    if web_invite_url:
        try:
            try:
                from ..utils.email_provider import send_transactional_email  # type: ignore
            except ImportError:
                from utils.email_provider import send_transactional_email  # type: ignore

            company = await get_corporate_account_by_id(company_id) or {}
            company_name = company.get("name") or "your company"
            email_sent = await send_transactional_email(
                to=body.email,
                subject=f"You're invited to {company_name} on Spinr for Business",
                text=(
                    f"You've been invited to join {company_name} on Spinr for Business "
                    f"as {'an admin' if body.role.value in ('admin', 'owner') else 'a member'}.\n\n"
                    f"Accept the invite and sign in with this email address:\n{web_invite_url}\n\n"
                    "The link signs you in with a one-time code sent to this address — "
                    "no password needed."
                ),
                log_id=member.get("id") or company_id,
                email_type="corporate_member_invite",
            )
        except Exception:
            logger.error("member invite: email delivery failed for member %s", member.get("id"), exc_info=True)
            email_sent = False

    return {
        "member": member,
        "invite_url": url,
        "web_invite_url": web_invite_url,
        "email_sent": bool(email_sent),
    }


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
    if "section_id" in patch:
        if patch["section_id"] == "":
            patch["section_id"] = None  # explicit unassign
        else:
            try:
                from ..db_supabase import get_rows as _get_rows
            except ImportError:
                from db_supabase import get_rows as _get_rows  # type: ignore
            _sections = await _get_rows(
                "corporate_sections",
                {"id": patch["section_id"], "company_id": company_id, "status": "active"},
                limit=1,
            )
            if not _sections:
                # Cross-company (or archived) section assignment is a
                # tenancy violation, not a typo — refuse loudly.
                raise HTTPException(status_code=404, detail="Section not found for this company")
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
async def list_allowances(
    company_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    guard=Depends(require_company_admin),
):
    rows = await list_company_allowances(company_id)
    return rows[skip : skip + limit]


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
    for money_key in ("amount", "auto_approve_topup_amount"):
        if patch.get(money_key) is not None:
            patch[money_key] = str(Decimal(str(patch[money_key])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
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
    for money_key in ("amount", "auto_approve_topup_amount"):
        if money_key in patch and patch[money_key] is not None:
            patch[money_key] = str(Decimal(str(patch[money_key])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
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
        amount_raw = request.get("amount")
        if amount_raw is None:
            raise HTTPException(status_code=422, detail="allowance request amount is required")
        await apply_grant(
            wallet_id=wallet["id"],
            allowance_id=allowance["id"],
            member_id=request["member_id"],
            amount=Decimal(str(amount_raw)),
            actor_user_id=guard["user"]["id"],
            notes=f"approved request {request_id}",
            floor=Decimal(str(wallet.get("soft_negative_floor", "-50"))),
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
    patch["allowed_payment_source"] = (
        patch["allowed_payment_source"].value
        if hasattr(patch["allowed_payment_source"], "value")
        else patch["allowed_payment_source"]
    )

    _validate_geofence(patch.get("allowed_geofence"))

    # Serialise TimeWindow objects to plain dicts for JSON storage.
    if patch.get("allowed_time_windows") is not None:
        patch["allowed_time_windows"] = [
            w.model_dump() if hasattr(w, "model_dump") else w for w in patch["allowed_time_windows"]
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

    if "allowed_payment_source" in patch and hasattr(patch["allowed_payment_source"], "value"):
        patch["allowed_payment_source"] = patch["allowed_payment_source"].value

    if "allowed_geofence" in patch:
        _validate_geofence(patch["allowed_geofence"])

    if "allowed_time_windows" in patch and patch["allowed_time_windows"] is not None:
        patch["allowed_time_windows"] = [
            w.model_dump() if hasattr(w, "model_dump") else w for w in patch["allowed_time_windows"]
        ]

    return await upsert_corporate_policy(company_id, patch)


# ---------- Billing (Plan 6) ----------


def _month_bounds(month: str) -> tuple[str, str]:
    """Return (from_iso, to_iso) [inclusive-exclusive] bounds for YYYY-MM."""
    from datetime import datetime as _dt

    try:
        anchor = _dt.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM") from exc
    if anchor.month == 12:
        end = anchor.replace(year=anchor.year + 1, month=1)
    else:
        end = anchor.replace(month=anchor.month + 1)
    return anchor.isoformat(), end.isoformat()


_ZERO = Decimal("0.00")
_TWO = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


def _money_str(v) -> str:
    """Quantize ``v`` to 2dp and emit as a JSON-safe string.

    Audit-17 P0-1 mandates that money fields cross the wire as decimal
    strings (``"12.50"``), never IEEE-754 floats. Use this for every
    money-shaped value placed into a dict response.
    """
    if isinstance(v, Decimal):
        return str(v.quantize(_TWO, rounding=ROUND_HALF_UP))
    return str(_d(v))


def _aggregate_rows(rows: list[dict]) -> dict:
    allowance_total = _ZERO
    master_total = _ZERO
    by_member: dict[str, dict] = {}
    for r in rows:
        ad = _d(r.get("allowance_debit_amount"))
        md = _d(r.get("master_fallback_amount"))
        allowance_total += ad
        master_total += md
        mid = r.get("member_id") or "unknown"
        slot = by_member.setdefault(
            mid,
            {
                "member_id": mid,
                "ride_count": 0,
                "allowance_total": _ZERO,
                "master_total": _ZERO,
                "total": _ZERO,
            },
        )
        slot["ride_count"] += 1
        slot["allowance_total"] += ad
        slot["master_total"] += md
        slot["total"] += ad + md
    total = allowance_total + master_total
    by_member_out = [
        {
            **v,
            "allowance_total": _money_str(v["allowance_total"]),
            "master_total": _money_str(v["master_total"]),
            "total": _money_str(v["total"]),
        }
        for v in sorted(by_member.values(), key=lambda m: m["total"], reverse=True)
    ]
    return {
        "ride_count": len(rows),
        "allowance_total": _money_str(allowance_total),
        "master_total": _money_str(master_total),
        "total": _money_str(total),
        "avg_fare": _money_str(total / len(rows)) if rows else "0.00",
        "by_member": by_member_out,
    }


@router.get("/billing/summary")
async def billing_summary(
    company_id: str,
    month: Optional[str] = None,
    guard=Depends(require_company_admin),
):
    """Aggregate spend for a month (defaults to the current month in UTC).

    Returns ride count, total company spend (allowance + master fallback),
    average fare, and per-member breakdown sorted by total desc.
    """
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    if month is None:
        month = _dt.now(_tz.utc).strftime("%Y-%m")
    from_iso, to_iso = _month_bounds(month)

    # Page through all rows so the summary is never silently truncated.
    all_rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        page = await list_company_ride_payment_sources(
            company_id=company_id,
            from_iso=from_iso,
            to_iso=to_iso,
            limit=page_size,
            offset=offset,
        )
        all_rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    wallet = await get_corporate_wallet_by_company(company_id) or {}
    return {
        "month": month,
        "wallet_balance": _money_str(wallet.get("balance") or "0"),
        "wallet_currency": wallet.get("currency") or "CAD",
        **_aggregate_rows(all_rows),
    }


@router.get("/billing/statements/{month}")
async def billing_statement(
    company_id: str,
    month: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    guard=Depends(require_company_admin),
):
    """Monthly statement line items + summary totals, paginated.

    `month` is YYYY-MM; returns 422 if the string is malformed.
    Line items are paginated via skip/limit. The summary covers the full
    month (all pages) regardless of the current page window.
    """
    from_iso, to_iso = _month_bounds(month)

    # Paginated line items for the current page
    line_items = await list_company_ride_payment_sources(
        company_id=company_id,
        from_iso=from_iso,
        to_iso=to_iso,
        limit=limit,
        offset=skip,
    )

    # Full-month aggregation (page through all rows so totals are accurate)
    all_rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        page = await list_company_ride_payment_sources(
            company_id=company_id,
            from_iso=from_iso,
            to_iso=to_iso,
            limit=page_size,
            offset=offset,
        )
        all_rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    return {
        "month": month,
        "from": from_iso,
        "to": to_iso,
        "line_items": line_items,
        "summary": _aggregate_rows(all_rows),
    }


@router.get("/billing/transactions")
async def billing_transactions(
    company_id: str,
    skip: int = 0,
    limit: int = 50,
    guard=Depends(require_company_admin),
):
    """Paged corporate wallet ledger (top-ups, debits, adjustments)."""
    wallet = await get_corporate_wallet_by_company(company_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    txns = await list_wallet_transactions(
        wallet_id=wallet["id"],
        skip=max(skip, 0),
        limit=min(max(limit, 1), 200),
    )
    return {
        "wallet_id": wallet["id"],
        "balance": _money_str(wallet.get("balance") or "0"),
        "currency": wallet.get("currency") or "CAD",
        "transactions": txns,
    }
