"""Company guest-booking endpoints (`/company/{company_id}/bookings*`).

Consumed by the company portal: employees book rides on behalf of customers,
watch live statuses, and cancel pre-trip. Guards are the same org-scoped
dependencies as routes/corporate_company.py — any active member may book and
sees their own bookings; owner/admin roles see the whole company's.

The pickup OTP is deliberately NEVER returned to the booker: it goes to the
customer by SMS only, so a desk employee cannot proxy-start the trip
(meter-start fraud). The tracking URL is safe to share — the public resolver
is PII-scrubbed and expires 24h after mint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

try:
    from .. import db_supabase
    from ..dependencies.company_guard import require_company_member
    from ..features import compute_fare_estimate
    from ..services.company_booking_service import create_company_guest_booking
    from ..validators import validate_phone
except ImportError:
    import db_supabase  # type: ignore
    from dependencies.company_guard import require_company_member  # type: ignore
    from features import compute_fare_estimate  # type: ignore
    from services.company_booking_service import create_company_guest_booking  # type: ignore
    from validators import validate_phone  # type: ignore

router = APIRouter(prefix="/company/{company_id}", tags=["Corporate Company Bookings"])

_ADMIN_ROLES = {"owner", "admin"}
_ACTIVE_RIDE_STATUSES = ["searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]


class CompanyGuestBookingRequest(BaseModel):
    customer_name: str = Field(..., min_length=1, max_length=80)
    customer_phone: str = Field(..., min_length=7, max_length=20)
    pickup_address: str = Field(..., min_length=3, max_length=500)
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str = Field(..., min_length=3, max_length=500)
    dropoff_lat: float
    dropoff_lng: float
    distance_km: float = Field(..., gt=0, le=500)
    duration_minutes: int = Field(..., gt=0, le=600)
    vehicle_type_id: str
    scheduled_time: Optional[datetime] = None
    rider_notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("customer_phone")
    @classmethod
    def _phone_e164(cls, v: str) -> str:
        _, normalized = validate_phone(v.strip())
        return normalized or v.strip()


def _booking_row(ride: Dict[str, Any], member: Optional[Dict[str, Any]], guest: Optional[Dict[str, Any]]) -> dict:
    """Portal-facing projection of a company ride. IDs + first names only —
    the booker sees what they entered at booking; no OTP, no full guest row."""
    return {
        "ride_id": ride.get("id"),
        "ride_code": ride.get("ride_code"),
        "status": ride.get("status"),
        "guest_booking": bool(ride.get("guest_booking")),
        "customer_first_name": (guest or {}).get("first_name"),
        "booked_by_member_id": ride.get("corporate_member_id"),
        "booked_by_name": (member or {}).get("invited_email") or (member or {}).get("display_name"),
        "section_id": (member or {}).get("section_id"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "scheduled_time": ride.get("scheduled_time"),
        "created_at": ride.get("created_at"),
        "grand_total": ride.get("grand_total"),
        "payment_status": ride.get("payment_status"),
        "cancelled_reason": ride.get("cancelled_reason"),
    }


@router.post("/bookings")
async def create_booking(
    body: CompanyGuestBookingRequest,
    ctx: dict = Depends(require_company_member),
):
    """Book a ride for a customer on the company's dime (booker's allowance)."""
    result = await create_company_guest_booking(ctx["company_id"], ctx["member"], body)
    ride = result["ride"]
    return {
        "success": True,
        "booking": _booking_row(ride, ctx["member"], result.get("guest_user")),
        "tracking_url": result.get("tracking_url"),
        "customer_has_app": result.get("customer_has_app"),
    }


@router.get("/bookings")
async def list_bookings(
    ctx: dict = Depends(require_company_member),
    status: Optional[str] = Query(default=None),
    member_id: Optional[str] = Query(default=None),
    section_id: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None, alias="from"),
    date_to: Optional[str] = Query(default=None, alias="to"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Company bookings, newest first. Members see their own bookings;
    owner/admin see everyone's (optionally filtered by member or section)."""
    filters: Dict[str, Any] = {"corporate_account_id": ctx["company_id"]}

    if ctx["role"] not in _ADMIN_ROLES:
        filters["corporate_member_id"] = ctx["member_id"]
    elif member_id:
        filters["corporate_member_id"] = member_id

    if status:
        filters["status"] = status
    if date_from:
        filters["created_at"] = {"$gte": date_from}
    if date_to:
        filters.setdefault("created_at", {})
        if isinstance(filters["created_at"], dict):
            filters["created_at"]["$lte"] = date_to

    rides = (
        await db_supabase.get_rows(
            "rides",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=skip,
        )
        or []
    )

    # Batch-join booker members and guest users (no N+1).
    member_ids = sorted({r["corporate_member_id"] for r in rides if r.get("corporate_member_id")})
    rider_ids = sorted({r["rider_id"] for r in rides if r.get("rider_id")})
    members_by_id: Dict[str, dict] = {}
    if member_ids:
        member_rows = (
            await db_supabase.get_rows(
                "corporate_members",
                {"id": {"$in": member_ids}},
                limit=len(member_ids),
            )
            or []
        )
        members_by_id = {m["id"]: m for m in member_rows}
    guests_by_id: Dict[str, dict] = {}
    if rider_ids:
        guest_rows = (
            await db_supabase.get_rows(
                "users",
                {"id": {"$in": rider_ids}},
                columns="id,first_name,is_guest",
                limit=len(rider_ids),
            )
            or []
        )
        guests_by_id = {u["id"]: u for u in guest_rows}

    rows: List[dict] = []
    for r in rides:
        member = members_by_id.get(r.get("corporate_member_id") or "")
        if section_id and (member or {}).get("section_id") != section_id:
            continue
        rows.append(_booking_row(r, member, guests_by_id.get(r.get("rider_id") or "")))

    return {"bookings": rows, "skip": skip, "limit": limit}


