"""Company guest-booking endpoints (`/company/{company_id}/bookings*`).

Consumed by the company portal: employees book rides on behalf of customers,
watch live statuses, and cancel pre-trip. Guards are the same org-scoped
dependencies as routes/corporate_company.py — any active member may book and
sees their own bookings; owner/admin roles see the whole company's.

The pickup OTP is deliberately NEVER returned to the booker: it goes to the
customer by SMS only, so a desk employee cannot proxy-start the trip
(meter-start fraud). The tracking URL is safe to share — the public resolver
is PII-scrubbed and expires 24h after mint.

NOTE: no `from __future__ import annotations` — string annotations resolved
through slowapi's @company_booking_limit wrapper globals silently downgraded
the POST /bookings pydantic body to a QUERY param, 422ing every real booking
(same failure mode documented in corporate_signup.py; auth.py precedent).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

try:
    from ..utils.metrics import inc as _metric_inc
    from ..utils.rate_limiter import company_booking_limit
except ImportError:
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.rate_limiter import company_booking_limit  # type: ignore
from pydantic import BaseModel, Field, field_validator, model_validator

try:
    from .. import db_supabase
    from ..dependencies.company_guard import require_company_admin, require_company_member
    from ..features import compute_fare_estimate
    from ..services.company_booking_service import create_company_guest_booking
    from ..utils.error_handling import db_error_text
    from ..validators import validate_phone
except ImportError:
    import db_supabase  # type: ignore
    from dependencies.company_guard import require_company_admin, require_company_member  # type: ignore
    from features import compute_fare_estimate  # type: ignore
    from services.company_booking_service import create_company_guest_booking  # type: ignore
    from utils.error_handling import db_error_text  # type: ignore
    from validators import validate_phone  # type: ignore

router = APIRouter(prefix="/company/{company_id}", tags=["Corporate Company Bookings"])

_ADMIN_ROLES = {"owner", "admin"}
_ACTIVE_RIDE_STATUSES = ["searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]


class CompanyGuestBookingRequest(BaseModel):
    # GUEST mode: name+phone required. ON-BEHALF mode (M5.6): for_member_id
    # instead — the ride goes to a staff member's own app account.
    customer_name: Optional[str] = Field(None, min_length=1, max_length=80)
    customer_phone: Optional[str] = Field(None, min_length=7, max_length=20)
    for_member_id: Optional[str] = Field(None, max_length=64)
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
    def _phone_e164(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        _, normalized = validate_phone(v.strip())
        return normalized or v.strip()

    @model_validator(mode="after")
    def _one_target(self):
        if self.for_member_id:
            return self
        if not (self.customer_name and self.customer_phone):
            raise ValueError("provide customer_name + customer_phone, or for_member_id")
        return self


def _booking_row(ride: Dict[str, Any], member: Optional[Dict[str, Any]], guest: Optional[Dict[str, Any]]) -> dict:
    """Portal-facing projection of a company ride. IDs + first names only —
    the booker sees what they entered at booking; no OTP, no full guest row."""
    return {
        "ride_id": ride.get("id"),
        "ride_code": ride.get("ride_code"),
        "status": ride.get("status"),
        "guest_booking": bool(ride.get("guest_booking")),
        "customer_first_name": (guest or {}).get("first_name"),
        # Payer (target member for on-behalf; booker for guest bookings).
        "booked_for_member_id": ride.get("corporate_member_id"),
        # Who actually created it (migration 230; falls back for old rows).
        "booked_by_member_id": ride.get("corporate_booked_by_member_id") or ride.get("corporate_member_id"),
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


async def _require_company_active(company_id: str) -> None:
    """Typed 403 for non-active companies (M2.6): a pending_verification /
    suspended / closed company cannot book. The portal reads the `code` to
    route the user to the verification page instead of a raw error."""
    company = await db_supabase.get_corporate_account_by_id(company_id) or {}
    if (company.get("status") or "").lower() != "active":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "company_not_active",
                "message": "This company hasn't completed verification yet. Bookings unlock once it's approved.",
            },
        )


@router.post("/bookings")
@company_booking_limit
async def create_booking(
    request: Request,
    body: CompanyGuestBookingRequest,
    ctx: dict = Depends(require_company_member),
):
    """Book a ride for a customer (guest) or a staff member (on-behalf, M5.6)."""
    await _require_company_active(ctx["company_id"])

    for_member = None
    if body.for_member_id and body.for_member_id != ctx["member_id"]:
        for_member = await db_supabase.get_corporate_member_by_id(body.for_member_id)
        if not for_member or for_member.get("company_id") != ctx["company_id"] or for_member.get("status") != "active":
            raise HTTPException(status_code=404, detail="Member not found")
        # Authz (Q12): admins book for anyone; a section HEAD only for members
        # of their own section; plain members only for themselves.
        if ctx["role"] not in _ADMIN_ROLES:
            target_section = for_member.get("section_id")
            is_their_head = False
            if target_section:
                sections = await db_supabase.get_rows(
                    "corporate_sections",
                    {"id": target_section, "company_id": ctx["company_id"]},
                    limit=1,
                )
                is_their_head = bool(sections) and sections[0].get("head_member_id") == ctx["member_id"]
            if not is_their_head:
                raise HTTPException(
                    status_code=403,
                    detail="Only company admins or the member's section head can book on their behalf.",
                )
    elif body.for_member_id:
        # Booking for yourself on-behalf = a normal member booking.
        for_member = ctx["member"]

    result = await create_company_guest_booking(ctx["company_id"], ctx["member"], body, for_member=for_member)
    ride = result["ride"]
    _metric_inc(
        "spinr_corporate_guest_booking_total",
        {"scheduled": "true" if ride.get("is_scheduled") else "false"},
    )
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

    # Q16: a member sees rides they PAID for (booked for them / self-booked)
    # AND rides they BOOKED for others (section heads). The second set is
    # fetched separately below and merged — the db helper has no OR filter.
    own_booked_extra: list = []
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

    if ctx["role"] not in _ADMIN_ROLES:
        booked_filters = {k: v for k, v in filters.items() if k != "corporate_member_id"}
        booked_filters["corporate_booked_by_member_id"] = ctx["member_id"]
        own_booked_extra = (
            await db_supabase.get_rows(
                "rides",
                booked_filters,
                order="created_at",
                desc=True,
                limit=limit,
                offset=skip,
            )
            or []
        )
        seen_ids = {r.get("id") for r in rides}
        rides = rides + [r for r in own_booked_extra if r.get("id") not in seen_ids]
        rides.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        rides = rides[:limit]

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
    is_portal_booking = bool(ride and (ride.get("guest_booking") or ride.get("corporate_booked_by_member_id")))
    if not ride or ride.get("corporate_account_id") != ctx["company_id"] or not is_portal_booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    # Cancel rights (M5.6): admins, the paying member, or the booker.
    allowed = ctx["role"] in _ADMIN_ROLES or ctx["member_id"] in (
        ride.get("corporate_member_id"),
        ride.get("corporate_booked_by_member_id"),
    )
    if not allowed:
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


# ── Sections (company departments — grouping + reporting, migration 206) ──


class SectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=300)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # min_length=1 accepts a spaces-only string; the handler strips after
        # validation, so without this a "   " name would persist as "" and
        # squat the company's unique empty name. Reject blank-after-trim (422).
        v = v.strip()
        if not v:
            raise ValueError("Section name cannot be blank")
        return v


class SectionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=300)
    status: Optional[str] = Field(default=None, pattern="^(active|archived)$")
    # M5.7 (Q11): the section's single department head — a corporate_members
    # id of any role. Empty string clears the head.
    head_member_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Section name cannot be blank")
        return v


@router.get("/sections")
async def list_sections(ctx: dict = Depends(require_company_member)):
    """Sections with live member counts. Member-readable — the bookings list
    filter needs them; only owner/admin can mutate."""
    sections = (
        await db_supabase.get_rows(
            "corporate_sections",
            {"company_id": ctx["company_id"]},
            order="created_at",
            limit=200,
        )
        or []
    )
    members = (
        await db_supabase.get_rows(
            "corporate_members",
            {"company_id": ctx["company_id"], "status": "active"},
            columns="id,section_id",
            limit=1000,
        )
        or []
    )
    counts: Dict[str, int] = {}
    for m in members:
        sid = m.get("section_id")
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    return {
        "sections": [{**s, "member_count": counts.get(s["id"], 0)} for s in sections],
    }


@router.post("/sections")
async def create_section(body: SectionCreate, ctx: dict = Depends(require_company_admin)):
    from datetime import datetime, timezone
    from uuid import uuid4

    row = {
        "id": str(uuid4()),
        "company_id": ctx["company_id"],
        "name": body.name.strip(),
        "description": (body.description or "").strip() or None,
        "status": "active",
        "created_by": ctx["user"]["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        inserted = await db_supabase.insert_one("corporate_sections", row)
    except Exception as e:
        msg = db_error_text(e)
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            raise HTTPException(status_code=409, detail="A section with this name already exists.") from e
        raise
    return inserted or row


@router.patch("/sections/{section_id}")
async def update_section(
    section_id: str,
    body: SectionUpdate,
    ctx: dict = Depends(require_company_admin),
):
    existing = (
        await db_supabase.get_rows("corporate_sections", {"id": section_id, "company_id": ctx["company_id"]}, limit=1)
        or []
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Section not found")
    patch = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "name" in patch:
        patch["name"] = patch["name"].strip()
    if "head_member_id" in patch:
        if patch["head_member_id"] == "":
            patch["head_member_id"] = None  # explicit clear
        else:
            head = await db_supabase.get_corporate_member_by_id(patch["head_member_id"])
            if not head or head.get("company_id") != ctx["company_id"] or head.get("status") != "active":
                raise HTTPException(status_code=404, detail="Member not found")
    if not patch:
        return existing[0]
    try:
        updated = await db_supabase.update_one("corporate_sections", {"id": section_id}, patch)
    except Exception as e:
        msg = db_error_text(e)
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            raise HTTPException(status_code=409, detail="A section with this name already exists.") from e
        raise
    return updated or {**existing[0], **patch}


@router.delete("/sections/{section_id}")
async def archive_section(section_id: str, ctx: dict = Depends(require_company_admin)):
    """Archive (never hard-delete): history keeps its section attribution;
    members' section_id stays until reassigned (the FK only nulls on a true
    row delete, which we don't do)."""
    existing = (
        await db_supabase.get_rows("corporate_sections", {"id": section_id, "company_id": ctx["company_id"]}, limit=1)
        or []
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Section not found")
    updated = await db_supabase.update_one("corporate_sections", {"id": section_id}, {"status": "archived"})
    return updated or {**existing[0], "status": "archived"}


# Re-exported for server.py mounting alongside corporate_company_router.
corporate_company_bookings_router = router
