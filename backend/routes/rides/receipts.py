"""Ride receipts: fetch and email.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    Decimal,
    Depends,
    HTTPException,
    Request,
    RideStatus,
    get_current_user,
    ride_action_limit,
    send_ride_receipt,
)
from ._shared import (  # noqa: F401
    _build_fare_breakdown,
    _d,
    _f,
    _sum_fare_breakdown,
    relabel_booked_distance_lines,
)

router = APIRouter()


@router.get("/{ride_id}/receipt")
async def get_ride_receipt(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get a detailed receipt for a completed ride"""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this receipt")

    if ride.get("status") not in RideStatus.terminal_statuses():
        raise HTTPException(
            status_code=400,
            detail="Receipts are only available for completed or cancelled rides",
        )

    driver = None
    if ride.get("driver_id"):
        driver = await _deps.db_supabase.get_driver_by_id(ride["driver_id"])

    driver_profile = None
    if driver and driver.get("user_id"):
        driver_profile = await _deps.db_supabase.get_user_by_id(driver["user_id"])

    vehicle = None
    if ride.get("vehicle_type_id"):
        vehicle = (lambda _r: _r[0] if _r else None)(
            await _deps.db_supabase.get_rows("vehicle_types", {"id": ride["vehicle_type_id"]}, limit=1)
        )

    corporate_account = None
    if ride.get("corporate_account_id"):
        corporate_account = (lambda _r: _r[0] if _r else None)(
            await _deps.db_supabase.get_rows("corporate_accounts", {"id": ride["corporate_account_id"]}, limit=1)
        )

    # Resolve fare display: snapshot (when fare_lock is active) or dynamic rebuild.
    snapshot = ride.get("fare_breakdown_snapshot")
    fare_locked = False
    if snapshot and isinstance(snapshot, dict) and snapshot.get("lines"):
        try:
            settings = await _deps.get_app_settings()
            fare_locked = settings.get("fare_lock_enabled", False) if settings else False
        except Exception:
            fare_locked = False

    if fare_locked and snapshot:
        fare_lines = list(snapshot["lines"])
        ride_tip = float(_d(ride.get("tip_amount") or 0))
        has_tip_line = any(ln.get("type") == "tip" for ln in fare_lines)
        if ride_tip > 0 and not has_tip_line:
            fare_lines.append({"label": "Tip", "amount": _f(_d(ride_tip)), "type": "tip"})
        # Relabel the frozen "Ride fare (X km)" line to "(X km booked)" when GPS
        # diverged, matching get_ride / ride-history (queries.py). Without this the
        # receipt shows the quoted road distance in the fare line but the
        # GPS-measured distance in the top-level tile — two numbers for one trip.
        fare_lines = relabel_booked_distance_lines(fare_lines, ride)
        receipt_grand_total = _sum_fare_breakdown(fare_lines)
    else:
        fare_lines = _build_fare_breakdown(ride)
        receipt_grand_total = ride.get("grand_total") or (
            (ride.get("total_fare", 0) or 0)
            + (ride.get("area_fees_total", 0) or 0)
            + (ride.get("tax_amount", 0) or 0)
            + (ride.get("tip_amount", 0) or 0)
        )

    receipt_data = {
        "ride_id": ride_id,
        "ride_code": ride.get("ride_code"),
        "date": ride.get("ride_completed_at") or ride.get("cancelled_at") or ride.get("created_at"),
        "status": ride.get("status"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "stops": ride.get("stops", []),
        # Under fare-lock the rider was quoted (and charged) the road distance,
        # so the receipt's headline distance must be the quoted planned distance,
        # not the GPS-measured value that ride_complete writes into distance_km.
        "distance_km": (
            ride.get("planned_distance_km")
            if fare_locked and ride.get("planned_distance_km") is not None
            else ride.get("distance_km")
        ),
        "duration_minutes": ride.get("duration_minutes"),
        "base_fare": ride.get("base_fare", 0),
        "distance_fare": ride.get("distance_fare", 0),
        "time_fare": ride.get("time_fare", 0),
        "airport_fee": ride.get("airport_fee", 0),
        "booking_fee": ride.get("booking_fee", 0),
        "cancellation_fee": (
            (ride.get("cancellation_fee_admin", 0) + ride.get("cancellation_fee_driver", 0))
            if ride.get("status") == RideStatus.CANCELLED
            else 0
        ),
        "surge_multiplier": ride.get("surge_multiplier", 1.0),
        "area_fees_total": ride.get("area_fees_total", 0),
        "area_fees_breakdown": ride.get("area_fees_breakdown", []),
        "tax_amount": ride.get("tax_amount", 0),
        "tax_breakdown": ride.get("tax_breakdown", {}),
        "tip_amount": ride.get("tip_amount", 0),
        "total_charged": ride.get("total_fare", 0),
        "grand_total": receipt_grand_total,
        "fare_breakdown": fare_lines,
        "fare_locked": fare_locked,
        "payment_method": (
            "Corporate Account"
            if corporate_account
            else (ride.get("payment_method_id") or "Credit Card ending in ****")
        ),
        "corporate_account_name": (corporate_account.get("company_name") if corporate_account else None),
        "driver_name": (
            f"{driver_profile.get('first_name', '')} {driver_profile.get('last_name', '')}".strip()
            if driver_profile
            else "Unknown Driver"
        ),
        "vehicle_type": vehicle.get("name") if vehicle else "Standard",
    }

    return {"success": True, "receipt": receipt_data}


@router.post("/{ride_id}/email-receipt")
@ride_action_limit
async def email_ride_receipt(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Re-send the receipt email for a completed ride."""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in RideStatus.terminal_statuses():
        raise HTTPException(status_code=400, detail="Receipts are only available for completed rides")

    tip_amount = Decimal(str(ride.get("tip_amount") or 0))
    email_sent = await send_ride_receipt(ride, current_user["id"], tip_amount)
    if not email_sent:
        raise HTTPException(
            status_code=503,
            detail="Could not send receipt email. Please try again later.",
        )
    return {"success": True}