@router.post("/bookings/{ride_id}/cancel")
async def cancel_booking(
    ride_id: str,
    request: Request,
    ctx: dict = Depends(require_company_member),
):
    """Cancel a company booking pre-trip. Members may cancel their own
    bookings; owner/admin any of the company's. Delegates to the canonical
    rider-cancel flow (atomic pre-trip claim, driver release, WS events)
    with the guest as the acting rider."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("corporate_account_id") != ctx["company_id"] or not ride.get("guest_booking"):
        raise HTTPException(status_code=404, detail="Booking not found")
    if ctx["role"] not in _ADMIN_ROLES and ride.get("corporate_member_id") != ctx["member_id"]:
        raise HTTPException(status_code=403, detail="You can only cancel your own bookings")

    guest_user = await db_supabase.get_user_by_id(ride["rider_id"])
    if not guest_user:
        raise HTTPException(status_code=404, detail="Booking customer not found")

    try:
        from .rides import cancel_ride_rider
    except ImportError:
        from routes.rides import cancel_ride_rider  # type: ignore

    result = await cancel_ride_rider(
        ride_id,
        reason="Cancelled by company",
        request=request,
        current_user=guest_user,
    )

    # Tell the customer their ride is off (SMS for guests; app-holders get
    # the standard cancel push from the flow above).
    try:
        from ..services.guest_notification_service import notify_guest_cancelled
        from ..utils.background import spawn
    except ImportError:
        from services.guest_notification_service import notify_guest_cancelled  # type: ignore
        from utils.background import spawn  # type: ignore
    spawn(notify_guest_cancelled(dict(ride)))

    return result


@router.get("/bookings/fare-estimate")
async def booking_fare_estimate(
    ctx: dict = Depends(require_company_member),
    pickup_lat: float = Query(...),
    pickup_lng: float = Query(...),
    dropoff_lat: float = Query(...),
    dropoff_lng: float = Query(...),
    distance_km: float = Query(..., gt=0, le=500),
    duration_minutes: int = Query(..., gt=0, le=600),
    vehicle_type_id: str = Query(...),
):
    """Fare preview for the portal book page — same canonical pipeline as the
    booking itself, surge pinned to 1.0 (corporate rides never surge)."""
    from decimal import Decimal

    return await compute_fare_estimate(
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        dropoff_lat=dropoff_lat,
        dropoff_lng=dropoff_lng,
        distance_km=distance_km,
        duration_minutes=duration_minutes,
        vehicle_type_id=vehicle_type_id,
        surge_override=Decimal("1"),
    )


# Re-exported for server.py mounting alongside corporate_company_router.
corporate_company_bookings_router = router
