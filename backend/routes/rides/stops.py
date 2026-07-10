"""Mid-trip stop management and ride notes.

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
    Optional,
    Request,
    RideStatus,
    datetime,
    get_current_user,
    logger,
    ride_action_limit,
    timezone,
)
from ._shared import (  # noqa: F401
    _reestimate_fare_for_stops,
)

router = APIRouter()


# ── Mid-Trip Stop Editing ─────────────────────────────────────────────


class AddStopMidTripRequest(BaseModel):
    address: str
    lat: float
    lng: float
    position: Optional[int] = None  # Insert at this index; None = append


@router.post("/{ride_id}/stops")
@ride_action_limit
async def add_stop_mid_trip(
    ride_id: str,
    req: AddStopMidTripRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Add a stop to an active ride mid-trip."""
    ride = await _deps.db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in (
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
        RideStatus.IN_PROGRESS,
    ):
        raise HTTPException(status_code=400, detail="Can only edit stops on an active ride")

    stops = ride.get("stops") or []
    new_stop = {"address": req.address, "lat": req.lat, "lng": req.lng}

    if req.position is not None and 0 <= req.position <= len(stops):
        stops.insert(req.position, new_stop)
    else:
        stops.append(new_stop)

    fare_update = _reestimate_fare_for_stops(ride, stops)
    await _deps.db.update_one(
        "rides",
        {"id": ride_id},
        {
            "$set": {
                **fare_update,
                "stops": stops,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    # Notify driver via WebSocket
    if ride.get("driver_id"):
        driver = await _deps.db.find_one("drivers", {"id": ride["driver_id"]})
        if driver and driver.get("user_id"):
            await _deps.manager.send_personal_message(
                {
                    "type": "stops_updated",
                    "ride_id": ride_id,
                    "stops": stops,
                    "estimated_fare": fare_update["estimated_fare"],
                },
                f"driver_{driver['user_id']}",
            )

    return {"success": True, "stops": stops, **fare_update}


@router.delete("/{ride_id}/stops/{stop_index}")
@ride_action_limit
async def remove_stop_mid_trip(
    ride_id: str,
    stop_index: int,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Remove a stop from an active ride by index."""
    ride = await _deps.db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in (
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
        RideStatus.IN_PROGRESS,
    ):
        raise HTTPException(status_code=400, detail="Can only edit stops on an active ride")

    stops = ride.get("stops") or []
    if stop_index < 0 or stop_index >= len(stops):
        raise HTTPException(status_code=400, detail="Invalid stop index")

    stops.pop(stop_index)

    fare_update = _reestimate_fare_for_stops(ride, stops)
    await _deps.db.update_one(
        "rides",
        {"id": ride_id},
        {
            "$set": {
                **fare_update,
                "stops": stops,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    # Notify driver
    if ride.get("driver_id"):
        driver = await _deps.db.find_one("drivers", {"id": ride["driver_id"]})
        if driver and driver.get("user_id"):
            await _deps.manager.send_personal_message(
                {
                    "type": "stops_updated",
                    "ride_id": ride_id,
                    "stops": stops,
                    "estimated_fare": fare_update["estimated_fare"],
                },
                f"driver_{driver['user_id']}",
            )

    return {"success": True, "stops": stops, **fare_update}


class RideNotesUpdateRequest(BaseModel):
    notes: str = Field(default="", max_length=200)


@router.patch("/{ride_id}/notes")
@ride_action_limit
async def patch_ride_notes(
    ride_id: str,
    body: RideNotesUpdateRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider attaches/updates a free-text note for the driver.

    UX intent: the booking screen no longer collects this field — riders
    add the note from the ride-status screen *after* confirming, matching
    the Uber/Lyft pattern. Allowed while the driver is still en route;
    once the trip starts the note is locked (the driver is already
    on the way; tail-end edits are confusing).
    """
    ride = await _deps.db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    allowed = (
        RideStatus.SEARCHING,
        RideStatus.DRIVER_ASSIGNED,
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
    )
    if ride.get("status") not in allowed:
        raise HTTPException(
            status_code=409,
            detail="Notes can only be edited before pickup",
        )

    notes = (body.notes or "").strip() or None
    await _deps.db.update_one(
        "rides",
        {"id": ride_id},
        {
            "$set": {
                "rider_notes": notes,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    # Push to the assigned driver if one exists. Best-effort — a failed
    # WS send must not error out the API (the driver-app will reconcile
    # via its next ride fetch).
    if ride.get("driver_id"):
        try:
            driver = await _deps.db.find_one("drivers", {"id": ride["driver_id"]})
            if driver and driver.get("user_id"):
                await _deps.manager.send_personal_message(
                    {"type": "ride_notes_updated", "ride_id": ride_id, "notes": notes},
                    f"driver_{driver['user_id']}",
                )
        except Exception as e:
            logger.warning(f"[notes] WS push to driver failed for ride {ride_id}: {e}")

    return {"success": True, "notes": notes}
