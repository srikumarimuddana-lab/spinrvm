"""
features.py – Extended feature endpoints for Spinr.
Includes: Support Tickets, FAQs, Surge Pricing, Scheduled Rides,
Multi-stop Rides, Safety Toolkit, Push Notifications.
"""

import asyncio
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

# Money helpers — keep tax / fee arithmetic in Decimal so 5% GST + 6% PST
# on values like $47.83 stays bit-exact and matches the receipt.
_TAX_TWO_PLACES = Decimal("0.01")


def _money(v: Any) -> Decimal:
    """Coerce DB / wire numerics to Decimal via str() (avoids float drift)."""
    return Decimal(str(v or 0))


def _q2(v: Decimal) -> Decimal:
    """Quantize to 2 dp HALF_UP."""
    return v.quantize(_TAX_TWO_PLACES, rounding=ROUND_HALF_UP)


try:
    from . import db_supabase
    from .dependencies import get_admin_user, get_current_user
    from .geo_utils import get_service_area_polygon
    from .models.ride_status import RideStatus
    from .services.fare_service import DEFAULT_FARE, calculate_fare
    from .services.fare_service import _d as _fare_d
    from .services.fare_service import _f as _fare_f
    from .utils.surge_engine import SURGE_CAP
except ImportError:
    import db_supabase
    from dependencies import get_admin_user, get_current_user
    from geo_utils import get_service_area_polygon
    from models.ride_status import RideStatus
    from services.fare_service import DEFAULT_FARE, calculate_fare
    from services.fare_service import _d as _fare_d
    from services.fare_service import _f as _fare_f
    from utils.surge_engine import SURGE_CAP

# Legacy alias for call sites that still reference the pre-refactor ``db`` module.
db = db_supabase

# ============ Routers ============
support_router = APIRouter(tags=["Support"])
# CS-001: router-level guard — every endpoint on this router requires a valid
# admin JWT. Previously bare (no auth), exposing surge, FAQ, ticket, and
# notification mutations to unauthenticated callers.
admin_support_router = APIRouter(tags=["Admin Support"], dependencies=[Depends(get_admin_user)])


# ============ Geometry Helpers ============


def point_in_polygon(lat: float, lng: float, polygon: List[Dict[str, float]]) -> bool:
    """Ray-casting algorithm to check if a point is inside a polygon.
    polygon is a list of dicts with 'lat' and 'lng' keys.
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i].get("lat", 0), polygon[i].get("lng", 0)
        yj, xj = polygon[j].get("lat", 0), polygon[j].get("lng", 0)
        # Guard: skip horizontal edges where yj == yi to avoid division by zero
        if yi != yj and ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


async def calculate_airport_fee(
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    stops: Optional[List[Dict[str, Any]]] = None,
    *,
    _all_areas: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Check if pickup, dropoff, or any stop falls in an airport zone.

    For normal rides outside airport zones, returns airport_fee=0 and no
    airport_zone_name — these fields should be omitted from invoices/apps.
    For multi-stop rides, each stop is also checked.

    ``_all_areas`` lets in-process callers pass a pre-fetched active
    service_areas list; this function then filters locally instead of
    issuing its own query. Omit it for standalone use.

    Returns {'airport_fee': float, 'airport_zone_name': str | None,
    'is_pickup': bool, 'is_dropoff': bool, 'is_stop': bool}
    """

    # Airport zone model:
    #   service_areas is a parent/child tree. A "service area" is a top-level
    #   row (e.g. Regina) with parent_service_area_id IS NULL. An "airport
    #   zone" is a CHILD row (parent_service_area_id set) with is_airport=true,
    #   its own polygon (just the airport property), and its own airport_fee.
    #   The parent's airport_fee column is functionally dead and must be ignored
    #   — surcharging by parent caused every Regina ride to look airport-bound.
    # Only sub-regions matched + active + flagged + fee > 0 + valid polygon
    # below count as airport zones.
    def _is_real_airport_zone(a: Dict[str, Any]) -> bool:
        return bool(
            a.get("is_airport")
            and a.get("is_active", True) is not False
            and a.get("parent_service_area_id")  # must be a sub-region
        )

    if _all_areas is not None:
        areas = [a for a in _all_areas if _is_real_airport_zone(a)]
    else:
        raw = await db_supabase.get_rows(
            "service_areas",
            {"is_airport": True, "is_active": True},
            limit=50,
        )
        areas = [a for a in raw if a.get("parent_service_area_id")]
    result: Dict[str, Any] = {
        "airport_fee": 0.0,
        "airport_zone_name": None,
        "is_pickup": False,
        "is_dropoff": False,
        "is_stop": False,
    }

    # One-line trace of every invocation so "I removed the airport zone but
    # the surcharge still shows" reports are debuggable: confirms how many
    # is_airport=true rows the function considered. If this logs "areas=0"
    # but a ride still shows airport_fee, the value is frozen on the ride
    # row from before the removal — historical rides don't get retro-fixed.
    logger.info(
        "[AIRPORT_FEE] check pickup=(%.5f,%.5f) dropoff=(%.5f,%.5f) areas=%d",
        pickup_lat,
        pickup_lng,
        dropoff_lat,
        dropoff_lng,
        len(areas),
    )

    for area in areas:
        polygon = get_service_area_polygon(area)
        fee = float(area.get("airport_fee", 0))
        if fee <= 0 or len(polygon) < 3:
            continue

        pickup_in = point_in_polygon(pickup_lat, pickup_lng, polygon)
        dropoff_in = point_in_polygon(dropoff_lat, dropoff_lng, polygon)

        # Check multi-stop waypoints
        stop_in = False
        if stops:
            for stop in stops:
                slat = stop.get("lat")
                slng = stop.get("lng")
                if slat and slng and point_in_polygon(slat, slng, polygon):
                    stop_in = True
                    break

        if pickup_in or dropoff_in or stop_in:
            result["airport_fee"] = fee
            result["airport_zone_name"] = area.get("name", "Airport")
            result["is_pickup"] = pickup_in
            result["is_dropoff"] = dropoff_in
            result["is_stop"] = stop_in
            # Surcharge is real money charged to the rider — log why it
            # applied so an "I went to Walmart and still got an airport
            # surcharge" report is debuggable. The most common cause when
            # neither endpoint looks like an airport is an oversized or
            # mirrored polygon saved in the admin Service Areas screen.
            lats = [p["lat"] for p in polygon]
            lngs = [p["lng"] for p in polygon]
            logger.info(
                "[AIRPORT_FEE] applied $%.2f for zone %r (id=%s): "
                "pickup_in=%s dropoff_in=%s stop_in=%s | "
                "pickup=(%.5f,%.5f) dropoff=(%.5f,%.5f) | "
                "polygon vertices=%d bbox lat=[%.5f,%.5f] lng=[%.5f,%.5f]",
                fee,
                area.get("name", "Airport"),
                area.get("id"),
                pickup_in,
                dropoff_in,
                stop_in,
                pickup_lat,
                pickup_lng,
                dropoff_lat,
                dropoff_lng,
                len(polygon),
                min(lats),
                max(lats),
                min(lngs),
                max(lngs),
            )
            break  # Use the first matching airport zone

    return result


# ============ Pydantic Models ============


class CreateTicketRequest(BaseModel):
    subject: str
    message: str
    category: str = "general"


class ReplyToTicketRequest(BaseModel):
    message: str


