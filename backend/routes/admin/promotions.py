import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from utils.audit_logger import log_admin_action  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Pydantic models ----------


class PromotionCreateRequest(BaseModel):
    code: str
    description: str = ""
    promo_type: str = "discount"
    discount_type: str = "flat"
    discount_value: float = 0
    max_discount: Optional[float] = None
    max_uses: int = 0
    max_uses_per_user: int = 1
    valid_from: Optional[str] = None
    expiry_date: Optional[str] = None
    min_ride_fare: float = 0
    first_ride_only: bool = False
    new_user_days: int = 0
    is_active: bool = True
    applicable_areas: Optional[List[Any]] = None
    applicable_vehicles: Optional[List[Any]] = None
    user_segments: Optional[List[Any]] = None
    valid_days: Optional[List[Any]] = None
    valid_hours_start: Optional[Any] = None
    valid_hours_end: Optional[Any] = None
    total_budget: Optional[float] = None
    referrer_user_id: Optional[str] = None
    referrer_reward: Optional[float] = None
    assigned_user_ids: List[str] = []
    inactive_days: int = 0
    min_total_rides: int = 0
    max_total_rides: int = 0


class PromotionUpdateRequest(BaseModel):
    code: Optional[str] = None
    description: Optional[str] = None
    promo_type: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    max_discount: Optional[float] = None
    max_uses: Optional[int] = None
    max_uses_per_user: Optional[int] = None
    valid_from: Optional[str] = None
    expiry_date: Optional[str] = None
    min_ride_fare: Optional[float] = None
    first_ride_only: Optional[bool] = None
    new_user_days: Optional[int] = None
    applicable_areas: Optional[List[Any]] = None
    applicable_vehicles: Optional[List[Any]] = None
    user_segments: Optional[List[Any]] = None
    total_budget: Optional[float] = None
    valid_days: Optional[List[Any]] = None
    valid_hours_start: Optional[Any] = None
    valid_hours_end: Optional[Any] = None
    referrer_reward: Optional[float] = None
    is_active: Optional[bool] = None
    assigned_user_ids: Optional[List[str]] = None
    inactive_days: Optional[int] = None
    min_total_rides: Optional[int] = None
    max_total_rides: Optional[int] = None


# ---------- Promotions (Discount Codes) ----------


