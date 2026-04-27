from __future__ import annotations

from datetime import datetime, timezone
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
        outside of legacy test paths, and an unscoped cross-machine
        broadcast would need a story for excluding admin-only channels.
        Prefer prefix-scoped broadcasts (``broadcast_to_admins``) which
        fan out correctly across VMs.
        """
        connections = list(self.active_connections.values())  # snapshot to avoid mutation during iteration
        for connection in connections:
            await connection.send_json(message)

    async def broadcast_to_admins(self, message: dict):
        """Broadcast to every admin client across the fleet.

        Fans out via Redis pub/sub so admins on other VMs receive the
        message — previously this was local-only, so an admin connected
        to VM-A would miss a driver_status_changed event triggered on
        VM-B. When pub/sub is down we fall back to local delivery,
        which matches the pre-change behaviour.
        """
        try:
            from utils.ws_pubsub import pubsub
        except ImportError:  # pragma: no cover — package-relative fallback
            from .utils.ws_pubsub import pubsub  # type: ignore

        if await pubsub.publish_broadcast("admin_", message):
            return
        await self._deliver_broadcast_local("admin_", message)

    async def broadcast_ride_status(
        self,
        ride_id: str,
        status: str,
        rider_id: str | None = None,
        **extra,
    ):
        """Emit a unified ``ride_status_changed`` event for a ride transition.

        Both the rider app and the admin dashboard listen for this single
        event type and switch on ``status``. Individual specific events
        (``driver_accepted``, ``ride_started``, …) still fire for
        backward-compat, but every backend state transition should also
        call this so admin live-monitoring stays consistent without
        per-event wiring.

        ``rider_id`` is optional — pass None for transitions that shouldn't
        fan out to the rider (e.g. the initial driver_assigned pick, which
        shouldn't trigger a rider UI update until the driver actually
        accepts).
        """
        payload = {"type": "ride_status_changed", "ride_id": ride_id, "status": status, **extra}
        if rider_id:
            try:
                await self.send_personal_message(payload, f"rider_{rider_id}")
            except Exception as e:
                logger.warning(f"broadcast_ride_status: rider send failed for {rider_id}: {e}")
        try:
            await self.broadcast_to_admins(payload)
        except Exception as e:
            logger.warning(f"broadcast_ride_status: admin broadcast failed for {ride_id}: {e}")

    async def _deliver_broadcast_local(self, prefix: str, message: dict):
        """Deliver ``message`` to every local socket whose key starts with
        ``prefix``. Snapshot the match list before iterating so a
        concurrent disconnect doesn't RuntimeError the loop.
        """
        keys = [k for k in self.active_connections if k.startswith(prefix)]
        for key in keys:
            ws = self.active_connections.get(key)
            if ws is None:
                continue
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to {key}: {e}")

    def update_driver_location(self, driver_id: str, lat: float, lng: float):
        self.driver_locations[driver_id] = {
            "lat": lat,
            "lng": lng,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_driver_location(self, driver_id: str):
        return self.driver_locations.get(driver_id)


manager = ConnectionManager()
