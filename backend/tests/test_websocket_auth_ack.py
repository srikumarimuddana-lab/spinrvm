"""
Tests for the WebSocket auth-ack behavior in backend/routes/websocket.py.

Context: the client (`driver-app/hooks/useDriverDashboard.ts`) only flips its
`connectionState` to 'connected' on the first non-error server message. Before
the fix there was none — the first server message was the 30 s heartbeat ping,
so the banner sat on "Connection lost" for the whole window and users toggled
the GO button repeatedly, tearing the socket down mid-auth. The backend now
sends `{"type": "auth_success"}` right after `manager.connect()` to close that
window. These tests pin that behavior.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_ws():
    """Build a minimal FastAPI app with just the websocket router mounted,
    so we don't need to boot the entire server (which touches Stripe, Redis,
    Firebase admin SDK, etc.) for a routing-level test."""
    from fastapi import FastAPI

    from backend.routes.websocket import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def driver_user():
    return {
        "id": "user_driver_1",
        "phone": "+15550001111",
        "role": "driver",
        "first_name": "Test",
    }


@pytest.fixture
def admin_user():
    return {
        "id": "user_admin_1",
        "phone": "+15550009999",
        "role": "admin",
        "first_name": "Admin",
    }


def _patch_auth_and_db(user, driver_profile=None):
    """Context-manager-ish helper: patch Firebase + db_supabase so the WS
    endpoint can complete auth without touching real services."""
    patches = [
        # Firebase verify → returns a payload carrying the user id
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            return_value={"uid": user["id"], "phone_number": user["phone"]},
        ),
        # db_supabase.get_user_by_id → returns our user fixture
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_id",
            new=AsyncMock(return_value=user),
        ),
        # For driver client type, the endpoint checks get_rows("drivers", ...)
        patch(
            "backend.routes.websocket.db_supabase.get_rows",
            new=AsyncMock(return_value=[driver_profile] if driver_profile else []),
        ),
        # Driver online-broadcast path touches db.find_one
        patch(
            "backend.routes.websocket.db.find_one",
            new=AsyncMock(return_value=driver_profile),
        ),
        # Silence the admin broadcast
        patch(
            "backend.routes.websocket.manager.broadcast_to_admins",
            new=AsyncMock(return_value=None),
        ),
    ]
    return patches


def test_driver_auth_success_is_sent_immediately(app_with_ws, driver_user):
    """After a driver authenticates, the backend MUST send an `auth_success`
    message before anything else — that's the signal the driver-app uses to
    flip off the "Connection lost" banner."""
    driver_profile = {"id": "driver_1", "user_id": driver_user["id"], "is_online": True}

    patches = _patch_auth_and_db(driver_user, driver_profile=driver_profile)
    for p in patches:
        p.start()
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{driver_user['id']}") as ws:
            ws.send_json({"type": "auth", "token": "fake-firebase-token"})
            msg = ws.receive_json()
            assert msg["type"] == "auth_success"
            assert msg["client_type"] == "driver"
    finally:
        for p in patches:
            p.stop()


def test_admin_auth_success_is_sent_immediately(app_with_ws, admin_user):
    """Same contract for admin clients — they use the same banner pattern."""
    patches = _patch_auth_and_db(admin_user, driver_profile=None)
    for p in patches:
        p.start()
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/admin/{admin_user['id']}") as ws:
            ws.send_json({"type": "auth", "token": "fake-firebase-token"})
            msg = ws.receive_json()
            assert msg["type"] == "auth_success"
            assert msg["client_type"] == "admin"
    finally:
        for p in patches:
            p.stop()


def test_missing_auth_message_closes_socket_without_ack(app_with_ws):
    """If the first message isn't a valid auth message, the socket is closed
    with an error — no auth_success should ever be emitted."""
    client = TestClient(app_with_ws)
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/driver/anyone") as ws:
            ws.send_json({"type": "not-auth"})
            # First server message must be the error, not an auth_success
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["message"] == "authentication_required"
            # Socket will close — the next receive raises WebSocketDisconnect
            ws.receive_json()


def test_invalid_token_closes_socket_without_ack(app_with_ws):
    """Firebase + legacy JWT both failing produces an error, never auth_success."""
    patches = [
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            side_effect=Exception("firebase bad token"),
        ),
        patch(
            "backend.routes.websocket.verify_jwt_token",
            side_effect=Exception("legacy jwt bad token"),
        ),
    ]
    for p in patches:
        p.start()
    try:
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/driver/anyone") as ws:
                ws.send_json({"type": "auth", "token": "rotten-token"})
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert "invalid_token" in msg["message"]
                ws.receive_json()
    finally:
        for p in patches:
            p.stop()


def test_driver_without_driver_row_gets_error_not_ack(app_with_ws, driver_user):
    """A user with no drivers-table row cannot connect as a driver — the
    endpoint closes with `user_is_not_a_driver`, NEVER auth_success."""
    # driver_profile=None → get_rows returns [] so the driver-row check fails
    patches = _patch_auth_and_db(driver_user, driver_profile=None)
    for p in patches:
        p.start()
    try:
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/driver/{driver_user['id']}") as ws:
                ws.send_json({"type": "auth", "token": "fake-firebase-token"})
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert msg["message"] == "user_is_not_a_driver"
                ws.receive_json()
    finally:
        for p in patches:
            p.stop()


def test_admin_without_admin_role_gets_error_not_ack(app_with_ws):
    """A user with role='rider' cannot connect as admin — closes with
    `admin_access_required`, never auth_success."""
    rider_user = {"id": "user_rider_1", "role": "rider", "phone": "+15551234567"}
    patches = _patch_auth_and_db(rider_user, driver_profile=None)
    for p in patches:
        p.start()
    try:
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/admin/{rider_user['id']}") as ws:
                ws.send_json({"type": "auth", "token": "fake-firebase-token"})
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert msg["message"] == "admin_access_required"
                ws.receive_json()
    finally:
        for p in patches:
            p.stop()