@router.get("/promotions")
async def admin_get_promotions(
    limit: int = Query(50),
    offset: int = Query(0),
    promo_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """Get promotions/discount codes with pagination.

    Filters:
    - `promo_type`: "public" (any promo whose promo_type != "private"), "private", or omit for all.
    - `status`: "active" (is_active=true and not expired), "inactive" (is_active=false and not
      expired), "not_expired" (expiry_date NULL or in the future, any is_active), "expired"
      (expiry_date in the past), or omit for all.
    - `search`: case-insensitive contains match on `code` or `description`.
    """
    filters: Dict[str, Any] = {}

    if promo_type == "private":
        filters["promo_type"] = "private"
    elif promo_type == "public":
        # Treat anything not flagged "private" as public.
        filters["promo_type"] = {"$ne": "private"}

    now_iso = datetime.now(timezone.utc).isoformat()
    if status == "active":
        filters["is_active"] = True
        filters["$or"] = [
            {"expiry_date": None},
            {"expiry_date": {"$gte": now_iso}},
        ]
    elif status == "inactive":
        filters["is_active"] = False
        filters["$or"] = [
            {"expiry_date": None},
            {"expiry_date": {"$gte": now_iso}},
        ]
    elif status == "not_expired":
        filters["$or"] = [
            {"expiry_date": None},
            {"expiry_date": {"$gte": now_iso}},
        ]
    elif status == "expired":
        filters["expiry_date"] = {"$lt": now_iso}

    if search:
        term = search.strip()
        if term:
            search_clauses = [
                {"code": {"$regex": re.escape(term), "$options": "i"}},
                {"description": {"$regex": re.escape(term), "$options": "i"}},
            ]
            # If we already used $or for status, AND the two via $and so neither is overwritten.
            if "$or" in filters:
                existing_or = filters.pop("$or")
                filters["$and"] = [{"$or": existing_or}, {"$or": search_clauses}]
            else:
                filters["$or"] = search_clauses

    promotions = await db_supabase.get_rows(
        "promotions", filters, order="created_at", desc=True, limit=limit, offset=offset
    )
    return promotions


@router.post("/promotions")
async def admin_create_promotion(promotion: PromotionCreateRequest, admin: dict = Depends(get_admin_user)):
    """Create a new promotion/discount code."""
    # Reject negative discount values — a negative discount is a charge (F-30).
    if promotion.discount_value < 0:
        raise HTTPException(status_code=400, detail="discount_value cannot be negative")
    # TASK-4-1: enforce upper bound so a typo can't grant 9999% off.
    if promotion.discount_type == "percentage" and promotion.discount_value > 100:
        raise HTTPException(
            status_code=400,
            detail="discount_value cannot exceed 100 for percentage discounts",
        )
    if promotion.discount_type == "flat" and promotion.discount_value > 500:
        raise HTTPException(
            status_code=400,
            detail="discount_value cannot exceed $500 for flat discounts",
        )
    for field, value in [
        ("max_discount", promotion.max_discount),
        ("total_budget", promotion.total_budget),
        ("referrer_reward", promotion.referrer_reward),
        ("min_ride_fare", promotion.min_ride_fare),
    ]:
        if value is not None and float(value) < 0:
            raise HTTPException(status_code=400, detail=f"{field} cannot be negative")

    # Only include fields that exist in the Supabase promotions table schema
    doc: Dict[str, Any] = {
        "code": promotion.code.strip().upper(),
        "description": promotion.description,
        "promo_type": promotion.promo_type,
        "discount_type": promotion.discount_type,
        "discount_value": promotion.discount_value,
        "max_discount": promotion.max_discount,
        "max_uses": promotion.max_uses,
        "max_uses_per_user": promotion.max_uses_per_user,
        "uses": 0,
        "valid_from": promotion.valid_from or datetime.now(timezone.utc).isoformat(),
        "expiry_date": promotion.expiry_date,
        "min_ride_fare": promotion.min_ride_fare,
        "first_ride_only": promotion.first_ride_only,
        "new_user_days": promotion.new_user_days,
        "is_active": promotion.is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Optional JSONB fields that exist in the schema
    if promotion.applicable_areas:
        doc["applicable_areas"] = promotion.applicable_areas
    if promotion.applicable_vehicles:
        doc["applicable_vehicles"] = promotion.applicable_vehicles
    if promotion.user_segments:
        doc["user_segments"] = promotion.user_segments
    if promotion.valid_days:
        doc["valid_days"] = promotion.valid_days
    if promotion.valid_hours_start is not None:
        doc["valid_hours_start"] = promotion.valid_hours_start
    if promotion.valid_hours_end is not None:
        doc["valid_hours_end"] = promotion.valid_hours_end
    if promotion.total_budget:
        doc["total_budget"] = promotion.total_budget
    if promotion.referrer_user_id:
        doc["referrer_user_id"] = promotion.referrer_user_id
    if promotion.referrer_reward:
        doc["referrer_reward"] = promotion.referrer_reward

    # Fields that require migration (may not exist yet in DB)
    # Insert safely - skip if column doesn't exist
    optional_fields = {
        "assigned_user_ids": promotion.assigned_user_ids,
        "inactive_days": promotion.inactive_days,
        "min_total_rides": promotion.min_total_rides,
        "max_total_rides": promotion.max_total_rides,
    }

    try:
        # Try inserting with all fields first
        full_doc = {**doc, **optional_fields}
        row = await db_supabase.insert_one("promotions", full_doc)
    except Exception:
        # Fallback: insert without optional fields that may not exist in schema
        logger.error(
            "Promotions insert failed with optional fields, retrying without them",
            exc_info=True,
        )
        row = await db_supabase.insert_one("promotions", doc)

    promotion_id = str(row.get("id") if row and isinstance(row, dict) else "")
    await log_admin_action(
        admin,
        "promotion_created",
        "promotions",
        promotion_id,
        {
            "code": doc["code"],
            "promo_type": doc["promo_type"],
            "discount_value": doc["discount_value"],
        },
    )
    return {"promotion_id": promotion_id}


@router.get("/promotions/usage")
async def admin_get_promo_usage(
    promo_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(100),
    offset: int = Query(0),
):
    """Get promo code usage/redemption history, enriched with user name and ride ID."""
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
        logger.error("promo_applications table missing or query failed", exc_info=True)
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

    if not applications:
        return []

    # Enrich with user display names (batch — one query for all unique user_ids).
    user_ids = list({a["user_id"] for a in applications if a.get("user_id")})
    user_name_map: Dict[str, str] = {}
    try:
        user_rows = await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("users")
                .select("id, first_name, last_name, phone")
                .in_("id", user_ids)
                .execute()
            )
        )
        for u in getattr(user_rows, "data", None) or []:
            first = u.get("first_name") or ""
            last = u.get("last_name") or ""
            name = f"{first} {last}".strip() or u.get("phone") or u["id"][:8]
            user_name_map[u["id"]] = name
    except Exception:
        logger.warning("Could not enrich promo usage with user names", exc_info=True)

    # Enrich with ride IDs (batch — rides store promo_application_id as FK).
    app_ids = [a["id"] for a in applications if a.get("id")]
    ride_id_map: Dict[str, str] = {}
    try:
        ride_rows = await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("rides")
                .select("id, promo_application_id")
                .in_("promo_application_id", app_ids)
                .execute()
            )
        )
        for r in getattr(ride_rows, "data", None) or []:
            if r.get("promo_application_id"):
                ride_id_map[r["promo_application_id"]] = r["id"]
    except Exception:
        logger.warning("Could not enrich promo usage with ride IDs", exc_info=True)

    for app in applications:
        app["user_name"] = user_name_map.get(app.get("user_id", ""), "")
        app["ride_id"] = ride_id_map.get(app.get("id", ""), None)

    return applications


