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

# Matches the test value we inject into the settings mock. Any non-empty
# string works — it just needs to equal payload["aud"] so the audience check
# passes without touching a real Firebase project.
_TEST_FIREBASE_APP_ID = "test-firebase-app-id"


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


def _make_settings_mock(client_type: str) -> MagicMock:
    """Return a settings-shaped mock with the right FIREBASE_*_APP_ID set."""
    m = MagicMock()
    m.FIREBASE_DRIVER_APP_ID = _TEST_FIREBASE_APP_ID if client_type == "driver" else ""
    m.FIREBASE_RIDER_APP_ID = _TEST_FIREBASE_APP_ID if client_type == "rider" else ""
    return m


def _patch_firebase_auth_and_db(user, driver_profile=None, client_type="driver"):
    """Patches for driver/rider WebSocket auth via Firebase token.

    Since B-P1-1 the endpoint validates payload["aud"] against
    settings.FIREBASE_{DRIVER,RIDER}_APP_ID. We inject a matching test value
    via a settings mock and include the same value in the Firebase payload so
    the audience check passes in test.
    """
    firebase_payload = {
        "uid": user["id"],
        "phone_number": user.get("phone", ""),
        "aud": _TEST_FIREBASE_APP_ID,
    }
    return [
        patch("backend.routes.websocket.settings", _make_settings_mock(client_type)),
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            return_value=firebase_payload,
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_rows",
            new=AsyncMock(return_value=[driver_profile] if driver_profile else []),
        ),
        patch(
            "backend.routes.websocket.db.find_one",
            new=AsyncMock(return_value=driver_profile),
        ),
        patch(
            "backend.routes.websocket.manager.broadcast_to_admins",
            new=AsyncMock(return_value=None),
        ),
    ]


def _patch_jwt_auth_and_db(user, db_user=None):
    """Patches for admin WebSocket auth via legacy JWT.

    Admin tokens are minted by the backend (not Firebase), so Firebase
    verify_id_token is forced to raise — this triggers the JWT fallback path
    in the endpoint. The no-aud admin shortcut was retired as a hardening
    (a crafted admin-001 token with role+email but no aud used to pass with
    zero DB verification), so an admin token now MUST carry aud=JWT_AUD_ADMIN
    and resolve to an active admin_staff row. Claims are derived from the
    user's role so the same helper serves both the admin-success path and the
    rider-rejection path (rider gets aud=JWT_AUD_MOBILE and no staff row).
    """
    from backend.dependencies import JWT_AUD_ADMIN, JWT_AUD_MOBILE

    _admin_roles = {"admin", "super_admin", "operations", "support", "finance", "custom"}
    role = user.get("role", "rider")
    is_admin = role in _admin_roles
    jwt_payload = {
        "user_id": user["id"],
        "role": role,
        "email": user.get("email", f"{user['id']}@spinr.test"),
        "phone": user.get("phone", ""),
        "aud": JWT_AUD_ADMIN if is_admin else JWT_AUD_MOBILE,
    }
    # _verify_admin_payload reads an active admin_staff row for admin tokens.
    staff_rows = [{"id": user["id"], "is_active": True, "token_version": 0}] if is_admin else []
    return [
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            side_effect=Exception("firebase not used for admin tokens"),
        ),
        patch(
            "backend.routes.websocket.verify_jwt_token",
            return_value=jwt_payload,
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_id",
            new=AsyncMock(return_value=db_user or user),
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_rows",
            new=AsyncMock(return_value=staff_rows),
        ),
        patch(
            "backend.routes.websocket.db_supabase.update_one",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "backend.routes.websocket.db.find_one",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.routes.websocket.manager.broadcast_to_admins",
            new=AsyncMock(return_value=None),
        ),
    ]


def test_driver_auth_success_is_sent_immediately(app_with_ws, driver_user):
    """After a driver authenticates, the backend MUST send an `auth_success`
    message before anything else — that's the signal the driver-app uses to
    flip off the "Connection lost" banner."""
    driver_profile = {"id": "driver_1", "user_id": driver_user["id"], "is_online": True}

    patches = _patch_firebase_auth_and_db(driver_user, driver_profile=driver_profile, client_type="driver")
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
    """Same contract for admin clients — they use the same banner pattern.
    Admin tokens go through the legacy JWT path (not Firebase), so Firebase
    is forced to fail and the JWT fallback is mocked instead."""
    patches = _patch_jwt_auth_and_db(admin_user)
    for p in patches:
        p.start()
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/admin/{admin_user['id']}") as ws:
            ws.send_json({"type": "auth", "token": "fake-jwt-token"})
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
    patches = _patch_firebase_auth_and_db(driver_user, driver_profile=None, client_type="driver")
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
    # JWT path: role='rider' not in admin roles → DB lookup → role check fails
    patches = _patch_jwt_auth_and_db(rider_user, db_user=rider_user)
    for p in patches:
        p.start()
    try:
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/admin/{rider_user['id']}") as ws:
                ws.send_json({"type": "auth", "token": "fake-jwt-token"})
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert msg["message"] == "admin_access_required"
                ws.receive_json()
    finally:
        for p in patches:
            p.stop()
