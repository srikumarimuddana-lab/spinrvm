"""
disputes.py – Payment dispute/refund request endpoints for Spinr.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from .. import db_supabase
    from ..dependencies import get_admin_user, get_current_user
    from ..features import send_push_notification
    from ..services.zoho_desk_integration import create_ticket_for_dispute
    from ..settings_loader import get_app_settings
    from ..utils.audit_logger import log_admin_action
    from ..utils.money import dollars_to_cents
except ImportError:
    import db_supabase
    from dependencies import get_admin_user, get_current_user
    from features import send_push_notification
    from services.zoho_desk_integration import create_ticket_for_dispute
    from settings_loader import get_app_settings
    from utils.audit_logger import log_admin_action
    from utils.money import dollars_to_cents  # noqa: F401

db = db_supabase  # legacy alias
get_current_admin = get_admin_user

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
            "disputes",
            {"ride_id": req.ride_id, "status": {"$in": ["open", "under_review"]}},
            limit=1,
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
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    await db_supabase.insert_one("disputes", dispute)

    rider_id = ride.get("rider_id") or current_user["id"]
    if rider_id:
        try:
            await send_push_notification(
                rider_id,
                "Dispute received",
                "We've received your dispute and will review it within 1-2 business days.",
                data={"type": "dispute_created", "dispute_id": str(dispute["id"])},
            )
        except Exception as notif_err:
            logger.debug(f"Dispute created notification failed: {notif_err}")

    # Raise a Zoho Desk ticket for support to track the dispute/refund —
    # fire-and-forget; no-op when the integration is disabled.
    asyncio.create_task(create_ticket_for_dispute(dispute, ride))

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

admin_router = APIRouter(prefix="/disputes", tags=["Admin Disputes"])


@admin_router.get("")
async def admin_get_disputes(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_admin: dict = Depends(get_current_admin),
):
    """Get all disputes with optional status filter."""
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    disputes = await db_supabase.get_rows(
        "disputes", filters, order="created_at", desc=True, limit=limit, offset=offset
    )

    # Batch-fetch users and rides in 2 queries instead of 2×N per-dispute calls.
    # user_phone intentionally omitted from response — P1-9 (PIPEDA: no PII in bulk list).
    user_ids = list({d["user_id"] for d in disputes if d.get("user_id")})
    ride_ids = list({d["ride_id"] for d in disputes if d.get("ride_id")})
    # Only first/last name are surfaced (user_phone is intentionally omitted —
    # P1-9). Project name columns to keep base64 profile_image out of the read.
    users = (
        await db_supabase.get_rows(
            "users", {"id": {"$in": user_ids}}, columns="id,first_name,last_name", limit=len(user_ids)
        )
        if user_ids
        else []
    )
    rides = await db_supabase.get_rows("rides", {"id": {"$in": ride_ids}}, limit=len(ride_ids)) if ride_ids else []
    users_by_id = {u["id"]: u for u in (users or [])}
    rides_by_id = {r["id"]: r for r in (rides or [])}

    enriched = []
    for d in disputes:
        user = users_by_id.get(d.get("user_id"))
        ride = rides_by_id.get(d.get("ride_id"))
        enriched.append(
            {
                **d,
                "user_name": (
                    f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Unknown"
                ),
                "ride_status": ride.get("status") if ride else None,
                "ride_fare": ride.get("total_fare") if ride else None,
            }
        )
    return enriched


@admin_router.put("/{dispute_id}/resolve")
async def admin_resolve_dispute(
    dispute_id: str,
    req: ResolveDisputeRequest,
    current_admin: dict = Depends(get_current_admin),
):
    """Resolve a dispute (approve/reject refund)."""
    dispute = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("disputes", {"id": dispute_id}, limit=1))
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    if dispute.get("status") in ("resolved", "rejected"):
        raise HTTPException(status_code=400, detail="Dispute already resolved")

    # Stripe refund runs BEFORE we mark the dispute resolved.
    # If Stripe fails we raise 502 — the dispute stays open so the admin can retry.
    original_fare = Decimal(str(dispute.get("original_fare") or 0))
    if req.refund_amount and req.refund_amount > original_fare:
        raise HTTPException(
            status_code=400,
            detail=f"Refund amount ${req.refund_amount} exceeds original fare ${original_fare}",
        )

    refund_result: Dict[str, Any] = {}
    if req.resolution in ("approved", "partial_refund") and req.refund_amount:
        # HALF_UP cents conversion — int() truncation shaved sub-cent refund
        # amounts (e.g. 10.005 → 1000 cents instead of 1001).
        refund_amount_cents = dollars_to_cents(req.refund_amount)
        ride = await db_supabase.get_ride(dispute.get("ride_id"))
        payment_intent_id = (ride or {}).get("stripe_charge_id") or (ride or {}).get("payment_intent_id")

        if not payment_intent_id:
            logger.warning(
                f"[REFUND] No PaymentIntent for dispute {dispute_id} / ride {dispute.get('ride_id')} — "
                "marking refunded in DB only (cash or unprocessed ride)"
            )
            refund_result = {"status": "manual_required", "reason": "no_payment_intent"}
        else:
            import stripe as _stripe  # noqa: PLC0415

            settings = await get_app_settings()
            stripe_secret = settings.get("stripe_secret_key", "")
            if not stripe_secret:
                raise HTTPException(status_code=503, detail="Stripe not configured")

            try:
                refund = await asyncio.to_thread(
                    lambda: _stripe.Refund.create(
                        payment_intent=payment_intent_id,
                        amount=refund_amount_cents,
                        reason="requested_by_customer",
                        idempotency_key=f"refund-dispute-{dispute_id}",
                        metadata={
                            "dispute_id": dispute_id,
                            "ride_id": str(dispute.get("ride_id")),
                            "admin_note": req.admin_note or "",
                        },
                        api_key=stripe_secret,
                    )
                )
                refund_result = {"status": refund.status, "refund_id": refund.id}
                logger.info(
                    f"[REFUND] Stripe refund {refund.id} ({refund.status}) "
                    f"${req.refund_amount} for dispute {dispute_id}"
                )
            except Exception as refund_err:
                logger.error(f"[REFUND] Stripe refund failed for dispute {dispute_id}: {refund_err}")
                raise HTTPException(
                    status_code=502,
                    detail="Stripe refund failed — retry to reuse idempotency key",
                ) from refund_err

    # Stripe succeeded (or not required) — persist the resolved status
    update_data: Dict[str, Any] = {
        "status": "resolved" if req.resolution != "rejected" else "rejected",
        "resolution": req.resolution,
        "refund_amount": req.refund_amount or 0,
        "admin_note": req.admin_note or "",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if refund_result:
        update_data["refund_result"] = refund_result

    await db_supabase.update_one("disputes", {"id": dispute_id}, update_data)

    await log_admin_action(
        current_admin,
        "dispute_resolved",
        "dispute",
        dispute_id,
        {
            "resolution": req.resolution,
            "refund_amount": str(req.refund_amount or 0),
            "admin_note": req.admin_note or "",
        },
    )

    # Notify rider of outcome
    rider_id = dispute.get("user_id")
    if rider_id:
        # `req.resolution` is one of approved | partial_refund | rejected
        # ("refund" is never a valid value — comparing against it here
        # previously made every notification say "reviewed" regardless of
        # outcome, even a full approval or an explicit rejection).
        resolution_label = {
            "approved": "approved",
            "partial_refund": "approved",
            "rejected": "rejected",
        }.get(req.resolution, "reviewed")
        amount_text = (
            f" A refund of ${req.refund_amount:.2f} has been issued."
            if req.resolution in ("approved", "partial_refund") and req.refund_amount
            else ""
        )
        try:
            await send_push_notification(
                rider_id,
                "Dispute update",
                f"Your dispute has been {resolution_label}.{amount_text}",
                data={
                    "type": "dispute_resolved",
                    "dispute_id": str(dispute_id),
                    "resolution": req.resolution,
                },
            )
        except Exception as notif_err:
            logger.debug(f"Dispute resolved notification failed: {notif_err}")

    return {
        "success": True,
        "dispute_id": dispute_id,
        "resolution": req.resolution,
        "refund": refund_result or None,
    }
