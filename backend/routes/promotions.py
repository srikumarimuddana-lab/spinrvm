"""
promotions.py – Promo codes & referral system for Spinr.
"""
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

try:
    from ..dependencies import get_current_user, get_admin_user
    from ..db import db
except ImportError:
    from dependencies import get_current_user, get_admin_user
    from db import db

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/promo", tags=["Promotions"])


# ============ Pydantic Models ============

class ValidatePromoRequest(BaseModel):
    code: str
    ride_fare: float = 0.0  # So we can calculate actual discount


class CreatePromoCodeRequest(BaseModel):
    code: str
    discount_type: str = "flat"         # flat | percentage
    discount_value: float               # e.g. 5.00 for $5 off, or 10.0 for 10%
    max_discount: Optional[float] = None  # Cap for percentage discounts
    max_uses: int = 100
    max_uses_per_user: int = 1
    expiry_date: Optional[str] = None   # ISO 8601
    is_active: bool = True
    description: Optional[str] = None


class UpdatePromoCodeRequest(BaseModel):
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    max_discount: Optional[float] = None
    max_uses: Optional[int] = None
    max_uses_per_user: Optional[int] = None
    expiry_date: Optional[str] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None


# ============ User-Facing Endpoints ============

@api_router.post("/validate")
async def validate_promo(
    req: ValidatePromoRequest,
    current_user: dict = Depends(get_current_user),
):
    """Validate a promo code and return the discount amount."""
    code = req.code.strip().upper()
    promo = await db.promo_codes.find_one({"code": code})

    if not promo:
        raise HTTPException(status_code=404, detail="Invalid promo code")

    if not promo.get("is_active", False):
        raise HTTPException(status_code=400, detail="This promo code is no longer active")

    # Check expiry
    expiry = promo.get("expiry_date")
    if expiry:
        if isinstance(expiry, str):
            try:
                expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                if expiry_dt < datetime.utcnow():
                    raise HTTPException(status_code=400, detail="This promo code has expired")
            except ValueError:
                pass

    # Check total usage
    total_uses = promo.get("uses", 0)
    max_uses = promo.get("max_uses", 100)
    if total_uses >= max_uses:
        raise HTTPException(status_code=400, detail="This promo code has reached its usage limit")

    # Check per-user usage
    max_per_user = promo.get("max_uses_per_user", 1)
    user_uses = await db.promo_applications.count_documents({
        "promo_code_id": promo["id"],
        "user_id": current_user["id"],
    })
    if user_uses >= max_per_user:
        raise HTTPException(status_code=400, detail="You have already used this promo code")

    # Calculate discount
    discount_type = promo.get("discount_type", "flat")
    discount_value = float(promo.get("discount_value", 0))

    if discount_type == "percentage":
        discount = round(req.ride_fare * (discount_value / 100), 2)
        max_cap = promo.get("max_discount")
        if max_cap and discount > max_cap:
            discount = max_cap
    else:
        discount = min(discount_value, req.ride_fare)

    return {
        "valid": True,
        "code": code,
        "discount_type": discount_type,
        "discount_value": discount_value,
        "discount_amount": discount,
        "promo_id": promo["id"],
        "description": promo.get("description", ""),
    }


@api_router.post("/apply")
async def apply_promo(
    req: ValidatePromoRequest,
    current_user: dict = Depends(get_current_user),
):
    """Apply a promo code (records usage). Call after ride creation."""
    # Re-validate
    validation = await validate_promo(req, current_user)

    # Record application
    application = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "promo_code_id": validation["promo_id"],
        "code": validation["code"],
        "discount_applied": validation["discount_amount"],
        "created_at": datetime.utcnow().isoformat(),
    }
    await db.promo_applications.insert_one(application)

    # Increment usage count
    promo = await db.promo_codes.find_one({"id": validation["promo_id"]})
    if promo:
        await db.promo_codes.update_one(
            {"id": validation["promo_id"]},
            {"$set": {"uses": promo.get("uses", 0) + 1}},
        )

    return {
        "success": True,
        "discount_applied": validation["discount_amount"],
        "application_id": application["id"],
    }


# ============ Admin Promo Code CRUD ============

admin_router = APIRouter(prefix="/admin/promo-codes", tags=["Admin Promotions"])


@admin_router.get("")
async def admin_get_promo_codes():
    """Get all promo codes."""
    codes = await db.get_rows("promo_codes", order="created_at", desc=True, limit=500)
    return codes


@admin_router.post("")
async def admin_create_promo_code(req: CreatePromoCodeRequest):
    """Create a new promo code."""
    code = req.code.strip().upper()

    # Check uniqueness
    existing = await db.promo_codes.find_one({"code": code})
    if existing:
        raise HTTPException(status_code=400, detail=f"Promo code '{code}' already exists")

    if req.discount_type not in ("flat", "percentage"):
        raise HTTPException(status_code=400, detail="discount_type must be 'flat' or 'percentage'")

    promo = {
        "id": str(uuid.uuid4()),
        "code": code,
        "discount_type": req.discount_type,
        "discount_value": req.discount_value,
        "max_discount": req.max_discount,
        "max_uses": req.max_uses,
        "max_uses_per_user": req.max_uses_per_user,
        "uses": 0,
        "expiry_date": req.expiry_date,
        "is_active": req.is_active,
        "description": req.description or "",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    await db.promo_codes.insert_one(promo)
    return {"success": True, "promo": promo}


@admin_router.put("/{promo_id}")
async def admin_update_promo_code(promo_id: str, req: UpdatePromoCodeRequest):
    """Update an existing promo code."""
    update_data: Dict[str, Any] = {"updated_at": datetime.utcnow().isoformat()}
    for field in [
        "discount_type", "discount_value", "max_discount",
        "max_uses", "max_uses_per_user", "expiry_date",
        "is_active", "description",
    ]:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    await db.promo_codes.update_one({"id": promo_id}, {"$set": update_data})
    updated = await db.promo_codes.find_one({"id": promo_id})
    if not updated:
        raise HTTPException(status_code=404, detail="Promo code not found")
    return updated


@admin_router.delete("/{promo_id}")
async def admin_delete_promo_code(promo_id: str):
    """Delete a promo code."""
    await db.promo_codes.delete_one({"id": promo_id})
    return {"deleted": True}
