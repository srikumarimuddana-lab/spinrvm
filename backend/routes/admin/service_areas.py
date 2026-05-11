import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...routes.fares import invalidate_fare_cache
    from ...utils.audit_logger import log_admin_action
    from ...utils.surge_engine import SURGE_CAP
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from routes.fares import invalidate_fare_cache
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.surge_engine import SURGE_CAP

logger = logging.getLogger(__name__)

router = APIRouter()

_SURGE_MAX = 10.0  # absolute ceiling for manual admin override


# ---------- Pydantic models ----------


class ServiceAreaCreateRequest(BaseModel):
    name: str
    city: str = ""
    geojson: Optional[Any] = None
    polygon: Optional[Any] = None
    is_active: bool = True
    parent_service_area_id: Optional[str] = None
    is_airport: bool = False
    airport_fee: float = Field(default=0, ge=0, le=100)
    surge_enabled: Optional[bool] = None
    surge_active: Optional[bool] = None
    surge_multiplier: float = Field(default=1.0, ge=1.0, le=2.5)
    gst_enabled: bool = True
    gst_rate: float = Field(default=5.0, ge=0, le=100)
    pst_enabled: bool = False
    pst_rate: float = Field(default=0.0, ge=0, le=100)
    hst_enabled: bool = False
    hst_rate: float = Field(default=0.0, ge=0, le=100)
    spinr_pass_enabled: bool = True
    subscription_plan_ids: List[str] = []
    driver_matching_algorithm: str = "nearest"
    search_radius_km: float = Field(default=10.0, ge=1, le=100)
    min_driver_rating: float = Field(default=4.0, ge=1.0, le=5.0)
    show_demand_heatmap: bool = False


class ServiceAreaUpdateRequest(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    geojson: Optional[Any] = None
    polygon: Optional[Any] = None
    is_active: Optional[bool] = None
    parent_service_area_id: Optional[str] = None
    is_airport: Optional[bool] = None
    airport_fee: Optional[float] = Field(default=None, ge=0, le=100)
    surge_enabled: Optional[bool] = None
    surge_active: Optional[bool] = None
    surge_multiplier: Optional[float] = Field(default=None, ge=1.0, le=2.5)
    gst_enabled: Optional[bool] = None
    gst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    pst_enabled: Optional[bool] = None
    pst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    hst_enabled: Optional[bool] = None
    hst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    required_documents: Optional[Any] = None
    spinr_pass_enabled: Optional[bool] = None
    subscription_plan_ids: Optional[List[str]] = None
    driver_matching_algorithm: Optional[str] = None
    search_radius_km: Optional[float] = Field(default=None, ge=1, le=100)
    min_driver_rating: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    show_demand_heatmap: Optional[bool] = None
    vehicle_pricing: Optional[List[Dict[str, Any]]] = None


class SurgePricingRequest(BaseModel):
    multiplier: float = Field(default=1.0, ge=1.0, le=2.5)
    is_active: bool = False


class AreaFeeCreateRequest(BaseModel):
    fee_name: str = ""
    fee_type: str = "custom"
    calc_mode: str = "flat"
    amount: float = Field(default=0, ge=0, le=100)
    description: str = ""
    conditions: Dict[str, Any] = {}
    is_active: bool = True


class AreaFeeUpdateRequest(BaseModel):
    fee_name: Optional[str] = None
    fee_type: Optional[str] = None
    calc_mode: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0, le=100)
    description: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class AreaTaxRequest(BaseModel):
    gst_enabled: Optional[bool] = None
    gst_rate: Optional[float] = None
    pst_enabled: Optional[bool] = None
    pst_rate: Optional[float] = None
    hst_enabled: Optional[bool] = None
    hst_rate: Optional[float] = None


# ---------- Service areas (table: service_areas) ----------


@router.get("/service-areas")
async def admin_get_service_areas():
    """Get all service areas. Sub-regions are nested under their parent as 'sub_regions'."""
    areas = await db_supabase.get_rows("service_areas", order="name", limit=500)
    # Build parent -> children mapping
    parent_map: Dict[str, list] = {}
    parents = []
    for a in areas:
        pid = a.get("parent_service_area_id")
        if pid:
            parent_map.setdefault(pid, []).append(a)
        else:
            parents.append(a)
    # Attach sub_regions to each parent
    for p in parents:
        p["sub_regions"] = parent_map.get(p["id"], [])
    return parents