class CreateFaqRequest(BaseModel):
    question: str
    answer: str
    category: str = "general"
    sort_order: int = 0
    # Required, explicit choice — a silent 'both' default is how driver-only
    # FAQs leaked into the rider app. None/[] service_area_ids = global.
    audience: str = Field(..., pattern="^(rider|driver|both)$")
    service_area_ids: Optional[List[str]] = None


class UpdateFaqRequest(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    category: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    audience: Optional[str] = Field(default=None, pattern="^(rider|driver|both)$")
    service_area_ids: Optional[List[str]] = None


class ScheduleRideRequest(BaseModel):
    rider_id: str
    vehicle_type_id: str
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    distance_km: float
    duration_minutes: int
    scheduled_time: str  # ISO 8601 datetime string
    stops: List[Dict[str, Any]] = []


class AddStopRequest(BaseModel):
    address: str
    lat: float
    lng: float
    order: int = 0


class ShareTripRequest(BaseModel):
    contact_name: str
    contact_phone: str


class UpdateSurgeRequest(BaseModel):
    surge_active: Optional[bool] = None
    surge_multiplier: Optional[float] = None


class RegisterFcmTokenRequest(BaseModel):
    token: str


class SendNotificationRequest(BaseModel):
    user_id: str
    title: str
    body: str
    data: Dict[str, str] = {}


# ============ Airport Fee Check (User/App facing) ============


@support_router.get("/rides/airport-fee")
async def check_airport_fee(
    pickup_lat: float = Query(...),
    pickup_lng: float = Query(...),
    dropoff_lat: float = Query(...),
    dropoff_lng: float = Query(...),
):
    """Check if a ride involves an airport zone and return the fee.
    Call this before ride request to show the airport surcharge in the fare estimate.
    """
    result = await calculate_airport_fee(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
    return result


# ============ Support Tickets (User-facing) ============


@support_router.post("/tickets")
async def create_ticket(req: CreateTicketRequest, current_user: dict = Depends(get_current_user)):
    """Create a new support ticket."""
    ticket = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "subject": req.subject,
        "message": req.message,
        "category": req.category,
        "status": "open",
        "replies": [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await db_supabase.insert_one("support_tickets", ticket)
    return ticket


class SafetyReportRequest(BaseModel):
    description: str


@support_router.post("/tickets/safety-report")
async def create_safety_report(req: SafetyReportRequest, current_user: dict = Depends(get_current_user)):
    """Create a new safety report ticket (high priority)."""
    ticket = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "subject": "SAFETY INCIDENT REPORT",
        "message": req.description,
        "category": "safety",
        "status": "open",
        "priority": "critical",
        "replies": [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await db_supabase.insert_one("support_tickets", ticket)
    return ticket


@support_router.get("/tickets")
async def get_user_tickets(current_user: dict = Depends(get_current_user)):
    """Get all tickets for the authenticated user."""
    tickets = await db_supabase.get_rows(
        "support_tickets", {"user_id": current_user["id"]}, limit=100, order="created_at", desc=True
    )
    return tickets


@support_router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific ticket by ID."""
    ticket = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("support_tickets", {"id": ticket_id}, limit=1)
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this ticket")
    return ticket


# ============ FAQs (User-facing) ============


@support_router.get("/faqs")
async def get_faqs(
    category: Optional[str] = None,
    audience: Optional[str] = Query(None, pattern="^(rider|driver)$"),
    service_area_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
):
    """Get active FAQs, optionally filtered by category, audience and location.

    This is the live GET /faqs handler (registered before routes/faqs.py). It
    MUST filter by audience — without it, driver-only FAQs surface in the rider
    app. Location (service_area_id or lat+lng) scopes to global FAQs plus those
    tagged for the caller's area or an ancestor area.
    """
    query: Dict[str, Any] = {"is_active": True}
    if category:
        query["category"] = category
    if audience:
        query["audience"] = {"$in": ["both", audience]}
    # Exclude the semantic-search embedding vector from the public payload.
    faqs = (
        await db_supabase.get_rows(
            "faqs",
            query,
            limit=200,
            order="sort_order",
            desc=False,
            columns="id,question,answer,category,sort_order,is_active,created_at,updated_at,audience,service_area_ids",
        )
        or []
    )

    try:
        from routes.fares import resolve_area_scope, resolve_service_area_for_point
    except ImportError:
        from .routes.fares import resolve_area_scope, resolve_service_area_for_point
    area_id = service_area_id
    if not area_id and lat is not None and lng is not None:
        try:
            area = await resolve_service_area_for_point(float(lat), float(lng))
            area_id = area.get("id") if area else None
        except Exception:
            logger.error("public faq service-area resolve failed", exc_info=True)
    scope = await resolve_area_scope(area_id)
    return [f for f in faqs if not f.get("service_area_ids") or (set(f["service_area_ids"]) & scope)]


# ============ Admin: Support Tickets ============


@admin_support_router.get("/tickets")
async def admin_get_tickets(status: Optional[str] = None):
    """Get all support tickets (admin)."""
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    tickets = await db_supabase.get_rows("support_tickets", query, limit=500, order="created_at", desc=True)
    return tickets


@admin_support_router.post("/tickets/{ticket_id}/reply")
async def admin_reply_ticket(ticket_id: str, req: ReplyToTicketRequest):
    """Reply to a support ticket."""
    ticket = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("support_tickets", {"id": ticket_id}, limit=1)
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    reply = {
        "message": req.message,
        "author": "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    replies = ticket.get("replies", [])
    replies.append(reply)

    await db_supabase.update_one(
        "support_tickets",
        {"id": ticket_id},
        {
            "replies": replies,
            "status": RideStatus.IN_PROGRESS,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    return {"status": "replied", "reply": reply}


@admin_support_router.post("/tickets/{ticket_id}/close")
async def admin_close_ticket(ticket_id: str):
    """Close a support ticket."""
    result = await db_supabase.update_one(
        "support_tickets", {"id": ticket_id}, {"status": "closed", "updated_at": datetime.now(timezone.utc)}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"status": "closed"}


# ============ Admin: FAQs ============


@admin_support_router.get("/faqs")
async def admin_get_faqs():
    """Get all FAQs (including inactive) for admin."""
    # Exclude the semantic-search embedding vector from the admin list payload.
    faqs = await db_supabase.get_rows(
        "faqs",
        None,
        limit=500,
        order="sort_order",
        desc=False,
        columns="id,question,answer,category,audience,service_area_ids,sort_order,is_active,created_at,updated_at",
    )
    return faqs


@admin_support_router.post("/faqs")
async def admin_create_faq(req: CreateFaqRequest):
    """Create a new FAQ."""
    faq = {
        "id": str(uuid.uuid4()),
        "question": req.question,
        "answer": req.answer,
        "category": req.category,
        "sort_order": req.sort_order,
        "audience": req.audience,
        "service_area_ids": req.service_area_ids,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await db_supabase.insert_one("faqs", faq)
    return faq


@admin_support_router.put("/faqs/{faq_id}")
async def admin_update_faq(faq_id: str, req: UpdateFaqRequest):
    """Update an existing FAQ."""
    update_data: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    if req.question is not None:
        update_data["question"] = req.question
    if req.answer is not None:
        update_data["answer"] = req.answer
    if req.category is not None:
        update_data["category"] = req.category
    if req.sort_order is not None:
        update_data["sort_order"] = req.sort_order
    if req.is_active is not None:
        update_data["is_active"] = req.is_active
    if req.audience is not None:
        update_data["audience"] = req.audience
    if "service_area_ids" in req.model_fields_set:
        update_data["service_area_ids"] = req.service_area_ids

    # Editing the question/answer invalidates any stored semantic embedding —
    # clear it so search re-embeds from the new text (stale vectors would keep
    # matching the old wording).
    if req.question is not None or req.answer is not None:
        update_data["embedding"] = None
        update_data["embedding_model"] = None

    await db_supabase.update_one("faqs", {"id": faq_id}, update_data)
    return (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "faqs",
            {"id": faq_id},
            limit=1,
            columns="id,question,answer,category,sort_order,is_active,created_at,updated_at,audience",
        )
    )


@admin_support_router.delete("/faqs/{faq_id}")
async def admin_delete_faq(faq_id: str):
    """Delete a FAQ."""
    await db_supabase.delete_one("faqs", {"id": faq_id})
    return {"deleted": True}


# ============ Admin: Surge Pricing ============


@admin_support_router.put("/service-areas/{area_id}/surge")
async def admin_update_surge(area_id: str, req: UpdateSurgeRequest):
    """Update surge pricing for a service area."""
    update_data: Dict[str, Any] = {}
    if req.surge_active is not None:
        update_data["surge_active"] = req.surge_active
    if req.surge_multiplier is not None:
        # This endpoint has no written-justification field and writes no
        # audit-log row, so it must not be a path to exceed the surge cap.
        # Above-cap (> 2.5x) overrides are a regulatory + reputational risk and
        # are only permitted via the canonical admin endpoint
        # (PUT /api/admin/service-areas/{id}/surge), which requires a
        # justification string and records a "surge_override_above_cap" audit
        # entry. Hard-reject anything above SURGE_CAP here.
        if req.surge_multiplier < 1.0 or req.surge_multiplier > SURGE_CAP:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"surge_multiplier must be between 1.0 and {SURGE_CAP}. "
                    "Above-cap overrides require the audited admin endpoint "
                    "with a written justification."
                ),
            )
        update_data["surge_multiplier"] = req.surge_multiplier

    # This v1 endpoint predates the per-area surge_enabled gate. Fares and the
    # surge engine now require surge_enabled, so activating surge here without
    # also setting the gate would return success yet leave every ride priced at
    # 1.0x. Mirror the activation intent onto surge_enabled: turning surge on
    # (active, or a >1.0 multiplier) enables the gate; an explicit
    # surge_active=false disables it so this endpoint can also turn surge off.
    if req.surge_active is True or (req.surge_multiplier is not None and req.surge_multiplier > 1.0):
        update_data["surge_enabled"] = True
    if req.surge_active is False:
        update_data["surge_enabled"] = False

    if update_data:
        await db_supabase.update_one("service_areas", {"id": area_id}, update_data)

    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    return area


@admin_support_router.put("/service-areas/{area_id}/surge/auto")
async def admin_reset_surge_to_auto(area_id: str):
    """Reset surge pricing to automatic mode for a service area."""
    area = await db.find_one("service_areas", {"id": area_id})
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    # Reset-to-auto is an explicit operator intent to have surge running on
    # automatic tiers for this area, so it must also flip the surge_enabled
    # gate on. Without this, an area that was previously disabled would stay
    # gated off: the surge engine would keep skipping it and fare calc would
    # keep ignoring surge, even though this endpoint returned success.
    await db.update_one(
        "service_areas",
        {"id": area_id},
        {"$set": {"surge_source": "auto", "surge_active": True, "surge_enabled": True}},
    )
    updated = await db.find_one("service_areas", {"id": area_id})
    return updated


# ============ Admin: Area Fees (Pricing) ============
# CS-001: router-level guard — every endpoint on this router requires a valid
# admin JWT. Previously bare (no auth), exposing area-fee, tax-rate, and
# driver-area mutations to unauthenticated callers.
pricing_router = APIRouter(tags=["Pricing"], dependencies=[Depends(get_admin_user)])


class CreateAreaFeeRequest(BaseModel):
    fee_name: str
    fee_type: str = "custom"  # airport | night | toll | event | holiday | custom
    calc_mode: str = "flat"  # flat | per_km | percentage
    amount: float = 0.0
    description: Optional[str] = None
    conditions: Dict[str, Any] = {}  # e.g. {"start_hour": 23, "end_hour": 5}
    is_active: bool = True


class UpdateAreaFeeRequest(BaseModel):
    fee_name: Optional[str] = None
    fee_type: Optional[str] = None
    calc_mode: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class UpdateTaxConfigRequest(BaseModel):
    gst_enabled: Optional[bool] = None
    gst_rate: Optional[float] = None
    pst_enabled: Optional[bool] = None
    pst_rate: Optional[float] = None
    hst_enabled: Optional[bool] = None
    hst_rate: Optional[float] = None


@pricing_router.get("/areas/{area_id}/fees")
async def get_area_fees(area_id: str):
    """Get all fees for a service area."""
    fees = await db_supabase.get_rows(
        "area_fees", {"service_area_id": area_id}, limit=100, order="created_at", desc=False
    )
    return fees


@pricing_router.post("/areas/{area_id}/fees")
async def create_area_fee(area_id: str, req: CreateAreaFeeRequest):
    """Add a fee to a service area."""
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")

    valid_modes = ["flat", "per_km", "percentage"]
    if req.calc_mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"calc_mode must be one of: {valid_modes}")

    fee = {
        "id": str(uuid.uuid4()),
        "service_area_id": area_id,
        "fee_name": req.fee_name,
        "fee_type": req.fee_type,
        "calc_mode": req.calc_mode,
        "amount": req.amount,
        "description": req.description,
        "conditions": req.conditions,
        "is_active": req.is_active,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await db_supabase.insert_one("area_fees", fee)
    return fee


@pricing_router.put("/areas/{area_id}/fees/{fee_id}")
async def update_area_fee(area_id: str, fee_id: str, req: UpdateAreaFeeRequest):
    """Update an area fee."""
    update_data: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    for field in ["fee_name", "fee_type", "calc_mode", "amount", "description", "conditions", "is_active"]:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    if "calc_mode" in update_data and update_data["calc_mode"] not in ["flat", "per_km", "percentage"]:
        raise HTTPException(status_code=400, detail="calc_mode must be flat, per_km, or percentage")

    await db_supabase.update_one("area_fees", {"id": fee_id, "service_area_id": area_id}, update_data)
    return (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("area_fees", {"id": fee_id}, limit=1))


@pricing_router.delete("/areas/{area_id}/fees/{fee_id}")
async def delete_area_fee(area_id: str, fee_id: str):
    """Delete an area fee."""
    await db_supabase.delete_one("area_fees", {"id": fee_id, "service_area_id": area_id})
    return {"deleted": True}


@pricing_router.put("/areas/{area_id}/tax")
async def update_area_tax(area_id: str, req: UpdateTaxConfigRequest):
    """Update tax configuration for a service area."""
    update_data: Dict[str, Any] = {}
    for field in ["gst_enabled", "gst_rate", "pst_enabled", "pst_rate", "hst_enabled", "hst_rate"]:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    if update_data:
        await db_supabase.update_one("service_areas", {"id": area_id}, update_data)

    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    return {
        "gst_enabled": area.get("gst_enabled", True),
        "gst_rate": area.get("gst_rate", 5.0),
        "pst_enabled": area.get("pst_enabled", False),
        "pst_rate": area.get("pst_rate", 0.0),
        "hst_enabled": area.get("hst_enabled", False),
        "hst_rate": area.get("hst_rate", 0.0),
    }


@pricing_router.get("/areas/{area_id}/tax")
async def get_area_tax(area_id: str):
    """Get tax configuration for a service area."""
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    return {
        "gst_enabled": area.get("gst_enabled", True),
        "gst_rate": area.get("gst_rate", 5.0),
        "pst_enabled": area.get("pst_enabled", False),
        "pst_rate": area.get("pst_rate", 0.0),
        "hst_enabled": area.get("hst_enabled", False),
        "hst_rate": area.get("hst_rate", 0.0),
    }


@pricing_router.get("/areas/{area_id}/vehicle-pricing")
async def get_vehicle_pricing(area_id: str):
    """Get all fare configs for a service area grouped by vehicle type."""
    configs = await db_supabase.get_rows("fare_configs", {"service_area_id": area_id}, limit=50)
    vehicles = await db_supabase.get_rows("vehicle_types", None, limit=50)
    return {"fare_configs": configs, "vehicle_types": vehicles}


@pricing_router.put("/drivers/{driver_id}/area")
async def assign_driver_area(driver_id: str, service_area_id: str = Query(...)):
    """Assign a driver to a service area (restricts them to that zone)."""
    area = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("service_areas", {"id": service_area_id}, limit=1)
    )
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")

    await db_supabase.update_one("drivers", {"id": driver_id}, {"service_area_id": service_area_id})
    return {"driver_id": driver_id, "service_area_id": service_area_id, "area_name": area.get("name")}


# ============ Fee Calculation Helpers ============


async def calculate_all_fees(
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    distance_km: float,
    subtotal: float,
    ride_time_hour: Optional[int] = None,
    *,
    _all_areas: Optional[List[Dict[str, Any]]] = None,
    _matched_area: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Calculate all area fees + taxes for a ride based on pickup/dropoff location.

    ``_all_areas`` and ``_matched_area`` let in-process callers pass
    pre-computed state from an earlier service_areas fetch / polygon
    resolution so this function does not re-query or re-run the
    point-in-polygon loop. When either is None the behaviour is
    unchanged.

    Returns {'fees': [...], 'fees_total': float, 'tax_amount': float, 'tax_breakdown': {...}, 'grand_total': float}
    """
    from datetime import datetime as dt
    from datetime import timezone as _tz

    if ride_time_hour is None:
        ride_time_hour = dt.now(_tz.utc).hour

    # Find which service area the pickup is in (unless caller already resolved it)
    all_areas = _all_areas
    if all_areas is None:
        all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=100)
    matched_area = _matched_area
    if matched_area is None:
        for area in all_areas:
            polygon = get_service_area_polygon(area)
            if len(polygon) >= 3 and point_in_polygon(pickup_lat, pickup_lng, polygon):
                matched_area = area
                break

    result = {
        "fees": [],
        "fees_total": 0.0,
        "tax_amount": 0.0,
        "tax_breakdown": {},
        "service_area_id": matched_area["id"] if matched_area else None,
        "service_area_name": matched_area.get("name") if matched_area else None,
    }

    if not matched_area:
        return result

    # Get all active fees for this area
    area_fees_list = await db_supabase.get_rows(
        "area_fees", {"service_area_id": matched_area["id"], "is_active": True}, limit=50
    )

    # Pre-compute airport zone check once (reuses all_areas already fetched above).
    # Mirror the strict filter from calculate_airport_fee — a soft-deactivated
    # row (is_active=false) must not count as an airport zone, otherwise an
    # area_fees row with fee_type='airport' would still be gated to "in_airport"
    # against a ghost zone the admin thought they had removed.
    # Only sub-region rows (parent_service_area_id set) count as airport
    # zones — same rule applied in calculate_airport_fee. A top-level row
    # with is_airport=true is a misconfiguration and must not gate
    # area_fees of fee_type='airport'.
    airport_areas = [
        a
        for a in all_areas
        if a.get("is_airport") and a.get("is_active", True) is not False and a.get("parent_service_area_id")
    ]
    in_airport = False
    for ap in airport_areas:
        ap_poly = get_service_area_polygon(ap)
        if len(ap_poly) >= 3:
            if point_in_polygon(pickup_lat, pickup_lng, ap_poly) or point_in_polygon(dropoff_lat, dropoff_lng, ap_poly):
                in_airport = True
                break

    # Decimal-driven fee + tax calculation. Floats are still acceptable
    # at the function signature for backwards compatibility — coerced via
    # _money() (str path) so the binary representation can't poison
    # GST/PST values that go on the rider's receipt.
    fees_total = Decimal("0")
    fee_items = []
    distance_d = _money(distance_km)
    subtotal_d = _money(subtotal)

    for fee in area_fees_list:
        fee_type = fee.get("fee_type", "custom")
        calc_mode = fee.get("calc_mode", "flat")
        amount = _money(fee.get("amount", 0))
        conditions = fee.get("conditions", {})

        # Check conditions
        if fee_type == "night":
            start_h = conditions.get("start_hour", 23)
            end_h = conditions.get("end_hour", 5)
            if start_h > end_h:  # Crosses midnight (e.g., 23-5)
                if not (ride_time_hour >= start_h or ride_time_hour < end_h):
                    continue
            else:
                if not (start_h <= ride_time_hour < end_h):
                    continue

        if fee_type == "airport":
            if not in_airport:
                continue

        # Calculate the fee amount based on calc_mode
        if calc_mode == "flat":
            fee_value = amount
        elif calc_mode == "per_km":
            fee_value = amount * distance_d
        elif calc_mode == "percentage":
            fee_value = (amount / Decimal("100")) * subtotal_d
        else:
            fee_value = amount

        fee_value = _q2(fee_value)
        fees_total += fee_value
        fee_items.append(
            {
                "id": fee.get("id"),
                "name": fee.get("fee_name"),
                "type": fee_type,
                "calc_mode": calc_mode,
                "amount": float(amount),
                "calculated_value": float(fee_value),
            }
        )

    result["fees"] = fee_items
    result["fees_total"] = float(_q2(fees_total))

    # Calculate taxes — Decimal end-to-end so the receipt line items
    # reconcile to the cent. Saskatchewan rideshare is GST 5% only — PST does
    # NOT apply to rideshare here, so pst_enabled defaults off. PST/HST remain
    # per-area configurable from the admin panel for future markets/cohorts:
    # GST + optional PST, or a combined HST rate. Each tax is quantized
    # independently before summing so the breakdown matches the displayed total.
    taxable_amount = subtotal_d + fees_total
    tax_breakdown: Dict[str, Dict[str, float]] = {}
    tax_total = Decimal("0")

    def _apply_tax(label: str, rate_value: Any) -> None:
        nonlocal tax_total
        rate = _money(rate_value)
        amount = _q2(taxable_amount * rate / Decimal("100"))
        tax_breakdown[label] = {"rate": float(rate), "amount": float(amount)}
        tax_total += amount

    if matched_area.get("hst_enabled"):
        _apply_tax("HST", matched_area.get("hst_rate", 0))
    else:
        if matched_area.get("gst_enabled", True):
            _apply_tax("GST", matched_area.get("gst_rate", 5.0))
        if matched_area.get("pst_enabled", False):
            _apply_tax("PST", matched_area.get("pst_rate", 0))

    result["tax_amount"] = float(_q2(tax_total))
    result["tax_breakdown"] = tax_breakdown

    return result


async def compute_fare_estimate(
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    distance_km: float,
    duration_minutes: int,
    vehicle_type_id: str,
    *,
    surge_override: "Decimal | None" = None,
):
    """Canonical fare pipeline (base/distance/time/booking + area fees + tax).

    Callable form of the /rides/fare-estimate endpoint so server-side bookers
    (corporate guest booking) share the exact same formula. ``surge_override``
    pins the multiplier — corporate-paid rides are always 1.0x (surge never
    applies to company-billed trips; see routes.rides._is_corporate_paid).
    """
    fare_config = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("fare_configs", {"vehicle_type_id": vehicle_type_id}, limit=1)
    )
    fare_info = fare_config if fare_config else dict(DEFAULT_FARE)

    # Resolve surge from the service area covering the pickup point
    all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=100)
    surge = Decimal("1")
    matched_area_id = None
    for area in all_areas:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(pickup_lat, pickup_lng, poly):
            matched_area_id = area.get("id")
            # Gate on the per-area surge master toggle, same as the main fare
            # builders (fare_service / routes.fares). Without this, a stale
            # surge_active flag or parked multiplier on a disabled area would
            # surge this estimate while the booking path prices at 1.0x —
            # inconsistent rider estimates for the same area.
            if area.get("surge_enabled") and area.get("surge_active") and area.get("surge_multiplier", 1.0) > 1.0:
                surge = min(_fare_d(area["surge_multiplier"]), _fare_d(SURGE_CAP))
            break
    if surge_override is not None:
        surge = surge_override

    fb = calculate_fare(fare_info, distance_km, duration_minutes, surge=surge)
    subtotal = _fare_f(fb.total_fare)

    # Calculate area fees + taxes
    fees_result = await calculate_all_fees(
        pickup_lat,
        pickup_lng,
        dropoff_lat,
        dropoff_lng,
        distance_km,
        subtotal,
        _all_areas=all_areas,
    )

    grand_total = round(subtotal + fees_result["fees_total"] + fees_result["tax_amount"], 2)

    return {
        "base_fare": _fare_f(fb.base_fare),
        "distance_fare": _fare_f(fb.distance_fare),
        "time_fare": _fare_f(fb.time_fare),
        "booking_fee": _fare_f(fb.booking_fee),
        "surge_multiplier": _fare_f(fb.surge_multiplier),
        "subtotal": subtotal,
        "area_fees": fees_result["fees"],
        "area_fees_total": fees_result["fees_total"],
        "tax_amount": fees_result["tax_amount"],
        "tax_breakdown": fees_result["tax_breakdown"],
        "grand_total": grand_total,
        "service_area": fees_result.get("service_area_name"),
        # Consumed by server-side bookers (corporate guest booking) that turn
        # this estimate straight into a ride row; harmless extras for the
        # public endpoint's JSON.
        "service_area_id": matched_area_id,
        "driver_earnings": _fare_f(fb.driver_earnings),
        "admin_earnings": _fare_f(fb.admin_earnings),
    }


@support_router.get("/rides/fare-estimate")
async def fare_estimate(
    pickup_lat: float = Query(...),
    pickup_lng: float = Query(...),
    dropoff_lat: float = Query(...),
    dropoff_lng: float = Query(...),
    distance_km: float = Query(...),
    duration_minutes: int = Query(...),
    vehicle_type_id: str = Query(...),
):
    """Full fare estimate including base fare, area fees, and taxes."""
    return await compute_fare_estimate(
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        dropoff_lat=dropoff_lat,
        dropoff_lng=dropoff_lng,
        distance_km=distance_km,
        duration_minutes=duration_minutes,
        vehicle_type_id=vehicle_type_id,
    )


@support_router.get("/area-config")
async def get_area_config(
    lat: float = Query(...),
    lng: float = Query(...),
):
    """Get service area config (fees, taxes, vehicle pricing) for a location.
    Called by rider/driver apps on launch to cache area settings."""
    all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=100)
    matched_area = None
    for area in all_areas:
        polygon = get_service_area_polygon(area)
        if len(polygon) >= 3 and point_in_polygon(lat, lng, polygon):
            matched_area = area
            break

    if not matched_area:
        return {"found": False, "service_area": None}

    # Get active fees for this area
    area_fees = await db_supabase.get_rows(
        "area_fees", {"service_area_id": matched_area["id"], "is_active": True}, limit=50
    )

    # Build tax config
    tax_config = {}
    if matched_area.get("hst_enabled"):
        tax_config = {"type": "HST", "rate": float(matched_area.get("hst_rate", 0))}
    else:
        taxes = []
        if matched_area.get("gst_enabled", True):
            taxes.append({"name": "GST", "rate": float(matched_area.get("gst_rate", 5.0))})
        if matched_area.get("pst_enabled", False):
            taxes.append({"name": "PST", "rate": float(matched_area.get("pst_rate", 0))})
        tax_config = {"type": "GST_PST", "taxes": taxes}

    return {
        "found": True,
        "service_area": {
            "id": matched_area["id"],
            "name": matched_area.get("name"),
            "city": matched_area.get("city"),
            "province": matched_area.get("province"),
            "currency": matched_area.get("currency", "CAD"),
        },
        "fees": [
            {
                "id": f.get("id"),
                "name": f.get("fee_name"),
                "type": f.get("fee_type"),
                "calc_mode": f.get("calc_mode", "flat"),
                "amount": float(f.get("amount", 0)),
                "description": f.get("description", ""),
                "conditions": f.get("conditions", {}),
            }
            for f in area_fees
        ],
        "tax": tax_config,
        "vehicle_pricing": matched_area.get("vehicle_pricing", []),
    }


