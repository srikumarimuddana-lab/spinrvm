"""In-ride rider/driver chat and typing indicators.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Depends,
    Field,
    HTTPException,
    Request,
    RideStatus,
    datetime,
    get_current_user,
    parse_iso_utc,
    ride_message_limit,
    timezone,
    uuid,
)

router = APIRouter()


@router.get("/{ride_id}/chat-status")
async def get_chat_status(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Check if chat is available for this ride (active rides + 24h post-trip window)."""
    ride = await _deps.db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Authorization: only the rider or the assigned driver may query chat
    # status. Without this guard any authenticated user could probe an
    # arbitrary ride_id and learn whether it exists and its status/timing.
    # Return the SAME 404 as a missing ride for an unauthorized caller — a 403
    # here would still leak ride existence (403 = exists-but-not-yours vs
    # 404 = no-such-ride), the exact disclosure this guard closes.
    if ride.get("rider_id") != current_user["id"]:
        driver = (lambda _r: _r[0] if _r else None)(
            await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        )
        if not (driver and ride.get("driver_id") == driver["id"]):
            raise HTTPException(status_code=404, detail="Ride not found")

    status = ride.get("status", "")
    if status == RideStatus.CANCELLED:
        return {"available": False, "reason": "Ride was cancelled"}

    if status == RideStatus.COMPLETED:
        completed_at = parse_iso_utc(ride.get("ride_completed_at") or ride.get("updated_at"))
        if completed_at:
            elapsed = (datetime.now(timezone.utc) - completed_at).total_seconds()
            remaining = max(0, 86400 - elapsed)
            if remaining <= 0:
                return {"available": False, "reason": "Post-trip chat window expired"}
            hours_left = int(remaining // 3600)
            return {"available": True, "post_trip": True, "hours_remaining": hours_left}
        return {"available": True, "post_trip": True, "hours_remaining": 24}

    # Active ride — chat is fully available
    return {"available": True, "post_trip": False}


# NOTE: there is deliberately no GET /{ride_id}/call endpoint. Rider↔driver
# contact is in-app chat only — real phone numbers are never exposed to the
# other party (privacy decision, 2026-06). test_coverage_rides.py pins this.


@router.get("/{ride_id}/messages")
async def get_ride_messages(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch persistent chat messages for a ride"""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Verify the user is part of the ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to track this ride")

    messages = await _deps.db_supabase.get_rows(
        "ride_messages", {"ride_id": ride_id}, limit=100, order="timestamp", desc=False
    )

    # Serialize datetime
    serialized = []
    for msg in messages:
        # Provide fallback serialize
        if "timestamp" in msg and isinstance(msg["timestamp"], datetime):
            msg["timestamp"] = msg["timestamp"].isoformat()
        serialized.append(msg)

    return {"success": True, "messages": serialized}


class SendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


@router.post("/{ride_id}/messages")
@ride_message_limit
async def send_ride_message(
    ride_id: str,
    body: SendMessageRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Send a chat message for an active or recently completed ride.

    Persists the message in `ride_messages` and forwards it to the
    other party via WebSocket (if they're connected). Works as a REST
    fallback for screens that don't hold a direct WS reference (e.g.
    the rider-app chat screen).

    Post-trip chat: messages are allowed for 24 hours after ride
    completion to support lost-item, feedback, and coordination use cases.
    Only the rider or the assigned driver of the ride can send.
    """
    ride = await _deps.db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Block chat on cancelled rides
    if ride.get("status") == RideStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Cannot send messages on a cancelled ride")

    # Post-trip chat window: allow messages for 24h after completion
    if ride.get("status") == RideStatus.COMPLETED:
        completed_at = parse_iso_utc(ride.get("ride_completed_at") or ride.get("updated_at"))
        if completed_at and (datetime.now(timezone.utc) - completed_at).total_seconds() > 86400:
            raise HTTPException(status_code=400, detail="Post-trip chat window has expired (24 hours)")

    is_rider = ride.get("rider_id") == current_user["id"]
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to send messages in this ride")

    sender = "rider" if is_rider else "driver"
    msg_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "text": body.text.strip(),
        "sender": sender,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await _deps.db.insert_one("ride_messages", msg_data)

    # Forward to the other party via WebSocket + push notification (for
    # backgrounded/offline recipients). The push fires as a background task
    # so it never adds latency to the HTTP response.
    target = None
    push_recipient_user_id: str | None = None
    push_target_app: str | None = None
    push_deeplink: str | None = None

    if sender == "rider" and ride.get("driver_id"):
        d = await _deps.db.find_one("drivers", {"id": ride["driver_id"]})
        if d and d.get("user_id"):
            target = f"driver_{d['user_id']}"
            push_recipient_user_id = d["user_id"]
            push_target_app = "driver"
            push_deeplink = f"/driver/chat?rideId={ride_id}"
    elif sender == "driver":
        target = f"rider_{ride['rider_id']}"
        push_recipient_user_id = ride["rider_id"]
        push_target_app = "rider"
        push_deeplink = f"/chat-driver?rideId={ride_id}"

    if target:
        await _deps.manager.send_personal_message({**msg_data, "type": "chat_message"}, target)

    if push_recipient_user_id:
        sender_name = (
            (current_user.get("first_name") or "").strip()
            or (current_user.get("name") or "").strip()
            or ("Rider" if sender == "rider" else "Driver")
        )
        preview = body.text.strip()
        if len(preview) > 100:
            preview = preview[:97] + "…"
        _deps.spawn(
            _deps.send_push_notification(
                push_recipient_user_id,
                f"Message from {sender_name}",
                preview,
                {
                    "type": "chat_message",
                    "ride_id": ride_id,
                    "deeplink": push_deeplink or "",
                },
                priority="normal",
                target_app=push_target_app,
            )
        )

    return {"success": True, "message": msg_data}


class TypingRequest(BaseModel):
    sender: str


@router.post("/{ride_id}/typing")
async def send_typing_indicator(
    ride_id: str,
    body: TypingRequest,
    current_user: dict = Depends(get_current_user),
):
    """Broadcast a typing indicator to the other party via WebSocket."""
    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    uid = current_user["id"]
    rider_id = ride.get("rider_id")
    driver_id = ride.get("driver_id")

    if uid == rider_id:
        sender = "rider"
        target = f"driver_{driver_id}" if driver_id else None
    elif uid == driver_id or uid in ((await _deps.db_supabase.get_driver_by_id(driver_id) or {}).get("user_id", ""),):
        sender = "driver"
        target = f"rider_{rider_id}" if rider_id else None
    else:
        raise HTTPException(status_code=403, detail="Not a participant")

    if target:
        await _deps.manager.send_personal_message(
            {"type": "chat_typing", "ride_id": ride_id, "sender": sender},
            target,
        )

    return {"success": True}
