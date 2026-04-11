"""
promotions.py – Promo codes & referral system for Spinr.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from ..db import db
    from ..dependencies import get_current_user
except ImportError:
    from db import db
    from dependencies import get_current_user

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/promo", tags=["Promotions"])


# ============ Pydantic Models ============


class ValidatePromoRequest(BaseModel):
    code: str
    ride_fare: float = 0.0  # So we can calculate actual discount


class CreatePromoCodeRequest(BaseModel):
    code: str
    discount_type: str = "flat"  # flat | percentage
    discount_value: float  # e.g. 5.00 for $5 off, or 10.0 for 10%
    max_discount: Optional[float] = None  # Cap for percentage discounts
    max_uses: int = 100
    max_uses_per_user: int = 1
    expiry_date: Optional[str] = None  # ISO 8601
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
    """Validate promo code against all rules: usage, expiry, area, user targeting, fare minimum."""
    code = req.code.strip().upper()
    now = datetime.utcnow()
    promo = await db.promotions.find_one({"code": code})

    if not promo:
        raise HTTPException(status_code=404, detail="Invalid promo code")

    if not promo.get("is_active", False):
        raise HTTPException(status_code=400, detail="This promo code is no longer active")

    # 1. Expiry
    expiry = promo.get("expiry_date")
    if expiry and isinstance(expiry, str):
        try:
            exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp_dt.tzinfo:
                exp_dt = exp_dt.replace(tzinfo=None)
            if exp_dt < now:
                raise HTTPException(status_code=400, detail="This promo code has expired")
        except (ValueError, HTTPException) as e:
            if isinstance(e, HTTPException):
                raise  # noqa: E701

    # 2. Total usage limit (0 = unlimited)
    max_uses = promo.get("max_uses", 0)
    if max_uses > 0 and promo.get("uses", 0) >= max_uses:
        raise HTTPException(status_code=400, detail="This promo code has reached its usage limit")

    # 3. Per-user usage limit (0 = unlimited)
    max_per_user = promo.get("max_uses_per_user", 1)
    if max_per_user > 0:
        user_uses = await db.promo_applications.count_documents(
            {
                "promo_id": promo["id"],
                "user_id": current_user["id"],
            }
        )
        if user_uses >= max_per_user:
            raise HTTPException(
                status_code=400, detail="You have already used this promo code the maximum number of times"
            )

    # 4. Minimum ride fare
    min_fare = promo.get("min_ride_fare", 0)
    if min_fare > 0 and req.ride_fare < min_fare:
        raise HTTPException(status_code=400, detail=f"Minimum ride fare of ${min_fare:.2f} required for this promo")

    # 5. Private coupon — assigned to specific users only
    assigned_users = promo.get("assigned_user_ids", [])
    if assigned_users and current_user["id"] not in assigned_users:
        raise HTTPException(status_code=400, detail="This promo code is not available for your account")

    # 6. First ride only
    if promo.get("first_ride_only"):
        ride_count = await db.rides.count_documents({"rider_id": current_user["id"], "status": "completed"})
        if ride_count > 0:
            raise HTTPException(status_code=400, detail="This promo is for first-time riders only")

    # 7. New user restriction (user account must be less than X days old)
    new_user_days = promo.get("new_user_days", 0)
    if new_user_days > 0:
        user = await db.users.find_one({"id": current_user["id"]})
        if user and user.get("created_at"):
            try:
                created = datetime.fromisoformat(str(user["created_at"]).replace("Z", "+00:00").replace("+00:00", ""))
                if (now - created).days > new_user_days:
                    raise HTTPException(status_code=400, detail="This promo is for new users only")
            except (ValueError, HTTPException) as e:
                if isinstance(e, HTTPException):
                    raise  # noqa: E701

    # 8. Inactive user targeting (no rides in X days)
    inactive_days = promo.get("inactive_days", 0)
    if inactive_days > 0:
        cutoff = (now - timedelta(days=inactive_days)).isoformat()
        recent_rides = await db.rides.count_documents(
            {
                "rider_id": current_user["id"],
                "status": "completed",
                "ride_completed_at": {"$gte": cutoff},
            }
        )
        if recent_rides > 0:
            raise HTTPException(
                status_code=400, detail="This promo is for returning riders who haven't ridden recently"
            )

    # 9. Minimum / maximum total rides
    min_rides = promo.get("min_total_rides", 0)
    max_rides = promo.get("max_total_rides", 0)
    if min_rides > 0 or max_rides > 0:
        total_rides = await db.rides.count_documents({"rider_id": current_user["id"], "status": "completed"})
        if min_rides > 0 and total_rides < min_rides:
            raise HTTPException(
                status_code=400, detail=f"You need at least {min_rides} completed rides to use this promo"
            )
        if max_rides > 0 and total_rides >= max_rides:
            raise HTTPException(status_code=400, detail="This promo is not available for your ride count")

    # 10. Budget check
    total_budget = promo.get("total_budget", 0)
    if total_budget > 0 and promo.get("budget_used", 0) >= total_budget:
        raise HTTPException(status_code=400, detail="This promotion has reached its budget limit")

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
        "max_discount": promo.get("max_discount"),
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
        "promo_id": validation["promo_id"],
        "code": validation["code"],
        "discount_applied": validation["discount_amount"],
        "created_at": datetime.utcnow().isoformat(),
    }
    await db.promo_applications.insert_one(application)

    # Increment usage count
    promo = await db.promotions.find_one({"id": validation["promo_id"]})
    if promo:
        await db.promotions.update_one(
            {"id": validation["promo_id"]},
            {"$set": {"uses": promo.get("uses", 0) + 1}},
        )

    return {
        "success": True,
        "discount_applied": validation["discount_amount"],
        "application_id": application["id"],
    }


@api_router.get("/available")
async def get_available_promos(
    ride_fare: float = Query(0.0),
    current_user: dict = Depends(get_current_user),
):
    """Get all promos available to this user, sorted by best discount first."""
    now = datetime.utcnow()
    promos = await db.promotions.find({"is_active": True}).to_list(100)

    # Pre-fetch user data for targeting checks
    user = await db.users.find_one({"id": current_user["id"]})
    total_rides = await db.rides.count_documents({"rider_id": current_user["id"], "status": "completed"})
    recent_cutoff_30 = (now - timedelta(days=30)).isoformat()
    await db.rides.count_documents(
        {
            "rider_id": current_user["id"],
            "status": "completed",
            "ride_completed_at": {"$gte": recent_cutoff_30},
        }
    )

    available = []
    for p in promos:
        try:
            # Expiry
            expiry = p.get("expiry_date")
            if expiry and isinstance(expiry, str):
                exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                if exp_dt.tzinfo:
                    exp_dt = exp_dt.replace(tzinfo=None)  # noqa: E701
                if exp_dt < now:
                    continue  # noqa: E701

            # Total usage (0 = unlimited)
            max_uses = p.get("max_uses", 0)
            if max_uses > 0 and p.get("uses", 0) >= max_uses:
                continue  # noqa: E701

            # Per-user usage (0 = unlimited)
            max_per_user = p.get("max_uses_per_user", 1)
            if max_per_user > 0:
                user_uses = await db.promo_applications.count_documents(
                    {"promo_id": p["id"], "user_id": current_user["id"]}
                )
                if user_uses >= max_per_user:
                    continue  # noqa: E701

            # Min fare
            if p.get("min_ride_fare", 0) > 0 and ride_fare < p["min_ride_fare"]:
                continue  # noqa: E701

            # Private coupon
            assigned = p.get("assigned_user_ids", [])
            if assigned and current_user["id"] not in assigned:
                continue  # noqa: E701

            # First ride only
            if p.get("first_ride_only") and total_rides > 0:
                continue  # noqa: E701

            # New user only
            new_days = p.get("new_user_days", 0)
            if new_days > 0 and user and user.get("created_at"):
                created = datetime.fromisoformat(str(user["created_at"]).replace("Z", "+00:00").replace("+00:00", ""))
                if (now - created).days > new_days:
                    continue  # noqa: E701

            # Inactive user targeting
            inactive_days = p.get("inactive_days", 0)
            if inactive_days > 0:
                cutoff = (now - timedelta(days=inactive_days)).isoformat()
                recent = await db.rides.count_documents(
                    {"rider_id": current_user["id"], "status": "completed", "ride_completed_at": {"$gte": cutoff}}
                )
                if recent > 0:
                    continue  # noqa: E701

            # Min/max ride count
            if p.get("min_total_rides", 0) > 0 and total_rides < p["min_total_rides"]:
                continue  # noqa: E701
            if p.get("max_total_rides", 0) > 0 and total_rides >= p["max_total_rides"]:
                continue  # noqa: E701

            # Budget
            if p.get("total_budget", 0) > 0 and p.get("budget_used", 0) >= p["total_budget"]:
                continue  # noqa: E701

            # Calculate discount
            discount_type = p.get("discount_type", "flat")
            discount_value = float(p.get("discount_value", 0))
            if discount_type == "percentage":
                discount = round(ride_fare * (discount_value / 100), 2) if ride_fare > 0 else 0
                max_cap = p.get("max_discount")
                if max_cap and discount > max_cap:
                    discount = max_cap  # noqa: E701
            else:
                discount = min(discount_value, ride_fare) if ride_fare > 0 else discount_value

            available.append(
                {
                    "promo_id": p["id"],
                    "code": p.get("code"),
                    "discount_type": discount_type,
                    "discount_value": discount_value,
                    "max_discount": p.get("max_discount"),
                    "discount_amount": discount,
                    "description": p.get("description", ""),
                    "expiry_date": p.get("expiry_date"),
                    "min_ride_fare": p.get("min_ride_fare", 0),
                }
            )
        except Exception:  # noqa: S112
            continue  # skip broken promos

    # Sort by biggest discount first
    available.sort(key=lambda x: x["discount_amount"], reverse=True)
    return available


# ============ Admin Promo Code CRUD ============

admin_router = APIRouter(prefix="/admin/promo-codes", tags=["Admin Promotions"])


@admin_router.get("")
async def admin_get_promo_codes():
    """Get all promo codes."""
    codes = await db.get_rows("promotions", order="created_at", desc=True, limit=500)
    return codes


@admin_router.post("")
async def admin_create_promo_code(req: CreatePromoCodeRequest):
    """Create a new promo code."""
    code = req.code.strip().upper()

    # Check uniqueness
    existing = await db.promotions.find_one({"code": code})
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

    await db.promotions.insert_one(promo)
    return {"success": True, "promo": promo}


@admin_router.put("/{promo_id}")
async def admin_update_promo_code(promo_id: str, req: UpdatePromoCodeRequest):
    """Update an existing promo code."""
    update_data: Dict[str, Any] = {"updated_at": datetime.utcnow().isoformat()}
    for field in [
        "discount_type",
        "discount_value",
        "max_discount",
        "max_uses",
        "max_uses_per_user",
        "expiry_date",
        "is_active",
        "description",
    ]:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    await db.promotions.update_one({"id": promo_id}, {"$set": update_data})
    updated = await db.promotions.find_one({"id": promo_id})
    if not updated:
        raise HTTPException(status_code=404, detail="Promo code not found")
    return updated


@admin_router.delete("/{promo_id}")
async def admin_delete_promo_code(promo_id: str):
    """Delete a promo code."""
    await db.promotions.delete_one({"id": promo_id})
    return {"deleted": True}