# ============ Scheduled Rides ============


@support_router.post("/rides/schedule")
async def schedule_ride(req: ScheduleRideRequest, current_user: dict = Depends(get_current_user)):
    """Schedule a ride for a future time."""
    try:
        scheduled_dt = datetime.fromisoformat(req.scheduled_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scheduled_time format. Use ISO 8601.") from None

    if scheduled_dt < datetime.now(timezone.utc) + timedelta(minutes=15):
        raise HTTPException(status_code=400, detail="Scheduled time must be at least 15 minutes from now.")

    fare_config = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("fare_configs", {"vehicle_type_id": req.vehicle_type_id}, limit=1)
    )
    fare_info = fare_config if fare_config else dict(DEFAULT_FARE)

    # CLAUDE.md: "Never apply surge to scheduled rides booked outside the
    # surge window" — scheduled rides lock the fare at booking time at 1.0×.
    surge = Decimal("1")

    airport_result = await calculate_airport_fee(req.pickup_lat, req.pickup_lng, req.dropoff_lat, req.dropoff_lng)
    airport_fee = airport_result["airport_fee"]

    fb = calculate_fare(fare_info, req.distance_km, req.duration_minutes, surge=surge, airport_fee=airport_fee)

    ride = {
        "id": str(uuid.uuid4()),
        "rider_id": current_user["id"],
        "vehicle_type_id": req.vehicle_type_id,
        "pickup_address": req.pickup_address,
        "pickup_lat": req.pickup_lat,
        "pickup_lng": req.pickup_lng,
        "dropoff_address": req.dropoff_address,
        "dropoff_lat": req.dropoff_lat,
        "dropoff_lng": req.dropoff_lng,
        "distance_km": req.distance_km,
        "duration_minutes": req.duration_minutes,
        "base_fare": _fare_f(fb.base_fare),
        "distance_fare": _fare_f(fb.distance_fare),
        "time_fare": _fare_f(fb.time_fare),
        "booking_fee": _fare_f(fb.booking_fee),
        "airport_fee": _fare_f(fb.airport_fee),
        "airport_zone_name": airport_result.get("airport_zone_name"),
        "total_fare": _fare_f(fb.total_fare),
        "driver_earnings": _fare_f(fb.driver_earnings),
        "admin_earnings": _fare_f(fb.admin_earnings),
        "status": RideStatus.SCHEDULED,
        "is_scheduled": True,
        "scheduled_time": scheduled_dt,
        "stops": req.stops,
        "ride_requested_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    await db_supabase.insert_ride(ride)
    return ride


@support_router.get("/rides/scheduled")
async def get_scheduled_rides(current_user: dict = Depends(get_current_user)):
    """Get all scheduled rides for the authenticated user."""
    rides = await db_supabase.get_rides_for_user(current_user["id"], limit=50)
    return rides


@support_router.delete("/rides/scheduled/{ride_id}")
async def cancel_scheduled_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Cancel a scheduled ride."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to cancel this ride")
    if ride.get("status") != RideStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="Only scheduled rides can be cancelled this way")

    await db_supabase.update_ride(ride_id, {"status": RideStatus.CANCELLED, "cancelled_at": datetime.now(timezone.utc)})
    return {"cancelled": True}


