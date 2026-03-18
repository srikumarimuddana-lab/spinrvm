"""
disputes.py – Payment dispute/refund request endpoints for Spinr.
"""
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

try:
    from ..dependencies import get_current_user, get_admin_user
    from ..db import db
except ImportError:
    from dependencies import get_current_user, get_admin_user
    from db import db

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/disputes", tags=["Disputes"])


class CreateDisputeRequest(BaseModel):
    ride_id: str
    reason: str  # overcharged | wrong_route | driver_issue | payment_error | other
    description: str
    requested_amount: Optional[float] = None  # If blank, full refund


class ResolveDisputeRequest(BaseModel):
    resolution: str  # approved | partial_refund | rejected
    refund_amount: Optional[float] = None
    admin_note: Optional[str] = None


@api_router.post("")
async def create_dispute(
    req: CreateDisputeRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a payment dispute / refund request for a ride."""
    ride = await db.rides.find_one({"id": req.ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized for this ride")

    if ride.get("status") not in ("completed", "cancelled"):
        raise HTTPException(
            status_code=400, detail="Can only dispute completed or cancelled rides"
        )

    # Check for existing open dispute on same ride
    existing = await db.disputes.find_one(
        {"ride_id": req.ride_id, "status": {"$in": ["open", "under_review"]}}
    )
    if existing:
        raise HTTPException(
            status_code=400, detail="A dispute is already open for this ride"
        )

    dispute = {
        "id": str(uuid.uuid4()),
        "ride_id": req.ride_id,
        "user_id": current_user["id"],
        "reason": req.reason,
        "description": req.description,
        "requested_amount": req.requested_amount or ride.get("total_fare", 0),
        "original_fare": ride.get("total_fare", 0),
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    await db.disputes.insert_one(dispute)
    return {"success": True, "dispute": dispute}


@api_router.get("")
async def get_user_disputes(current_user: dict = Depends(get_current_user)):
    """Get all disputes filed by the current user."""
    disputes = await db.get_rows(
        "disputes",
        {"user_id": current_user["id"]},
        order="created_at",
        desc=True,
        limit=50,
    )
    return disputes


@api_router.get("/{dispute_id}")
async def get_dispute(dispute_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific dispute by ID."""
    dispute = await db.disputes.find_one({"id": dispute_id})
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    if dispute.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return dispute


# ============ Admin Dispute Endpoints ============

admin_router = APIRouter(prefix="/admin/disputes", tags=["Admin Disputes"])


@admin_router.get("")
async def admin_get_disputes(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Get all disputes with optional status filter."""
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    disputes = await db.get_rows(
        "disputes", filters, order="created_at", desc=True, limit=limit, offset=offset
    )

    # Enrich with user + ride info
    enriched = []
    for d in disputes:
        user = await db.users.find_one({"id": d.get("user_id")}) if d.get("user_id") else None
        ride = await db.rides.find_one({"id": d.get("ride_id")}) if d.get("ride_id") else None
        enriched.append({
            **d,
            "user_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Unknown",
            "user_phone": user.get("phone") if user else None,
            "ride_status": ride.get("status") if ride else None,
            "ride_fare": ride.get("total_fare") if ride else None,
        })
    return enriched


@admin_router.put("/{dispute_id}/resolve")
async def admin_resolve_dispute(dispute_id: str, req: ResolveDisputeRequest):
    """Resolve a dispute (approve/reject refund)."""
    dispute = await db.disputes.find_one({"id": dispute_id})
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    if dispute.get("status") in ("resolved", "rejected"):
        raise HTTPException(status_code=400, detail="Dispute already resolved")

    update_data = {
        "status": "resolved" if req.resolution != "rejected" else "rejected",
        "resolution": req.resolution,
        "refund_amount": req.refund_amount or 0,
        "admin_note": req.admin_note or "",
        "resolved_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    await db.disputes.update_one({"id": dispute_id}, {"$set": update_data})

    # If approved, initiate refund via Stripe (stub for now)
    if req.resolution in ("approved", "partial_refund") and req.refund_amount:
        logger.info(
            f"Refund of ${req.refund_amount} initiated for dispute {dispute_id}"
        )
        # In production: stripe.Refund.create(payment_intent=..., amount=...)

    return {"success": True, "dispute_id": dispute_id, "resolution": req.resolution}
