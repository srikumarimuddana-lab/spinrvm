import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_admin_user  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/incentives", tags=["Admin – Incentives"])


# ---------- Pydantic models ----------


class IncentiveCreateRequest(BaseModel):
    service_area_id: Optional[str] = None
    vehicle_type_id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    incentive_type: str = Field(..., pattern=r"^(per_ride|peak_hours|time_limited|min_distance|area_boost)$")
    bonus_amount: float = Field(..., gt=0, le=500)
    bonus_type: str = "flat"
    conditions: Optional[Dict[str, Any]] = None
    is_active: bool = True
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    priority: int = 0
    max_budget: Optional[float] = None


class IncentiveUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    incentive_type: Optional[str] = None
    bonus_amount: Optional[float] = Field(None, gt=0, le=500)
    bonus_type: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    priority: Optional[int] = None
    max_budget: Optional[float] = None
    service_area_id: Optional[str] = None
    vehicle_type_id: Optional[str] = None


# ---------- Endpoints ----------


@router.get("")
async def list_incentives(
    service_area_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
):
    try:
        q = (
            db_supabase.supabase.table("ride_incentives")
            .select("*")
            .order("priority", desc=True)
            .order("created_at", desc=True)
        )
        if service_area_id:
            q = q.eq("service_area_id", service_area_id)
        if active_only:
            q = q.eq("is_active", True)
        result = await db_supabase.run_sync(q.execute)
        return result.data or []
    except Exception as e:
        logger.error(f"[INCENTIVES] list failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to fetch incentives") from e


@router.get("/claims/stats")
async def incentive_claim_stats(
    incentive_id: Optional[str] = Query(None),
    driver_id: Optional[str] = Query(None),
):
    try:
        q = db_supabase.supabase.table("ride_incentive_claims").select("*")
        if incentive_id:
            q = q.eq("incentive_id", incentive_id)
        if driver_id:
            q = q.eq("driver_id", driver_id)
        result = await db_supabase.run_sync(q.order("claimed_at", desc=True).limit(100).execute)
        claims = result.data or []
        total_paid = sum(float(c.get("bonus_amount", 0)) for c in claims)
        return {
            "claims": claims,
            "total_claims": len(claims),
            "total_paid": round(total_paid, 2),
        }
    except Exception as e:
        logger.error(f"[INCENTIVES] claims stats failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to fetch claim stats") from e


@router.get("/{incentive_id}")
async def get_incentive(incentive_id: str):
    try:
        result = await db_supabase.run_sync(
            db_supabase.supabase.table("ride_incentives").select("*").eq("id", incentive_id).single().execute
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Incentive not found")
        return result.data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[INCENTIVES] get {incentive_id} failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to fetch incentive") from e


@router.post("")
async def create_incentive(req: IncentiveCreateRequest, admin: dict = Depends(get_admin_user)):
    row: Dict[str, Any] = {
        "name": req.name,
        "description": req.description or "",
        "incentive_type": req.incentive_type,
        "bonus_amount": req.bonus_amount,
        "bonus_type": req.bonus_type,
        "conditions": req.conditions or {},
        "is_active": req.is_active,
        "priority": req.priority,
    }
    if req.service_area_id:
        row["service_area_id"] = req.service_area_id
    if req.vehicle_type_id:
        row["vehicle_type_id"] = req.vehicle_type_id
    if req.start_date:
        row["start_date"] = req.start_date
    if req.end_date:
        row["end_date"] = req.end_date
    if req.max_budget is not None:
        row["max_budget"] = req.max_budget

    try:
        result = await db_supabase.run_sync(db_supabase.supabase.table("ride_incentives").insert(row).execute)
        created = (result.data or [{}])[0]
        logger.info(f"[INCENTIVES] created incentive {created.get('id')} name={req.name}")
        await log_admin_action(
            admin,
            "incentive_created",
            "ride_incentives",
            str(created.get("id") or ""),
            {"name": req.name, "incentive_type": req.incentive_type, "bonus_amount": req.bonus_amount},
        )
        return created
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[INCENTIVES] create failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to create incentive") from e


@router.patch("/{incentive_id}")
async def update_incentive(incentive_id: str, req: IncentiveUpdateRequest, admin: dict = Depends(get_admin_user)):
    updates: Dict[str, Any] = {}
    for field, value in req.model_dump(exclude_none=True).items():
        updates[field] = value
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        result = await db_supabase.run_sync(
            db_supabase.supabase.table("ride_incentives").update(updates).eq("id", incentive_id).execute
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Incentive not found")
        logger.info(f"[INCENTIVES] updated {incentive_id} fields={list(updates.keys())}")
        await log_admin_action(
            admin,
            "incentive_updated",
            "ride_incentives",
            incentive_id,
            {"fields": sorted(k for k in updates if k != "updated_at")},
        )
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[INCENTIVES] update {incentive_id} failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to update incentive") from e


@router.patch("/{incentive_id}/toggle")
async def toggle_incentive(incentive_id: str, admin: dict = Depends(get_admin_user)):
    try:
        current = await db_supabase.run_sync(
            db_supabase.supabase.table("ride_incentives")
            .select("id, is_active")
            .eq("id", incentive_id)
            .single()
            .execute
        )
        if not current.data:
            raise HTTPException(status_code=404, detail="Incentive not found")

        new_active = not current.data["is_active"]
        result = await db_supabase.run_sync(
            db_supabase.supabase.table("ride_incentives")
            .update({"is_active": new_active, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", incentive_id)
            .execute
        )
        logger.info(f"[INCENTIVES] toggled {incentive_id} is_active={new_active}")
        await log_admin_action(
            admin,
            "incentive_toggled",
            "ride_incentives",
            incentive_id,
            {"is_active": new_active},
        )
        return result.data[0] if result.data else {"id": incentive_id, "is_active": new_active}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[INCENTIVES] toggle {incentive_id} failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to toggle incentive") from e


@router.delete("/{incentive_id}")
async def delete_incentive(incentive_id: str, admin: dict = Depends(get_admin_user)):
    try:
        result = await db_supabase.run_sync(
            db_supabase.supabase.table("ride_incentives").delete().eq("id", incentive_id).execute
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Incentive not found")
        logger.info(f"[INCENTIVES] deleted {incentive_id}")
        await log_admin_action(admin, "incentive_deleted", "ride_incentives", incentive_id, {})
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[INCENTIVES] delete {incentive_id} failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to delete incentive") from e