# ============ Multi-stop Rides ============


@support_router.post("/rides/{ride_id}/stops")
async def add_stop(ride_id: str, req: AddStopRequest, current_user: dict = Depends(get_current_user)):
    """Add a stop to an existing ride."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to modify this ride")
    if ride.get("status") in RideStatus.terminal_statuses():
        raise HTTPException(status_code=400, detail="Cannot add stops to completed/cancelled rides")

    stops = ride.get("stops", [])
    new_stop = {
        "id": str(uuid.uuid4()),
        "address": req.address,
        "lat": req.lat,
        "lng": req.lng,
        "order": req.order,
        "arrived_at": None,
        "completed_at": None,
    }
    stops.append(new_stop)
    stops.sort(key=lambda s: s.get("order", 0))

    await db_supabase.update_ride(ride_id, {"stops": stops, "updated_at": datetime.now(timezone.utc)})
    return {"stops": stops}


@support_router.put("/rides/{ride_id}/stops/{stop_id}/complete")
async def complete_stop(ride_id: str, stop_id: str, current_user: dict = Depends(get_current_user)):
    """Mark a stop as completed."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to update this ride")

    stops = ride.get("stops", [])
    for stop in stops:
        if stop.get("id") == stop_id:
            stop["completed_at"] = datetime.now(timezone.utc).isoformat()
            stop["arrived_at"] = stop.get("arrived_at") or datetime.now(timezone.utc).isoformat()
            break
    else:
        raise HTTPException(status_code=404, detail="Stop not found")

    await db_supabase.update_ride(ride_id, {"stops": stops, "updated_at": datetime.now(timezone.utc)})
    return {"stops": stops}


