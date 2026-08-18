"""Lost & Found route.

Cases can be opened by a rider (left something), a driver (found something),
or admin on behalf of either party. Each case has its own chat thread.

Endpoints:
    GET  /lost-and-found                    — list cases the caller is party to
    POST /lost-and-found/driver-report      — driver reports a found item
    GET  /lost-and-found/{case_id}          — single case detail
    PUT  /lost-and-found/{case_id}/respond  — driver marks found / not_found
    GET  /lost-and-found/{case_id}/messages — fetch chat messages
    POST /lost-and-found/{case_id}/messages — send a chat message
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..features import send_push_notification
    from ..services.zoho_desk_integration import create_ticket_for_lost_and_found
    from ..utils.error_handling import DuplicateRecordError
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_current_user  # type: ignore
    from features import send_push_notification  # type: ignore
    from services.zoho_desk_integration import create_ticket_for_lost_and_found  # type: ignore
    from utils.error_handling import DuplicateRecordError  # type: ignore

api_router = APIRouter(prefix="/lost-and-found", tags=["Lost & Found"])

_VALID_CATEGORIES = {"electronics", "clothing", "bag", "document", "keys", "other"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _driver_for_user(user_id: str) -> Optional[dict]:
    rows = await db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1)
    return rows[0] if rows else None


async def _require_participant(case_id: str, user_id: str, driver_id: Optional[str] = None) -> dict:
    """Return the case or raise 403/404."""
    case = await db_supabase.find_one("lost_and_found", {"id": case_id})
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    is_reporter = case.get("reporter_id") == user_id
    is_rider = case.get("rider_user_id") == user_id
    is_driver = driver_id is not None and case.get("driver_id") == driver_id
    if not (is_reporter or is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not a participant in this case")
    return case


async def _insert_system_message(case_id: str, text: str) -> None:
    await db_supabase.insert_one(
        "lost_and_found_messages",
        {
            "id": str(uuid.uuid4()),
            "lost_and_found_id": case_id,
            "sender_id": None,
            "sender_role": "system",
            "message": text,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _push_safe(user_id: str, title: str, body: str, data: dict, target_app: str) -> None:
    try:
        await send_push_notification(user_id, title, body, data, target_app=target_app)
    except Exception:
        logger.opt(exception=True).error("lost_and_found push failed user={} target_app={}", user_id, target_app)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@api_router.get("")
async def list_cases(
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Return all open cases the caller is party to (as rider or driver)."""
    user_id = current_user["id"]
    driver = await _driver_for_user(user_id)

    reporter_filters: dict = {"reporter_id": user_id}
    rider_filters: dict = {"rider_user_id": user_id}
    if status:
        reporter_filters["status"] = status
        rider_filters["status"] = status

    as_reporter = await db_supabase.get_rows(
        "lost_and_found",
        reporter_filters,
        order="created_at",
        desc=True,
        limit=50,
    )
    as_rider = await db_supabase.get_rows(
        "lost_and_found",
        rider_filters,
        order="created_at",
        desc=True,
        limit=50,
    )

    as_driver: list = []
    if driver:
        driver_filters: dict = {"driver_id": driver["id"]}
        if status:
            driver_filters["status"] = status
        as_driver = await db_supabase.get_rows(
            "lost_and_found",
            driver_filters,
            order="created_at",
            desc=True,
            limit=50,
        )

    seen: set = set()
    merged: list = []
    for case in sorted(
        as_reporter + as_rider + as_driver,
        key=lambda c: c.get("created_at", ""),
        reverse=True,
    ):
        if case["id"] not in seen:
            seen.add(case["id"])
            merged.append(case)

    return {"cases": merged[:50]}


# ---------------------------------------------------------------------------
# Single case
# ---------------------------------------------------------------------------


@api_router.get("/{case_id}")
async def get_case(case_id: str, current_user: dict = Depends(get_current_user)):
    driver = await _driver_for_user(current_user["id"])
    case = await _require_participant(case_id, current_user["id"], driver["id"] if driver else None)
    return {"case": case}


# ---------------------------------------------------------------------------
# Driver reports a found item
# ---------------------------------------------------------------------------


class DriverReportRequest(BaseModel):
    ride_id: str
    item_description: str = Field(..., min_length=3, max_length=500)
    item_category: str = Field(default="other")


