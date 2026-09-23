"""Mid-trip stop management and ride notes.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

import json
import math

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
    uuid,
)
from ._shared import (  # noqa: F401
    _reestimate_fare_for_stops,
)

router = APIRouter()


def _stops_cas_filters(ride: dict) -> dict:
    """Match the exact route version we read before changing stop state."""
    old_stops = ride.get("stops")
    return {
        "id": ride["id"],
        "status": {"$eq": ride.get("status")},
        "stops": {"$eq": json.dumps(old_stops, separators=(",", ":"))} if old_stops is not None else None,
    }


def _valid_stop(stop: dict) -> bool:
    try:
        lat, lng = float(stop.get("lat")), float(stop.get("lng"))
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(lat)
        and math.isfinite(lng)
        and -90 <= lat <= 90
        and -180 <= lng <= 180
        and (lat != 0 or lng != 0)
    )


# ── Mid-Trip Stop Editing ─────────────────────────────────────────────


class AddStopMidTripRequest(BaseModel):
    address: str
    lat: float
    lng: float
    position: Optional[int] = None  # Insert at this index; None = append


class CompleteStopRequest(BaseModel):
    expected_stops: list[dict]


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

    stops = list(ride.get("stops") or [])
    if not _valid_stop({"lat": req.lat, "lng": req.lng}):
        raise HTTPException(status_code=400, detail="Stop has no valid destination")
    new_stop = {"address": req.address, "lat": req.lat, "lng": req.lng}

    if req.position is not None and 0 <= req.position <= len(stops):
        stops.insert(req.position, new_stop)
    else:
        stops.append(new_stop)

    fare_update = await _reestimate_fare_for_stops(ride, stops)
    updated = await _deps.db.update_one(
        "rides",
        _stops_cas_filters(ride),
        {
            "$set": {
                **fare_update,
                "stops": stops,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Stops changed. Refresh the ride and try again.")

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
                    "grand_total": fare_update.get("grand_total"),
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

    stops = list(ride.get("stops") or [])
    if stop_index < 0 or stop_index >= len(stops):
        raise HTTPException(status_code=400, detail="Invalid stop index")

    stops.pop(stop_index)

    fare_update = await _reestimate_fare_for_stops(ride, stops)
    updated = await _deps.db.update_one(
        "rides",
        _stops_cas_filters(ride),
        {
            "$set": {
                **fare_update,
                "stops": stops,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Stops changed. Refresh the ride and try again.")

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
                    "grand_total": fare_update.get("grand_total"),
                },
                f"driver_{driver['user_id']}",
            )

    return {"success": True, "stops": stops, **fare_update}


@router.post("/{ride_id}/stops/{stop_index}/complete")
@ride_action_limit
async def complete_stop(
    ride_id: str,
    stop_index: int,
    body: CompleteStopRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Persist explicit driver arrival at one ordered mid-trip stop."""
    drivers = await _deps.db.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    driver = drivers[0] if drivers else None
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    ride = await _deps.db.find_one("rides", {"id": ride_id, "driver_id": driver["id"]})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("status") != RideStatus.IN_PROGRESS:
        raise HTTPException(status_code=409, detail="Stops can only be completed during an active trip")
    stops = list(ride.get("stops") or [])
    if body.expected_stops != stops:
        raise HTTPException(status_code=409, detail="Stops changed. Refresh the ride and try again.")
    if stop_index < 0 or stop_index >= len(stops):
        raise HTTPException(status_code=400, detail="Invalid stop index")
    first_incomplete = next(
        (index for index, item in enumerate(stops) if not isinstance(item, dict) or item.get("completed") is not True),
        None,
    )
    if stop_index != first_incomplete:
        raise HTTPException(status_code=409, detail="Complete stops in route order.")
    stop = stops[stop_index]
    if not isinstance(stop, dict) or not _valid_stop(stop):
        raise HTTPException(status_code=400, detail="Stop has no valid destination")
    if stop.get("completed") is True:
        return {"success": True, "stops": stops}

    # Assign stable identities while touching legacy rows. A concurrent rider
    # edit changes the CAS predicate and returns 409 rather than completing a
    # different stop that moved into this index.
    stops = [dict(item) if isinstance(item, dict) else item for item in stops]
    for item in stops:
        if isinstance(item, dict):
            item.setdefault("id", str(uuid.uuid4()))
    stops[stop_index]["completed"] = True
    updated = await _deps.db.update_one(
        "rides",
        {**_stops_cas_filters(ride), "driver_id": driver["id"]},
        {"$set": {"stops": stops, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Stops changed. Refresh the ride and try again.")
    event = {"type": "stops_updated", "ride_id": ride_id, "stops": stops}
    await _deps.manager.send_personal_message(event, f"rider_{ride['rider_id']}")
    if driver.get("user_id"):
        await _deps.manager.send_personal_message(event, f"driver_{driver['user_id']}")
    return {"success": True, "stops": stops}


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