# ============ Safety Toolkit ============


@support_router.post("/rides/{ride_id}/share")
async def share_trip(ride_id: str, req: ShareTripRequest, current_user: dict = Depends(get_current_user)):
    """Share a live trip link with a contact."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to share this ride")

    # Generate or reuse the share token
    token = ride.get("shared_trip_token") or secrets.token_urlsafe(32)

    contacts = ride.get("shared_trip_contacts", [])
    contacts.append(
        {
            "name": req.contact_name,
            "phone": req.contact_phone,
            "shared_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    await db_supabase.update_ride(
        ride_id,
        {
            "shared_trip_token": token,
            "shared_trip_contacts": contacts,
        },
    )

    # The share URL format – frontend or web page would render this
    share_url = f"/trip/live/{token}"

    return {
        "share_url": share_url,
        "token": token,
        "contacts": contacts,
    }


@support_router.get("/trip/live/{token}")
async def get_shared_trip(token: str):
    """Get live trip info via share token (no auth required)."""
    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("rides", {"shared_trip_token": token}, limit=1)
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Trip not found or link expired")

    # Return safe subset of ride data  – no sensitive info
    return {
        "status": ride.get("status"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "pickup_lat": ride.get("pickup_lat"),
        "pickup_lng": ride.get("pickup_lng"),
        "dropoff_lat": ride.get("dropoff_lat"),
        "dropoff_lng": ride.get("dropoff_lng"),
        "driver_name": None,  # Would be populated from driver lookup
        "vehicle_info": None,
        "stops": ride.get("stops", []),
        "ride_started_at": str(ride.get("ride_started_at", "")),
    }


# ============ Push Notification Helpers ============


@support_router.post("/users/fcm-token")
async def register_fcm_token(req: RegisterFcmTokenRequest, current_user: dict = Depends(get_current_user)):
    """Register/update the authenticated user's FCM token for push notifications."""
    await db_supabase.update_one("users", {"id": current_user["id"]}, {"fcm_token": req.token})
    return {"registered": True}


