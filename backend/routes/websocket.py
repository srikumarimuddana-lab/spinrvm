import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from firebase_admin import auth as firebase_auth

try:
    from .. import db_supabase
    from ..dependencies import verify_jwt_token
    from ..socket_manager import manager
except ImportError:
    import db_supabase
    from dependencies import verify_jwt_token
    from socket_manager import manager

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

# Note: WebSocket routes are usually attached directly to the app, but APIRouter supports them too.
# However, the original server.py had it on @app.websocket.
# We will define a function here that can be registered in server.py, or use a router.
# Let's use a router.

router = APIRouter()

# GAP FIX: Heartbeat constants (matching industry standard for rideshare apps)
HEARTBEAT_INTERVAL = 30  # Send ping every 30 seconds
HEARTBEAT_TIMEOUT = 10  # Expect pong within 10 seconds

# Rate limiting: max messages per second per connection
WS_MAX_MESSAGES_PER_SECOND = 30
WS_MAX_MESSAGE_SIZE = 64 * 1024  # 64 KB max message payload


async def heartbeat_task(websocket: WebSocket, connection_key: str):
    """Background task that sends periodic ping messages to keep the connection alive
    and detect dead connections early. This is critical for rideshare apps where
    a silently disconnected driver would miss ride offers."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await websocket.send_json({"type": "ping", "timestamp": datetime.utcnow().isoformat()})
            except Exception:
                logger.info(f"Heartbeat failed for {connection_key} - connection likely dead")
                break
    except asyncio.CancelledError:
        pass


@router.websocket("/ws/{client_type}/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_type: str, client_id: str):
    """Require clients to authenticate via a first 'auth' message that contains a Firebase ID token or legacy JWT.

    After successful verification we register the connection as '{client_type}_{user_id}' and proceed to handle messages.
    """
    await websocket.accept()
    user = None
    connection_key = None
    hb_task = None

    try:
        # Require the first message to be an auth message containing a token
        auth_msg = await websocket.receive_json()
        if not auth_msg or auth_msg.get("type") != "auth" or not auth_msg.get("token"):
            await websocket.send_json({"type": "error", "message": "authentication_required"})
            await websocket.close()
            return

        token = auth_msg.get("token")
        # Try Firebase token first
        try:
            payload = firebase_auth.verify_id_token(token)
            uid = payload.get("uid") or payload.get("user_id")
            user = await db_supabase.get_user_by_id(uid)
            if not user:
                phone = payload.get("phone_number")
                if phone:
                    user = await db_supabase.get_user_by_phone(phone)
                if not user:
                    new_user = {
                        "id": uid,
                        "phone": phone or "",
                        "role": "rider",
                        "created_at": datetime.utcnow(),
                        "profile_complete": False,
                    }
                    await db_supabase.create_user(new_user)
                    user = new_user
        except Exception:
            # Fallback to legacy JWT
            try:
                payload = verify_jwt_token(token)
                user = await db_supabase.get_user_by_id(payload["user_id"])
            except Exception:
                user = None

        if not user:
            await websocket.send_json({"type": "error", "message": "invalid_token_or_user_not_found"})
            await websocket.close()
            return

        # If connecting as driver, ensure user has a driver profile
        if client_type == "driver":
            driver_profile = (lambda _r: _r[0] if _r else None)(
                await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
            )
            if not driver_profile:
                await websocket.send_json({"type": "error", "message": "user_is_not_a_driver"})
                await websocket.close()
                return
        elif client_type == "admin":
            # Admin clients must have admin or super_admin role
            if user.get("role") not in ("admin", "super_admin"):
                await websocket.send_json({"type": "error", "message": "admin_access_required"})
                await websocket.close()
                return

        # Register the connection with a server-controlled key to prevent impersonation
        connection_key = f"{client_type}_{user['id']}"
        await manager.connect(websocket, connection_key)

        # Notify admins that a driver came online
        if client_type == "driver":
            driver_profile_for_status = await db.find_one("drivers", {"user_id": user["id"]})
            if driver_profile_for_status:
                await manager.broadcast_to_admins(
                    {
                        "type": "driver_status_changed",
                        "driver_id": driver_profile_for_status["id"],
                        "is_online": True,
                    }
                )

        # GAP FIX: Start heartbeat background task
        hb_task = asyncio.create_task(heartbeat_task(websocket, connection_key))

        # Rate limiting state
        _msg_timestamps: list = []

        # Main message loop
        while True:
            raw = await websocket.receive_text()

            # Message size guard
            if len(raw) > WS_MAX_MESSAGE_SIZE:
                await websocket.send_json({"type": "error", "message": "message_too_large"})
                continue

            import json as _json

            try:
                data = _json.loads(raw)
            except (ValueError, TypeError):
                await websocket.send_json({"type": "error", "message": "invalid_json"})
                continue

            # Per-connection rate limiting
            now_ts = asyncio.get_event_loop().time()
            _msg_timestamps = [t for t in _msg_timestamps if now_ts - t < 1.0]
            if len(_msg_timestamps) >= WS_MAX_MESSAGES_PER_SECOND:
                await websocket.send_json({"type": "error", "message": "rate_limited"})
                continue
            _msg_timestamps.append(now_ts)

            # GAP FIX: Handle pong responses (client acknowledges our ping)
            if data.get("type") == "pong":
                # Client is alive, nothing to do
                continue

            if data.get("type") in ("driver_location", "location_update"):
                # Accept both message types for backwards compat
                driver_id = data.get("driver_id")
                lat = data.get("lat")
                lng = data.get("lng")

                # If driver_id not sent, look it up from the authenticated user
                if not driver_id and client_type == "driver":
                    dp = (lambda _r: _r[0] if _r else None)(
                        await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
                    )
                    if dp:
                        driver_id = dp["id"]

                # Verify driver ownership
                is_valid_driver = False
                if client_type == "driver" and driver_id:
                    owned_driver = (lambda _r: _r[0] if _r else None)(
                        await db_supabase.get_rows("drivers", {"id": driver_id, "user_id": user["id"]}, limit=1)
                    )
                    if owned_driver:
                        is_valid_driver = True

                if driver_id and lat and lng and is_valid_driver:
                    manager.update_driver_location(driver_id, lat, lng)
                    await db_supabase.update_driver_location(driver_id, lat, lng)

                    # ── Persist GPS breadcrumb ──────────────────────
                    active_ride = (lambda _r: _r[0] if _r else None)(
                        await db_supabase.get_rows(
                            "rides",
                            {
                                "driver_id": driver_id,
                                "status": {
                                    "$in": ["driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]
                                },
                            },
                            limit=1,
                        )
                    )
                    ride_id = active_ride["id"] if active_ride else None

                    # Determine tracking phase
                    tracking_phase = "online_idle"
                    if active_ride:
                        status_map = {
                            "driver_assigned": "navigating_to_pickup",
                            "driver_accepted": "navigating_to_pickup",
                            "driver_arrived": "arrived_at_pickup",
                            "in_progress": "trip_in_progress",
                        }
                        tracking_phase = status_map.get(active_ride.get("status", ""), "online_idle")

                    breadcrumb = {
                        "id": str(uuid.uuid4()),
                        "driver_id": driver_id,
                        "ride_id": ride_id,
                        "lat": lat,
                        "lng": lng,
                        "speed": data.get("speed"),
                        "heading": data.get("heading"),
                        "tracking_phase": tracking_phase,
                        "timestamp": datetime.utcnow(),
                    }
                    # 'accuracy' and 'altitude' columns seem missing in Supabase schema, so omitted for now.

                    await db_supabase.insert_one("driver_location_history", breadcrumb)

                    # Forward to rider in real-time
                    rides = await db_supabase.get_rows(
                        "rides",
                        {
                            "driver_id": driver_id,
                            "status": {"$in": ["driver_assigned", "driver_arrived", "in_progress"]},
                        },
                        limit=10,
                    )
                    for ride in rides:
                        await manager.send_personal_message(
                            {
                                "type": "driver_location_update",
                                "driver_id": driver_id,
                                "lat": lat,
                                "lng": lng,
                                "speed": data.get("speed"),
                                "heading": data.get("heading"),
                            },
                            f"rider_{ride['rider_id']}",
                        )

                    # Broadcast live location to all connected admin monitoring clients
                    await manager.broadcast_to_admins(
                        {
                            "type": "driver_location_update",
                            "driver_id": driver_id,
                            "lat": lat,
                            "lng": lng,
                            "speed": data.get("speed"),
                            "heading": data.get("heading"),
                        }
                    )

            elif data.get("type") == "location_batch":
                # Batch upload of buffered GPS points (offline recovery)
                points = data.get("points", [])
                driver_id = data.get("driver_id")
                if not driver_id and client_type == "driver":
                    dp = (lambda _r: _r[0] if _r else None)(
                        await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
                    )
                    if dp:
                        driver_id = dp["id"]
                if driver_id and points and client_type == "driver":
                    owned = (lambda _r: _r[0] if _r else None)(
                        await db_supabase.get_rows("drivers", {"id": driver_id, "user_id": user["id"]}, limit=1)
                    )
                    if owned:
                        docs = []
                        for pt in points[:500]:  # cap at 500 points per batch
                            docs.append(
                                {
                                    "id": str(uuid.uuid4()),
                                    "driver_id": driver_id,
                                    "ride_id": pt.get("ride_id"),
                                    "lat": pt.get("lat"),
                                    "lng": pt.get("lng"),
                                    "speed": pt.get("speed"),
                                    "heading": pt.get("heading"),
                                    "accuracy": pt.get("accuracy"),
                                    "altitude": pt.get("altitude"),
                                    "tracking_phase": pt.get("tracking_phase", "online_idle"),
                                    "timestamp": datetime.fromisoformat(pt["timestamp"])
                                    if pt.get("timestamp")
                                    else datetime.utcnow(),
                                }
                            )
                        if docs:
                            await db_supabase.insert_many("driver_location_history", docs)
                        await websocket.send_json({"type": "location_batch_ack", "count": len(docs)})

            elif data.get("type") == "ride_status_update":
                ride_id = data.get("ride_id")
                status = data.get("status")
                if ride_id and status:
                    ride = await db_supabase.get_ride(ride_id)
                    if ride:
                        await manager.send_personal_message(
                            {"type": "ride_status_changed", "ride_id": ride_id, "status": status},
                            f"rider_{ride['rider_id']}",
                        )
                        # Broadcast to admin monitoring clients
                        await manager.broadcast_to_admins(
                            {
                                "type": "ride_status_changed",
                                "ride_id": ride_id,
                                "status": status,
                            }
                        )

            elif data.get("type") == "get_nearby_drivers":
                lat = data.get("lat")
                lng = data.get("lng")
                radius = data.get("radius", 5)  # km
                if lat and lng:
                    drivers = await db_supabase.get_rows(
                        "drivers", {"is_online": True, "is_available": True}, limit=100
                    )

                    nearby = []
                    for driver in drivers:
                        # Calculate distance
                        from ..geo_utils import calculate_distance

                        dist = calculate_distance(lat, lng, driver["lat"], driver["lng"])
                        if dist <= radius:
                            nearby.append(
                                {
                                    "id": driver["id"],
                                    "lat": driver["lat"],
                                    "lng": driver["lng"],
                                    "vehicle_type_id": driver["vehicle_type_id"],
                                }
                            )

                    await websocket.send_json({"type": "nearby_drivers", "drivers": nearby})

            elif data.get("type") == "chat_message":
                ride_id = data.get("ride_id")
                message = data.get("text")
                sender = data.get("sender")
                if ride_id and message:
                    ride = await db_supabase.get_ride(ride_id)
                    if ride:
                        target = None
                        if sender == "driver":
                            target = f"rider_{ride['rider_id']}"
                        elif sender == "rider" and ride.get("driver_id"):
                            driver = await db_supabase.get_driver_by_id(ride["driver_id"])
                            if driver and driver.get("user_id"):
                                target = f"driver_{driver['user_id']}"

                        msg_data = {
                            "id": str(uuid.uuid4()),
                            "ride_id": ride_id,
                            "text": message,
                            "sender": sender,
                            "timestamp": datetime.utcnow(),
                        }

                        # Persist message to database
                        await db_supabase.insert_one("ride_messages", msg_data)

                        # Forward to connected target
                        if target:
                            # Format timestamp strings for JSON
                            msg_data["timestamp"] = msg_data["timestamp"].isoformat()
                            msg_data["type"] = "chat_message"
                            await manager.send_personal_message(msg_data, target)

    except WebSocketDisconnect:
        if connection_key and connection_key.startswith("driver_"):
            # Notify admins the driver went offline
            driver_profile_off = await db.find_one("drivers", {"user_id": user["id"]}) if user else None
            if driver_profile_off:
                await manager.broadcast_to_admins(
                    {
                        "type": "driver_status_changed",
                        "driver_id": driver_profile_off["id"],
                        "is_online": False,
                    }
                )
        if connection_key:
            manager.disconnect(connection_key)
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        if connection_key:
            manager.disconnect(connection_key)
        try:
            await websocket.close()
        except Exception:  # noqa: S110
            pass
    finally:
        # GAP FIX: Cancel heartbeat task on disconnect
        if hb_task:
            hb_task.cancel()
