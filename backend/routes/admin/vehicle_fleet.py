import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import (  # noqa: F401
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user  # noqa: F401
    from ...documents import _validate_file_type  # noqa: F401
    from ...routes.fares import invalidate_fare_cache
    from ...supabase_client import supabase  # noqa: F401
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from documents import _validate_file_type  # noqa: F401
    from routes.fares import invalidate_fare_cache
    from supabase_client import supabase  # noqa: F401

# Cap admin asset uploads at 500 KB — illustrations are small SVGs/PNGs
# meant to render in a 72×48 dp card on the rider app.
_MAX_ILLUSTRATION_BYTES = 500 * 1024
_ILLUSTRATION_BUCKET = "vehicle-illustrations"
_ILLUSTRATION_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})

logger = logging.getLogger(__name__)

router = APIRouter()

# Map marker icons bundled in the rider/driver apps. Must stay in sync with
# shared/components/CarMarker.tsx CAR_IMAGES and the CHECK constraint in
# migration 140 — adding a marker requires an app release + new migration.
_VALID_MARKER_VARIANTS = frozenset({"standard", "xl", "premium"})


def _validate_marker_variant(value: str) -> None:
    if value not in _VALID_MARKER_VARIANTS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid marker_variant '{value}'. Must be one of: {', '.join(sorted(_VALID_MARKER_VARIANTS))}",
        )


# Corner pixels count as "transparent enough" below this alpha (0-255).
# Real car art always has fully transparent corners; a small tolerance
# absorbs anti-aliasing halos from export tools.
_CORNER_ALPHA_MAX = 32


def _validate_image_transparency(file_bytes: bytes, content_type: str, asset_label: str) -> None:
    """Reject car art with a baked-in background (white box, checkerboard).

    These assets render directly on the rider-app ride card / map, so they
    must have a real alpha channel with transparent corners — otherwise the
    background ships inside the image and shows as an ugly box in the app.
    JPEG cannot store transparency at all, so it is rejected outright.
    """
    fix_hint = (
        f"The {asset_label} is drawn directly on the app UI, so it needs a "
        "transparent background. Export a PNG or WebP with alpha — no white "
        "or checkerboard backdrop baked into the pixels."
    )
    if content_type == "image/jpeg":
        raise HTTPException(status_code=400, detail=f"JPEG cannot store transparency. {fix_hint}")
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = img.convert("RGBA")
    except (UnidentifiedImageError, OSError) as e:
        raise HTTPException(status_code=400, detail="Could not decode image file") from e
    width, height = img.size
    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    if any(img.getpixel(c)[3] > _CORNER_ALPHA_MAX for c in corners):
        raise HTTPException(status_code=400, detail=f"Image has an opaque background. {fix_hint}")


# ---------- Pydantic models ----------


class VehicleTypeCreateRequest(BaseModel):
    name: str
    description: str = ""
    icon: str = ""
    base_fare: Optional[float] = None
    price_per_km: Optional[float] = None
    price_per_minute: Optional[float] = None
    is_active: bool = True
    # Public URL of the rider-app car illustration (uploaded via
    # /vehicle-types/{id}/upload-illustration). Optional on create —
    # admins typically upload after the type is created so the path
    # can be keyed by id.
    illustration_url: Optional[str] = None
    # Which bundled map marker icon the rider app renders for drivers of
    # this type (standard | xl | premium).
    marker_variant: str = "standard"


class VehicleTypeUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    base_fare: Optional[float] = None
    price_per_km: Optional[float] = None
    price_per_minute: Optional[float] = None
    is_active: Optional[bool] = None
    illustration_url: Optional[str] = None
    marker_variant: Optional[str] = None
    # Custom map marker image URL (set via /upload-marker; "" clears it so
    # the apps revert to the bundled marker_variant).
    marker_image_url: Optional[str] = None


class FareConfigCreateRequest(BaseModel):
    name: str = ""
    service_area_id: str = ""
    vehicle_type_id: str = ""
    base_fare: float = 0
    per_km_rate: Optional[float] = None
    price_per_km: Optional[float] = None  # alias accepted from frontend
    per_minute_rate: Optional[float] = None
    price_per_minute: Optional[float] = None  # alias accepted from frontend
    minimum_fare: float = 0
    booking_fee: float = 2.0
    is_active: bool = True