@api_router.post("/driver-report")
async def driver_report_found_item(
    req: DriverReportRequest,
    current_user: dict = Depends(get_current_user),
):
    """Driver opens a case after finding an unclaimed item in their vehicle."""
    driver = await _driver_for_user(current_user["id"])
    if not driver:
        raise HTTPException(status_code=403, detail="Driver profile not found")

    ride = await db_supabase.get_ride(req.ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=403, detail="You were not the driver on this ride")
    if ride.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Can only report items for completed rides")

    category = req.item_category if req.item_category in _VALID_CATEGORIES else "other"
    rider_user_id: Optional[str] = ride.get("rider_id")

    existing = await db_supabase.get_rows(
        "lost_and_found",
        {"ride_id": req.ride_id, "driver_id": driver["id"]},
        limit=1,
    )
    if existing:
        return {"success": True, "case": existing[0], "existing": True}

    try:
        case = await db_supabase.insert_one(
            "lost_and_found",
            {
                "id": str(uuid.uuid4()),
                "ride_id": req.ride_id,
                "reporter_id": current_user["id"],
                "rider_user_id": rider_user_id,
                "driver_id": driver["id"],
                "reporter_type": "driver",
                "item_description": req.item_description,
                "item_category": category,
                "status": "driver_found",
                "contact_method": "in_app",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except (DuplicateRecordError, Exception) as exc:
        if "duplicate" not in str(exc).lower() and "unique" not in str(exc).lower():
            raise
        dup = await db_supabase.get_rows(
            "lost_and_found",
            {"ride_id": req.ride_id, "driver_id": driver["id"]},
            limit=1,
        )
        if dup:
            return {"success": True, "case": dup[0], "existing": True}
        raise

    await _insert_system_message(
        case["id"],
        f'Driver reported finding an item: "{req.item_description}". They will coordinate return through this chat.',
    )

    if rider_user_id:
        await _push_safe(
            rider_user_id,
            "Item Found in Your Recent Ride",
            f"Your driver found an item that may be yours: {req.item_description}. "
            "Open Lost & Found to coordinate the return.",
            {"type": "lost_and_found", "case_id": case["id"]},
            target_app="rider",
        )

    # Raise a Zoho Desk ticket for support to track the return — fire-and-forget
    # so a Zoho hiccup never blocks or fails the driver's report. No-op when the
    # integration is disabled.
    asyncio.create_task(create_ticket_for_lost_and_found(case, ride))

    return {"success": True, "case": case}


# ---------------------------------------------------------------------------
# Driver responds to a rider report (found / not found)
# ---------------------------------------------------------------------------


class RespondRequest(BaseModel):
    found: bool


@api_router.put("/{case_id}/respond")
async def driver_respond(
    case_id: str,
    req: RespondRequest,
    current_user: dict = Depends(get_current_user),
):
    """Driver confirms whether the reported item is in their vehicle."""
    driver = await _driver_for_user(current_user["id"])
    if not driver:
        raise HTTPException(status_code=403, detail="Driver profile not found")

    case = await db_supabase.find_one("lost_and_found", {"id": case_id})
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=403, detail="Not the driver on this case")
    if case.get("status") not in ("reported", "driver_notified"):
        raise HTTPException(status_code=400, detail="Case is not awaiting driver response")

    new_status = "found" if req.found else "not_found"
    system_text = (
        "Driver confirmed: item is in the vehicle. Use this chat to arrange return."
        if req.found
        else "Driver confirmed: item was not found in the vehicle. Contact support if you need further help."
    )

    await db_supabase.update_one(
        "lost_and_found",
        {"id": case_id},
        {"status": new_status, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    await _insert_system_message(case_id, system_text)

    rider_user_id = case.get("rider_user_id") or case.get("reporter_id")
    if rider_user_id:
        push_title = "Your Lost Item: Update" if req.found else "Lost Item Report Update"
        push_body = (
            "Good news — your driver has the item. Open Lost & Found to arrange return."
            if req.found
            else "Your driver checked and the item was not found. Contact support for help."
        )
        await _push_safe(
            rider_user_id,
            push_title,
            push_body,
            {"type": "lost_and_found", "case_id": case_id},
            target_app="rider",
        )

    return {"success": True, "status": new_status}


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------


@api_router.get("/{case_id}/messages")
async def list_messages(
    case_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: Optional[str] = Query(None, description="ISO timestamp cursor for pagination"),
    current_user: dict = Depends(get_current_user),
):
    driver = await _driver_for_user(current_user["id"])
    await _require_participant(case_id, current_user["id"], driver["id"] if driver else None)

    filters: dict = {"lost_and_found_id": case_id}
    if before:
        filters["created_at"] = {"$lt": before}
    # Fetch newest `limit` messages, then reverse so clients receive them
    # in ascending (oldest-first) order for display.
    rows = await db_supabase.get_rows(
        "lost_and_found_messages",
        filters,
        order="created_at",
        desc=True,
        limit=limit,
    )
    return {"messages": list(reversed(rows))}


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


@api_router.post("/{case_id}/messages")
async def send_message(
    case_id: str,
    req: SendMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["id"]
    driver = await _driver_for_user(user_id)
    case = await _require_participant(case_id, user_id, driver["id"] if driver else None)

    if case.get("status") in ("not_found", "returned", "resolved", "unresolved", "closed"):
        raise HTTPException(status_code=400, detail="This case is closed")

    is_driver_sender = driver and case.get("driver_id") == driver["id"]
    sender_role = "driver" if is_driver_sender else "rider"

    msg = await db_supabase.insert_one(
        "lost_and_found_messages",
        {
            "id": str(uuid.uuid4()),
            "lost_and_found_id": case_id,
            "sender_id": user_id,
            "sender_role": sender_role,
            "message": req.message.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Push the other party
    if is_driver_sender:
        notify_id = case.get("rider_user_id") or case.get("reporter_id")
        if notify_id:
            await _push_safe(
                notify_id,
                "Lost & Found",
                req.message.strip(),
                {"type": "lost_and_found_message", "case_id": case_id},
                target_app="rider",
            )
    else:
        driver_user_id = None
        if case.get("driver_id"):
            d = await db_supabase.get_rows("drivers", {"id": case["driver_id"]}, limit=1)
            if d:
                driver_user_id = d[0].get("user_id")
        if driver_user_id:
            await _push_safe(
                driver_user_id,
                "Lost & Found",
                req.message.strip(),
                {"type": "lost_and_found_message", "case_id": case_id},
                target_app="driver",
            )

    return {"success": True, "message": msg}