def _is_expo_token(token: str) -> bool:
    """Return True if the token is an Expo push token (not a native FCM token)."""
    return token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken[")


async def _send_expo_push(token: str, title: str, body: str, data: Dict[str, str] | None = None) -> bool:
    """Send a push notification via Expo's push API (for Expo-managed tokens)."""
    import httpx

    payload = {
        "to": token,
        "title": title,
        "body": body,
        "data": data or {},
        "sound": "default",
        "priority": "high",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://exp.host/--/api/v2/push/send",
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            result = resp.json()
            data_block = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
            status = data_block.get("status")
            if status == "ok":
                logger.info(f"Expo push sent OK to {token[:35]}...")
                return True
            error_code = data_block.get("details", {}).get("error", "")
            if error_code == "DeviceNotRegistered":
                logger.warning(f"Expo token unregistered (DeviceNotRegistered): {token[:35]}...")
            else:
                logger.error(f"Expo push non-ok response: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Expo push notification: {e}", exc_info=True)
        return False


def _stringify_push_data(data: Dict[str, Any] | None) -> Dict[str, str]:
    """Coerce a push data payload to all-string values.

    FCM (messaging.Message.data) and Expo both require every data value to be a
    string and reject the whole message otherwise ("Message.data must not
    contain non-string values."). Callers occasionally pass a bool / int / None
    (e.g. ``{"is_auto": True}``), which would drop the notification entirely.
    Normalise here, at the single delivery choke point, so no caller can break
    delivery:
      - None        → key dropped (FCM has no null; an empty string is ambiguous)
      - bool        → "true" / "false" (lowercase, for JS-side equality checks)
      - dict / list → JSON string (client JSON.parses it)
      - everything  → str(value)
    """
    if not data:
        return {}
    out: Dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[str(key)] = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            out[str(key)] = json.dumps(value, separators=(",", ":"))
        else:
            out[str(key)] = str(value)
    return out


async def _deliver_push_now(
    token: str,
    title: str,
    body: str,
    data: Dict[str, str] | None,
    user_id: str,
    target_app: str | None,
) -> bool:
    """Attempt a single, immediate push delivery. Returns True on success.

    Routes Expo push tokens via Expo's REST API; everything else is treated as
    an FCM token and sent through the Firebase Admin SDK. A stale FCM token is
    purged so the next login re-registers. Never raises — returns False on any
    failure so the caller can decide whether to enqueue a retry.
    """
    data = _stringify_push_data(data)
    if _is_expo_token(token):
        return await _send_expo_push(token, title, body, data)

    try:
        from firebase_admin import exceptions as firebase_exceptions
        from firebase_admin import messaging
    except ImportError:
        logger.error("firebase_admin not available for push notifications — FCM delivery will fail")
        return False

    is_dispatch = (data or {}).get("type") == "new_ride_assignment"
    is_live_activity = (data or {}).get("type") == "live_activity"
    # Live-activity updates are also data-only so the rider app's Notifee handler
    # renders/updates the ongoing notification itself (a system banner would
    # duplicate it and could not be made ongoing/updated-in-place).
    is_data_only = is_dispatch or is_live_activity

    # Rider app creates "ride-updates"; driver app creates "ride-offers".
    # Android silently drops notifications to channels that don't exist on
    # the receiving app, so we must select the channel that matches the target.
    android_channel = "ride-updates" if target_app == "rider" else "ride-offers"

    try:
        # Android: data-only for dispatch so Notifee (driver app) renders the
        # rich heads-up + full-screen-intent notification with Accept/Decline
        # action buttons. Otherwise let the OS show its default banner.
        android_cfg = messaging.AndroidConfig(
            priority="high",
            notification=None
            if is_data_only
            else messaging.AndroidNotification(
                channel_id=android_channel,
            ),
        )
        # iOS rich image: surface the offer-card banner URL via fcm_options.image
        # so the Notification Service Extension (driver app) downloads + attaches
        # it. mutable_content (set below) is what lets the NSE run. Harmless when
        # no NSE is installed — iOS just ignores the image.
        _apns_image = (data or {}).get("offer_card_url") if is_dispatch else None
        if is_dispatch:
            # Dispatch offer rides on a time-sensitive alert payload (custom sound
            # + category for the Accept/Decline actions). Without this aps block,
            # iOS shows nothing for an otherwise data-only dispatch message.
            _apns_payload = messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(title=title, body=body),
                    sound="ride_offer.caf",
                    category="ride-offer",
                    content_available=True,
                    mutable_content=True,
                ),
            )
        elif is_live_activity:
            # Live-activity FCM is Android-only (iOS uses the direct ActivityKit
            # APNs path). If it ever reaches an iOS token it must be a silent
            # background push, not a malformed alert (Apple rate-limits those).
            _apns_payload = messaging.APNSPayload(aps=messaging.Aps(content_available=True))
        else:
            _apns_payload = None
        message = messaging.Message(
            notification=None if is_data_only else messaging.Notification(title=title, body=body),
            data=data or {},
            token=token,
            android=android_cfg,
            apns=messaging.APNSConfig(
                headers={
                    "apns-priority": "5" if is_live_activity else "10",
                    "apns-push-type": "background" if is_live_activity else "alert",
                },
                fcm_options=messaging.APNSFCMOptions(image=_apns_image) if _apns_image else None,
                payload=_apns_payload,
            ),
        )
        response = await asyncio.to_thread(messaging.send, message)
        logger.info(f"Push notification sent to {user_id}: {response} (dispatch={is_dispatch})")
        return True
    except firebase_exceptions.NotFoundError:
        # Token is stale (app uninstalled / token rotated). Purge it so the
        # next login registers a fresh token and delivery resumes.
        logger.warning(f"Stale FCM token for user {user_id} (target_app={target_app}) — purging")
        try:
            purge: dict = {"fcm_token": None}
            if target_app == "rider":
                purge["fcm_token_rider"] = None
            elif target_app == "driver":
                purge["fcm_token_driver"] = None
            await db.update_one("users", {"id": user_id}, purge)
            rows = await db_supabase.get_rows("push_tokens", {"user_id": user_id, "token": token}, limit=1)
            if rows:
                await db_supabase.delete_one("push_tokens", {"id": rows[0]["id"]})
        except Exception:
            logger.error("Failed to purge stale FCM token", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Failed to send push notification to user {user_id}: {e}", exc_info=True)
        return False


async def send_push_notification(
    user_id: str,
    title: str,
    body: str,
    data: Dict[str, str] | None = None,
    priority: str = "normal",
    target_app: str | None = None,
):
    """Send a push notification to a user.

    Routes automatically: Expo push tokens go via Expo's REST API; all other
    tokens are assumed to be FCM and sent via Firebase Admin SDK.

    ``target_app`` selects which per-app token column to read:
      - ``"rider"``  → ``fcm_token_rider``
      - ``"driver"`` → ``fcm_token_driver``
      - ``None``     → legacy ``fcm_token`` (backward compat)

    Delivery is attempted INLINE for every priority. Dispatch (ride offer) and
    safety (SOS) pushes are time-critical — a ride offer expires in ~15s — so
    they must never wait on the 30s push_retry loop. If the immediate attempt
    fails for any reason — the FCM/Expo send OR a transient error in the users
    lookup — dispatch/safety pushes fall back to the push_retry_queue so the
    offer/SOS is never silently dropped (the retry loop re-reads the user at
    send time). Normal (informational) pushes are best-effort: a lookup error
    surfaces to the caller rather than being masked.
    """
    time_critical = priority in ("dispatch", "safety")

    # The whole immediate path (users lookup + token select + send) is guarded:
    # for a time-critical push a transient Supabase read hiccup must fall back
    # to the retry queue, NOT escape — dispatch fires this fire-and-forget, so a
    # raise would vanish and drop the offer (the queued-first path it replaced
    # could not do that). _deliver_push_now never raises, so a caught exception
    # here is the lookup path.
    try:
        user = await db.find_one("users", {"id": user_id})
        if not user:
            logger.warning(f"No user found for {user_id} — push dropped")
            return False

        token: str | None = None
        if target_app == "rider":
            token = user.get("fcm_token_rider") or user.get("fcm_token")
        elif target_app == "driver":
            token = user.get("fcm_token_driver") or user.get("fcm_token")
        else:
            token = user.get("fcm_token")

        if not token:
            logger.warning(f"No FCM token on file for user {user_id} (target_app={target_app}) — push dropped")
            return False

        # Primary path: deliver right now (≈100–300 ms) so a ride offer reaches
        # the driver's phone well inside the offer window. A queued-only dispatch
        # would arrive after the 30s retry tick — long after the offer expired.
        delivered = await _deliver_push_now(token, title, body, data, user_id, target_app)
        if delivered:
            return True
    except Exception:
        logger.error(
            f"push: immediate send path errored for user {user_id} (priority={priority})",
            exc_info=True,
        )
        if not time_critical:
            # Informational push: keep the existing best-effort contract and let
            # the DB error surface to the caller instead of masking it.
            raise
        # Time-critical: fall through to the retry-queue enqueue below.

    # Immediate delivery failed (send returned False) or the immediate path
    # errored for a time-critical push. Enqueue for retry (exponential back-off)
    # so a transient outage doesn't silently drop a ride offer or SOS alert. The
    # retry loop re-reads the user/token at send time, so a failed lookup here is
    # recoverable; a genuinely missing user/token already returned above.
    if time_critical:
        try:
            try:
                from .utils.push_retry import enqueue_push
            except ImportError:
                from utils.push_retry import enqueue_push

            await enqueue_push(user_id, title, body, data, priority=priority, target_app=target_app)
            logger.warning(f"push: immediate send failed for user {user_id} — enqueued {priority} push for retry")
        except Exception:
            logger.error("push_retry enqueue (fallback) failed", exc_info=True)
    return False


async def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    html: Optional[str] = None,
    attachments: Optional[list] = None,
    email_type: str = "transactional",
    recipient_user_id: Optional[str] = None,
    log_id: str = "-",
) -> bool:
    """Send an email: AWS SES primary, Resend guardrail.

    Delegates to utils.email_provider.send_transactional_email, which tries
    AWS SES first and falls back to Resend when SES is unconfigured or fails.
    In dev/test (neither provider configured) the message is log-only.
    Returns True if either provider accepted the message, False otherwise.

    ``html`` adds a rich alternative part; ``attachments`` is a list of
    ``{"filename", "content" (bytes), "mime"}`` dicts (e.g. a data-export ZIP).
    ``email_type``/``recipient_user_id``/``log_id`` flow into email_send_log
    for PIPEDA-safe auditing (never the recipient address).
    """
    if not to:
        return False

    try:
        from .utils.email_provider import send_transactional_email
    except ImportError:
        from utils.email_provider import send_transactional_email  # type: ignore

    return await send_transactional_email(
        to=to,
        subject=subject,
        text=body,
        html=html,
        attachments=attachments,
        email_type=email_type,
        recipient_user_id=recipient_user_id,
        log_id=log_id,
    )


