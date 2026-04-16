"""
disputes.py – Payment dispute/refund request endpoints for Spinr.
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..settings_loader import get_app_settings
except ImportError:
    import db_supabase
    from dependencies import get_current_user
    from settings_loader import get_app_settings

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/disputes", tags=["Disputes"])


class CreateDisputeRequest(BaseModel):
    ride_id: str
    reason: str  # overcharged | wrong_route | driver_issue | payment_error | other
    description: str
    requested_amount: Optional[Decimal] = None  # If blank, full refund


class ResolveDisputeRequest(BaseModel):
    resolution: str  # approved | partial_refund | rejected
    refund_amount: Optional[Decimal] = None
    admin_note: Optional[str] = None


@api_router.post("")
async def create_dispute(
    req: CreateDisputeRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a payment dispute / refund request for a ride."""
    ride = await db_supabase.get_ride(req.ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized for this ride")

    if ride.get("status") not in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail="Can only dispute completed or cancelled rides")

    # Check for existing open dispute on same ride
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "disputes", {"ride_id": req.ride_id, "status": {"$in": ["open", "under_review"]}}, limit=1
        )
    )
    if existing:
        raise HTTPException(status_code=400, detail="A dispute is already open for this ride")

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

    await db_supabase.insert_one("disputes", dispute)
    return {"success": True, "dispute": dispute}


@api_router.get("")
async def get_user_disputes(current_user: dict = Depends(get_current_user)):
    """Get all disputes filed by the current user."""
    disputes = await db_supabase.get_rows(
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
    dispute = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("disputes", {"id": dispute_id}, limit=1))
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
    disputes = await db_supabase.get_rows(
        "disputes", filters, order="created_at", desc=True, limit=limit, offset=offset
    )

    # Enrich with user + ride info
    enriched = []
    for d in disputes:
        user = await db_supabase.get_user_by_id(d.get("user_id")) if d.get("user_id") else None
        ride = await db_supabase.get_ride(d.get("ride_id")) if d.get("ride_id") else None
        enriched.append(
            {
                **d,
                "user_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Unknown",
                "user_phone": user.get("phone") if user else None,
                "ride_status": ride.get("status") if ride else None,
                "ride_fare": ride.get("total_fare") if ride else None,
            }
        )
    return enriched


@admin_router.put("/{dispute_id}/resolve")
async def admin_resolve_dispute(dispute_id: str, req: ResolveDisputeRequest):
    """Resolve a dispute (approve/reject refund)."""
    dispute = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("disputes", {"id": dispute_id}, limit=1))
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

    await db_supabase.update_one("disputes", {"id": dispute_id}, update_data)

    # If approved/partial, initiate Stripe refund against the ride's PaymentIntent
    refund_result: Dict[str, Any] = {}
    if req.resolution in ("approved", "partial_refund") and req.refund_amount:
        refund_amount_cents = int(float(req.refund_amount) * 100)
        ride = await db.find_one("rides", {"id": dispute.get("ride_id")})
        payment_intent_id = (ride or {}).get("stripe_charge_id") or (ride or {}).get("payment_intent_id")

        if not payment_intent_id:
            logger.warning(
                f"[REFUND] No PaymentIntent for dispute {dispute_id} / ride {dispute.get('ride_id')} — "
                "marking refunded in DB only (cash or unprocessed ride)"
            )
            refund_result = {"status": "manual_required", "reason": "no_payment_intent"}
        else:
            try:
                import stripe as _stripe  # noqa: PLC0415

                settings = await get_app_settings()
                stripe_secret = settings.get("stripe_secret_key", "")
                if not stripe_secret:
                    raise ValueError("Stripe secret key not configured")

                refund = _stripe.Refund.create(
                    payment_intent=payment_intent_id,
                    amount=refund_amount_cents,
                    reason="requested_by_customer",
                    metadata={
                        "dispute_id": dispute_id,
                        "ride_id": str(dispute.get("ride_id")),
                        "admin_note": req.admin_note or "",
                    },
                    api_key=stripe_secret,
                )
                refund_result = {"status": refund.status, "refund_id": refund.id}
                logger.info(
                    f"[REFUND] Stripe refund {refund.id} ({refund.status}) "
                    f"${req.refund_amount} for dispute {dispute_id}"
                )
            except Exception as refund_err:  # noqa: BLE001
                # Don't roll back the resolved status — log and surface the error so
                # the admin knows they may need to issue the refund manually.
                logger.error(f"[REFUND] Stripe refund failed for dispute {dispute_id}: {refund_err}")
                refund_result = {"status": "failed", "error": str(refund_err)}

        # Persist refund outcome on the dispute record
        await db.update_one(
            "disputes",
            {"id": dispute_id},
            {"$set": {"refund_result": refund_result, "updated_at": datetime.utcnow().isoformat()}},
        )

    return {
        "success": True,
        "dispute_id": dispute_id,
        "resolution": req.resolution,
        "refund": refund_result or None,
    }
