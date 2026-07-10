"""Live route polyline for an in-progress ride.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    Depends,
    ErrorKeys,
    HTTPException,
    RideNotFoundException,
    get_current_user,
)

router = APIRouter()


@router.get("/{ride_id}/live-route")
async def get_live_route(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Road-routed line + ETA from the driver's live position to the active
    destination (pickup pre-trip, dropoff in-trip) for the live trip map.

    Routing provider chain lives in utils.route_distance.compute_route: self-
    hosted OSRM first, Google Directions fallback (budget-gated) when OSRM is
    unconfigured or failing. Returns an empty polyline (eta_seconds=None) when
    there's no active route, no live driver position, or every provider failed
    — the client then keeps its saved planned line. Keeps the routing engine
    internal (the apps draw this line on the Google map canvas)."""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise RideNotFoundException(ride_id=ride_id, message_key=ErrorKeys.RIDE_NOT_FOUND)

    # Ownership: rider or assigned driver (admin allowed).
    is_rider = ride.get("rider_id") == current_user["id"]
    driver_self = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = bool(driver_self) and ride.get("driver_id") == driver_self["id"]
    if not (is_rider or is_driver) and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to view this ride")

    status = ride.get("status")
    if status in ("driver_assigned", "driver_accepted", "driver_arrived"):
        dest_lat, dest_lng, destination = ride.get("pickup_lat"), ride.get("pickup_lng"), "pickup"
    elif status == "in_progress":
        dest_lat, dest_lng, destination = ride.get("dropoff_lat"), ride.get("dropoff_lng"), "dropoff"
    else:
        # No live leg for searching / scheduled / completed / cancelled.
        return {"polyline": [], "eta_seconds": None, "distance_km": None, "destination": None}

    empty = {"polyline": [], "eta_seconds": None, "distance_km": None, "destination": destination}

    # Live origin = the assigned driver's current position (drivers.lat/lng).
    assigned = (
        (lambda _r: _r[0] if _r else None)(
            await _deps.db_supabase.get_rows("drivers", {"id": ride.get("driver_id")}, limit=1)
        )
        if ride.get("driver_id")
        else None
    )
    o_lat = assigned.get("lat") if assigned else None
    o_lng = assigned.get("lng") if assigned else None
    if o_lat is None or o_lng is None or dest_lat is None or dest_lng is None:
        return empty

    try:
        from ...utils.route_distance import compute_route
    except ImportError:
        from utils.route_distance import compute_route  # type: ignore

    result = await compute_route(float(o_lat), float(o_lng), float(dest_lat), float(dest_lng))
    if not result:
        return empty
    result["destination"] = destination
    return result