@router.get("/promotions/stats")
async def admin_get_promo_stats(date_range: Optional[str] = Query(None, alias="range")):
    """Get promotion statistics with daily usage data."""
    # Promos table is bounded (< 10k ever in practice); load all for counts.
    all_promos = await db_supabase.get_rows("promotions", {}, limit=2000)

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # Derive DB-level date cutoff so we never load more than 30d of usage rows.
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
    else:
        # Default: last 30 days so we never do an unbounded table scan.
        range_start = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    usage_filter: Dict[str, Any] = {"created_at": {"$gte": range_start}}
    try:
        all_usage = await db_supabase.get_rows(
            "promo_applications",
            usage_filter,
            order="created_at",
            desc=True,
            limit=5000,
        )
    except Exception:
        logger.error("promo_applications table missing or query failed", exc_info=True)
        all_usage = []

    filtered_usage = all_usage

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
    total_discount = float(sum(Decimal(str(u.get("discount_applied", 0))) for u in filtered_usage))

    # Daily usage for charts (last 30 days), split by promo type so the
    # admin dashboard's "Public codes only" / "Private coupons only" chart
    # filter has real per-type data to select from (previously the filter
    # was wired up client-side with no backend breakdown to filter against).
    promo_is_private = {p["id"]: p.get("promo_type") == "private" for p in all_promos}

    daily: Dict[str, Dict[str, Any]] = {}
    for i in range(30):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {
            "date": d,
            "count": 0,
            "amount": 0.0,
            "public_count": 0,
            "public_amount": 0.0,
            "private_count": 0,
            "private_amount": 0.0,
        }

    for u in all_usage:
        d = u.get("created_at", "")[:10]
        if d not in daily:
            continue
        discount = Decimal(str(u.get("discount_applied", 0)))
        is_private = promo_is_private.get(u.get("promo_id"), False)

        daily[d]["count"] += 1
        daily[d]["amount"] = float(Decimal(str(daily[d]["amount"])) + discount)

        if is_private:
            daily[d]["private_count"] += 1
            daily[d]["private_amount"] = float(Decimal(str(daily[d]["private_amount"])) + discount)
        else:
            daily[d]["public_count"] += 1
            daily[d]["public_amount"] = float(Decimal(str(daily[d]["public_amount"])) + discount)

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
async def admin_update_promotion(
    promotion_id: str,
    promotion: PromotionUpdateRequest,
    admin: dict = Depends(get_admin_user),
):
    """Update a promotion."""
    updates = promotion.model_dump(exclude_none=True)

    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            await db_supabase.update_one("promotions", {"id": promotion_id}, updates)
        except Exception:
            # If update fails (e.g. column doesn't exist yet), remove optional fields and retry
            for f in [
                "assigned_user_ids",
                "inactive_days",
                "min_total_rides",
                "max_total_rides",
            ]:
                updates.pop(f, None)
            if updates:
                await db_supabase.update_one("promotions", {"id": promotion_id}, updates)
        await log_admin_action(
            admin,
            "promotion_updated",
            "promotions",
            promotion_id,
            {"updated_fields": list(updates.keys())},
        )
    return {"message": "Promotion updated"}


@router.delete("/promotions/{promotion_id}")
async def admin_delete_promotion(promotion_id: str, admin: dict = Depends(get_admin_user)):
    """Delete a promotion."""
    await db_supabase.delete_many("promotions", {"id": promotion_id})
    await log_admin_action(admin, "promotion_deleted", "promotions", promotion_id)
    return {"message": "Promotion deleted"}
