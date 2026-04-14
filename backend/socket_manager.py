from datetime import datetime
from typing import Dict

from fastapi import WebSocket
from loguru import logger

try:
    from .db import diag_logger  # type: ignore
except ImportError:
    from db import diag_logger  # type: ignore


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
        msg_type = message.get("type", "?") if isinstance(message, dict) else "?"
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_json(message)
                diag_logger.info(f"[WS] SEND ok client_id={client_id} type={msg_type}")
            except Exception as e:
                diag_logger.info(f"[WS] SEND FAILED client_id={client_id} type={msg_type} err={e}")
        else:
            diag_logger.info(
                f"[WS] SEND DROPPED (not connected) client_id={client_id} "
                f"type={msg_type} "
                f"currently_connected={list(self.active_connections.keys())}"
            )

    async def broadcast(self, message: dict):
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
