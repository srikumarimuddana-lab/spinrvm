import logging
import uuid
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter

try:
    from ... import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

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
async def admin_create_service_area(area: Dict[str, Any]):
    """Create service area with full configuration."""
    doc = {
        "id": str(uuid.uuid4()),
        "name": area.get("name"),
        "city": area.get("city", ""),
        "polygon": area.get("geojson", area.get("polygon", [])),
        "is_active": area.get("is_active", True),
        # Sub-region support (e.g. airport zone inside a parent area)
        "parent_service_area_id": area.get("parent_service_area_id"),
        "is_airport": area.get("is_airport", False),
        "airport_fee": area.get("airport_fee", 0),
        "surge_active": area.get("surge_enabled", area.get("surge_active", False)),
        "surge_multiplier": area.get("surge_multiplier", 1.0),
        "gst_enabled": area.get("gst_enabled", True),
        "gst_rate": area.get("gst_rate", 5.0),
        "pst_enabled": area.get("pst_enabled", False),
        "pst_rate": area.get("pst_rate", 0.0),
        "hst_enabled": area.get("hst_enabled", False),
        "hst_rate": area.get("hst_rate", 0.0),
        # Spinr Pass kill switch
        "spinr_pass_enabled": area.get("spinr_pass_enabled", True),
        "subscription_plan_ids": area.get("subscription_plan_ids", []),
        # Driver matching settings (per-area)
        "driver_matching_algorithm": area.get("driver_matching_algorithm", "nearest"),
        "search_radius_km": area.get("search_radius_km", 10.0),
        "min_driver_rating": area.get("min_driver_rating", 4.0),
        # Demand heatmap — when true, drivers in this area see ride demand overlay
        "show_demand_heatmap": area.get("show_demand_heatmap", False),
        "created_at": datetime.utcnow().isoformat(),
    }
    await db_supabase.insert_one("service_areas", doc)
    return {"area_id": doc["id"]}


@router.put("/service-areas/{area_id}")
async def admin_update_service_area(area_id: str, area: Dict[str, Any]):
    """Update service area — accepts any field."""
    allowed = [
        "name",
        "city",
        "polygon",  # previously geojson mapped to polygon below
        "is_active",
        "parent_service_area_id",
        "is_airport",
        "airport_fee",
        "surge_active",
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
    ]

    # Map geojson from frontend to polygon in DB schema if present
    if "geojson" in area:
        area["polygon"] = area["geojson"]

    # Map surge_enabled to surge_active
    if "surge_enabled" in area:
        area["surge_active"] = area["surge_enabled"]

    update_payload = {k: v for k, v in area.items() if k in allowed and v is not None}

    if update_payload:
        # NOTE: service_areas table does not have an updated_at column in Supabase schema.
        # Adding it causes PGRST204 -> 500 error.
        await db_supabase.update_one("service_areas", {"id": area_id}, update_payload)
    return {"message": "Service area updated"}


@router.delete("/service-areas/{area_id}")
async def admin_delete_service_area(area_id: str):
    """Delete service area."""
    await db_supabase.delete_many("service_areas", {"id": area_id})
    return {"message": "Service area deleted"}


# ---------- Surge Pricing ----------


@router.put("/service-areas/{area_id}/surge")
async def admin_update_surge_pricing(area_id: str, surge: Dict[str, Any]):
    """Update surge pricing for a service area."""
    surge_doc = {
        "id": str(uuid.uuid4()),
        "service_area_id": area_id,
        "multiplier": surge.get("multiplier", 1.0),
        "demand_count": 0,
        "supply_count": 0,
        "ratio": 0,
        "source": "manual",
        "is_active": surge.get("is_active", False),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    existing = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("surge_pricing", {"service_area_id": area_id}, limit=1))
    if existing:
        await db_supabase.update_one("surge_pricing", {"service_area_id": area_id}, surge_doc)
    else:
        await db_supabase.insert_one("surge_pricing", surge_doc)

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
async def admin_create_area_fee(area_id: str, fee: Dict[str, Any]):
    """Create a new fee for a service area."""
    doc = {
        "id": str(uuid.uuid4()),
        "service_area_id": area_id,
        "fee_name": fee.get("fee_name", ""),
        "fee_type": fee.get("fee_type", "custom"),
        "calc_mode": fee.get("calc_mode", "flat"),
        "amount": float(fee.get("amount", 0)),
        "description": fee.get("description", ""),
        "conditions": fee.get("conditions", {}),
        "is_active": fee.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db_supabase.insert_one("area_fees", doc)
    return doc


@router.put("/areas/{area_id}/fees/{fee_id}")
async def admin_update_area_fee(area_id: str, fee_id: str, fee: Dict[str, Any]):
    """Update an area fee."""
    allowed = ["fee_name", "fee_type", "calc_mode", "amount", "description", "conditions", "is_active"]
    updates = {k: fee[k] for k in allowed if k in fee}
    if "amount" in updates:
        updates["amount"] = float(updates["amount"])
    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
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
async def admin_update_area_tax(area_id: str, tax: Dict[str, Any]):
    """Update tax configuration for a service area."""
    allowed = ["gst_enabled", "gst_rate", "pst_enabled", "pst_rate", "hst_enabled", "hst_rate"]
    updates = {k: tax[k] for k in allowed if k in tax}
    if updates:
        await db_supabase.update_one("service_areas", {"id": area_id}, updates)
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    return {k: area.get(k) for k in allowed}


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
