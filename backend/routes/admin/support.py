import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ... import db_supabase
except ImportError:
    import db_supabase

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


# ---------- Disputes ----------


@router.get("/disputes")
async def admin_get_disputes():
    """Get all disputes."""
    try:
        disputes = await db_supabase.get_rows("disputes", order="created_at", desc=True, limit=500)
    except Exception:
        logger.warning("disputes table may not exist yet")
        return []
    return disputes


@router.post("/disputes")
async def admin_create_dispute(dispute: Dict[str, Any]):
    """Create a dispute manually from admin."""
    doc = {
        "id": str(uuid.uuid4()),
        "ride_id": dispute.get("ride_id"),
        "user_id": dispute.get("user_id"),
        "user_name": dispute.get("user_name", ""),
        "user_type": dispute.get("user_type", "rider"),
        "reason": dispute.get("reason", ""),
        "description": dispute.get("description", ""),
        "status": "pending",
        "refund_amount": dispute.get("refund_amount", 0),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db_supabase.insert_one("disputes", doc)
    return {"success": True, "dispute": doc}


@router.get("/disputes/{dispute_id}")
async def admin_get_dispute_details(dispute_id: str):
    """Get detailed dispute information."""
    dispute = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("disputes", {"id": dispute_id}, limit=1))
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    # Get related ride information
    ride = await db_supabase.get_ride(dispute.get("ride_id"))

    return {**dispute, "ride_details": ride}


@router.put("/disputes/{dispute_id}")
async def admin_update_dispute(dispute_id: str, dispute: Dict[str, Any]):
    """Update a dispute."""
    allowed = ["reason", "description", "status", "refund_amount", "user_type"]
    updates = {k: v for k, v in dispute.items() if k in allowed and v is not None}
    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db_supabase.update_one("disputes", {"id": dispute_id}, updates)
    return {"message": "Dispute updated"}


@router.put("/disputes/{dispute_id}/resolve")
async def admin_resolve_dispute(dispute_id: str, resolution: Dict[str, Any]):
    """Resolve a dispute."""
    resolution_data = {
        "resolution_status": resolution.get("status"),  # resolved, rejected, pending
        "resolution_notes": resolution.get("notes", ""),
        "resolved_at": datetime.utcnow().isoformat(),
        "resolved_by": resolution.get("resolved_by", "admin"),
    }

    await db_supabase.update_one("disputes", {"id": dispute_id}, resolution_data)
    return {"message": "Dispute resolved"}


@router.delete("/disputes/{dispute_id}")
async def admin_delete_dispute(dispute_id: str):
    """Delete a dispute."""
    await db_supabase.delete_many("disputes", {"id": dispute_id})
    return {"message": "Dispute deleted"}


# ---------- Support Tickets ----------


@router.get("/tickets")
async def admin_get_tickets():
    """Get all support tickets."""
    tickets = await db_supabase.get_rows("support_tickets", order="created_at", desc=True, limit=500)
    return tickets


