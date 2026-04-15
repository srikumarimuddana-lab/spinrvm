import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

try:
    from ... import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------- Promotions (Discount Codes) ----------


@router.get("/promotions")
async def admin_get_promotions():
    """Get all promotions/discount codes."""
    promotions = await db_supabase.get_rows("promotions", order="created_at", desc=True, limit=500)
    return promotions


@router.post("/promotions")
async def admin_create_promotion(promotion: Dict[str, Any]):
    """Create a new promotion/discount code."""
    # Only include fields that exist in the Supabase promotions table schema
    doc: Dict[str, Any] = {
        "code": (promotion.get("code") or "").strip().upper(),
        "description": promotion.get("description", ""),
        "promo_type": promotion.get("promo_type", "discount"),
        "discount_type": promotion.get("discount_type", "flat"),
        "discount_value": promotion.get("discount_value", 0),
        "max_discount": promotion.get("max_discount"),
        "max_uses": promotion.get("max_uses", 0),
        "max_uses_per_user": promotion.get("max_uses_per_user", 1),
        "uses": 0,
        "valid_from": promotion.get("valid_from", datetime.utcnow().isoformat()),
        "expiry_date": promotion.get("expiry_date"),
        "min_ride_fare": promotion.get("min_ride_fare", 0),
        "first_ride_only": promotion.get("first_ride_only", False),
        "new_user_days": promotion.get("new_user_days", 0),
        "is_active": promotion.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    # Optional JSONB fields that exist in the schema
    if promotion.get("applicable_areas"):
        doc["applicable_areas"] = promotion["applicable_areas"]
    if promotion.get("applicable_vehicles"):
        doc["applicable_vehicles"] = promotion["applicable_vehicles"]
    if promotion.get("user_segments"):
        doc["user_segments"] = promotion["user_segments"]
    if promotion.get("valid_days"):
        doc["valid_days"] = promotion["valid_days"]
    if promotion.get("valid_hours_start") is not None:
        doc["valid_hours_start"] = promotion["valid_hours_start"]
    if promotion.get("valid_hours_end") is not None:
        doc["valid_hours_end"] = promotion["valid_hours_end"]
    if promotion.get("total_budget"):
        doc["total_budget"] = promotion["total_budget"]
    if promotion.get("referrer_user_id"):
        doc["referrer_user_id"] = promotion["referrer_user_id"]
    if promotion.get("referrer_reward"):
        doc["referrer_reward"] = promotion["referrer_reward"]

    # Fields that require migration (may not exist yet in DB)
    # Insert safely - skip if column doesn't exist
    optional_fields = {
        "assigned_user_ids": promotion.get("assigned_user_ids", []),
        "inactive_days": promotion.get("inactive_days", 0),
        "min_total_rides": promotion.get("min_total_rides", 0),
        "max_total_rides": promotion.get("max_total_rides", 0),
    }

    try:
        # Try inserting with all fields first
        full_doc = {**doc, **optional_fields}
        row = await db_supabase.insert_one("promotions", full_doc)
    except Exception:
        # Fallback: insert without optional fields that may not exist in schema
        logger.warning("Promotions insert failed with optional fields, retrying without them")
        row = await db_supabase.insert_one("promotions", doc)

    return {"promotion_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@router.get("/promotions/usage")
async def admin_get_promo_usage(
    promo_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(100),
    offset: int = Query(0),
):
    """Get promo code usage/redemption history."""
    filters: Dict[str, Any] = {}
    if promo_id:
        filters["promo_id"] = promo_id

    try:
        applications = await db_supabase.get_rows(
            "promo_applications",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.warning("promo_applications table may not exist yet")
        return []

    # Filter by date range in Python (supabase may not support complex date filters)
    if date_from or date_to:
        filtered = []
        for app in applications:
            created = app.get("created_at", "")
            if date_from and created < date_from:
                continue
            if date_to and created > date_to + "T23:59:59Z":
                continue
            filtered.append(app)
        applications = filtered

    return applications


@router.get("/promotions/stats")
async def admin_get_promo_stats(date_range: Optional[str] = Query(None, alias="range")):
    """Get promotion statistics with daily usage data."""
    all_promos = await db_supabase.get_rows("promotions", {}, limit=10000)
    try:
        all_usage = await db_supabase.get_rows("promo_applications", {}, order="created_at", desc=True, limit=10000)
    except Exception:
        logger.warning("promo_applications table may not exist yet")
        all_usage = []

    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")

    # Date range filtering
    range_start = None
    if date_range == "today":
        range_start = today
    elif date_range == "yesterday":
        range_start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    elif date_range == "week":
        range_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    elif date_range == "last_week":
        range_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    elif date_range == "month":
        range_start = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    # Filter usage by range
    filtered_usage = all_usage
    if range_start:
        filtered_usage = [u for u in all_usage if u.get("created_at", "") >= range_start]

    # Promo counts
    total_codes = len([p for p in all_promos if p.get("promo_type") != "private"])
    active_codes = len([p for p in all_promos if p.get("promo_type") != "private" and p.get("is_active")])
    expired_codes = len(
        [
            p
            for p in all_promos
            if p.get("promo_type") != "private"
            and not p.get("is_active")
            and p.get("expiry_date")
            and p.get("expiry_date", "") < now.isoformat()
        ]
    )
    total_private = len([p for p in all_promos if p.get("promo_type") == "private"])
    active_private = len([p for p in all_promos if p.get("promo_type") == "private" and p.get("is_active")])

    # Usage stats
    total_redemptions = len(filtered_usage)
    total_discount = sum(float(u.get("discount_applied", 0)) for u in filtered_usage)

    # Daily usage for charts (last 30 days)
    daily: Dict[str, Dict[str, Any]] = {}
    for i in range(30):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {"date": d, "count": 0, "amount": 0.0}

    for u in all_usage:
        d = u.get("created_at", "")[:10]
        if d in daily:
            daily[d]["count"] += 1
            daily[d]["amount"] += float(u.get("discount_applied", 0))

    daily_usage = sorted(daily.values(), key=lambda x: x["date"])

    return {
        "total_codes": total_codes,
        "active_codes": active_codes,
        "expired_codes": expired_codes,
        "total_private": total_private,
        "active_private": active_private,
        "total_redemptions": total_redemptions,
        "total_discount_given": round(total_discount, 2),
        "daily_usage": daily_usage,
    }


@router.put("/promotions/{promotion_id}")
async def admin_update_promotion(promotion_id: str, promotion: Dict[str, Any]):
    """Update a promotion."""
    allowed_fields = [
        "code",
        "description",
        "promo_type",
        "discount_type",
        "discount_value",
        "max_discount",
        "max_uses",
        "max_uses_per_user",
        "valid_from",
        "expiry_date",
        "min_ride_fare",
        "first_ride_only",
        "new_user_days",
        "applicable_areas",
        "applicable_vehicles",
        "user_segments",
        "total_budget",
        "valid_days",
        "valid_hours_start",
        "valid_hours_end",
        "referrer_reward",
        "is_active",
        "assigned_user_ids",
        "inactive_days",
        "min_total_rides",
        "max_total_rides",
    ]
    updates = {k: v for k, v in promotion.items() if k in allowed_fields and v is not None}

    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        try:
            await db_supabase.update_one("promotions", {"id": promotion_id}, updates)
        except Exception:
            # If update fails (e.g. column doesn't exist yet), remove optional fields and retry
            for f in ["assigned_user_ids", "inactive_days", "min_total_rides", "max_total_rides"]:
                updates.pop(f, None)
            if updates:
                await db_supabase.update_one("promotions", {"id": promotion_id}, updates)
    return {"message": "Promotion updated"}


@router.delete("/promotions/{promotion_id}")
async def admin_delete_promotion(promotion_id: str):
    """Delete a promotion."""
    await db_supabase.delete_many("promotions", {"id": promotion_id})
    return {"message": "Promotion deleted"}
