import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...routes.fares import invalidate_fare_cache
except ImportError:
    import db_supabase
    from routes.fares import invalidate_fare_cache

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------- Vehicle types (table: vehicle_types) ----------


@router.get("/vehicle-types")
async def admin_get_vehicle_types():
    """Get all vehicle types."""
    types = await db_supabase.get_rows("vehicle_types", order="created_at", limit=100)
    return types


@router.post("/vehicle-types")
async def admin_create_vehicle_type(vtype: Dict[str, Any]):
    """Create vehicle type."""
    doc = {
        "name": vtype.get("name"),
        "description": vtype.get("description", ""),
        "icon": vtype.get("icon", ""),
        "base_fare": vtype.get("base_fare"),
        "price_per_km": vtype.get("price_per_km"),
        "price_per_minute": vtype.get("price_per_minute"),
        "is_active": vtype.get("is_active", True),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await db_supabase.insert_one("vehicle_types", doc)
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    return {"type_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@router.put("/vehicle-types/{type_id}")
async def admin_update_vehicle_type(type_id: str, vtype: Dict[str, Any]):
    """Update vehicle type."""
    update_payload = {}
    if vtype.get("name") is not None:
        update_payload["name"] = vtype.get("name")
    if vtype.get("description") is not None:
        update_payload["description"] = vtype.get("description")
    if vtype.get("icon") is not None:
        update_payload["icon"] = vtype.get("icon")
    if vtype.get("base_fare") is not None:
        update_payload["base_fare"] = vtype.get("base_fare")
    if vtype.get("price_per_km") is not None:
        update_payload["price_per_km"] = vtype.get("price_per_km")
    if vtype.get("price_per_minute") is not None:
        update_payload["price_per_minute"] = vtype.get("price_per_minute")
    if vtype.get("is_active") is not None:
        update_payload["is_active"] = vtype.get("is_active")

    if update_payload:
        await db_supabase.update_one("vehicle_types", {"id": type_id}, update_payload)
        # PERF-001: Invalidate fare cache
        await invalidate_fare_cache()
    return {"message": "Vehicle type updated"}


@router.delete("/vehicle-types/{type_id}")
async def admin_delete_vehicle_type(type_id: str):
    """Delete a vehicle type — only if nothing still references it.

    Vehicle types are parents of two structures:

      * fare_configs.vehicle_type_id (real FK — TEXT REFERENCES
        vehicle_types(id)); Postgres would reject the delete on
        RESTRICT or silently cascade on CASCADE, but we want a
        controlled 409 with the usage list the operator can act on.
      * service_areas.vehicle_pricing (JSONB array keyed by NAME, no
        FK). Orphans silently without this check — operator sees a
        stale "Economy" row with no matching vehicle_types entry.

    Block the delete when either exists and return 409 with the list
    of affected areas / fare_configs. Operator clears them first in
    Service Areas → <area> → Vehicle Pricing, then re-tries the
    delete.
    """
    vt = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("vehicle_types", {"id": type_id}, limit=1)
    )
    if not vt:
        raise HTTPException(status_code=404, detail="Vehicle type not found")
    vt_name = vt.get("name") or ""

    # fare_configs referencing this type
    fare_cfg_rows = await db_supabase.get_rows(
        "fare_configs", {"vehicle_type_id": type_id}, limit=500
    )
    fare_cfg_count = len(fare_cfg_rows or [])

    # service_areas whose vehicle_pricing JSONB array contains a row
    # with this name. JSONB contains-filter across unknown array
    # positions is awkward in PostgREST, so pull all areas and scan
    # client-side — admins rarely have >50 areas.
    all_areas = await db_supabase.get_rows("service_areas", limit=1000)
    area_names_using_it: list[str] = []
    for area in all_areas or []:
        pricing = area.get("vehicle_pricing") or []
        if not isinstance(pricing, list):
            continue
        for row in pricing:
            if isinstance(row, dict) and row.get("vehicle_type") == vt_name:
                area_names_using_it.append(area.get("name") or area.get("id"))
                break

    if fare_cfg_count > 0 or area_names_using_it:
        parts = []
        if area_names_using_it:
            preview = ", ".join(area_names_using_it[:5])
            more = f" and {len(area_names_using_it) - 5} more" if len(area_names_using_it) > 5 else ""
            parts.append(f"{len(area_names_using_it)} service area(s): {preview}{more}")
        if fare_cfg_count > 0:
            parts.append(f"{fare_cfg_count} fare config(s)")
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete '{vt_name}' — still used by "
                + " and ".join(parts)
                + ". Remove those references first in Service Areas → Vehicle Pricing."
            ),
        )

    await db_supabase.delete_many("vehicle_types", {"id": type_id})
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    return {"message": "Vehicle type deleted"}


# ---------- Fare configs (table: fare_configs; schema column names) ----------