@router.post("/tickets")
async def admin_create_ticket(ticket: Dict[str, Any]):
    """Create a support ticket manually from admin."""
    doc = {
        "id": str(uuid.uuid4()),
        "subject": ticket.get("subject", ""),
        "category": ticket.get("category", "general"),
        "message": ticket.get("message", ""),
        "priority": ticket.get("priority", "medium"),
        "user_id": ticket.get("user_id"),
        "user_name": ticket.get("user_name", "Admin"),
        "user_email": ticket.get("user_email", ""),
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db_supabase.insert_one("support_tickets", doc)
    return {"success": True, "ticket": doc}


@router.get("/tickets/{ticket_id}")
async def admin_get_ticket_details(ticket_id: str):
    """Get detailed ticket information."""
    ticket = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("support_tickets", {"id": ticket_id}, limit=1)
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Get ticket messages
    messages = await db_supabase.get_rows("support_messages", {"ticket_id": ticket_id}, order="created_at", limit=100)

    return {**ticket, "messages": messages}


@router.post("/tickets/{ticket_id}/reply")
async def admin_reply_to_ticket(ticket_id: str, reply: Dict[str, Any]):
    """Reply to a support ticket."""
    message_doc = {
        "ticket_id": ticket_id,
        "sender_type": "admin",
        "sender_id": "admin-001",  # Could be dynamic based on current admin
        "message": reply.get("message", ""),
        "created_at": datetime.utcnow().isoformat(),
    }

    # Insert message
    await db_supabase.insert_one("support_messages", message_doc)

    # Update ticket status if needed
    if reply.get("status"):
        await db_supabase.update_one(
            "support_tickets",
            {"id": ticket_id},
            {"status": reply.get("status"), "updated_at": datetime.utcnow().isoformat()},
        )

    return {"message": "Reply sent"}


@router.post("/tickets/{ticket_id}/close")
async def admin_close_ticket(ticket_id: str):
    """Close a support ticket."""
    await db_supabase.update_one(
        "support_tickets", {"id": ticket_id}, {"status": "closed", "closed_at": datetime.utcnow().isoformat()}
    )
    return {"message": "Ticket closed"}


@router.put("/tickets/{ticket_id}")
async def admin_update_ticket(ticket_id: str, ticket: Dict[str, Any]):
    """Update a support ticket."""
    allowed = ["subject", "category", "priority", "status"]
    updates = {k: v for k, v in ticket.items() if k in allowed and v is not None}
    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db_supabase.update_one("support_tickets", {"id": ticket_id}, updates)
    return {"message": "Ticket updated"}


@router.delete("/tickets/{ticket_id}")
async def admin_delete_ticket(ticket_id: str):
    """Delete a support ticket."""
    await db_supabase.delete_many("support_tickets", {"id": ticket_id})
    return {"message": "Ticket deleted"}


# ---------- Flags ----------


@router.post("/rides/{ride_id}/flag")
async def admin_flag_ride_participant(ride_id: str, req: FlagRequest):
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
    return result


@router.get("/flags")
async def admin_list_flags(
    limit: int = 100,
    offset: int = 0,
):
    """List all flags with optional pagination."""
    flags = await db_supabase.get_rows("flags", order="created_at", desc=True, limit=limit, offset=offset)
    return flags


@router.put("/flags/{flag_id}/deactivate")
async def admin_deactivate_flag(flag_id: str):
    """Deactivate a flag (soft delete)."""
    result = await db_supabase.update_one("flags", {"id": flag_id}, {"$set": {"is_active": False}})
    if not result:
        raise HTTPException(status_code=404, detail="Flag not found")
    return {"message": "Flag deactivated"}


@router.delete("/flags/{flag_id}")
async def admin_delete_flag(flag_id: str):
    """Permanently delete a flag."""
    await db_supabase.delete_one("flags", {"id": flag_id})
    return {"message": "Flag deleted"}


# ---------- Complaints ----------


@router.post("/rides/{ride_id}/complaint")
async def admin_create_complaint(ride_id: str, req: ComplaintRequest):
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
    return complaint


@router.put("/complaints/{complaint_id}/resolve")
async def admin_resolve_complaint(complaint_id: str, req: ComplaintResolveRequest):
    """Resolve or dismiss a complaint."""
    result = await db_supabase.resolve_complaint(
        complaint_id,
        {
            "status": req.status,
            "resolution": req.resolution,
            "resolved_by": "admin",
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    if not result:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return result


@router.get("/complaints")
async def admin_list_complaints(limit: int = 100, offset: int = 0):
    """List all complaints."""
    return await db_supabase.get_rows("complaints", order="created_at", desc=True, limit=limit, offset=offset)


@router.delete("/complaints/{complaint_id}")
async def admin_delete_complaint(complaint_id: str):
    """Delete a complaint."""
    await db_supabase.delete_one("complaints", {"id": complaint_id})
    return {"message": "Complaint deleted"}