async def notify_safety_team(incident: dict) -> dict:
    """Fan out a safety_incidents row to the configured alert channels.

    Three side effects, each best-effort and isolated so one channel
    failing doesn't suppress the others:
      1. Admin WS broadcast — wakes the safety queue UI in real time.
      2. Email to settings.safety_alert_emails (comma-separated).
         Skipped if blank so this can ship before ops finalises the
         distribution list.
      3. Critical log line so on-call alerting on log levels (Sentry,
         Better Stack, Logtail) can fire even when email is unconfigured.

    Returns a dict {ws, email_sent, email_attempted} the caller can
    log for ops visibility. Caller is responsible for already having
    inserted the safety_incidents row — this helper only notifies.
    """
    incident_id = incident.get("id")
    category = incident.get("category") or "unknown"
    ride_id = incident.get("ride_id")
    role = incident.get("role") or "rider"
    reported_by = incident.get("reported_by_user_id")

    # Log at CRITICAL — on-call paging via log-aggregator alert rules
    # works even before Resend is configured. Never include the raw
    # description here (may contain rider PII / addresses).
    logger.critical(
        f"[SAFETY] Incident opened id={incident_id} category={category} role={role} ride_id={ride_id or '-'}"
    )

    ws_ok = False
    try:
        try:
            from .socket_manager import manager as _ws_manager
        except ImportError:
            from socket_manager import manager as _ws_manager  # type: ignore
        await _ws_manager.broadcast_to_admins(
            {
                "type": "safety_incident_opened",
                "incident_id": incident_id,
                "ride_id": ride_id,
                "category": category,
                "role": role,
                "reported_by_user_id": reported_by,
                "created_at": incident.get("created_at"),
            }
        )
        ws_ok = True
    except Exception:
        logger.error(
            f"[SAFETY] Admin WS broadcast failed for incident {incident_id}",
            exc_info=True,
        )

    # Email fan-out. Each recipient gets its own send_email call so
    # one bad address doesn't poison the rest.
    email_attempted = 0
    email_sent = 0
    try:
        try:
            from .settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        settings = await get_app_settings()
        raw = (settings.get("safety_alert_emails") or "").strip()
        recipients = [addr.strip() for addr in raw.split(",") if addr.strip() and "@" in addr]
        if recipients:
            # PII in the email body is fine — this is going to the
            # safety team, which has read access to the incident anyway.
            # Avoid putting the rider's location in the SUBJECT (subject
            # lines hit a wider audit trail via SMTP intermediaries).
            subject = f"[Spinr Safety] {category} · {role} · ref {incident_id[:8] if incident_id else '?'}"
            body_lines = [
                "A new safety incident was opened in Spinr admin.",
                "",
                f"Category:  {category}",
                f"Role:      {role}",
                f"Reporter:  {reported_by or '(system)'}",
                f"Ride ID:   {ride_id or '(none)'}",
                f"Created:   {incident.get('created_at') or '(now)'}",
                "",
                f"Description: {incident.get('description') or '(no description)'}",
            ]
            if incident.get("latitude") and incident.get("longitude"):
                body_lines.append(f"Location:  {incident['latitude']:.5f}, {incident['longitude']:.5f}")
            body_lines += [
                "",
                "Open the safety queue in the admin dashboard to triage.",
                "This message is auto-generated; do not reply.",
            ]
            body = "\n".join(body_lines)
            for addr in recipients:
                email_attempted += 1
                try:
                    ok = await send_email(to=addr, subject=subject, body=body)
                    if ok:
                        email_sent += 1
                except Exception:
                    logger.error(
                        f"[SAFETY] send_email failed for incident {incident_id} to {addr}",
                        exc_info=True,
                    )
        else:
            logger.warning(
                f"[SAFETY] No safety_alert_emails configured — email step skipped for incident {incident_id}"
            )
    except Exception:
        logger.error(
            f"[SAFETY] notify_safety_team email fan-out failed for incident {incident_id}",
            exc_info=True,
        )

    return {"ws": ws_ok, "email_sent": email_sent, "email_attempted": email_attempted}