@router.post("/service-areas")
async def admin_create_service_area(area: ServiceAreaCreateRequest, admin: dict = Depends(get_admin_user)):
    """Create service area with full configuration."""
    polygon = area.geojson if area.geojson is not None else area.polygon or []
    surge_active = area.surge_active if area.surge_active is not None else (area.surge_enabled or False)
    doc = {
        "id": str(uuid.uuid4()),
        "name": area.name,
        "city": area.city,
        "polygon": polygon,
        "is_active": area.is_active,
        "parent_service_area_id": area.parent_service_area_id,
        "is_airport": area.is_airport,
        "airport_fee": area.airport_fee,
        "surge_active": surge_active,
        "surge_multiplier": area.surge_multiplier,
        "gst_enabled": area.gst_enabled,
        "gst_rate": area.gst_rate,
        "pst_enabled": area.pst_enabled,
        "pst_rate": area.pst_rate,
        "hst_enabled": area.hst_enabled,
        "hst_rate": area.hst_rate,
        "spinr_pass_enabled": area.spinr_pass_enabled,
        "subscription_plan_ids": area.subscription_plan_ids,
        "driver_matching_algorithm": area.driver_matching_algorithm,
        "search_radius_km": area.search_radius_km,
        "min_driver_rating": area.min_driver_rating,
        "show_demand_heatmap": area.show_demand_heatmap,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("service_areas", doc)
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    await log_admin_action(admin, "service_area_created", "service_areas", doc["id"], {"name": area.name})
    return {"area_id": doc["id"]}


@router.put("/service-areas/{area_id}")
async def admin_update_service_area(
    area_id: str, area: ServiceAreaUpdateRequest, admin: dict = Depends(get_admin_user)
):
    """Update service area — accepts any field."""
    # Resolve aliases
    polygon = area.geojson if area.geojson is not None else area.polygon
    surge_active = area.surge_active if area.surge_active is not None else area.surge_enabled

    # Validate surge_multiplier at the API boundary (F-26).
    # fare_service.py always applies SURGE_CAP (2.5×) at calculation time, so
    # values above SURGE_CAP stored here only take effect as manual overrides
    # that require documented justification per CLAUDE.md.
    if area.surge_multiplier is not None:
        sm = float(area.surge_multiplier)
        if sm < 1.0 or sm > _SURGE_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"surge_multiplier must be between 1.0 and {_SURGE_MAX}",
            )
        if sm > SURGE_CAP:
            logger.warning(
                "surge_multiplier %.2f exceeds auto-mode cap (%.1f) for area %s — "
                "manual override; fare_service enforces cap for auto-mode areas",
                sm,
                SURGE_CAP,
                area_id,
            )

    update_payload: Dict[str, Any] = {}
    for field in [
        "name",
        "city",
        "is_active",
        "parent_service_area_id",
        "is_airport",
        "airport_fee",
        "surge_multiplier",
        "gst_enabled",
        "gst_rate",
        "pst_enabled",
        "pst_rate",
        "hst_enabled",
        "hst_rate",
        "required_documents",
        "spinr_pass_enabled",
        "subscription_plan_ids",
        "driver_matching_algorithm",
        "search_radius_km",
        "min_driver_rating",
        "show_demand_heatmap",
        "vehicle_pricing",
    ]:
        val = getattr(area, field)
        if val is not None:
            update_payload[field] = val
    if polygon is not None:
        update_payload["polygon"] = polygon
    if surge_active is not None:
        update_payload["surge_active"] = surge_active

    if update_payload:
        # NOTE: service_areas table does not have an updated_at column in Supabase schema.
        # Adding it causes PGRST204 -> 500 error.
        await db_supabase.update_one("service_areas", {"id": area_id}, update_payload)
        # PERF-001: Invalidate fare cache
        await invalidate_fare_cache()
        await log_admin_action(
            admin, "service_area_updated", "service_areas", area_id, {"updated_fields": list(update_payload.keys())}
        )
    return {"message": "Service area updated"}


@router.delete("/service-areas/{area_id}")
async def admin_delete_service_area(area_id: str, admin: dict = Depends(get_admin_user)):
    """Delete service area."""
    await db_supabase.delete_many("service_areas", {"id": area_id})
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    await log_admin_action(admin, "service_area_deleted", "service_areas", area_id)
    return {"message": "Service area deleted"}


# ---------- Surge Pricing ----------


@router.put("/service-areas/{area_id}/surge")
async def admin_update_surge_pricing(area_id: str, surge: SurgePricingRequest, admin: dict = Depends(get_admin_user)):
    """Update surge pricing for a service area."""
    surge_doc = {
        "id": str(uuid.uuid4()),
        "service_area_id": area_id,
        "multiplier": surge.multiplier,
        "demand_count": 0,
        "supply_count": 0,
        "ratio": 0,
        "source": "manual",
        "is_active": surge.is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("surge_pricing", {"service_area_id": area_id}, limit=1)
    )
    if existing:
        await db_supabase.update_one("surge_pricing", {"service_area_id": area_id}, surge_doc)
    else:
        await db_supabase.insert_one("surge_pricing", surge_doc)

    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    await log_admin_action(
        admin,
        "surge_pricing_updated",
        "service_areas",
        area_id,
        {"multiplier": surge.multiplier, "is_active": surge.is_active},
    )

    return {"message": "Surge pricing updated"}