class FareConfigUpdateRequest(BaseModel):
    name: Optional[str] = None
    base_fare: Optional[float] = None
    per_km_rate: Optional[float] = None
    price_per_km: Optional[float] = None  # alias accepted from frontend
    per_minute_rate: Optional[float] = None
    price_per_minute: Optional[float] = None  # alias accepted from frontend
    area_geojson: Optional[Any] = None
    is_active: Optional[bool] = None


class LostAndFoundRequest(BaseModel):
    item_description: str
    service_area_id: Optional[str] = None


class LostAndFoundUpdateRequest(BaseModel):
    item_description: Optional[str] = None
    status: Optional[str] = None
    admin_notes: Optional[str] = None


class LostAndFoundResolveRequest(BaseModel):
    status: str  # resolved or unresolved
    admin_notes: Optional[str] = None


# ---------- Vehicle types (table: vehicle_types) ----------


@router.get("/vehicle-types")
async def admin_get_vehicle_types():
    """Get all vehicle types."""
    types = await db_supabase.get_rows("vehicle_types", order="created_at", limit=100)
    return types


@router.post("/vehicle-types")
async def admin_create_vehicle_type(vtype: VehicleTypeCreateRequest):
    """Create vehicle type."""
    _validate_marker_variant(vtype.marker_variant)
    doc = {
        "name": vtype.name,
        "description": vtype.description,
        "icon": vtype.icon,
        "base_fare": vtype.base_fare,
        "price_per_km": vtype.price_per_km,
        "price_per_minute": vtype.price_per_minute,
        "is_active": vtype.is_active,
        "illustration_url": vtype.illustration_url,
        "marker_variant": vtype.marker_variant,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await db_supabase.insert_one("vehicle_types", doc)
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    return {"type_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@router.put("/vehicle-types/{type_id}")
async def admin_update_vehicle_type(type_id: str, vtype: VehicleTypeUpdateRequest):
    """Update vehicle type."""
    update_payload: Dict[str, Any] = {}
    if vtype.name is not None:
        update_payload["name"] = vtype.name
    if vtype.description is not None:
        update_payload["description"] = vtype.description
    if vtype.icon is not None:
        update_payload["icon"] = vtype.icon
    if vtype.base_fare is not None:
        update_payload["base_fare"] = vtype.base_fare
    if vtype.price_per_km is not None:
        update_payload["price_per_km"] = vtype.price_per_km
    if vtype.price_per_minute is not None:
        update_payload["price_per_minute"] = vtype.price_per_minute
    if vtype.is_active is not None:
        update_payload["is_active"] = vtype.is_active
    if vtype.illustration_url is not None:
        # Admin may pass "" to clear the override and revert to icon fallback.
        update_payload["illustration_url"] = vtype.illustration_url or None
    if vtype.marker_variant is not None:
        _validate_marker_variant(vtype.marker_variant)
        update_payload["marker_variant"] = vtype.marker_variant
    if vtype.marker_image_url is not None:
        # "" clears the custom marker → apps fall back to marker_variant.
        update_payload["marker_image_url"] = vtype.marker_image_url or None

    if update_payload:
        await db_supabase.update_one("vehicle_types", {"id": type_id}, update_payload)
        # PERF-001: Invalidate fare cache
        await invalidate_fare_cache()
    return {"message": "Vehicle type updated"}


@router.post("/vehicle-types/{type_id}/upload-illustration")
async def admin_upload_vehicle_illustration(
    type_id: str,
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    """Upload a PNG/JPEG/WebP illustration for a vehicle type.

    Stores the file in Supabase Storage bucket `vehicle-illustrations` at
    `{type_id}/{uuid4}{ext}`, then writes the resulting public URL to
    `vehicle_types.illustration_url`. The bucket must be set to public
    read in the Supabase dashboard so the rider-app's `<Image>` tag can
    load it without an auth header.
    """
    # Verify the vehicle type exists before consuming the upload.
    vt = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("vehicle_types", {"id": type_id}, limit=1))
    if not vt:
        raise HTTPException(status_code=404, detail="Vehicle type not found")

    content_type = file.content_type or "application/octet-stream"
    if content_type == "image/jpg":  # normalise iOS picker quirk
        content_type = "image/jpeg"
    if content_type not in _ILLUSTRATION_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content-type {content_type}. Accepted: {sorted(_ILLUSTRATION_MIME_TYPES)}",
        )

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_ILLUSTRATION_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 500 KB limit")
    # Magic-byte check (shared with driver-document upload path) catches
    # mis-declared types (e.g. a .png renamed to .jpg).
    _validate_file_type(file_bytes, content_type)
    _validate_image_transparency(file_bytes, content_type, "car illustration")

    ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
    ext = ext_map.get(content_type, ".bin")
    object_path = f"{type_id}/{uuid.uuid4()}{ext}"

    try:
        supabase.storage.from_(_ILLUSTRATION_BUCKET).upload(
            file=file_bytes,
            path=object_path,
            file_options={"content-type": content_type, "upsert": "true"},
        )
    except Exception as e:
        logger.error("Vehicle illustration upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Storage upload failed") from e

    # Public URL — bucket is public-read so no signed URL needed.
    public_url_res = supabase.storage.from_(_ILLUSTRATION_BUCKET).get_public_url(object_path)
    # supabase-py returns either a string or an object with a publicUrl attr
    public_url = public_url_res if isinstance(public_url_res, str) else getattr(public_url_res, "public_url", None)
    if not public_url:
        raise HTTPException(
            status_code=502,
            detail="Could not resolve public URL for uploaded illustration",
        )

    await db_supabase.update_one("vehicle_types", {"id": type_id}, {"illustration_url": public_url})
    await invalidate_fare_cache()

    logger.info(
        "[admin] vehicle illustration uploaded type_id=%s admin_id=%s bytes=%d",
        type_id,
        admin.get("id"),
        len(file_bytes),
    )
    return {"illustration_url": public_url}


@router.post("/vehicle-types/{type_id}/upload-marker")
async def admin_upload_vehicle_marker(
    type_id: str,
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    """Upload a custom map marker image for a vehicle type.

    PNG/WebP only — the marker is composited over the map, so it must carry
    an alpha channel (a JPEG would render as an opaque rectangle around the
    car). Art direction: top-down car, facing north (rotation = GPS heading
    is applied client-side), tight portrait crop, ≤ 500 KB.

    Stored in the public `vehicle-illustrations` bucket under
    `markers/{type_id}/{uuid4}{ext}`; the public URL is written to
    `vehicle_types.marker_image_url`, which the apps sync on open.
    """
    vt = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("vehicle_types", {"id": type_id}, limit=1))
    if not vt:
        raise HTTPException(status_code=404, detail="Vehicle type not found")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ("image/png", "image/webp"):
        raise HTTPException(
            status_code=400,
            detail="Markers must be transparent PNG or WebP (JPEG has no alpha channel)",
        )

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_ILLUSTRATION_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 500 KB limit")
    _validate_file_type(file_bytes, content_type)
    _validate_image_transparency(file_bytes, content_type, "map marker")

    ext = ".png" if content_type == "image/png" else ".webp"
    object_path = f"markers/{type_id}/{uuid.uuid4()}{ext}"

    try:
        supabase.storage.from_(_ILLUSTRATION_BUCKET).upload(
            file=file_bytes,
            path=object_path,
            file_options={"content-type": content_type, "upsert": "true"},
        )
    except Exception as e:
        logger.error("Vehicle marker upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Storage upload failed") from e

    public_url_res = supabase.storage.from_(_ILLUSTRATION_BUCKET).get_public_url(object_path)
    public_url = public_url_res if isinstance(public_url_res, str) else getattr(public_url_res, "public_url", None)
    if not public_url:
        raise HTTPException(
            status_code=502,
            detail="Could not resolve public URL for uploaded marker",
        )

    await db_supabase.update_one("vehicle_types", {"id": type_id}, {"marker_image_url": public_url})
    await invalidate_fare_cache()

    logger.info(
        "[admin] vehicle marker uploaded type_id=%s admin_id=%s bytes=%d",
        type_id,
        admin.get("id"),
        len(file_bytes),
    )
    return {"marker_image_url": public_url}


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
    vt = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("vehicle_types", {"id": type_id}, limit=1))
    if not vt:
        raise HTTPException(status_code=404, detail="Vehicle type not found")
    vt_name = vt.get("name") or ""

    # fare_configs referencing this type
    fare_cfg_rows = await db_supabase.get_rows("fare_configs", {"vehicle_type_id": type_id}, limit=500)
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
async def admin_create_fare_config(config: FareConfigCreateRequest):
    """Create fare configuration."""
    doc = {
        "name": config.name,
        "service_area_id": config.service_area_id,
        "vehicle_type_id": config.vehicle_type_id,
        "base_fare": config.base_fare,
        "per_km_rate": (config.per_km_rate if config.per_km_rate is not None else config.price_per_km or 0),
        "per_minute_rate": (
            config.per_minute_rate if config.per_minute_rate is not None else config.price_per_minute or 0
        ),
        "minimum_fare": config.minimum_fare,
        "booking_fee": config.booking_fee,
        "is_active": config.is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await db_supabase.insert_one("fare_configs", doc)
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    return {"config_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@router.put("/fare-configs/{config_id}")
async def admin_update_fare_config(config_id: str, config: FareConfigUpdateRequest):
    """Update fare configuration."""
    updates: Dict[str, Any] = {}
    if config.name is not None:
        updates["name"] = config.name
    if config.base_fare is not None:
        updates["base_fare"] = config.base_fare
    per_km = config.per_km_rate if config.per_km_rate is not None else config.price_per_km
    if per_km is not None:
        updates["per_km_rate"] = per_km
    per_min = config.per_minute_rate if config.per_minute_rate is not None else config.price_per_minute
    if per_min is not None:
        updates["per_minute_rate"] = per_min
    if config.area_geojson is not None:
        updates["area_geojson"] = config.area_geojson
    if config.is_active is not None:
        updates["is_active"] = config.is_active

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

    item_data: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "reporter_id": rider_id,
        "driver_id": driver_id,
        "item_description": req.item_description,
        "status": "reported",
    }
    if req.service_area_id:
        item_data["service_area_id"] = req.service_area_id

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
                # N3 (ACTION_ITEMS.md): send_push_notification's first arg is
                # a users.id, which it looks up itself to find the token —
                # not the raw fcm_token string. Passing the token here meant
                # the internal `db.find_one("users", {"id": user_id})` always
                # missed (a token never equals a user id), silently dropping
                # every lost-item push.
                await send_push_notification(
                    driver["user_id"],
                    "Lost Item Report",
                    f"A rider reported a lost item: {req.item_description}. Please check your vehicle.",
                    {"type": "lost_and_found", "ride_id": ride_id},
                    target_app="driver",
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
        logger.error(f"Failed to send lost item notification for ride {ride_id}: {e}", exc_info=True)

    return item


@router.put("/lost-and-found/{item_id}/resolve")
async def admin_resolve_lost_item(item_id: str, req: LostAndFoundResolveRequest):
    """Resolve or mark a lost and found item as unresolved."""
    if req.status not in ("resolved", "unresolved"):
        raise HTTPException(status_code=422, detail="status must be 'resolved' or 'unresolved'")
    update_data: Dict[str, Any] = {
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
    status: Optional[str] = None,
    service_area_id: Optional[str] = None,
):
    """List all lost and found items. Optional `status` / `service_area_id` filters."""
    filters: Dict[str, Any] = {}
    if status and status != "all":
        filters["status"] = status
    if service_area_id and service_area_id != "all":
        filters["service_area_id"] = service_area_id
    items = await db_supabase.get_rows(
        "lost_and_found",
        filters,
        order="created_at",
        desc=True,
        limit=limit,
        offset=offset,
    )
    return items


_VALID_STATUSES = frozenset(
    {
        "reported",
        "driver_notified",
        "driver_found",
        "admin_created",
        "found",
        "not_found",
        "returned",
        "unclaimed",
        "resolved",
        "unresolved",
        "closed",
    }
)


@router.put("/lost-and-found/{item_id}")
async def admin_update_lost_item(item_id: str, req: LostAndFoundUpdateRequest):
    """Update a lost and found item."""
    if req.status is not None and req.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )
    update: Dict[str, Any] = {}
    if req.item_description is not None:
        update["item_description"] = req.item_description
    if req.status is not None:
        update["status"] = req.status
    if req.admin_notes is not None:
        update["admin_notes"] = req.admin_notes
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db_supabase.update_lost_and_found(item_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Item not found")
    return result


@router.delete("/lost-and-found/{item_id}")
async def admin_delete_lost_item(item_id: str):
    """Delete a lost and found item."""
    existing = await db_supabase.get_rows("lost_and_found", {"id": item_id}, limit=1)
    if not existing:
        raise HTTPException(status_code=404, detail="Item not found")
    await db_supabase.delete_one("lost_and_found", {"id": item_id})
    return {"message": "Item deleted"}
