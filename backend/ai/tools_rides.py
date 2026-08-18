"""Ride read tools for the AI assistant.

All handlers are read-only, scoped to the authenticated user, and return
whitelisted fields only. Precise GPS coordinates are deliberately excluded
from results (addresses are enough for the model to answer); driver info is
limited to the DriverPublicView field set minus location.
"""

import logging
from typing import Any, Dict, Optional

try:
    from .tools import ToolSpec, register, register_ownership_verifier
except ImportError:  # bare-import runtime (python -m backend.server puts backend/ on sys.path)
    from ai.tools import ToolSpec, register, register_ownership_verifier

try:
    from .. import db_supabase
    from ..models.ride_status import RideStatus
    from ..utils.pii import first_name_only
except ImportError:
    import db_supabase
    from models.ride_status import RideStatus
    from utils.pii import first_name_only  # type: ignore

logger = logging.getLogger(__name__)

# Rider-facing whitelist. No rider_id (the caller is the rider), no GPS
# coordinates, no OTPs, no driver earnings split, no payment intent ids.
_RIDE_FIELDS = (
    "id",
    "status",
    "pickup_address",
    "dropoff_address",
    "distance_km",
    "duration_minutes",
    "surge_multiplier",
    "total_fare",
    "grand_total",
    "tip_amount",
    "discount_amount",
    "promo_code",
    "payment_method",
    "payment_status",
    "is_scheduled",
    "scheduled_time",
    "ride_requested_at",
    "driver_accepted_at",
    "ride_started_at",
    "ride_completed_at",
    "cancelled_at",
    "cancellation_reason",
)

_DRIVER_PUBLIC_FIELDS = (
    "rating",
    "total_rides",
    "vehicle_make",
    "vehicle_model",
    "vehicle_color",
    "license_plate",
)


def _pick(row: Dict[str, Any], fields) -> Dict[str, Any]:
    return {k: row[k] for k in fields if row.get(k) is not None}


