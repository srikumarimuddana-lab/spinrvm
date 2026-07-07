import asyncio
import math
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from firebase_admin import auth as firebase_auth
from loguru import logger

try:
    from ..utils.breadcrumb_buffer import buffer_ride_breadcrumb, flush_driver_breadcrumbs
    from ..utils.breadcrumbs import persist_ride_breadcrumbs, resolve_active_rides_cached
    from ..utils.location_integrity import check_location_integrity
except ImportError:
    from utils.breadcrumb_buffer import buffer_ride_breadcrumb, flush_driver_breadcrumbs  # type: ignore
    from utils.breadcrumbs import persist_ride_breadcrumbs, resolve_active_rides_cached  # type: ignore
    from utils.location_integrity import check_location_integrity  # type: ignore

try:
    from .. import db_supabase
    from ..core.config import settings
    from ..dependencies import (
        _account_inaccessible,
        _firebase_session_revoked,
        _verify_admin_payload,
        verify_jwt_token,
    )
    from ..socket_manager import manager
    from ..utils.driver_presence import clear_presence, mark_present
    from ..utils.redis_client import redis_expire, redis_incr
except ImportError:
    import db_supabase
    from core.config import settings
    from dependencies import (
        _account_inaccessible,
        _firebase_session_revoked,
        _verify_admin_payload,
        verify_jwt_token,
    )
    from socket_manager import manager
    from utils.driver_presence import clear_presence, mark_present
    from utils.redis_client import redis_expire, redis_incr  # type: ignore

db = db_supabase  # legacy alias

# ETA helpers — separate try/except so they survive the dual-import pattern
# without the formatter stripping them from the main block's except branch.
try:
    from ..settings_loader import get_app_settings
    from ..utils.maps_eta import get_cached_ride_eta, get_ride_eta_seconds, refresh_ride_eta
except ImportError:
    try:
        from settings_loader import get_app_settings  # type: ignore[no-redef]
        from utils.maps_eta import (  # type: ignore[no-redef]
            get_cached_ride_eta,
            get_ride_eta_seconds,
            refresh_ride_eta,
        )
    except ImportError:
        # Stubs used when backend is imported outside its package (e.g. tests).
        async def get_app_settings():  # type: ignore[misc]
            return {}

        async def get_ride_eta_seconds(*_a, **_kw):  # type: ignore[misc]
            return None

        async def get_cached_ride_eta(*_a, **_kw):  # type: ignore[misc]
            return None

        async def refresh_ride_eta(*_a, **_kw):  # type: ignore[misc]
            return None


# ── Maps API key in-process cache ─────────────────────────────────────────
# get_app_settings() hits Supabase on every call. We cache the key for 60 s
# so the hot GPS-ping loop (60 pings/min/driver) stays DB-free.
_maps_key_cache: str = ""
_maps_key_fetched_at: float = 0.0
_MAPS_KEY_CACHE_TTL = 60.0  # seconds

# Statuses where Distance Matrix ETA is sent to the rider (driver en-route to
# pickup only). "in_progress" is intentionally excluded: during the trip the
# rider app computes ETA client-side via haversine, saving ~60 Maps API calls
# per 15-minute ride. Do not add "in_progress" here without adjusting billing.
_ETA_PICKUP_STATUSES = {"driver_assigned", "driver_accepted", "driver_arrived"}
# Statuses for which the driver's live location is forwarded to the RIDER so the
# car marker moves on their map. Starts at driver_accepted — once the offer is
# accepted the rider is watching the driver approach (the "driver arriving"
# screen). Excludes pre-acceptance states (searching / driver_assigned) so an
# offered-but-not-yet-accepted driver's position is never leaked to the rider.
_RIDER_LOCATION_STATUSES = {"driver_accepted", "driver_arrived", "in_progress"}

# Note: WebSocket routes are usually attached directly to the app, but APIRouter supports them too.
# However, the original server.py had it on @app.websocket.
# We will define a function here that can be registered in server.py, or use a router.
# Let's use a router.

router = APIRouter()


