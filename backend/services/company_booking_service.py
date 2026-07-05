"""Corporate guest booking: a company employee books a ride for a customer.

Composed from the existing verified building blocks rather than reusing the
rider-centric ``create_ride`` endpoint (whose suspension checks, wallet/card
pre-validation, pre-auth/SCA and promo branches all assume caller == rider):

  * customer identity  → services.guest_user_service (phone-keyed users row)
  * fare               → features.compute_fare_estimate (canonical pipeline,
                         surge pinned to 1.0 — corporate rides never surge)
  * policy             → corporate_policy_service.evaluate_policy_for_ride
                         evaluated against the BOOKER (their allowance pays)
  * pickup OTP         → dependencies.generate_pickup_otp (universal verify
                         flow: driver must enter it to start the trip)
  * insert + dispatch  → routes.rides._insert_ride_with_code and
                         _prep_and_dispatch (normal matching pipeline)

Payment: rides are stamped payment_method="company_allowance",
corporate_member_id=<booking employee>, guest_booking=True. Settlement runs
server-side on completion (payment_service.auto_settle_guest_corporate) —
the guest never pays and never sees a payment screen.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import HTTPException

try:
    from ..dependencies import generate_pickup_otp
    from ..features import compute_fare_estimate
    from ..schemas import Ride
    from ..services.corporate_policy_service import evaluate_policy_for_ride
    from ..services.guest_user_service import find_or_create_guest_by_phone
    from ..utils.background import spawn
    from ..utils.money import to_decimal as _d
except ImportError:
    from dependencies import generate_pickup_otp  # type: ignore
    from features import compute_fare_estimate  # type: ignore
    from schemas import Ride  # type: ignore
    from services.corporate_policy_service import evaluate_policy_for_ride  # type: ignore
    from services.guest_user_service import find_or_create_guest_by_phone  # type: ignore
    from utils.background import spawn  # type: ignore
    from utils.money import to_decimal as _d  # type: ignore

logger = logging.getLogger(__name__)

# Ride states the dispatcher understands (mirrors routes.rides.RideStatus —
# imported lazily below to avoid a service→routes import at module load).
_STATUS_SEARCHING = "searching"
_STATUS_SCHEDULED = "scheduled"


def _round2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"))


async def create_company_guest_booking(
    company_id: str,
    booker_member: Dict[str, Any],
    payload: Any,
) -> Dict[str, Any]:
    """Create a guest ride for ``company_id`` booked by ``booker_member``.

    ``payload`` is the validated CompanyGuestBookingRequest (see
    routes/corporate_company_bookings.py). Returns
    ``{"ride": <row>, "tracking_url": <str|None>, "customer_has_app": bool}``.
    """
    guest_user, customer_has_app = await find_or_create_guest_by_phone(payload.customer_phone, payload.customer_name)

    # Canonical fare, surge pinned at 1.0 (corporate-paid rides never surge).
    fare = await compute_fare_estimate(
        pickup_lat=payload.pickup_lat,
        pickup_lng=payload.pickup_lng,
        dropoff_lat=payload.dropoff_lat,
        dropoff_lng=payload.dropoff_lng,
        distance_km=payload.distance_km,
        duration_minutes=payload.duration_minutes,
        vehicle_type_id=payload.vehicle_type_id,
        surge_override=Decimal("1"),
    )
    grand_total = _d(str(fare["grand_total"]))

    # Booking-phase policy — evaluated against the BOOKING EMPLOYEE: their
    # allowance is the payment source (the guest has no membership).
    is_deferred = bool(payload.scheduled_time)
    pickup_time = payload.scheduled_time or datetime.now(timezone.utc)
    policy_result = await evaluate_policy_for_ride(
        corporate_account_id=company_id,
        rider_id=booker_member["user_id"],
        estimated_fare=grand_total,
        ride_type=payload.vehicle_type_id or "standard",
        pickup_time=pickup_time,
        policy_override=booker_member.get("policy_override", False),
    )
    if not policy_result.passed:
        raise HTTPException(
            status_code=403,
            detail={"reason": "policy_violation", "failed_rules": policy_result.failed_rules},
        )

    # Pre-auth buffer check (skip for unlimited) — mirrors the work-profile
    # branch in routes/rides.py: unless the master wallet may back-stop the
    # ride, require 1.5x headroom so the completion settlement can't strand.
    _allowance = policy_result.allowance or {}
    _policy = policy_result.policy or {}
    if _allowance.get("type") != "unlimited":
        _remaining = _d(str(_allowance.get("amount") or 0)) - max(_d(str(_allowance.get("used") or 0)), _d("0"))
        _master_permitted = _policy.get("allowed_payment_source", "both") in ("master_only", "both")
        if _remaining < _round2(grand_total * _d("1.5")) and not _master_permitted:
            raise HTTPException(status_code=403, detail={"reason": "allowance_low"})

    pickup_otp_plain = generate_pickup_otp()
    ride = Ride(
        rider_id=guest_user["id"],
        vehicle_type_id=payload.vehicle_type_id,
        pickup_address=payload.pickup_address,
        pickup_lat=payload.pickup_lat,
        pickup_lng=payload.pickup_lng,
        dropoff_address=payload.dropoff_address,
        dropoff_lat=payload.dropoff_lat,
        dropoff_lng=payload.dropoff_lng,
        distance_km=round(payload.distance_km, 2),
        duration_minutes=payload.duration_minutes,
        base_fare=float(fare["base_fare"]),
        distance_fare=float(fare["distance_fare"]),
        time_fare=float(fare["time_fare"]),
        booking_fee=float(fare["booking_fee"]),
        surge_multiplier=1.0,
        total_fare=float(fare["subtotal"]),
        is_scheduled=is_deferred,
        rider_notes=payload.rider_notes,
        scheduled_time=payload.scheduled_time,
        driver_earnings=float(fare["driver_earnings"]),
        admin_earnings=float(fare["admin_earnings"]),
        payment_method="company_allowance",
        status=_STATUS_SCHEDULED if is_deferred else _STATUS_SEARCHING,
        pickup_otp=pickup_otp_plain,
        ride_requested_at=datetime.now(timezone.utc),
    )

    ride_data = ride.dict()
    ride_data["corporate_account_id"] = company_id
    ride_data["corporate_member_id"] = booker_member["id"]
    ride_data["guest_booking"] = True
    ride_data["planned_distance_km"] = round(payload.distance_km, 2)
    ride_data["area_fees_breakdown"] = fare.get("area_fees", [])
    ride_data["area_fees_total"] = fare.get("area_fees_total", 0)
    ride_data["tax_amount"] = fare.get("tax_amount", 0)
    ride_data["tax_breakdown"] = fare.get("tax_breakdown", {})
    ride_data["grand_total"] = fare["grand_total"]
    if fare.get("service_area_id"):
        ride_data["service_area_id"] = fare["service_area_id"]

    tracking_url: Optional[str] = None
    if not is_deferred:
        # Immediate rides carry the public tracking link from the first SMS.
        # Scheduled rides mint theirs at driver-accept instead — the token
        # expires 24h after mint (routes/rides.py track resolver), which
        # would dead-link a ride scheduled for tomorrow.
        ride_data["shared_trip_token"] = secrets.token_urlsafe(32)
        ride_data["shared_trip_token_created_at"] = datetime.now(timezone.utc).isoformat()

    # Shared insert helper (ride_code allocation + idempotency semantics).
    # Lazy import: routes.rides imports many services at module load; the
    # reverse edge stays function-local to avoid an import cycle.
    try:
        from ..routes.rides import _insert_ride_with_code, _prep_and_dispatch
    except ImportError:
        from routes.rides import _insert_ride_with_code, _prep_and_dispatch  # type: ignore

    try:
        fresh_ride, _reused = await _insert_ride_with_code(ride_data, guest_user["id"])
    except HTTPException as e:
        if e.status_code == 409:
            # rides_one_active_per_rider: this customer phone already has an
            # active ride (possibly booked by another company). Reword for
            # the portal — "you" is the booker here, not the passenger.
            raise HTTPException(
                status_code=409,
                detail="This customer already has an active ride.",
            ) from e
        raise

    if not is_deferred:
        spawn(
            _prep_and_dispatch(
                fresh_ride.get("id") or ride_data["id"],
                fresh_ride,
                pickup_lat=payload.pickup_lat,
                pickup_lng=payload.pickup_lng,
                stops=[],
                dispatch=True,
            )
        )
        token = ride_data.get("shared_trip_token")
        if token:
            try:
                from ..core.config import settings as _settings
            except ImportError:
                from core.config import settings as _settings  # type: ignore
            tracking_url = f"{_settings.TRACKING_BASE_URL}/{token}"

    # Customer notification: SMS with pickup OTP + tracking link for guests,
    # a corporate_ride_booked push for existing app users. Off the response
    # path — the portal shouldn't wait on Twilio.
    try:
        from ..services.guest_notification_service import notify_guest_booking_created
    except ImportError:
        from services.guest_notification_service import notify_guest_booking_created  # type: ignore
    _notify_ride = {**ride_data, **(fresh_ride or {})}
    spawn(notify_guest_booking_created(_notify_ride, guest_user, customer_has_app, tracking_url))

    logger.info(
        "guest booking created: ride=%s company=%s booker_member=%s guest_user=%s scheduled=%s",
        fresh_ride.get("id") or ride_data["id"],
        company_id,
        booker_member["id"],
        guest_user["id"],
        is_deferred,
    )
    return {
        "ride": fresh_ride,
        "tracking_url": tracking_url,
        "customer_has_app": customer_has_app,
        "guest_user": guest_user,
    }