async def _driver_public(driver_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not driver_id:
        return None
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        return None
    info = _pick(driver, _DRIVER_PUBLIC_FIELDS)
    user = await db_supabase.get_user_by_id(driver.get("user_id"))
    # 2026-08-18 fleet audit: this used to send the driver's full legal name
    # (first + last) into the model's context on every "who's my driver"
    # query -- a PIPEDA violation (full names are explicitly forbidden in
    # AI/analytics payloads) that scrub_pii_deep in tools.py cannot catch,
    # since a plain name isn't regex-detectable. First-name-only is this
    # codebase's established driver-display-name convention elsewhere
    # (utils/pii.py::first_name_only, already used for the rider's name on
    # the driver-facing WS/push path in routes/rides/matching.py) -- reused
    # here rather than inventing a second minimization rule.
    info["name"] = first_name_only(user, fallback="Driver")
    return info


async def _owned_ride(ride_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a ride and verify ownership. Foreign/missing id → None — the
    model cannot distinguish 'not yours' from 'does not exist'."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("rider_id") != user_id:
        return None
    return ride


async def _verify_ride_ownership(ride_id: str, user_id: str) -> bool:
    """Central ownership gate (backend/ai/tools.py) for any tool arg declared
    owned_id_args={'ride_id': 'ride'} — the handler's own _owned_ride check
    stays as defense in depth."""
    return await _owned_ride(ride_id, user_id) is not None


register_ownership_verifier("ride", _verify_ride_ownership)


async def get_active_ride(user: Dict[str, Any]) -> Dict[str, Any]:
    rows = await db_supabase.get_rows(
        "rides",
        {"rider_id": user["id"], "status": {"$in": [s.value for s in RideStatus.active_statuses()]}},
        limit=1,
    )
    if not rows:
        return {"active": False, "message": "No active ride right now."}
    ride = _pick(rows[0], _RIDE_FIELDS)
    ride["driver"] = await _driver_public(rows[0].get("driver_id"))
    return {"active": True, "ride": ride}


async def get_ride_history(user: Dict[str, Any], limit: int = 5, status: Optional[str] = None) -> Dict[str, Any]:
    filters: Dict[str, Any] = {"rider_id": user["id"]}
    if status:
        filters["status"] = status
    rows = await db_supabase.get_rows("rides", filters, order="created_at", desc=True, limit=limit)
    return {
        "count": len(rows),
        "rides": [_pick(r, _RIDE_FIELDS) for r in rows],
    }


async def get_ride_details(user: Dict[str, Any], ride_id: str) -> Dict[str, Any]:
    ride = await _owned_ride(ride_id, user["id"])
    if not ride:
        return {"error": "ride not found"}
    details = _pick(ride, _RIDE_FIELDS)
    details["driver"] = await _driver_public(ride.get("driver_id"))
    return {"ride": details}


async def get_ride_receipt(user: Dict[str, Any], ride_id: str) -> Dict[str, Any]:
    ride = await _owned_ride(ride_id, user["id"])
    if not ride:
        return {"error": "ride not found"}
    receipt: Dict[str, Any] = {
        "ride_id": ride["id"],
        "status": ride.get("status"),
        "payment_status": ride.get("payment_status"),
        "payment_method": ride.get("payment_method"),
        "tip_amount": ride.get("tip_amount"),
        "promo_code": ride.get("promo_code"),
        "discount_amount": ride.get("discount_amount"),
        "grand_total": ride.get("grand_total"),
    }
    # The frozen booking-time line items are the receipt source of truth
    # (see create_ride fare_breakdown_snapshot); fall back to components.
    snapshot = ride.get("fare_breakdown_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("lines"):
        receipt["lines"] = snapshot["lines"]
        receipt["lines_total"] = snapshot.get("grand_total")
    else:
        receipt["components"] = _pick(
            ride,
            ("base_fare", "distance_fare", "time_fare", "booking_fee", "surge_multiplier", "total_fare"),
        )
    return {"receipt": receipt}


register(
    ToolSpec(
        name="get_active_ride",
        description=(
            "Call this when the rider asks about their current ride — where the driver is, "
            "how long until pickup, what car is coming, or the trip status. Returns the "
            "active ride (if any) with status, addresses, fare and the assigned driver's "
            "public details."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_active_ride,
    )
)

register(
    ToolSpec(
        name="get_ride_history",
        description=(
            "Call this when the rider asks about past trips — recent rides, how many trips "
            "they took, or to find a specific ride before asking for its receipt. Returns "
            "the most recent rides, newest first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How many rides to return (default 5).",
                },
                "status": {
                    "type": "string",
                    "enum": ["completed", "cancelled"],
                    "description": "Optional status filter.",
                },
            },
            "required": [],
        },
        handler=get_ride_history,
    )
)

register(
    ToolSpec(
        name="get_ride_details",
        description=(
            "Call this for full details of one specific ride (status, addresses, fare, "
            "driver) when you already know its ride_id — usually from get_ride_history "
            "or get_active_ride."
        ),
        input_schema={
            "type": "object",
            "properties": {"ride_id": {"type": "string", "maxLength": 64, "description": "The ride id."}},
            "required": ["ride_id"],
        },
        handler=get_ride_details,
        owned_id_args={"ride_id": "ride"},
    )
)

register(
    ToolSpec(
        name="get_ride_receipt",
        description=(
            "Call this when the rider asks why a ride cost what it did, about a charge, "
            "fee, tip, discount or tax on a trip. Returns the receipt line items for one "
            "ride — explain them plainly and never invent amounts."
        ),
        input_schema={
            "type": "object",
            "properties": {"ride_id": {"type": "string", "maxLength": 64, "description": "The ride id."}},
            "required": ["ride_id"],
        },
        handler=get_ride_receipt,
        owned_id_args={"ride_id": "ride"},
    )
)