@router.get("/fare-configs")
async def admin_get_fare_configs():
    """Get all fare configurations."""
    configs = await db_supabase.get_rows("fare_configs", order="created_at", desc=True, limit=200)
    return configs


@router.post("/fare-configs")
async def admin_create_fare_config(config: Dict[str, Any]):
    """Create fare configuration."""
    doc = {
        "name": config.get("name", ""),
        "service_area_id": config.get("service_area_id", ""),
        "vehicle_type_id": config.get("vehicle_type_id", ""),
        "base_fare": config.get("base_fare", 0),
        "per_km_rate": config.get("price_per_km", config.get("per_km_rate", 0)),
        "per_minute_rate": config.get("price_per_minute", config.get("per_minute_rate", 0)),
        "minimum_fare": config.get("minimum_fare", 0),
        "booking_fee": config.get("booking_fee", 2.0),
        "is_active": config.get("is_active", True),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await db_supabase.insert_one("fare_configs", doc)
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    return {"config_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@router.put("/fare-configs/{config_id}")
async def admin_update_fare_config(config_id: str, config: Dict[str, Any]):
    """Update fare configuration."""
    updates = {
        "name": config.get("name"),
        "base_fare": config.get("base_fare"),
        "per_km_rate": config.get("price_per_km", config.get("per_km_rate")),
        "per_minute_rate": config.get("price_per_minute", config.get("per_minute_rate")),
        "area_geojson": config.get("area_geojson"),
        "is_active": config.get("is_active"),
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    if updates:
        await db_supabase.update_one("fare_configs", {"id": config_id}, updates)
        # PERF-001: Invalidate fare cache
        await invalidate_fare_cache()
    return {"message": "Fare configuration updated"}


@router.delete("/fare-configs/{config_id}")
async def admin_delete_fare_config(config_id: str):
    """Delete fare configuration."""
    await db_supabase.delete_many("fare_configs", {"id": config_id})
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    return {"message": "Fare configuration deleted"}


# ---------- Lost and Found ----------


class LostAndFoundRequest(BaseModel):
    item_description: str


class LostAndFoundResolveRequest(BaseModel):
    status: str  # resolved or unresolved
    admin_notes: Optional[str] = None


@router.post("/rides/{ride_id}/lost-and-found")
async def admin_report_lost_item(ride_id: str, req: LostAndFoundRequest):
    """Report a lost item from a ride and notify the driver."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    driver_id = ride.get("driver_id")
    rider_id = ride.get("rider_id")
    if not driver_id:
        raise HTTPException(status_code=400, detail="No driver assigned to this ride")

    item_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "rider_id": rider_id or "",
        "driver_id": driver_id,
        "item_description": req.item_description,
        "status": "reported",
        "created_by": "admin",
    }

    item = await db_supabase.create_lost_and_found(item_data)

    # Send push notification to driver
    try:
        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            driver_user = await db_supabase.get_user_by_id(driver["user_id"])
            if driver_user and driver_user.get("fcm_token"):
                try:
                    from ...features import send_push_notification
                except ImportError:
                    from features import send_push_notification
                await send_push_notification(
                    driver_user["fcm_token"],
                    "Lost Item Report",
                    f"A rider reported a lost item: {req.item_description}. Please check your vehicle.",
                    {"type": "lost_and_found", "ride_id": ride_id},
                )
                # Update status to driver_notified
                await db_supabase.update_lost_and_found(
                    item["id"],
                    {
                        "status": "driver_notified",
                        "notified_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
    except Exception as e:
        logger.warning(f"Failed to send lost item notification: {e}")

    return item


@router.put("/lost-and-found/{item_id}/resolve")
async def admin_resolve_lost_item(item_id: str, req: LostAndFoundResolveRequest):
    """Resolve or mark a lost and found item as unresolved."""
    update_data = {
        "status": req.status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if req.admin_notes:
        update_data["admin_notes"] = req.admin_notes
    if req.status == "resolved":
        update_data["resolved_at"] = datetime.now(timezone.utc).isoformat()

    result = await db_supabase.update_lost_and_found(item_id, update_data)
    if not result:
        raise HTTPException(status_code=404, detail="Item not found")
    return result


@router.get("/lost-and-found")
async def admin_list_lost_and_found(
    limit: int = 100,
    offset: int = 0,
):
    """List all lost and found items."""
    items = await db_supabase.get_rows("lost_and_found", order="created_at", desc=True, limit=limit, offset=offset)
    return items


@router.put("/lost-and-found/{item_id}")
async def admin_update_lost_item(item_id: str, req: dict):
    """Update a lost and found item."""
    update = {k: v for k, v in req.items() if k in ("item_description", "status", "admin_notes")}
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db_supabase.update_lost_and_found(item_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Item not found")
    return result


@router.delete("/lost-and-found/{item_id}")
async def admin_delete_lost_item(item_id: str):
    """Delete a lost and found item."""
    await db_supabase.delete_one("lost_and_found", {"id": item_id})
    return {"message": "Item deleted"}
