"""Admin CRUD for curated pickup venues (malls/airport).

A venue has a detection center + radius and a list of named pickup points
(driver-reachable meeting spots). The rider app reads them via
GET /maps/pickup-points; admins manage them here.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/venues", tags=["Admin Venues"])

_TABLE = "venues"


class PickupPoint(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class VenueRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    center_lat: float = Field(..., ge=-90, le=90)
    center_lng: float = Field(..., ge=-180, le=180)
    radius_m: int = Field(default=150, ge=20, le=2000)
    pickup_points: List[PickupPoint] = Field(default_factory=list, max_length=30)
    service_area_id: Optional[str] = None
    is_active: bool = True
    # ADR-008 airport FIFO dispatch. A venue is EITHER a terminal
    # (fifo_enabled, pickup detection) OR a feeder lot (queue_for_venue_id →
    # the terminal whose queue its waiting drivers join) — never both. Mirrors
    # the venues_fifo_lot_exclusive DB CHECK; validated here for a clean 400.
    fifo_enabled: bool = False
    queue_for_venue_id: Optional[str] = None

    @model_validator(mode="after")
    def _fifo_exclusive(self) -> "VenueRequest":
        if self.fifo_enabled and self.queue_for_venue_id:
            raise ValueError("A venue cannot be a FIFO terminal and a feeder lot at the same time")
        return self


@router.get("")
async def list_venues(
    service_area_id: Optional[str] = None,
    admin: dict = Depends(get_admin_user),
):
    # Filter server-side when an area is selected so we only fetch the venues
    # that belong to it rather than the whole table.
    filters: dict = {}
    if service_area_id:
        filters["service_area_id"] = service_area_id
    try:
        rows = await db_supabase.get_rows(_TABLE, filters, order="created_at", desc=True, limit=2000)
    except Exception as e:
        logger.error(f"Failed to list venues: {e}", exc_info=True, extra={"domain": "admin"})
        raise HTTPException(status_code=503, detail="venues_unavailable") from e
    return {"venues": rows}


def _payload(body: VenueRequest) -> dict:
    return {
        "name": body.name.strip(),
        "center_lat": body.center_lat,
        "center_lng": body.center_lng,
        "radius_m": body.radius_m,
        "pickup_points": [p.model_dump() for p in body.pickup_points],
        "service_area_id": body.service_area_id,
        "is_active": body.is_active,
        "fifo_enabled": body.fifo_enabled,
        "queue_for_venue_id": body.queue_for_venue_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _validate_queue_target(body: VenueRequest, venue_id: Optional[str] = None) -> None:
    """Reject a feeder-lot link that can't form a valid FIFO queue (clean 400).

    The DB CHECK + FK ultimately guard this, but surfacing the reason here gives
    the admin dashboard a usable message instead of a 503/constraint error.
    """
    target_id = body.queue_for_venue_id
    if not target_id:
        return
    if venue_id is not None and target_id == venue_id:
        raise HTTPException(status_code=400, detail="A venue cannot queue for itself")
    target = await db_supabase.find_one(_TABLE, {"id": target_id})
    if not target:
        raise HTTPException(status_code=400, detail="queue_for_venue_id does not reference an existing venue")
    if not target.get("fifo_enabled"):
        raise HTTPException(
            status_code=400,
            detail="queue_for_venue_id must reference a fifo_enabled terminal venue",
        )


@router.post("")
async def create_venue(body: VenueRequest, admin: dict = Depends(get_admin_user)):
    await _validate_queue_target(body)
    try:
        row = await db_supabase.insert_one(_TABLE, _payload(body))
    except Exception as e:
        logger.error(f"Failed to create venue: {e}", exc_info=True, extra={"domain": "admin"})
        raise HTTPException(status_code=503, detail="venues_unavailable") from e
    await log_admin_action(admin, "venue_created", _TABLE, (row or {}).get("id", ""), {"name": body.name})
    return row


@router.put("/{venue_id}")
async def update_venue(venue_id: str, body: VenueRequest, admin: dict = Depends(get_admin_user)):
    existing = await db_supabase.find_one(_TABLE, {"id": venue_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Venue not found")
    await _validate_queue_target(body, venue_id=venue_id)
    try:
        await db_supabase.update_one(_TABLE, {"id": venue_id}, _payload(body))
    except Exception as e:
        logger.error(f"Failed to update venue: {e}", exc_info=True, extra={"domain": "admin"})
        raise HTTPException(status_code=503, detail="venues_unavailable") from e
    await log_admin_action(admin, "venue_updated", _TABLE, venue_id, {"name": body.name})
    return await db_supabase.find_one(_TABLE, {"id": venue_id})


@router.delete("/{venue_id}")
async def delete_venue(venue_id: str, admin: dict = Depends(get_admin_user)):
    existing = await db_supabase.find_one(_TABLE, {"id": venue_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Venue not found")
    try:
        await db_supabase.delete_one(_TABLE, {"id": venue_id})
    except Exception as e:
        logger.error(f"Failed to delete venue: {e}", exc_info=True, extra={"domain": "admin"})
        raise HTTPException(status_code=503, detail="venues_unavailable") from e
    await log_admin_action(admin, "venue_deleted", _TABLE, venue_id, {"name": existing.get("name")})
    return {"success": True}
