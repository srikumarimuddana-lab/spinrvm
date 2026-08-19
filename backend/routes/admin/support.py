import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...services.zoho_desk_integration import (
        create_ticket_for_complaint,
        create_ticket_for_dispute,
        create_ticket_for_flag,
    )
    from ...utils.audit_logger import log_admin_action
    from ...utils.money import to_decimal
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from services.zoho_desk_integration import (
        create_ticket_for_complaint,
        create_ticket_for_dispute,
        create_ticket_for_flag,
    )
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.money import to_decimal  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Pydantic models ----------


class FlagRequest(BaseModel):
    target_type: str  # 'rider' or 'driver'
    reason: str
    description: Optional[str] = None


class ComplaintRequest(BaseModel):
    against_type: str  # 'rider' or 'driver'
    category: str  # safety, behavior, fraud, damage, other
    description: str


class ComplaintResolveRequest(BaseModel):
    status: str  # resolved or dismissed
    resolution: str


class DisputeCreateRequest(BaseModel):
    # user_name was removed (PIPEDA): names are joined from users at read
    # time. Pydantic ignores the extra field if an old client still sends it.
    ride_id: Optional[str] = None
    user_id: Optional[str] = None
    user_type: str = "rider"
    reason: str = ""
    description: str = ""
    refund_amount: float = 0


class DisputeUpdateRequest(BaseModel):
    reason: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    refund_amount: Optional[float] = None
    user_type: Optional[str] = None


class DisputeResolveRequest(BaseModel):
    status: Optional[str] = None  # resolved, rejected, pending
    notes: Optional[str] = None


class TicketCreateRequest(BaseModel):
    subject: str = ""
    category: str = "general"
    message: str = ""
    priority: str = "medium"
    user_id: Optional[str] = None
    user_name: str = "Admin"
    user_email: str = ""


class TicketReplyRequest(BaseModel):
    message: str
    status: Optional[str] = None


class TicketUpdateRequest(BaseModel):
    subject: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None


# ---------- Disputes ----------