@router.get("/surge/status")
async def admin_get_surge_status():
    """Get current surge status for all active service areas."""
    try:
        from utils.surge_engine import get_surge_status

        return await get_surge_status()
    except ImportError:
        from ...utils.surge_engine import get_surge_status

        return await get_surge_status()


# ---------- Area Management (Pricing, Tax, Vehicle Pricing) ----------


@router.get("/areas/{area_id}/fees")
async def admin_get_area_fees(area_id: str):
    """Get all fees for a service area."""
    fees = await db_supabase.get_rows("area_fees", {"service_area_id": area_id}, order="created_at", limit=100)
    return fees


@router.post("/areas/{area_id}/fees")
async def admin_create_area_fee(area_id: str, fee: AreaFeeCreateRequest):
    """Create a new fee for a service area."""
    doc = {
        "id": str(uuid.uuid4()),
        "service_area_id": area_id,
        "fee_name": fee.fee_name,
        "fee_type": fee.fee_type,
        "calc_mode": fee.calc_mode,
        "amount": fee.amount,
        "description": fee.description,
        "conditions": fee.conditions,
        "is_active": fee.is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("area_fees", doc)
    return doc


@router.put("/areas/{area_id}/fees/{fee_id}")
async def admin_update_area_fee(area_id: str, fee_id: str, fee: AreaFeeUpdateRequest):
    """Update an area fee."""
    updates: Dict[str, Any] = {}
    if fee.fee_name is not None:
        updates["fee_name"] = fee.fee_name
    if fee.fee_type is not None:
        updates["fee_type"] = fee.fee_type
    if fee.calc_mode is not None:
        updates["calc_mode"] = fee.calc_mode
    if fee.amount is not None:
        updates["amount"] = fee.amount
    if fee.description is not None:
        updates["description"] = fee.description
    if fee.conditions is not None:
        updates["conditions"] = fee.conditions
    if fee.is_active is not None:
        updates["is_active"] = fee.is_active
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("area_fees", {"id": fee_id}, updates)
    return {"message": "Area fee updated"}


@router.delete("/areas/{area_id}/fees/{fee_id}")
async def admin_delete_area_fee(area_id: str, fee_id: str):
    """Delete an area fee."""
    await db_supabase.delete_many("area_fees", {"id": fee_id})
    return {"message": "Area fee deleted"}


@router.get("/areas/{area_id}/tax")
async def admin_get_area_tax(area_id: str):
    """Get tax configuration for a service area."""
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        return {
            "service_area_id": area_id,
            "gst_enabled": True,
            "gst_rate": 5.0,
            "pst_enabled": False,
            "pst_rate": 0,
            "hst_enabled": False,
            "hst_rate": 0,
        }
    return {
        "service_area_id": area_id,
        "gst_enabled": area.get("gst_enabled", True),
        "gst_rate": area.get("gst_rate", 5.0),
        "pst_enabled": area.get("pst_enabled", False),
        "pst_rate": area.get("pst_rate", 0),
        "hst_enabled": area.get("hst_enabled", False),
        "hst_rate": area.get("hst_rate", 0),
    }


@router.put("/areas/{area_id}/tax")
async def admin_update_area_tax(area_id: str, tax: AreaTaxRequest):
    """Update tax configuration for a service area."""
    updates = tax.model_dump(exclude_none=True)
    if updates:
        await db_supabase.update_one("service_areas", {"id": area_id}, updates)
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    _TAX_FIELDS = ["gst_enabled", "gst_rate", "pst_enabled", "pst_rate", "hst_enabled", "hst_rate"]
    return {k: area.get(k) for k in _TAX_FIELDS}


@router.get("/areas/{area_id}/vehicle-pricing")
async def admin_get_vehicle_pricing(area_id: str):
    """Get vehicle pricing configuration for a service area.

    Returns {vehicle_types, fare_configs} so the fare-config editor can
    display a row per vehicle type with the area's specific rates.
    """
    vehicle_types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, order="name", limit=50)
    fare_configs = await db_supabase.get_rows("fare_configs", {"service_area_id": area_id}, limit=100)
    return {
        "vehicle_types": vehicle_types or [],
        "fare_configs": fare_configs or [],
    }
