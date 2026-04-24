import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from firebase_admin import auth as firebase_auth
from loguru import logger

try:
    from .. import db_supabase
    from ..dependencies import verify_jwt_token
    from ..socket_manager import manager
    from ..utils.driver_presence import clear_presence, mark_present
except ImportError:
    import db_supabase
    from dependencies import verify_jwt_token
    from socket_manager import manager
    from utils.driver_presence import clear_presence, mark_present

db = db_supabase  # legacy alias

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


async def _handle_driver_ws_offline(connection_key: str | None, user: dict | None) -> None:
    """When a driver's WebSocket drops, flip their DB row offline so the
    admin panel and dispatch engine see reality.

    Without this, `drivers.is_online` stays True forever after a single Go
    Online toggle — through app closes, crashes, network drops — so admins
    see phantom online drivers and dispatch picks drivers whose app isn't
    running. is_online is now treated as "driver has intent AND active
    socket"; on reconnect the driver must tap Go Online again, matching
    standard rideshare behavior.
    """
    logger.info(
        f"[GO-ONLINE] _handle_driver_ws_offline ENTER connection_key={connection_key} "
        f"user_id={(user or {}).get('id')}"
    )
    if not connection_key or not connection_key.startswith("driver_") or not user:
        logger.info(f"[GO-ONLINE] _handle_driver_ws_offline SKIP (bad args) connection_key={connection_key}")
        return
    # If a newer WS has already reconnected under the same key, the driver is
    # actually still present — don't flip them offline just because this older
    # socket's disconnect handler fired late.
    if connection_key in manager.active_connections:
        logger.info(f"[GO-ONLINE] _handle_driver_ws_offline SKIP (newer WS present) connection_key={connection_key}")
        return
    try:
        driver_profile_off = await db.find_one("drivers", {"user_id": user["id"]})
        if not driver_profile_off:
            logger.info(f"[GO-ONLINE] _handle_driver_ws_offline SKIP (no driver row) user_id={user['id']}")
            return
        driver_id = driver_profile_off["id"]
        logger.info(
            f"[GO-ONLINE] _handle_driver_ws_offline FLIPPING driver_id={driver_id} "
            f"pre_is_online={driver_profile_off.get('is_online')} "
            f"pre_is_available={driver_profile_off.get('is_available')} "
            f"last_status_changed_at={driver_profile_off.get('last_status_changed_at')}"
        )
        # Only write if we're actually changing state — skip the write for
        # drivers who were already offline (e.g. WS opened but never toggled
        # online), to avoid unnecessary DB churn and updated_at bumps.
        was_online = bool(driver_profile_off.get("is_online"))
        if was_online or driver_profile_off.get("is_available"):
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                await db_supabase.update_one(
                    "drivers",
                    {"id": driver_id},
                    {
                        "is_online": False,
                        "is_available": False,
                        "updated_at": now_iso,
                        # Bump last_status_changed_at so admin "offline since …"
                        # reflects the actual disconnect time.
                        "last_status_changed_at": now_iso,
                    },
                )
            except Exception as _db_exc:
                logger.warning(
                    f"[WS] Could not flip driver {driver_id} offline on disconnect: {_db_exc}"
                )
            # Audit trail — only when the driver was actually intent-online,
            # so idle-socket disconnects (driver opened app, never tapped go
            # online) don't spam the log with no-op events.
            if was_online:
                try:
                    await db_supabase.insert_one(
                        "driver_activity_log",
                        {
                            "id": str(uuid.uuid4()),
                            "driver_id": driver_id,
                            "event_type": "went_offline",
                            "title": "Went offline (socket disconnected)",
                            "description": "Driver WebSocket closed — app was backgrounded, force-killed, or lost network.",
                            "metadata": {"reason": "ws_disconnect", "source": "websocket"},
                            "actor": "system",
                            "created_at": now_iso,
                        },
                    )
                except Exception as _log_exc:
                    logger.warning(f"[WS] activity log insert failed for {driver_id}: {_log_exc}")
        await manager.broadcast_to_admins(
            {
                "type": "driver_status_changed",
                "driver_id": driver_id,
                "is_online": False,
            }
        )
    except Exception as _exc:
        logger.warning(f"[WS] offline-on-disconnect handler failed for {connection_key}: {_exc}")


