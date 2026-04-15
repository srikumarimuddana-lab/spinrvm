from datetime import datetime
from typing import Dict

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
        for connection in self.active_connections.values():
            await connection.send_json(message)

    async def broadcast_to_admins(self, message: dict):
        """Broadcast a message to all connected admin WebSocket clients."""
        admin_keys = [k for k in self.active_connections if k.startswith("admin_")]
        for key in admin_keys:
            try:
                await self.active_connections[key].send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to admin {key}: {e}")

    def update_driver_location(self, driver_id: str, lat: float, lng: float):
        self.driver_locations[driver_id] = {"lat": lat, "lng": lng, "updated_at": datetime.utcnow().isoformat()}

    def get_driver_location(self, driver_id: str):
        return self.driver_locations.get(driver_id)


manager = ConnectionManager()