def _parse_live_coordinate(value):
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _valid_live_coordinates(lat: float, lng: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


# GAP FIX: Heartbeat constants — tightened from 30s to 10s to match
# Uber's ~4s/7s cadence more closely. A 30s ping meant a dead connection
# could go undetected for 45s+ (driver misses 2-3 ride offers).
HEARTBEAT_INTERVAL = 10  # Send ping every 10 seconds
HEARTBEAT_TIMEOUT = 10  # Expect pong within 10 seconds

# Rate limiting: max messages per second per connection
WS_MAX_MESSAGES_PER_SECOND = 30
WS_MAX_MESSAGE_SIZE = 64 * 1024  # 64 KB max message payload

# Per-connection cooldown for ride_status_update echoes. Each echo costs a
# get_ride read plus a rider send and an all-admin broadcast; the global
# 30 msg/s socket cap alone would let any ride participant amplify that
# against the DB and every admin console. Legit clients send this on screen
# focus / resync, never in bursts.
RIDE_STATUS_ECHO_COOLDOWN_S = 2.0

# F8: min seconds between durable drivers-row location UPDATEs per connection.
# 1 Hz pings still refresh the Redis cache + rider/admin fan-out every tick;
# only the authoritative Postgres write is throttled to this interval.
_DRIVER_LOC_DB_WRITE_INTERVAL_S = 3.0

_ADMIN_ROLES = {"admin", "super_admin", "operations", "support", "finance", "custom"}


async def _handle_driver_ws_disconnect(connection_key: str | None, user: dict | None) -> None:
    """Narrow post-disconnect hook: surface "socket dropped" to admins.

    Intent (``drivers.is_online``) and reachability (Redis presence) are now
    separated Uber/Lyft-style. The socket dying does NOT mean the driver
    tapped Stop — the app could be momentarily backgrounded, on a flaky
    tunnel, or restarting after an OS memory pressure kill. Flipping
    ``is_online=False`` here produced the "driver taps Go Online, phone
    shows offline a second later" bug because the iOS background-location
    permission dialog sends the app into 'inactive', which closed the WS,
    which ran this handler, which overwrote the Go-Online write.

    So this function deliberately does NOT write to the drivers table.
    It only:

    1. Broadcasts a ``driver_connection_lost`` admin event so the live
       monitoring dashboard can show "intent: online / reachable: no" —
       the same visual distinction Uber's ops console makes.
    2. Records a best-effort audit entry so operators can see *why* the
       driver stopped appearing in dispatch (socket died) even though
       the DB still says online.

    Reconciliation is handled elsewhere: the presence sweeper flips
    ``is_online=False`` for drivers whose presence TTL has expired for
    longer than the grace window, and dispatch already filters on live
    presence so ghost-online rows can't be routed to.
    """
    if not connection_key or not connection_key.startswith("driver_") or not user:
        return
    # A newer WS reconnecting under the same key means this late-firing
    # disconnect is stale — nothing to broadcast.
    if connection_key in manager.active_connections:
        return
    try:
        driver_profile_off = await db.find_one("drivers", {"user_id": user["id"]})
        if not driver_profile_off:
            return
        driver_id = driver_profile_off["id"]
        was_online = bool(driver_profile_off.get("is_online"))

        # Only log + broadcast for drivers who were actually intent-online;
        # idle sockets (app open, never tapped Go Online) disconnecting is
        # not operationally interesting.
        if was_online:
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                await db_supabase.insert_one(
                    "driver_activity_log",
                    {
                        "id": str(uuid.uuid4()),
                        "driver_id": driver_id,
                        "event_type": "connection_lost",
                        "title": "Connection lost",
                        "description": "Driver WebSocket closed — app backgrounded, force-killed, or lost network. Intent stays online; presence sweeper will reconcile if the app doesn't reconnect.",
                        "metadata": {"reason": "ws_disconnect", "source": "websocket"},
                        "actor": "system",
                        "created_at": now_iso,
                    },
                )
            except Exception as _log_exc:
                logger.warning(f"[WS] activity log insert failed for {driver_id}: {_log_exc}")

            await manager.broadcast_to_admins(
                {
                    "type": "driver_connection_lost",
                    "driver_id": driver_id,
                    "is_reachable": False,
                }
            )
    except Exception as _exc:
        logger.warning(f"[WS] disconnect handler failed for {connection_key}: {_exc}")


async def _read_token_version(connection_key: str, user_id: str) -> int | None:
    """Read the row's current ``token_version`` for this connection (B-P1-11).

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
            row = await db_supabase.get_user_by_id(user_id)
        if not row:
            return None
        return int(row.get("token_version") or 0)
    except Exception as e:
        logger.warning(f"WS token_version read failed for {connection_key}: {e}")
        return None


async def _read_firebase_session_revoked(connection_key: str, user_id: str, auth_time: int) -> bool:
    """Re-read the user's ``sessions_invalid_before`` watermark and return True
    if a Firebase token with this ``auth_time`` is now revoked (B-P1-11).

    Firebase ID tokens carry no token_version claim, so the heartbeat re-checks
    the watermark instead — mirroring the HTTP path and the WS handshake. DB read
    failure or a missing row is treated as "do not act" (returns False), matching
    _read_token_version, so a transient blip never closes a healthy socket.
    """
    try:
        row = await db_supabase.get_user_by_id(user_id)
        if not row:
            return False
        return _firebase_session_revoked({"auth_time": auth_time}, row.get("sessions_invalid_before"))
    except Exception as e:
        logger.warning(f"WS watermark read failed for {connection_key}: {e}")
        return False


async def heartbeat_task(
    websocket: WebSocket,
    connection_key: str,
    conn_state: dict | None = None,
    *,
    user_id: str | None = None,
    driver_id: str | None = None,
    claim_token_version: int = 0,
    firebase_auth_time: int | None = None,
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

    B-P1-11 also re-validates the user's ``token_version`` each tick.
    If /auth/logout-all (or the B-P1-3 reuse cascade) bumped the row's
    version since this socket connected, close the socket so the user
    is forced through /auth/refresh — which will fail (refresh tokens
    revoked) and surface session-expired UX. Without this re-check, a
    user who hit "Sign out everywhere" would keep receiving ride
    events on the old socket until it dropped on its own. DB read
    failure is treated as "do not act" — see _read_token_version.

    Either path raises ``WebSocketDisconnect`` in the receive loop, which
    triggers ``_handle_driver_ws_disconnect`` to notify admins but does NOT
    write ``is_online=False``. Presence handling differs by cause: a
    revocation close clears the driver's presence key here immediately (it is
    a deliberate server kill); a plain network drop is left for the 30s
    presence TTL so a reconnect within the grace window doesn't hide the
    driver from riders. The presence sweeper is the reconciliation layer that
    flips ``is_online`` when a driver stays unreachable past the grace window.
    """
    loop = asyncio.get_event_loop()
    # Tolerate one missed ping window before giving up — HEARTBEAT_INTERVAL
    # for the ping to go out, HEARTBEAT_TIMEOUT for the pong to come back,
    # plus one interval of slack for mobile network latency.
    stale_threshold = (HEARTBEAT_INTERVAL * 2) + HEARTBEAT_TIMEOUT
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)

            # B-P1-11: token_version re-validation. Skipped when user_id
            # wasn't passed (legacy callers), so this hook is opt-in and
            # backwards-compatible with any future heartbeat caller that
            # hasn't been updated.
            if user_id:
                if firebase_auth_time is not None:
                    # Firebase connection: token_version is meaningless (no
                    # claim), so re-check the sessions_invalid_before watermark.
                    _revoked = await _read_firebase_session_revoked(connection_key, user_id, firebase_auth_time)
                else:
                    stored_version = await _read_token_version(connection_key, user_id)
                    _revoked = stored_version is not None and stored_version > claim_token_version
                if _revoked:
                    logger.info(f"WS heartbeat: session revoked for {connection_key}; closing")
                    # Revocation is a DELIBERATE server-forced close (Sign out
                    # everywhere / token-version bump / Firebase session
                    # invalidation), not a flaky-network blip. Clear the driver's
                    # presence key NOW rather than letting the 30s TTL lapse:
                    # otherwise /drivers/nearby + dispatch would keep the revoked
                    # driver reachable for up to 30s and could route an offer to a
                    # socket that no longer exists. The involuntary-disconnect
                    # grace only applies to network drops, which stay untouched.
                    #
                    # Ownership guard: if the driver already reconnected (a newer
                    # socket registered under the same connection_key and called
                    # mark_present), this stale heartbeat must NOT wipe the live
                    # socket's presence key. Only clear when this websocket is
                    # still the active connection for the key — same guard the
                    # disconnect branch uses before its own cleanup.
                    if driver_id and manager.active_connections.get(connection_key) is websocket:
                        try:
                            await clear_presence(driver_id)
                        except Exception:  # noqa: S110 — presence best-effort; TTL still bounds it
                            pass
                    try:
                        await websocket.send_json({"type": "session_revoked", "reason": "token_revoked"})
                    except Exception:  # noqa: S110 — socket may already be dead
                        pass
                    try:
                        await websocket.close(code=1008, reason="token_revoked")
                    except Exception:  # noqa: S110
                        pass
                    break

            try:
                await websocket.send_json(
                    {
                        "type": "ping",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception:
                logger.info(f"Heartbeat send failed for {connection_key} — connection likely dead")
                break
            # Pong-staleness check is opt-in via conn_state; legacy/test
            # callers that pass conn_state=None skip this branch and rely
            # purely on the send-side failure path above.
            if conn_state is None:
                continue
            last_pong = conn_state.get("last_pong_at", 0.0)
            if loop.time() - last_pong > stale_threshold:
                logger.info(
                    f"[WS] {connection_key} no pong for {loop.time() - last_pong:.1f}s — closing stale connection"
                )
                try:
                    await websocket.close(code=1001)  # 1001 = going away
                except Exception:  # noqa: S110
                    pass
                break
    except asyncio.CancelledError:
        pass


@router.websocket("/ws/{client_type}/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    client_type: str,
    client_id: str,
    last_seq: int | None = None,
):
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
        # Require the first message to be an auth message containing a token.
        # Enforce a 30-second deadline so clients that connect but never send
        # anything cannot hold the connection open indefinitely (resource
        # exhaustion risk under load). Close code 1008 = Policy Violation per
        # RFC 6455 — correct for an auth protocol timeout.
        try:
            auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("[WS] Auth timeout — no message received within 30s, closing connection")
            await websocket.close(code=1008)  # 1008 = Policy Violation
            return
        except Exception:
            await websocket.close(code=1011)
            return
        if not auth_msg or auth_msg.get("type") != "auth" or not auth_msg.get("token"):
            await websocket.send_json({"type": "error", "message": "authentication_required"})
            await websocket.close()
            return

        token = auth_msg.get("token")
        # Phase 1 — token verification only, no DB. verify_id_token is a
        # synchronous crypto call (RSA verify + key fetch) that blocks the
        # event loop for tens of ms under cold-cache — enough to stall every
        # other socket on this VM during a reconnect storm. Push it into the
        # default threadpool so only this coroutine waits. DB lookups happen
        # AFTER this block so a Supabase outage is closed as 1013 "try again
        # later" instead of being misreported as invalid_token (which client
        # backoff treats as terminal).
        firebase_payload = None
        jwt_payload = None
        try:
            firebase_payload = await asyncio.to_thread(firebase_auth.verify_id_token, token)
        except Exception:
            # Fallback to legacy JWT. Same threadpool rationale — jwt.decode
            # performs HMAC verification which is fast but still blocking.
            try:
                jwt_payload = await asyncio.to_thread(verify_jwt_token, token)
            except Exception:
                jwt_payload = None

        user = None
        payload = None
        if firebase_payload is not None:
            payload = firebase_payload
            # B-P1-1 / DV-10: bind audience to client_type. Without this a
            # driver-app Firebase token could authenticate a rider socket
            # (or vice versa) — same cross-app reuse hazard fixed in
            # routes/auth.py and dependencies/__init__.py. Production fails
            # fast in core/config._guard_production_secrets when either
            # FIREBASE_*_APP_ID is unset, so the empty-string branch is
            # only reachable in dev/test.
            expected_aud = ""
            if client_type == "driver":
                expected_aud = settings.FIREBASE_DRIVER_APP_ID or ""
            elif client_type == "rider":
                expected_aud = settings.FIREBASE_RIDER_APP_ID or ""
            if not expected_aud:
                await websocket.send_json({"type": "error", "message": "firebase_audience_not_configured"})
                await websocket.close()
                return
            if payload.get("aud") != expected_aud:
                await websocket.send_json({"type": "error", "message": "ERR_TOKEN_AUDIENCE"})
                await websocket.close()
                return

            uid = payload.get("uid") or payload.get("user_id")
            try:
                user = await db_supabase.get_user_by_id(uid)
                if not user:
                    phone = payload.get("phone_number")
                    if phone:
                        user = await db_supabase.get_user_by_phone(phone)
            except Exception as _db_exc:
                # DB errors surface loudly (CLAUDE.md): close 1013 so the
                # client retries — never degrade an outage into invalid_token.
                _orig = getattr(_db_exc, "details", None)
                _orig = _orig.get("original") if isinstance(_orig, dict) else None
                logger.error(
                    "WS auth: user lookup failed for firebase uid={} — closing 1013: {}",
                    uid,
                    _orig or _db_exc,
                )
                await websocket.close(code=1013, reason="service_unavailable_retry")
                return
            if not user:
                # Mirror get_current_user (dependencies/__init__.py): NEVER
                # auto-create on a lookup miss. A valid Firebase token with no
                # users row is almost always a transient replica miss — and a
                # forged row forks a phantom account, masks the outage, and
                # lets a PIPEDA-deleted account re-provision itself over a
                # socket. New Firebase users are created only by /auth/firebase.
                logger.error(
                    "WS auth(firebase): no user row for uid={} — refusing to auto-create (likely transient DB miss)",
                    uid,
                )
                await websocket.close(code=1013, reason="service_unavailable_retry")
                return
        elif jwt_payload is not None:
            payload = jwt_payload
            # Admin tokens are minted with a `role` claim and have no row
            # in the `users` table (admin-001 is env-var creds; admin_staff
            # rows live in a separate table). Without this branch the
            # lookup returns None and the client is told
            # invalid_token_or_user_not_found — which the monitoring
            # hook reacts to with exponential-backoff reconnects, i.e.
            # the repeating "WebSocket failed" loop seen in the admin
            # live-monitoring console. Mirrors get_current_user() in
            # dependencies/__init__.py.
            # _verify_admin_payload runs the same full admin checks as the
            # HTTP path (aud, JTI revocation, staff active, token_version,
            # idle timeout) and raises HTTPException for a revoked / expired /
            # disabled admin token — a genuine auth failure, handled below as
            # invalid_token. Only non-HTTP errors (DB outage) close 1013.
            try:
                admin_user = await _verify_admin_payload(payload)
                if admin_user is not None:
                    user = admin_user
                elif payload.get("user_id"):
                    user = await db_supabase.get_user_by_id(payload["user_id"])
            except HTTPException:
                user = None
            except Exception as _db_exc:
                _orig = getattr(_db_exc, "details", None)
                _orig = _orig.get("original") if isinstance(_orig, dict) else None
                logger.error(
                    "WS auth: user lookup failed for jwt user_id={} — closing 1013: {}",
                    payload.get("user_id"),
                    _orig or _db_exc,
                )
                await websocket.close(code=1013, reason="service_unavailable_retry")
                return

        if not user:
            await websocket.send_json({"type": "error", "message": "invalid_token_or_user_not_found"})
            await websocket.close()
            return

        # PIPEDA: a deletion-requested / purged account must not hold a realtime
        # channel. This is the one auth surface NOT behind get_current_user, so the
        # status guard has to be enforced here too. (admin_user rows carry no
        # status/deleted_at, so this is a no-op for admins.)
        if _account_inaccessible(user):
            await websocket.send_json({"type": "error", "message": "ERR_ACCOUNT_DELETED"})
            await websocket.close()
            return

        # B-P1-11: Reject the handshake if the session has been revoked via
        # /auth/logout-all (or a refresh-token-reuse cascade). This catches a
        # stale token cached client-side reconnecting. HTTP requests enforce the
        # same in dependencies.py; without the gate here a stale-token reconnect
        # would silently succeed and start receiving ride events again.
        #
        # Firebase ID tokens carry no token_version claim, so the JWT
        # token_version comparison (claim defaults to 0) would wrongly close the
        # socket for any Firebase user whose row has token_version > 0 —
        # including a fresh post-logout-all re-sign-in that HTTP auth accepted,
        # breaking realtime updates. Detect Firebase tokens by their auth_time
        # claim and enforce revocation via the same sessions_invalid_before
        # watermark the HTTP path uses; keep the token_version check for JWTs.
        claim_token_version = 0
        firebase_auth_time: int | None = None
        if (payload or {}).get("auth_time") is not None:
            try:
                firebase_auth_time = int(payload.get("auth_time"))
            except (TypeError, ValueError):
                firebase_auth_time = None
            session_revoked = _firebase_session_revoked(payload, (user or {}).get("sessions_invalid_before"))
        else:
            try:
                claim_token_version = int((payload or {}).get("token_version") or 0)
            except (TypeError, ValueError):
                claim_token_version = 0
            try:
                stored_token_version = int((user or {}).get("token_version") or 0)
            except (TypeError, ValueError):
                stored_token_version = 0
            session_revoked = claim_token_version < stored_token_version
        if session_revoked:
            await websocket.send_json({"type": "error", "message": "session_revoked"})
            await websocket.close()
            return

        # If connecting as driver, ensure user has a driver profile.
        # Cache the driver_id on the connection — the location-update
        # handler used to re-look-this-up on every GPS ping (1 Hz × N
        # drivers = constant DB load); keeping it here makes subsequent
        # per-message handlers pure in-memory lookups.
        driver_profile = None
        if client_type == "driver":
            driver_profile = (lambda _r: _r[0] if _r else None)(
                await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
            )
            if not driver_profile:
                await websocket.send_json({"type": "error", "message": "user_is_not_a_driver"})
                await websocket.close()
                return
            current_driver_id = driver_profile["id"]
        elif client_type == "admin":
            if user.get("role") not in _ADMIN_ROLES:
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
        # intent-based status, not socket-presence. We reuse the profile
        # we already fetched above rather than issuing a second lookup.
        if client_type == "driver" and driver_profile:
            # Uber/Lyft-style presence: socket is alive → driver is present.
            # Expires after PRESENCE_TTL if the socket dies without a
            # clean disconnect. Refreshed on every pong and location ping.
            await mark_present(current_driver_id)
            await manager.broadcast_to_admins(
                {
                    "type": "driver_status_changed",
                    "driver_id": current_driver_id,
                    "is_online": bool(driver_profile.get("is_online")),
                }
            )

        # Replay missed sequenced messages (if any)
        if last_seq is not None:
            try:
                try:
                    from ..utils.ws_pubsub import pubsub
                except ImportError:
                    from utils.ws_pubsub import pubsub

                messages = await pubsub.get_outbox(connection_key)
                replayed = 0
                for msg_obj in messages:
                    if msg_obj.get("seq", 0) > last_seq:
                        await websocket.send_json(msg_obj)
                        replayed += 1
                if replayed > 0:
                    logger.info(f"Replayed {replayed} messages to {connection_key} (last_seq={last_seq})")
            except Exception as e:
                logger.warning(f"Failed to replay messages for {connection_key}: {e}")

        # GAP FIX: Start heartbeat background task. conn_state is shared with
        # heartbeat_task so it can detect stale connections — every pong the
        # client sends bumps last_pong_at; the heartbeat closes the socket
        # if pongs stop arriving (phone died, app killed, airplane mode).
        # B-P1-11: pass user_id + claim_token_version so the heartbeat can
        # re-validate against the DB row each tick and close the socket
        # if /auth/logout-all bumped token_version since connect.
        conn_state: dict = {"last_pong_at": asyncio.get_event_loop().time()}
        # -inf, not 0.0: loop time starts near zero on a fresh loop, so a 0.0
        # sentinel would swallow the first echo of a connection's lifetime.
        _last_status_echo_at = float("-inf")
        hb_task = asyncio.create_task(
            heartbeat_task(
                websocket,
                connection_key,
                conn_state,
                user_id=user["id"],
                driver_id=current_driver_id,
                claim_token_version=claim_token_version,
                firebase_auth_time=firebase_auth_time,
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
                user["id"],
                max_per_second=WS_MAX_MESSAGES_PER_SECOND,
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
                # Accept both message types for backwards compat.
                # Ownership is enforced by the cached driver_id from auth —
                # we deliberately ignore any driver_id the client sent to
                # prevent a compromised token from spoofing locations for
                # another driver.
                lat = _parse_live_coordinate(data.get("lat"))
                lng = _parse_live_coordinate(data.get("lng"))
                driver_id = current_driver_id if client_type == "driver" else None

                if driver_id and lat is not None and lng is not None and _valid_live_coordinates(lat, lng):
                    trusted, reason = await check_location_integrity(
                        driver_id,
                        lat,
                        lng,
                        speed=data.get("speed"),
                        accuracy=data.get("accuracy"),
                        mocked=data.get("mocked"),
                    )
                    if not trusted:
                        continue

                    # Persist to the authoritative drivers table FIRST (before
                    # the ephemeral Redis cache below — a Redis blip there
                    # previously raised and skipped this write, leaving an
                    # online driver with no car marker), but THROTTLED: at 1 Hz
                    # pings this was one Postgres UPDATE per second per driver
                    # (write amplification vs the 150ms location-write SLA).
                    # The admin marker / /drivers/nearby readers tolerate a few
                    # seconds of row staleness; the Redis cache and rider/admin
                    # fan-out below still run on EVERY ping.
                    _loc_now = asyncio.get_event_loop().time()
                    if _loc_now - conn_state.get("last_loc_db_write", 0.0) >= _DRIVER_LOC_DB_WRITE_INTERVAL_S:
                        await db_supabase.update_driver_location(driver_id, lat, lng, heading=data.get("heading"))
                        conn_state["last_loc_db_write"] = _loc_now
                    # Best-effort 60 s cache for the rider-map fast path;
                    # swallows its own Redis errors (see ConnectionManager).
                    await manager.update_driver_location(driver_id, lat, lng)
                    # Location pings are an even stronger liveness signal
                    # than pongs — fresh GPS proves the app is running and
                    # foregrounded, not just that TCP is open.
                    await mark_present(driver_id)

                    # Resolve active rides for rider fan-out. B3.1: served from
                    # a 5 s Redis cache — this runs on EVERY GPS ping and used
                    # to be a per-ping rides query. The shared breadcrumb writer
                    # below independently derives ride_id and phase from server
                    # milestones so single pings and buffered batches follow
                    # the same billing/dispute rules.
                    active_rides = await resolve_active_rides_cached(driver_id)
                    active_ride = active_rides[0] if active_rides else None
                    ride_id = active_ride["id"] if active_ride else None

                    # B3.3: buffer per-ping points and write them as one
                    # insert (~10 points / 10s) through the shared breadcrumb
                    # path. That path stores the device capture timestamp
                    # when supplied (captured_at/device_timestamp/recorded_at/
                    # timestamp), records received_at separately, and derives
                    # ride phase from server ride milestones instead of trusting
                    # the client's current message timing or phase tag. The
                    # buffer flushes early on ride-context change, and the
                    # disconnect/completion paths flush the remainder.
                    await buffer_ride_breadcrumb(driver_id, data, active_ride=active_ride)

                    # Refresh the Maps API key from DB at most every 60 s.
                    now_mono = asyncio.get_event_loop().time()
                    global _maps_key_cache, _maps_key_fetched_at
                    if now_mono - _maps_key_fetched_at > _MAPS_KEY_CACHE_TTL:
                        try:
                            _app_cfg = await get_app_settings()
                            _maps_key_cache = (_app_cfg or {}).get("google_maps_api_key") or ""
                        except Exception:
                            logger.debug(
                                "Maps API key refresh failed; retaining stale key",
                                exc_info=True,
                            )
                        _maps_key_fetched_at = now_mono

                    location_update = {
                        "type": "driver_location_update",
                        "driver_id": driver_id,
                        "lat": lat,
                        "lng": lng,
                        "speed": data.get("speed"),
                        "heading": data.get("heading"),
                    }

                    # Forward to riders of confirmed rides (driver_accepted →
                    # in_progress) so the car marker moves as the driver
                    # approaches pickup and during the trip. Pre-acceptance
                    # states (searching / driver_assigned) are skipped so an
                    # un-accepted offer's driver location isn't leaked.
                    # Each ride gets a location_update optionally enriched with
                    # eta_seconds when the driver is still en-route to pickup.
                    for ride in active_rides:
                        if ride.get("status") not in _RIDER_LOCATION_STATUSES:
                            continue

                        ride_status = ride.get("status", "")
                        rider_msg = location_update.copy()

                        if ride_status in _ETA_PICKUP_STATUSES:
                            pickup_lat = ride.get("pickup_lat")
                            pickup_lng = ride.get("pickup_lng")
                            if pickup_lat is not None and pickup_lng is not None:
                                try:
                                    # #1: hot-path read is cache-only (one Redis
                                    # GET). On a miss, refresh the ETA OFF the
                                    # receive loop so an OSRM/Google round-trip
                                    # never blocks the <150ms location write; the
                                    # value lands in cache for the next ping.
                                    eta_sec = await get_cached_ride_eta(driver_id, ride["id"])
                                    if eta_sec is not None:
                                        rider_msg["eta_seconds"] = eta_sec
                                    else:
                                        asyncio.create_task(
                                            refresh_ride_eta(
                                                driver_lat=lat,
                                                driver_lng=lng,
                                                dest_lat=pickup_lat,
                                                dest_lng=pickup_lng,
                                                maps_api_key=_maps_key_cache,
                                                driver_id=driver_id,
                                                ride_id=ride["id"],
                                            )
                                        )
                                except Exception:
                                    logger.debug(
                                        "ETA fetch failed; omitting eta_seconds from location update",
                                        exc_info=True,
                                    )

                        # durable=False: 1 Hz location pings must not fill the
                        # 50-entry replay outbox and evict ride events (F8);
                        # the next ping supersedes this one anyway.
                        await manager.send_personal_message(
                            rider_msg,
                            f"rider_{ride['rider_id']}",
                            durable=False,
                        )

                    # Broadcast live location to admin monitoring clients —
                    # throttled per driver (#3) so 1 Hz pings don't fan out
                    # N drivers x A admins every second.
                    await manager.broadcast_driver_location_to_admins(driver_id, location_update)

            elif data.get("type") in ("location_batch", "driver_location_batch"):
                # Batch upload of buffered GPS points (offline recovery).
                # Same ownership model as single-point updates: always use
                # the driver_id bound to this authenticated socket; ignore
                # any client-supplied value.
                points = data.get("points", [])
                driver_id = current_driver_id if client_type == "driver" else None
                if not isinstance(points, list):
                    await websocket.send_json({"type": "location_batch_ack", "count": 0})
                    continue

                # Per-USER sliding-window rate limit on total batch points to
                # prevent a single user from flooding driver_location_history
                # across N concurrent sockets (500-point batches × N sockets
                # sent every second = N × 30 000 inserts/min).
                # Uses the same Redis key pattern as the per-message rate limiter
                # (note_user_message) so the limit aggregates across every WS
                # connection the user has open on any replica.
                _BATCH_WINDOW_SECS = 10
                _BATCH_POINTS_LIMIT = 1000  # max points per user per 10-second window
                _batch_rl_key = f"ws:batch_rl:{user['id']}"
                try:
                    _batch_count = await redis_incr(_batch_rl_key)
                    # Only set the TTL on the first increment so we don't
                    # keep resetting the window on every message.
                    if _batch_count == 1:
                        await redis_expire(_batch_rl_key, _BATCH_WINDOW_SECS)
                    # Accumulate the full point count, not just message count.
                    # INCRBY is not in our thin redis_client wrapper, so call
                    # incr once per point would be expensive; instead we incr
                    # by len(points) by calling redis_incr len(points)-1 extra
                    # times. For batches of reasonable size (≤500) this is
                    # acceptable; for the common single-connection driver it
                    # hits the Redis fast-path each time.
                    for _ in range(len(points) - 1):
                        await redis_incr(_batch_rl_key)
                    _batch_total = _batch_count + max(0, len(points) - 1)
                except Exception:
                    # Redis unavailable — fall back to allowing the request
                    # rather than blocking all batch uploads in degraded mode.
                    logger.warning(
                        "ws:batch_rl Redis check failed for user %s; allowing batch",
                        user["id"],
                    )
                    _batch_total = 0
                if _batch_total > _BATCH_POINTS_LIMIT:
                    await websocket.send_json(
                        {
                            "type": "rate_limited",
                            "scope": "location_batch",
                            "limit": _BATCH_POINTS_LIMIT,
                            "window_seconds": _BATCH_WINDOW_SECS,
                        }
                    )
                    continue

                if driver_id and isinstance(points, list) and points:
                    dict_points = [p for p in points if isinstance(p, dict)]
                    if not dict_points:
                        await websocket.send_json({"type": "location_batch_ack", "count": 0})
                        continue
                    # Shared persistence with the REST path: server-derived phase
                    # per point (from its own timestamp vs the ride milestones),
                    # stale / other-ride discard, and the 500-point cap. Never
                    # trusts the client's ride_id/tracking_phase.
                    inserted = await persist_ride_breadcrumbs(driver_id, dict_points)
                    # Live marker from the most recent point (best-effort). Accept
                    # both compact lat/lng and REST-style latitude/longitude keys.
                    last_pt = dict_points[-1]
                    _lat = _parse_live_coordinate(
                        last_pt.get("latitude") if last_pt.get("latitude") is not None else last_pt.get("lat")
                    )
                    _lng = _parse_live_coordinate(
                        last_pt.get("longitude") if last_pt.get("longitude") is not None else last_pt.get("lng")
                    )
                    if _lat is not None and _lng is not None and _valid_live_coordinates(_lat, _lng):
                        trusted, _reason = await check_location_integrity(
                            driver_id,
                            _lat,
                            _lng,
                            speed=last_pt.get("speed"),
                            accuracy=last_pt.get("accuracy"),
                            mocked=last_pt.get("mocked"),
                        )
                        if trusted:
                            # Authoritative DB write first; the ephemeral Redis
                            # cache is best-effort and must not gate it (mirrors
                            # the single-ping handler — a Redis blip on the cache
                            # previously skipped persistence and hid the marker).
                            # Same F8 throttle as the single-ping handler.
                            _loc_now = asyncio.get_event_loop().time()
                            if _loc_now - conn_state.get("last_loc_db_write", 0.0) >= _DRIVER_LOC_DB_WRITE_INTERVAL_S:
                                await db_supabase.update_driver_location(
                                    driver_id, _lat, _lng, heading=last_pt.get("heading")
                                )
                                conn_state["last_loc_db_write"] = _loc_now
                            await manager.update_driver_location(driver_id, _lat, _lng)
                            await mark_present(driver_id)

                            # Fan-out latest batch position to riders — the single-ping
                            # handler does this for `driver_location` messages; batches
                            # must mirror that so the rider's blue dot moves during trips.
                            _batch_active_rides = await db_supabase.get_rows(
                                "rides",
                                {
                                    "driver_id": driver_id,
                                    "status": {
                                        "$in": [
                                            "driver_assigned",
                                            "driver_accepted",
                                            "driver_arrived",
                                            "in_progress",
                                        ]
                                    },
                                },
                                limit=10,
                            )
                            _batch_loc_update = {
                                "type": "driver_location_update",
                                "driver_id": driver_id,
                                "lat": _lat,
                                "lng": _lng,
                                "speed": last_pt.get("speed"),
                                "heading": last_pt.get("heading"),
                            }
                            for _batch_ride in _batch_active_rides:
                                if _batch_ride.get("status") not in _RIDER_LOCATION_STATUSES:
                                    continue
                                _batch_rider_msg = _batch_loc_update.copy()
                                _batch_ride_status = _batch_ride.get("status", "")
                                if _batch_ride_status in _ETA_PICKUP_STATUSES:
                                    _p_lat = _batch_ride.get("pickup_lat")
                                    _p_lng = _batch_ride.get("pickup_lng")
                                    if _p_lat is not None and _p_lng is not None:
                                        try:
                                            # #1: cache-only on the hot path;
                                            # refresh off-loop on a miss.
                                            _eta_sec = await get_cached_ride_eta(driver_id, _batch_ride["id"])
                                            if _eta_sec is not None:
                                                _batch_rider_msg["eta_seconds"] = _eta_sec
                                            else:
                                                asyncio.create_task(
                                                    refresh_ride_eta(
                                                        driver_lat=_lat,
                                                        driver_lng=_lng,
                                                        dest_lat=_p_lat,
                                                        dest_lng=_p_lng,
                                                        maps_api_key=_maps_key_cache,
                                                        driver_id=driver_id,
                                                        ride_id=_batch_ride["id"],
                                                    )
                                                )
                                        except Exception:
                                            logger.debug(
                                                "ETA fetch failed for batch; omitting eta_seconds",
                                                exc_info=True,
                                            )
                                # durable=False: same replay-outbox exemption
                                # as the single-ping fan-out (F8).
                                await manager.send_personal_message(
                                    _batch_rider_msg,
                                    f"rider_{_batch_ride['rider_id']}",
                                    durable=False,
                                )
                            await manager.broadcast_driver_location_to_admins(driver_id, _batch_loc_update)
                    await websocket.send_json({"type": "location_batch_ack", "count": inserted})

            elif data.get("type") == "ride_status_update":
                # Cooldown (see RIDE_STATUS_ECHO_COOLDOWN_S): excess echoes
                # are dropped silently — every real transition is broadcast
                # by the HTTP handlers regardless, so a dropped echo costs a
                # spammer their amplification, not a rider their update.
                _echo_now = asyncio.get_event_loop().time()
                if _echo_now - _last_status_echo_at < RIDE_STATUS_ECHO_COOLDOWN_S:
                    continue
                _last_status_echo_at = _echo_now
                ride_id = data.get("ride_id")
                if ride_id:
                    ride = await db_supabase.get_ride(ride_id)
                    if ride:
                        # Participant check — same policy as chat_message
                        # below: only the assigned driver or the ride's rider
                        # may trigger a rebroadcast. Without it any
                        # authenticated socket could push events for any ride
                        # to the victim rider and every admin console.
                        # Driver identity is the socket-bound
                        # current_driver_id (fetched once at auth) — no
                        # per-message drivers read.
                        authorized = False
                        if client_type == "driver":
                            authorized = current_driver_id == ride.get("driver_id")
                        elif client_type == "rider":
                            authorized = user["id"] == ride.get("rider_id")
                        if not authorized:
                            await websocket.send_json({"type": "error", "message": "not_ride_participant"})
                        else:
                            # Echo the canonical DB status — never relay the
                            # client-supplied string. Real transitions happen
                            # via the HTTP endpoints (which emit their own WS
                            # events); this message only re-broadcasts the
                            # ride's current state.
                            await manager.send_personal_message(
                                {
                                    "type": "ride_status_changed",
                                    "ride_id": ride_id,
                                    "status": ride["status"],
                                },
                                f"rider_{ride['rider_id']}",
                            )
                            # Broadcast to admin monitoring clients
                            await manager.broadcast_to_admins(
                                {
                                    "type": "ride_status_changed",
                                    "ride_id": ride_id,
                                    "status": ride["status"],
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

                    try:
                        from ..utils.driver_online import intent_online
                    except ImportError:
                        from utils.driver_online import intent_online  # type: ignore

                    try:
                        from ..geo_utils import calculate_distance
                    except ImportError:
                        from geo_utils import calculate_distance  # type: ignore

                    nearby = []
                    for driver in drivers:
                        # Authoritative intent gate (migration 97): trust the
                        # went_online_at / went_offline_at timestamps over the
                        # legacy is_online flag. Falls back to is_online for
                        # unmigrated rows.
                        if not intent_online(driver):
                            continue
                        d_lat = driver.get("lat")
                        d_lng = driver.get("lng")
                        # Same (0, 0) registration-default guard as /drivers/nearby.
                        if d_lat is None or d_lng is None or (d_lat == 0 and d_lng == 0):
                            continue
                        # Calculate distance
                        dist = calculate_distance(lat, lng, d_lat, d_lng)
                        if dist <= radius:
                            nearby.append(
                                {
                                    "id": driver["id"],
                                    "lat": d_lat,
                                    "lng": d_lng,
                                    "vehicle_type_id": driver["vehicle_type_id"],
                                }
                            )

                    await websocket.send_json({"type": "nearby_drivers", "drivers": nearby})

            elif data.get("type") == "chat_message":
                ride_id = data.get("ride_id")
                message = data.get("text")
                if ride_id and isinstance(message, str) and message.strip():
                    ride = await db_supabase.get_ride(ride_id)
                    if ride:
                        # Derive sender from the authenticated connection key rather
                        # than trusting the client-supplied "sender" field. This
                        # prevents impersonation via a crafted WS payload.
                        sender: str | None = None
                        target: str | None = None
                        if client_type == "driver":
                            # Verify this driver is actually assigned to the ride.
                            _dp = await db_supabase.get_rows("drivers", {"user_id": user["id"]}, limit=1)
                            _dp = _dp[0] if _dp else None
                            if not _dp or _dp["id"] != ride.get("driver_id"):
                                await websocket.send_json({"type": "error", "message": "not_ride_participant"})
                            else:
                                sender = "driver"
                                target = f"rider_{ride['rider_id']}"
                        elif client_type == "rider":
                            if user["id"] != ride.get("rider_id"):
                                await websocket.send_json({"type": "error", "message": "not_ride_participant"})
                            else:
                                sender = "rider"
                                if ride.get("driver_id"):
                                    _dd = await db_supabase.get_driver_by_id(ride["driver_id"])
                                    if _dd and _dd.get("user_id"):
                                        target = f"driver_{_dd['user_id']}"

                        # Persist the message regardless of whether the recipient is
                        # currently connected — they can fetch history on next open.
                        # Only gate on sender (participant verified); target may be
                        # None when no driver is yet assigned.
                        if sender:
                            msg_data = {
                                "id": str(uuid.uuid4()),
                                "ride_id": ride_id,
                                "text": message.strip(),
                                "sender": sender,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                            await db_supabase.insert_one("ride_messages", msg_data)
                            if target:
                                await manager.send_personal_message({**msg_data, "type": "chat_message"}, target)

            elif data.get("type") in ("get_drivers_snapshot", "get_rides_snapshot") and client_type == "admin":
                try:
                    from ..routes.admin.monitoring import (
                        fetch_monitoring_drivers,
                        fetch_monitoring_rides,
                    )
                except ImportError:
                    from routes.admin.monitoring import (  # type: ignore[no-redef]
                        fetch_monitoring_drivers,
                        fetch_monitoring_rides,
                    )
                try:
                    if data["type"] == "get_drivers_snapshot":
                        drivers = await fetch_monitoring_drivers()
                        await websocket.send_json({"type": "drivers_snapshot", "drivers": drivers})
                    else:
                        rides = await fetch_monitoring_rides()
                        await websocket.send_json({"type": "rides_snapshot", "rides": rides})
                except Exception as _snap_exc:
                    logger.warning(f"[WS] snapshot fetch failed for {connection_key}: {_snap_exc}")
                    await websocket.send_json({"type": "error", "message": "snapshot_failed"})

            else:
                logger.warning(f"Unknown WS message type: {data.get('type')}")

    except WebSocketDisconnect as _wsd:
        # A newer WS can register under the same connection_key while this
        # one's disconnect handler is queued. Only run cleanup if THIS
        # specific socket is still the one the manager has for this key —
        # otherwise we'd evict the newer socket from active_connections
        # and clear its presence key, stranding a live connection.
        # The check is performed atomically inside the disconnect block (not
        # pre-computed) so a reconnect arriving between the check and the
        # actual removal cannot slip through.
        logger.info(
            f"[GO-ONLINE] WS branch=WebSocketDisconnect connection_key={connection_key} "
            f"code={getattr(_wsd, 'code', None)} reason={getattr(_wsd, 'reason', None)} "
            f"current_driver_id={current_driver_id}"
        )
        if connection_key and manager.active_connections.get(connection_key) is websocket:
            # Remove ourselves from the manager dict FIRST. _handle_driver_ws_offline's
            # "newer WS present" check looks at whether the key is still populated
            # after we leave — with manager.disconnect happening after, the check
            # was always tripping on the current socket and the offline flip never
            # ran on a clean disconnect.
            manager.disconnect(connection_key)
            # Do NOT clear the Redis presence key on an involuntary disconnect.
            # Presence carries a 30s TTL precisely so a flaky-network blip (socket
            # drops, client reconnects seconds later) doesn't yank the driver out
            # of the rider-facing /drivers/nearby + /rides/estimate results.
            # Force-clearing here defeated that grace: every reconnect cycle wiped
            # the key, so the driver flickered offline to riders while admin (which
            # reads the durable is_online column) still showed them online. A truly
            # dead app just lets the TTL lapse, and the presence sweeper reconciles
            # is_online after the grace window. Explicit Go Offline still clears
            # immediately (routes/drivers.py), and dispatch re-checks presence at
            # offer time, so a dead socket cannot silently absorb a ride.
            await _handle_driver_ws_disconnect(connection_key, user)
    except Exception as e:
        logger.exception(
            f"[GO-ONLINE] WS branch=Exception connection_key={connection_key} "
            f"current_driver_id={current_driver_id} err={e}"
        )
        if connection_key and manager.active_connections.get(connection_key) is websocket:
            manager.disconnect(connection_key)
            # Same as the clean-disconnect branch above: let presence lapse via its
            # 30s TTL rather than force-clearing on an involuntary drop, so a
            # network blip doesn't hide the driver from riders.
            await _handle_driver_ws_disconnect(connection_key, user)
        try:
            await websocket.close()
        except Exception:  # noqa: S110
            pass
    finally:
        # GAP FIX: Cancel heartbeat task on disconnect
        if hb_task:
            hb_task.cancel()
        # B3.3: persist any buffered breadcrumbs so a disconnect never strands
        # the tail of a trail. Best-effort — cleanup must not mask the
        # original disconnect, and the on-device buffer re-uploads via REST.
        if current_driver_id:
            # P7: release the admin-location throttle slot so _admin_loc_last
            # doesn't grow one entry per driver ever seen.
            manager.forget_driver_location_throttle(current_driver_id)
            try:
                await flush_driver_breadcrumbs(current_driver_id)
            except Exception:
                logger.error(
                    f"[WS] breadcrumb flush on disconnect failed for driver {current_driver_id}",
                    exc_info=True,
                )
