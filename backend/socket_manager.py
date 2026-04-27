from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

from fastapi import WebSocket
from loguru import logger

try:
    from .logging_utils import diag_logger  # type: ignore
except ImportError:
    from logging_utils import diag_logger  # type: ignore


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.driver_locations: Dict[str, Dict] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        # WebSocket is already accepted in the endpoint handler
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket connected: {client_id}")
        diag_logger.info(
            f"[WS] CONNECT client_id={client_id} "
            f"total_connections={len(self.active_connections)} "
            f"all_keys={list(self.active_connections.keys())}"
        )

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        logger.info(f"WebSocket disconnected: {client_id}")
        diag_logger.info(
            f"[WS] DISCONNECT client_id={client_id} "
            f"remaining={len(self.active_connections)} "
            f"all_keys={list(self.active_connections.keys())}"
        )

    async def send_personal_message(self, message: dict, client_id: str):
        """Send a message to a client, possibly on a different machine.

        When ``ws_pubsub`` is active (production / multi-machine) the
        message goes onto a shared Redis channel; every machine's
        subscriber receives it and delivers iff the client is in its
        local dict. That includes THIS machine — so we must NOT also
        call ``_deliver_local`` here, or local connections would
        receive the message twice. The Redis round-trip for same-VM
        delivery is sub-millisecond and buys us correctness across the
        fleet.

        When pub/sub is disabled (dev / degraded Redis) we fall back
        to direct local delivery — which is exactly the pre-P0-B3
        behaviour.
        """
        try:
            from utils.ws_pubsub import pubsub
        except ImportError:  # pragma: no cover — package-relative fallback
            from .utils.ws_pubsub import pubsub  # type: ignore

        if await pubsub.publish(client_id, message):
            return
        await self._deliver_local(message, client_id)

    async def _deliver_local(self, message: dict, client_id: str):
        """Write ``message`` to the socket for ``client_id`` on THIS machine only.

        Called by both the direct-send fallback and by the Redis pub/sub
        subscriber. Keeping it as a single method means diagnostics and
        error handling stay consistent regardless of which path
        triggered the delivery.
        """
        msg_type = message.get("type", "?") if isinstance(message, dict) else "?"
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_json(message)
                diag_logger.info(f"[WS] SEND ok client_id={client_id} type={msg_type}")
            except Exception as e:
                diag_logger.info(f"[WS] SEND FAILED client_id={client_id} type={msg_type} err={e}")
        else:
            # In multi-machine mode this is expected for every message
            # whose target happens to live on another VM — don't treat
            # it as a drop unless we're the only machine serving the
            # fleet.
            diag_logger.debug(
                f"[WS] not local client_id={client_id} type={msg_type} "
                f"(message may have been delivered by another machine)"
            )

    async def broadcast(self, message: dict):
        """Broadcast to every socket connected to THIS machine.

        Intentionally local-only: broadcast() has no current callers
        outside of legacy test paths, and cross-machine broadcast is a
        different feature (room-based fan-out) that we haven't yet had
        a real use for. When we do, add a ``pubsub.publish_broadcast``
        helper rather than changing this method's semantics.
        """
        connections = list(self.active_connections.values())  # snapshot to avoid mutation during iteration
        for connection in connections:
            await connection.send_json(message)

    async def broadcast_to_admins(self, message: dict):
        """Broadcast a message to all connected admin WebSocket clients."""
        admin_keys = [k for k in self.active_connections if k.startswith("admin_")]
        for key in admin_keys:
            try:
                await self.active_connections[key].send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to admin {key}: {e}")

    async def disconnect_user(
        self,
        user_id: str,
        *,
        client_types: Optional[Iterable[str]] = None,
        reason: str = "token_revoked",
    ) -> int:
        """Force-close every LOCAL WebSocket whose key matches user_id.

        Returns the number of sockets actually closed. ``client_types``
        defaults to {rider, driver, admin}; pass a narrower iterable to
        scope the kick (e.g. ["rider", "driver"] for /auth/logout-all,
        ["admin"] for /admin/auth/logout-all).

        This only touches sockets on THIS machine. For multi-replica
        deployments the caller should route through ``kick_user`` (or
        ``ws_pubsub.publish_kick_user``) so every VM gets the signal.

        We try to send a final ``session_revoked`` frame before closing
        so the client can surface "you signed out everywhere" rather
        than a generic disconnect — best-effort only; a dead socket
        will raise on send and we proceed to close anyway.
        """
        types = list(client_types) if client_types else ["rider", "driver", "admin"]
        closed = 0
        for client_type in types:
            key = f"{client_type}_{user_id}"
            ws = self.active_connections.get(key)
            if ws is None:
                continue
            try:
                await ws.send_json({"type": "session_revoked", "reason": reason})
            except Exception:  # noqa: S110 — socket may already be dead
                pass
            try:
                # 1008 = "policy violation" per RFC 6455. The reason
                # string is capped at 123 bytes by the spec; trim
                # defensively so we never raise a ProtocolError when
                # the caller passes something verbose.
                await ws.close(code=1008, reason=(reason or "")[:120])
            except Exception as e:
                logger.warning(f"disconnect_user: close failed for {key}: {e}")
            # Pop unconditionally — the socket is unusable either way,
            # and a stale entry would let send_personal_message try to
            # write to a dead handle.
            self.active_connections.pop(key, None)
            closed += 1
        if closed:
            logger.info(
                f"disconnect_user: kicked {closed} local socket(s) "
                f"for user_id={user_id} reason={reason}"
            )
        return closed

    async def kick_user(
        self,
        user_id: str,
        *,
        client_types: Optional[Iterable[str]] = None,
        reason: str = "token_revoked",
    ) -> int:
        """Force-disconnect every WS for ``user_id`` across the fleet.

        Cross-replica: best-effort fan-out via Redis pub/sub when active.
        Local: always runs (idempotent if pub/sub loops back to us).

        Returns the local-machine kick count. Remote kicks happen
        asynchronously on the receiving replicas and are not
        enumerable from here — that's by design, the caller doesn't
        need to wait for every machine.
        """
        types_list = list(client_types) if client_types else None
        try:
            from utils.ws_pubsub import pubsub
        except ImportError:  # pragma: no cover — package-relative fallback
            from .utils.ws_pubsub import pubsub  # type: ignore
        # Fire the cross-replica signal first so other VMs start their
        # disconnect work in parallel with our local close. publish
        # is itself best-effort; a False return just means single-machine
        # mode, which is fine because the local close below covers us.
        try:
            await pubsub.publish_kick_user(
                user_id, client_types=types_list, reason=reason,
            )
        except Exception as e:
            logger.warning(f"kick_user: pub/sub publish failed for {user_id}: {e}")
        return await self.disconnect_user(
            user_id, client_types=types_list, reason=reason,
        )

    def update_driver_location(self, driver_id: str, lat: float, lng: float):
        self.driver_locations[driver_id] = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc).isoformat()}

    def get_driver_location(self, driver_id: str):
        return self.driver_locations.get(driver_id)


manager = ConnectionManager()