async def heartbeat_task(
    websocket: WebSocket,
    connection_key: str,
    conn_state: dict,
):
    """Background task that sends periodic ping messages to keep the connection
    alive and detect dead connections early. Critical for rideshare apps where
    a silently disconnected driver would miss ride offers.

    Detection works in two ways:

    1. If the ping itself fails to send (TCP buffer rejected, RST received),
       close the socket immediately — pre-existing behavior.
    2. If pings keep succeeding but the client hasn't sent a pong recently
       (phone died mid-connection, airplane mode, OS killed the app in the
       background — all cases where there's no clean close frame), we close
       the socket after a grace window. `conn_state["last_pong_at"]` is
       updated by the pong-handler branch in the main receive loop.

    Either path raises ``WebSocketDisconnect`` in the receive loop, which
    triggers ``_handle_driver_ws_offline`` and flips the DB row.
    """
    loop = asyncio.get_event_loop()
    # Tolerate one missed ping window before giving up — HEARTBEAT_INTERVAL
    # for the ping to go out, HEARTBEAT_TIMEOUT for the pong to come back,
    # plus one interval of slack for mobile network latency.
    stale_threshold = (HEARTBEAT_INTERVAL * 2) + HEARTBEAT_TIMEOUT
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await websocket.send_json(
                    {"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()}
                )
            except Exception:
                logger.info(f"Heartbeat send failed for {connection_key} — connection likely dead")
                break
            last_pong = conn_state.get("last_pong_at", 0.0)
            if loop.time() - last_pong > stale_threshold:
                logger.info(
                    f"[WS] {connection_key} no pong for {loop.time() - last_pong:.1f}s — "
                    f"closing stale connection"
                )
                try:
                    await websocket.close(code=1001)  # 1001 = going away
                except Exception:  # noqa: S110
                    pass
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
    # Track the driver row id when this socket authenticates as a driver so
    # the disconnect / error branches can clear Redis presence regardless of
    # where the drop happened.
    current_driver_id: str | None = None

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
                        "created_at": datetime.now(timezone.utc),
                        "profile_complete": False,
                    }
                    await db_supabase.create_user(new_user)
                    user = new_user
        except Exception:
            # Fallback to legacy JWT
            try:
                payload = verify_jwt_token(token)
                # Admin tokens are minted with a `role` claim and have no row
                # in the `users` table (admin-001 is env-var creds; admin_staff
                # rows live in a separate table). Without this branch the
                # lookup returns None and the client is told
                # invalid_token_or_user_not_found — which the monitoring
                # hook reacts to with exponential-backoff reconnects, i.e.
                # the repeating "WebSocket failed" loop seen in the admin
                # live-monitoring console. Mirrors get_current_user() in
                # dependencies/__init__.py.
                _admin_roles = {
                    "admin", "super_admin", "operations",
                    "support", "finance", "custom",
                }
                if payload.get("role") in _admin_roles and payload.get("email"):
                    user = {
                        "id": payload["user_id"],
                        "email": payload.get("email"),
                        "phone": payload.get("phone", ""),
                        "role": payload["role"],
                    }
                else:
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

        # Immediately ack auth so the client can flip its banner from
        # "Connection lost" to connected. Without this, the first server
        # message is the 30s-later heartbeat ping — the UI sits on
        # "Connection lost" for that whole window and users assume the
        # socket is broken (observed: repeated online toggles on Railway).
        await websocket.send_json({"type": "auth_success", "client_type": client_type})

        # Notify admins the driver's socket reconnected. Reflect whatever
        # the DB says about is_online — don't assume reconnect == online,
        # because the driver might still be toggled off (e.g. they opened
        # the app but haven't hit Go Online yet). Admins should always see
        # intent-based status, not socket-presence.
        if client_type == "driver":
            driver_profile_for_status = await db.find_one("drivers", {"user_id": user["id"]})
            if driver_profile_for_status:
                current_driver_id = driver_profile_for_status["id"]
                # Uber/Lyft-style presence: socket is alive → driver is present.
                # Expires after PRESENCE_TTL if the socket dies without a
                # clean disconnect. Refreshed on every pong and location ping.
                await mark_present(current_driver_id)
                await manager.broadcast_to_admins(
                    {
                        "type": "driver_status_changed",
                        "driver_id": current_driver_id,
                        "is_online": bool(driver_profile_for_status.get("is_online")),
                    }
                )

        # GAP FIX: Start heartbeat background task. conn_state is shared with
        # heartbeat_task so it can detect stale connections — every pong the
        # client sends bumps last_pong_at; the heartbeat closes the socket
        # if pongs stop arriving (phone died, app killed, airplane mode).
        conn_state: dict = {"last_pong_at": asyncio.get_event_loop().time()}
        hb_task = asyncio.create_task(heartbeat_task(websocket, connection_key, conn_state))

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

            # GAP FIX: Handle pong responses (client acknowledges our ping).
            # Bump last_pong_at so heartbeat_task knows the client is alive —
            # without this, the staleness check inside heartbeat_task would
            # treat every connection as dead after a few intervals.
            if data.get("type") == "pong":
                conn_state["last_pong_at"] = asyncio.get_event_loop().time()
                # Refresh presence TTL on every heartbeat — this is the
                # primary signal that the driver is still reachable.
                if current_driver_id:
                    await mark_present(current_driver_id)
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
                    # Location pings are an even stronger liveness signal
                    # than pongs — fresh GPS proves the app is running and
                    # foregrounded, not just that TCP is open.
                    await mark_present(driver_id)

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
                        "timestamp": datetime.now(timezone.utc),
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
                                    else datetime.now(timezone.utc),
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
                    # is_verified + status='active' prevent unverified / suspended
                    # drivers from being broadcast to riders via the realtime map.
                    drivers = await db_supabase.get_rows(
                        "drivers",
                        {
                            "is_online": True,
                            "is_available": True,
                            "is_verified": True,
                            "status": "active",
                        },
                        limit=100,
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
                            "timestamp": datetime.now(timezone.utc),
                        }

                        # Persist message to database
                        await db_supabase.insert_one("ride_messages", msg_data)

                        # Forward to connected target
                        if target:
                            # Format timestamp strings for JSON
                            msg_data["timestamp"] = msg_data["timestamp"].isoformat()
                            msg_data["type"] = "chat_message"
                            await manager.send_personal_message(msg_data, target)

            else:
                logger.warning(f"Unknown WS message type: {data.get('type')}")

    except WebSocketDisconnect as _wsd:
        # A newer WS can register under the same connection_key while this
        # one's disconnect handler is queued. Only run cleanup if THIS
        # specific socket is still the one the manager has for this key —
        # otherwise we'd evict the newer socket from active_connections
        # and clear its presence key, stranding a live connection.
        still_current = bool(
            connection_key and manager.active_connections.get(connection_key) is websocket
        )
        logger.info(
            f"[GO-ONLINE] WS branch=WebSocketDisconnect connection_key={connection_key} "
            f"code={getattr(_wsd, 'code', None)} reason={getattr(_wsd, 'reason', None)} "
            f"current_driver_id={current_driver_id} still_current={still_current}"
        )
        if still_current:
            # Remove ourselves from the manager dict FIRST. _handle_driver_ws_offline's
            # "newer WS present" check looks at whether the key is still populated
            # after we leave — with manager.disconnect happening after, the check
            # was always tripping on the current socket and the offline flip never
            # ran on a clean disconnect.
            manager.disconnect(connection_key)
            if current_driver_id:
                await clear_presence(current_driver_id)
            await _handle_driver_ws_offline(connection_key, user)
    except Exception as e:
        still_current = bool(
            connection_key and manager.active_connections.get(connection_key) is websocket
        )
        logger.exception(
            f"[GO-ONLINE] WS branch=Exception connection_key={connection_key} "
            f"current_driver_id={current_driver_id} still_current={still_current} err={e}"
        )
        if still_current:
            manager.disconnect(connection_key)
            if current_driver_id:
                await clear_presence(current_driver_id)
            await _handle_driver_ws_offline(connection_key, user)
        try:
            await websocket.close()
        except Exception:  # noqa: S110
            pass
    finally:
        # GAP FIX: Cancel heartbeat task on disconnect
        if hb_task:
            hb_task.cancel()
