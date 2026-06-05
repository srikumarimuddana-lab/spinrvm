"""Regression: a driver WebSocket disconnect must NOT clear presence.

Backgrounding the driver app drops the socket (close 1006/1001). The intent-
vs-reachability design keeps the driver online across that drop: is_online
stays true, presence expires via its TTL, and the background location batch
(/drivers/location-batch -> mark_present) refreshes it. The disconnect handler
used to call clear_presence() on every drop, nuking the TTL grace window and
dropping backgrounded drivers off the admin map and out of dispatch instantly.

This test drives a real driver connect -> disconnect through the endpoint and
asserts the disconnect path runs (manager.disconnect + _handle_driver_ws_disconnect)
while driver_presence.clear_presence is never awaited. Explicit Go Offline
(routes/drivers.py) remains the only place presence is cleared.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_TEST_FIREBASE_APP_ID = "test-firebase-app-id-presence"


@pytest.fixture
def app_with_ws():
    from backend.routes.websocket import router

    app = FastAPI()
    app.include_router(router)
    return app


def _settings_mock() -> MagicMock:
    m = MagicMock()
    m.FIREBASE_DRIVER_APP_ID = _TEST_FIREBASE_APP_ID
    m.FIREBASE_RIDER_APP_ID = ""
    return m


def test_driver_disconnect_does_not_clear_presence(app_with_ws):
    driver_user = {"id": "user_drv_pres_1", "phone": "+15550002222", "role": "driver"}
    driver_profile = {"id": "driver_pres_1", "user_id": driver_user["id"], "is_online": True}
    firebase_payload = {"uid": driver_user["id"], "phone_number": driver_user["phone"], "aud": _TEST_FIREBASE_APP_ID}

    mark = AsyncMock(return_value=None)
    clear = AsyncMock(return_value=None)
    handle_disconnect = AsyncMock(return_value=None)

    patches = [
        patch("backend.routes.websocket.settings", _settings_mock()),
        patch("backend.routes.websocket.firebase_auth.verify_id_token", return_value=firebase_payload),
        patch("backend.routes.websocket.db_supabase.get_user_by_id", new=AsyncMock(return_value=driver_user)),
        patch("backend.routes.websocket.db_supabase.get_rows", new=AsyncMock(return_value=[driver_profile])),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.mark_present", new=mark),
        patch("backend.routes.websocket._handle_driver_ws_disconnect", new=handle_disconnect),
        # clear_presence is no longer imported into the websocket module — patch
        # the source so a future re-introduction in the disconnect path is caught.
        patch("backend.utils.driver_presence.clear_presence", new=clear),
    ]
    for p in patches:
        p.start()
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{driver_user['id']}") as ws:
            ws.send_json({"type": "auth", "token": "fake-firebase-token"})
            assert ws.receive_json()["type"] == "auth_success"
        # Socket closed on context exit → server runs the disconnect handler.
        # Wait (briefly) for the server coroutine to finish processing it.
        deadline = time.time() + 2.0
        while handle_disconnect.await_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        assert mark.await_count >= 1, "presence should be marked on connect"
        assert handle_disconnect.await_count == 1, "disconnect handler should run"
        assert clear.await_count == 0, "presence must NOT be cleared on a transient disconnect"
    finally:
        for p in patches:
            p.stop()
