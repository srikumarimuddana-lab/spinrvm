"""Rider rates driver after a completed trip.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    ROUND_HALF_UP,
    APIRouter,
    Decimal,
    Depends,
    HTTPException,
    Request,
    RideRatingRequest,
    RideStatus,
    build_earnings_snapshot,
    datetime,
    get_current_user,
    idempotent_endpoint,
    log_user_action,
    logger,
    ride_rating_limit,
    timezone,
)
from ._shared import (  # noqa: F401
    _d,
    _f,
    _push_in_background,
    _round,
)

router = APIRouter()


@router.post("/{ride_id}/rate")
@ride_rating_limit
@idempotent_endpoint(scope="ride_rate")
async def rate_driver(
    ride_id: str,
    rating_data: RideRatingRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider rates the driver"""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride or ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=404, detail="Ride not found or unauthorized")

    if ride.get("status") != RideStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Ride must be completed before rating")

    if ride.get("rider_rating") is not None:
        raise HTTPException(status_code=409, detail="Ride already rated")

    _pay_status = (ride.get("payment_status") or "").lower()
    if rating_data.tip_amount > 0 and _pay_status not in ("pending", "failed", ""):
        raise HTTPException(
            status_code=400,
            detail="Tip cannot be added after payment has been settled",
        )

    # Save rating using existing columns (rider_rating = rating rider gave the driver)
    await _deps.db_supabase.update_ride(
        ride_id,
        {
            "rider_rating": rating_data.rating,
            "rider_comment": rating_data.comment or "",
            "updated_at": datetime.now(timezone.utc),
        },
    )

    driver_id = ride.get("driver_id")
    if not driver_id:
        return {"success": True}

    if rating_data.tip_amount > 0:
        tip = _round(_d(rating_data.tip_amount))
        new_driver_earnings = _round(_d(ride.get("driver_earnings") or 0) + tip)
        _rate_update: dict = {"tip_amount": _f(tip), "driver_earnings": _f(new_driver_earnings)}
        des = ride.get("driver_earnings_snapshot")
        if des and isinstance(des, dict):
            des.update(
                build_earnings_snapshot(
                    fare=des.get("fare") or 0,
                    tip=tip,
                    incentive=des.get("incentive") or 0,
                    tax=des.get("tax") or 0,
                    cancel_fee=des.get("cancel_fee") or 0,
                )
            )
            _rate_update["driver_earnings_snapshot"] = des
        await _deps.db_supabase.update_ride(ride_id, _rate_update)

    # Aggregate driver rating using rolling average to avoid O(n) ride fetch.
    driver = await _deps.db_supabase.get_driver_by_id(driver_id)
    if driver:
        old_count = int(driver.get("total_ratings") or 0)
        old_avg = Decimal(str(driver.get("rating") or 0))
        new_count = old_count + 1
        new_avg = ((old_avg * old_count + Decimal(str(rating_data.rating))) / new_count).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        await _deps.db_supabase.update_one(
            "drivers",
            {"id": driver_id},
            {
                "rating": float(new_avg),
                "total_ratings": new_count,
            },
        )

    # G19: Notify the driver that they received a rating. This creates a
    # feedback loop — drivers see their rating improve/decline in real time
    # instead of only noticing on their next profile check.
    if driver and driver.get("user_id") and rating_data.rating:
        stars = "⭐" * int(rating_data.rating)
        tip_note = f" + ${rating_data.tip_amount:.2f} tip!" if rating_data.tip_amount > 0 else ""
        _push_in_background(
            driver["user_id"],
            f"New Rating: {stars}",
            f"A rider rated you {rating_data.rating}/5{tip_note}",
            {
                "type": "rating_received",
                "rating": str(rating_data.rating),
                "ride_id": ride_id,
            },
            _ctx=f"[RATING] driver {driver['user_id']}",
        )

    _deps.spawn(
        log_user_action(
            current_user,
            "driver_rated",
            "rides",
            ride_id,
            {"rating": str(rating_data.rating), "driver_id": driver_id},
        )
    )
    return {"success": True}