@admin_support_router.post("/notifications/send")
async def admin_send_notification(req: SendNotificationRequest):
    """Send a push notification to a specific user (admin)."""
    success = await send_push_notification(req.user_id, req.title, req.body, req.data)
    return {"sent": success}


# ============ Scheduled Ride Background Checker ============


async def check_scheduled_rides():
    """Background task: dispatches scheduled rides when their time arrives."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            # Find rides scheduled within the next 5 minutes
            window = now + timedelta(minutes=5)

            scheduled = await db_supabase.get_rows(
                "rides",
                {
                    "status": RideStatus.SCHEDULED,
                    "is_scheduled": True,
                },
                limit=50,
            )

            for ride in scheduled:
                sched_time = ride.get("scheduled_time")
                if sched_time and isinstance(sched_time, str):
                    sched_time = datetime.fromisoformat(sched_time.replace("Z", "+00:00"))

                if sched_time and sched_time <= window:
                    # Transition to "searching" so the normal matching logic picks it up
                    await db_supabase.update_ride(
                        ride["id"],
                        {
                            "status": RideStatus.SEARCHING,
                            "ride_requested_at": datetime.now(timezone.utc),
                            "updated_at": datetime.now(timezone.utc),
                        },
                    )
                    logger.info(f"Dispatched scheduled ride {ride['id']}")

                    # Send push notification to rider
                    await send_push_notification(
                        ride["rider_id"],
                        "Ride Dispatched! 🚗",
                        f"Your scheduled ride to {ride.get('dropoff_address', 'destination')} is being matched with a driver.",
                        {"ride_id": ride["id"], "type": "scheduled_dispatch"},
                    )
        except Exception as e:
            logger.error(f"Scheduled ride checker error: {e}")

        await asyncio.sleep(60)  # Check every minute
