import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

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


async def _read_token_version(connection_key: str, user_id: str) -> Optional[int]:
    """Read the row's current ``token_version`` for this connection.

    The connection_key prefix tells us which table to hit:
    ``admin_*`` → admin_staff, ``rider_*``/``driver_*`` → users.

    Returns the integer token_version, or None if the read failed
    (transient DB hiccup) or the row no longer exists. Callers MUST
    treat None as "do not act" — closing a healthy socket because of
    a one-off DB blip would be a worse failure mode than letting a
    revoked token live another 30s until the next heartbeat tick.
    """
    try:
        if connection_key.startswith("admin_"):
            row = await db.find_one("admin_staff", {"id": user_id})
        else:
            row = await db.get_user_by_id(user_id)
        if not row:
            return None
        return int(row.get("token_version") or 0)
    except Exception as e:
        logger.warning(f"WS token_version read failed for {connection_key}: {e}")
        return None


async def heartbeat_task(
    websocket: WebSocket,
    connection_key: str,
    *,
    user_id: str,
    claim_token_version: int,
):
    """Background task that sends periodic ping messages to keep the connection alive
    and detect dead connections early. This is critical for rideshare apps where
    a silently disconnected driver would miss ride offers.

    B-P1-11: also re-validates the user's ``token_version`` each tick.
    If /auth/logout-all (or the B-P1-3 reuse cascade) bumped the row's
    version since this socket connected, close the socket so the user
    is forced through /auth/refresh — which will fail (refresh tokens
    revoked) and surface session-expired UX. Without this re-check, a
    user who hit "Sign out everywhere" would keep receiving ride
    events on the old socket until it dropped on its own.

    DB read failure is treated as "do not act" — see _read_token_version."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)

            stored_version = await _read_token_version(connection_key, user_id)
            if stored_version is not None and stored_version > claim_token_version:
                logger.info(
                    f"WS heartbeat: token_version stale for {connection_key} "
                    f"(stored={stored_version} > claim={claim_token_version}); closing"
                )
                try:
                    await websocket.send_json(
                        {"type": "session_revoked", "reason": "token_revoked"}
                    )
                except Exception:  # noqa: S110 — socket may already be dead
                    pass
                try:
                    await websocket.close(code=1008, reason="token_revoked")
                except Exception:  # noqa: S110
                    pass
                break

            try:
                await websocket.send_json({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()})
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

        # B-P1-11: Reject the handshake if the JWT's token_version claim
        # is below the row's current value. This catches the case where
        # the user called /auth/logout-all and is now reconnecting with
        # a stale token they had cached client-side. HTTP requests
        # already enforce this in dependencies.py::_token_version_mismatch;
        # without the same gate here, a stale-token reconnect would
        # silently succeed and start receiving ride events again.
        # Firebase tokens carry no token_version claim — treated as 0,
        # which matches the existing HTTP-side behaviour.
        try:
            claim_token_version = int((payload or {}).get("token_version") or 0)
        except (TypeError, ValueError):
            claim_token_version = 0
        try:
            stored_token_version = int((user or {}).get("token_version") or 0)
        except (TypeError, ValueError):
            stored_token_version = 0
        if claim_token_version < stored_token_version:
            await websocket.send_json({"type": "error", "message": "session_revoked"})
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
        # B-P1-11: pass user_id + claim_token_version so the heartbeat
        # can re-validate against the DB row each tick.
        hb_task = asyncio.create_task(
            heartbeat_task(
                websocket,
                connection_key,
                user_id=user["id"],
                claim_token_version=claim_token_version,
            )
        )

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

            # B-P1-12: Per-USER rate limiting (not per-connection).
            # Replaces the old closure-scoped _msg_timestamps which an
            # attacker could side-step by opening N sockets to get
            # N×WS_MAX_MESSAGES_PER_SECOND throughput. The manager's
            # bucket aggregates across every WebSocket the user has
            # open on this machine. See docs/runbooks/websockets.md
            # for the multi-replica caveat. We drop the offending
            # message but keep the socket alive — a brief burst from
            # a buggy reconnect should not force a re-auth round-trip.
            if not manager.note_user_message(
                user["id"], max_per_second=WS_MAX_MESSAGES_PER_SECOND,
            ):
                await websocket.send_json(
                    {
                        "type": "rate_limited",
                        "scope": "user",
                        "limit": WS_MAX_MESSAGES_PER_SECOND,
                        "window_seconds": 1,
                        "retry_after_seconds": 1,
                    }
                )
                continue

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