@router.get("/disputes")
async def admin_get_disputes(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
):
    """Get disputes with pagination. Optional `status` filter."""
    filters: Dict[str, Any] = {}
    if status and status != "all":
        filters["status"] = status
    try:
        disputes = await db_supabase.get_rows(
            "disputes",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.warning("disputes table may not exist yet")
        return []

    if not disputes:
        return disputes

    # Enrich with user_name joined from users table — PIPEDA-safe because
    # we derive the name at read time from user_id rather than storing it.
    user_ids = list({d["user_id"] for d in disputes if d.get("user_id")})
    user_name_map: Dict[str, str] = {}
    if user_ids:
        try:
            users = await db_supabase.get_rows(
                "users",
                {"id": {"$in": user_ids}},
                limit=len(user_ids),
            )
            for u in users or []:
                parts = [u.get("first_name") or "", u.get("last_name") or ""]
                name = " ".join(p for p in parts if p).strip()
                if name and u.get("id"):
                    user_name_map[u["id"]] = name
        except Exception as exc:
            logger.error("Failed to enrich disputes with user names: %s", exc, exc_info=True)
            raise HTTPException(status_code=503, detail="ERR_DATABASE") from exc

    return [{**d, "user_name": user_name_map.get(d.get("user_id", ""), "")} for d in disputes]


@router.get("/disputes/stats")
async def admin_get_dispute_stats():
    """Aggregate dispute counts and refund totals across all rows."""
    try:
        rows = await db_supabase.get_rows("disputes", {}, limit=10000)
    except Exception:
        logger.warning("disputes table may not exist yet")
        return {
            "open": 0,
            "under_review": 0,
            "resolved": 0,
            "rejected": 0,
            "total_refunded": 0,
        }

    counts = {"open": 0, "under_review": 0, "resolved": 0, "rejected": 0}
    total_refunded = Decimal(0)
    for d in rows or []:
        s = d.get("status")
        if s in counts:
            counts[s] += 1
        if s == "resolved":
            try:
                total_refunded += Decimal(str(d.get("refund_amount") or 0))
            except (TypeError, ValueError):
                pass
    return {**counts, "total_refunded": float(to_decimal(total_refunded))}


@router.get("/disputes/chargebacks")
async def admin_get_chargebacks(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
):
    """Card-network chargebacks (Stripe disputes) — C23 Action item 3.

    Distinct from `disputes` above (rider-raised refund requests): this
    reads `stripe_disputes`, populated by the `charge.dispute.created`/
    `.closed`/`.updated` webhook handlers (B27, C23 Action 1). Read-only —
    chargebacks are resolved via the Stripe Dashboard today
    (`docs/runbooks/payment-dispute-evidence.md`); this endpoint exists so
    an admin can *see* an open chargeback's deadline without a raw SQL
    query, not to act on it.

    Must be registered before the `/disputes/{dispute_id}` path-param
    routes below — FastAPI matches in registration order, and
    `{dispute_id}` would otherwise swallow the literal `chargebacks` path
    segment (same reason `/disputes/stats` sits above them too).
    """
    filters: Dict[str, Any] = {}
    if status and status != "all":
        filters["status"] = status
    try:
        rows = await db_supabase.get_rows(
            "stripe_disputes",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.error("Failed to fetch stripe_disputes: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="ERR_DATABASE") from exc

    if not rows:
        return rows

    # Enrich with the human-readable ride_code (not the raw ride row — no
    # PII needed here, just something an admin can search/recognize).
    ride_ids = list({r["ride_id"] for r in rows if r.get("ride_id")})
    ride_code_map: Dict[str, str] = {}
    if ride_ids:
        try:
            rides = await db_supabase.get_rows(
                "rides",
                {"id": {"$in": ride_ids}},
                limit=len(ride_ids),
            )
            for r in rides or []:
                if r.get("id") and r.get("ride_code"):
                    ride_code_map[r["id"]] = r["ride_code"]
        except Exception as exc:
            logger.error("Failed to enrich chargebacks with ride codes: %s", exc, exc_info=True)
            raise HTTPException(status_code=503, detail="ERR_DATABASE") from exc

    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        days_remaining = None
        due_by_raw = r.get("evidence_due_by")
        if due_by_raw and not r.get("evidence_submitted_at"):
            try:
                due_by = datetime.fromisoformat(str(due_by_raw).replace("Z", "+00:00"))
                if due_by.tzinfo is None:
                    due_by = due_by.replace(tzinfo=timezone.utc)
                days_remaining = (due_by - now).days
            except (TypeError, ValueError):
                days_remaining = None
        result.append(
            {
                **r,
                "ride_code": ride_code_map.get(r.get("ride_id") or "", None),
                "days_remaining": days_remaining,
            }
        )
    return result


@router.post("/disputes")
async def admin_create_dispute(dispute: DisputeCreateRequest, admin: dict = Depends(get_admin_user)):
    """Create a dispute manually from admin."""
    doc = {
        "id": str(uuid.uuid4()),
        "ride_id": dispute.ride_id,
        "user_id": dispute.user_id,
        # PIPEDA data minimization: user_name is NOT persisted — the admin
        # list endpoint joins users by user_id at read time instead.
        "user_type": dispute.user_type,
        "reason": dispute.reason,
        "description": dispute.description,
        "status": "pending",
        "refund_amount": dispute.refund_amount,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("disputes", doc)
    asyncio.create_task(create_ticket_for_dispute(doc))
    await log_admin_action(admin, "dispute_created", "disputes", doc["id"], {"ride_id": dispute.ride_id})
    return {"success": True, "dispute": doc}


@router.get("/disputes/{dispute_id}")
async def admin_get_dispute_details(dispute_id: str):
    """Get detailed dispute information."""
    dispute = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("disputes", {"id": dispute_id}, limit=1))
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    ride = await db_supabase.get_ride(dispute.get("ride_id"))
    return {**dispute, "ride_details": ride}


@router.put("/disputes/{dispute_id}")
async def admin_update_dispute(dispute_id: str, dispute: DisputeUpdateRequest, admin: dict = Depends(get_admin_user)):
    """Update a dispute."""
    updates: Dict[str, Any] = {}
    if dispute.reason is not None:
        updates["reason"] = dispute.reason
    if dispute.description is not None:
        updates["description"] = dispute.description
    if dispute.status is not None:
        updates["status"] = dispute.status
    if dispute.refund_amount is not None:
        updates["refund_amount"] = dispute.refund_amount
    if dispute.user_type is not None:
        updates["user_type"] = dispute.user_type
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("disputes", {"id": dispute_id}, updates)
        await log_admin_action(
            admin,
            "dispute_updated",
            "disputes",
            dispute_id,
            {"fields": sorted(k for k in updates if k != "updated_at")},
        )
    return {"message": "Dispute updated"}


@router.put("/disputes/{dispute_id}/resolve")
async def admin_resolve_dispute(
    dispute_id: str,
    resolution: DisputeResolveRequest,
    admin: dict = Depends(get_admin_user),
):
    """Resolve a dispute. resolved_by is set from the authenticated admin (F-32)."""
    resolution_data = {
        "resolution_status": resolution.status,
        "resolution_notes": resolution.notes or "",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_by": admin["id"],
    }
    await db_supabase.update_one("disputes", {"id": dispute_id}, resolution_data)
    await log_admin_action(
        admin,
        "dispute_resolved",
        "disputes",
        dispute_id,
        {"status": resolution.status, "notes": resolution.notes},
    )
    return {"message": "Dispute resolved"}


@router.delete("/disputes/{dispute_id}")
async def admin_delete_dispute(dispute_id: str, admin: dict = Depends(get_admin_user)):
    """Delete a dispute."""
    await db_supabase.delete_many("disputes", {"id": dispute_id})
    await log_admin_action(admin, "dispute_deleted", "disputes", dispute_id, {})
    return {"message": "Dispute deleted"}


# ---------- Support Tickets ----------


@router.get("/tickets")
async def admin_get_tickets(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    service_area_id: Optional[str] = None,
):
    """Get support tickets with pagination. Optional `status` / `service_area_id` filters."""
    filters: Dict[str, Any] = {}
    if status and status != "all":
        filters["status"] = status
    if service_area_id and service_area_id != "all":
        filters["service_area_id"] = service_area_id
    tickets = await db_supabase.get_rows(
        "support_tickets",
        filters,
        order="created_at",
        desc=True,
        limit=limit,
        offset=offset,
    )
    return tickets


@router.post("/tickets")
async def admin_create_ticket(ticket: TicketCreateRequest, admin: dict = Depends(get_admin_user)):
    """Create a support ticket manually from admin."""
    doc = {
        "id": str(uuid.uuid4()),
        "subject": ticket.subject,
        "category": ticket.category,
        "message": ticket.message,
        "priority": ticket.priority,
        "user_id": ticket.user_id,
        "user_name": ticket.user_name,
        "user_email": ticket.user_email,
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("support_tickets", doc)
    await log_admin_action(admin, "ticket_created", "support_tickets", doc["id"], {"subject": ticket.subject})
    return {"success": True, "ticket": doc}


@router.get("/tickets/{ticket_id}")
async def admin_get_ticket_details(ticket_id: str):
    """Get detailed ticket information."""
    ticket = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("support_tickets", {"id": ticket_id}, limit=1)
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    messages = await db_supabase.get_rows("support_messages", {"ticket_id": ticket_id}, order="created_at", limit=100)
    return {**ticket, "messages": messages}


@router.post("/tickets/{ticket_id}/reply")
async def admin_reply_to_ticket(ticket_id: str, reply: TicketReplyRequest, admin: dict = Depends(get_admin_user)):
    """Reply to a support ticket. sender_id is set from the authenticated admin (F-29)."""
    message_doc = {
        "ticket_id": ticket_id,
        "sender_type": "admin",
        "sender_id": admin["id"],
        "message": reply.message,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("support_messages", message_doc)

    if reply.status:
        await db_supabase.update_one(
            "support_tickets",
            {"id": ticket_id},
            {
                "status": reply.status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    await log_admin_action(
        admin,
        "ticket_reply",
        "support_tickets",
        ticket_id,
        {"status": reply.status},
    )
    return {"message": "Reply sent"}


@router.post("/tickets/{ticket_id}/close")
async def admin_close_ticket(ticket_id: str, admin: dict = Depends(get_admin_user)):
    """Close a support ticket."""
    await db_supabase.update_one(
        "support_tickets",
        {"id": ticket_id},
        {"status": "closed", "closed_at": datetime.now(timezone.utc).isoformat()},
    )
    await log_admin_action(admin, "ticket_closed", "support_tickets", ticket_id, {})
    return {"message": "Ticket closed"}


@router.put("/tickets/{ticket_id}")
async def admin_update_ticket(ticket_id: str, ticket: TicketUpdateRequest, admin: dict = Depends(get_admin_user)):
    """Update a support ticket."""
    updates: Dict[str, Any] = {}
    if ticket.subject is not None:
        updates["subject"] = ticket.subject
    if ticket.category is not None:
        updates["category"] = ticket.category
    if ticket.priority is not None:
        updates["priority"] = ticket.priority
    if ticket.status is not None:
        updates["status"] = ticket.status
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("support_tickets", {"id": ticket_id}, updates)
        await log_admin_action(
            admin,
            "ticket_updated",
            "support_tickets",
            ticket_id,
            {"fields": sorted(k for k in updates if k != "updated_at")},
        )
    return {"message": "Ticket updated"}


@router.delete("/tickets/{ticket_id}")
async def admin_delete_ticket(ticket_id: str, admin: dict = Depends(get_admin_user)):
    """Delete a support ticket."""
    await db_supabase.delete_many("support_tickets", {"id": ticket_id})
    await log_admin_action(admin, "ticket_deleted", "support_tickets", ticket_id, {})
    return {"message": "Ticket deleted"}


# ---------- Flags ----------


@router.post("/rides/{ride_id}/flag")
async def admin_flag_ride_participant(ride_id: str, req: FlagRequest, admin: dict = Depends(get_admin_user)):
    """Flag a rider or driver from a ride. 3 active flags = auto-ban."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if req.target_type not in ("rider", "driver"):
        raise HTTPException(status_code=400, detail="target_type must be 'rider' or 'driver'")

    target_id = ride.get("rider_id") if req.target_type == "rider" else ride.get("driver_id")
    if not target_id:
        raise HTTPException(status_code=400, detail=f"No {req.target_type} assigned to this ride")

    flag_data = {
        "id": str(uuid.uuid4()),
        "target_type": req.target_type,
        "target_id": target_id,
        "ride_id": ride_id,
        "reason": req.reason,
        "description": req.description,
        "flagged_by": "admin",
        "is_active": True,
    }
    result = await db_supabase.create_flag(flag_data)
    asyncio.create_task(create_ticket_for_flag(result or flag_data, ride))
    await log_admin_action(
        admin,
        "flag_created",
        "flags",
        flag_data["id"],
        {
            "ride_id": ride_id,
            "target_type": req.target_type,
            "target_id": target_id,
            "reason": req.reason,
        },
    )
    return result


@router.get("/flags")
async def admin_list_flags(
    limit: int = 100,
    offset: int = 0,
    target_type: Optional[str] = None,
    service_area_id: Optional[str] = None,
    is_active: Optional[bool] = None,
):
    """List all flags. Optional `target_type` / `service_area_id` / `is_active` filters."""
    filters: Dict[str, Any] = {}
    if target_type and target_type != "all":
        filters["target_type"] = target_type
    if service_area_id and service_area_id != "all":
        filters["service_area_id"] = service_area_id
    if is_active is not None:
        filters["is_active"] = is_active
    flags = await db_supabase.get_rows("flags", filters, order="created_at", desc=True, limit=limit, offset=offset)
    return flags


@router.put("/flags/{flag_id}/deactivate")
async def admin_deactivate_flag(flag_id: str, admin: dict = Depends(get_admin_user)):
    """Deactivate a flag (soft delete)."""
    result = await db_supabase.update_one("flags", {"id": flag_id}, {"$set": {"is_active": False}})
    if not result:
        raise HTTPException(status_code=404, detail="Flag not found")
    await log_admin_action(admin, "flag_deactivated", "flags", flag_id, {})
    return {"message": "Flag deactivated"}


@router.delete("/flags/{flag_id}")
async def admin_delete_flag(flag_id: str, admin: dict = Depends(get_admin_user)):
    """Permanently delete a flag."""
    await db_supabase.delete_one("flags", {"id": flag_id})
    await log_admin_action(admin, "flag_deleted", "flags", flag_id, {})
    return {"message": "Flag deleted"}


# ---------- Complaints ----------


@router.post("/rides/{ride_id}/complaint")
async def admin_create_complaint(ride_id: str, req: ComplaintRequest, admin: dict = Depends(get_admin_user)):
    """Create a complaint against a rider or driver from a ride."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if req.against_type not in ("rider", "driver"):
        raise HTTPException(status_code=400, detail="against_type must be 'rider' or 'driver'")

    against_id = ride.get("rider_id") if req.against_type == "rider" else ride.get("driver_id")
    if not against_id:
        raise HTTPException(status_code=400, detail=f"No {req.against_type} assigned to this ride")

    complaint_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "against_type": req.against_type,
        "against_id": against_id,
        "category": req.category,
        "description": req.description,
        "status": "open",
        "created_by": "admin",
    }
    complaint = await db_supabase.create_complaint(complaint_data)
    # Raise a Zoho Desk ticket for support — fire-and-forget, no-op if disabled.
    asyncio.create_task(create_ticket_for_complaint(complaint or complaint_data, ride))
    await log_admin_action(
        admin, "complaint_created", "complaints", complaint_data["id"], {"against_type": req.against_type}
    )
    return complaint


@router.put("/complaints/{complaint_id}/resolve")
async def admin_resolve_complaint(
    complaint_id: str,
    req: ComplaintResolveRequest,
    admin: dict = Depends(get_admin_user),
):
    """Resolve or dismiss a complaint. resolved_by is set from the authenticated admin (F-32)."""
    result = await db_supabase.resolve_complaint(
        complaint_id,
        {
            "status": req.status,
            "resolution": req.resolution,
            "resolved_by": admin["id"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if not result:
        raise HTTPException(status_code=404, detail="Complaint not found")
    await log_admin_action(
        admin,
        "complaint_resolved",
        "complaints",
        complaint_id,
        {"status": req.status},
    )
    return result


@router.get("/complaints")
async def admin_list_complaints(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    against_type: Optional[str] = None,
    service_area_id: Optional[str] = None,
):
    """List all complaints. Optional `status` / `against_type` / `service_area_id` filters."""
    filters: Dict[str, Any] = {}
    if status and status != "all":
        filters["status"] = status
    if against_type and against_type != "all":
        filters["against_type"] = against_type
    if service_area_id and service_area_id != "all":
        filters["service_area_id"] = service_area_id
    return await db_supabase.get_rows("complaints", filters, order="created_at", desc=True, limit=limit, offset=offset)


@router.delete("/complaints/{complaint_id}")
async def admin_delete_complaint(complaint_id: str, admin: dict = Depends(get_admin_user)):
    """Delete a complaint."""
    await db_supabase.delete_one("complaints", {"id": complaint_id})
    await log_admin_action(admin, "complaint_deleted", "complaints", complaint_id, {})
    return {"message": "Complaint deleted"}
