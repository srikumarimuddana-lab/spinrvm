"""Rider lost-item reports.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Depends,
    Field,
    HTTPException,
    Request,
    RideStatus,
    datetime,
    get_current_user,
    logger,
    ride_action_limit,
    timezone,
    uuid,
)

router = APIRouter()


class RiderLostItemRequest(BaseModel):
    item_description: str = Field(..., min_length=3, max_length=500)
    item_category: str = Field(default="other")


@router.post("/{ride_id}/lost-and-found")
@ride_action_limit
async def rider_report_lost_item(
    ride_id: str,
    req: RiderLostItemRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider reports a lost item from a completed ride."""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != RideStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail="Lost items can only be reported for completed rides",
        )

    driver_id = ride.get("driver_id")
    if not driver_id:
        raise HTTPException(status_code=400, detail="No driver assigned to this ride")

    valid_categories = {"electronics", "clothing", "bag", "document", "keys", "other"}
    category = req.item_category if req.item_category in valid_categories else "other"

    item_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "reporter_id": current_user["id"],
        "rider_user_id": current_user["id"],
        "driver_id": driver_id,
        "reporter_type": "rider",
        "item_description": req.item_description,
        "item_category": category,
        "status": "reported",
        "contact_method": "in_app",
    }

    item = await _deps.db_supabase.create_lost_and_found(item_data)

    try:
        driver = await _deps.db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            driver_user = await _deps.db_supabase.get_user_by_id(driver["user_id"])
            if driver_user:
                await _deps.send_push_notification(
                    driver_user["id"],
                    "Lost Item Report",
                    f"A rider reported a lost item: {req.item_description}. Please check your vehicle.",
                    {"type": "lost_and_found", "case_id": item["id"], "ride_id": ride_id},
                    target_app="driver",
                )
                await _deps.db_supabase.update_lost_and_found(
                    item["id"],
                    {
                        "status": "driver_notified",
                        "notified_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
    except Exception as e:
        logger.error(f"Lost item driver notification failed for ride {ride_id}: {e}", exc_info=True)

    return {"success": True, "item": item}
