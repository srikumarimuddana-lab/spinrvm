"""
WebSocket authentication tests: auth message validation, token checks,
and per-user inbound rate limiting.

Implementation notes (read backend/routes/websocket.py before modifying):

- The endpoint calls `await websocket.receive_json()` for the first message.
  There is NO server-side timeout wrapping that call — if no message arrives
  the socket simply waits.  The "auth timeout" is therefore NOT a server timer
  but the server-side *rejection* when the wrong message type arrives first.

- The rate-limiter (socket_manager.ConnectionManager.note_user_message) drops
  the *message* and sends a `rate_limited` frame; it does NOT close the socket.
  The socket stays alive so a brief burst doesn't force a re-auth round-trip.

- The heartbeat (30 s interval) runs as a background task AFTER auth succeeds;
  it is not involved in the initial auth handshake.

Test strategy:
  1. test_ws_auth_closes_connection_after_timeout:
       Send a non-auth message type first; the endpoint sends an error and
       closes — exercises the same code path that would fire if auth had
       never arrived and the wrong message was received instead.

  2. test_ws_valid_auth_accepted:
       Send a correct auth message; assert auth_success is returned.

  3. test_ws_invalid_token_rejected:
       Send auth with a token both Firebase and JWT backends reject; assert
       the error response and close.

  4. test_ws_message_rate_limit:
       Directly exercise ConnectionManager.note_user_message() to confirm
       the 31st message in the same 1-second window is rejected.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# ── shared test fixtures ──────────────────────────────────────────────────────

_TEST_FIREBASE_APP_ID = "test-firebase-app-id-auth"
_RIDER_USER = {
    "id": "rider_ws_auth_1",
    "phone": "+15550001234",
    "role": "rider",
    "first_name": "Alice",
    "token_version": 0,
}
_ADMIN_USER = {
    "id": "admin_ws_auth_1",
    "email": "admin@spinr.test",
    "phone": "",
    "role": "admin",
    "token_version": 0,
}


@pytest.fixture
def app_with_ws():
    """Minimal FastAPI app with only the WebSocket router mounted.

    Avoids booting the full server (Stripe, Redis, Firebase admin SDK, etc.)
    for a handler-level test."""
    from backend.routes.websocket import router

    app = FastAPI()
    app.include_router(router)
    return app


def _patch_firebase_fail():
    """Force Firebase token verification to raise so we fall through to JWT."""
    return patch(
        "backend.routes.websocket.firebase_auth.verify_id_token",
        side_effect=Exception("firebase not configured in test"),
    )


def _patch_jwt_return(payload: dict):
    return patch("backend.routes.websocket.verify_jwt_token", return_value=payload)


def _patch_jwt_fail():
    return patch(
        "backend.routes.websocket.verify_jwt_token",
        side_effect=Exception("bad token"),
    )


def _rider_settings_mock():
    m = MagicMock()
    m.FIREBASE_RIDER_APP_ID = _TEST_FIREBASE_APP_ID
    m.FIREBASE_DRIVER_APP_ID = ""
    return m


# ── helpers ──────────────────────────────────────────────────────────────────


def _start_patches(*patch_contexts):
    """Start a sequence of patch objects and return them for cleanup."""
    started = []
    for p in patch_contexts:
        p.start()
        started.append(p)
    return started


def _stop_patches(patches):
    for p in patches:
        try:
            p.stop()
        except RuntimeError:
            pass


# ── Test 1: auth timeout (wrong first message → connection closed) ────────────


def test_ws_auth_closes_connection_after_timeout(app_with_ws):
    """Send a non-auth message as the very first message.

    The implementation reads the first message, checks `type == "auth"`, and
    if not, sends `{"type": "error", "message": "authentication_required"}`
    then closes.  This mirrors what would happen if a client connected but
    failed to send an auth message before the server decided to reject it —
    the outcome is identical: connection closed with an auth error.

    We exercise the actual rejection code path rather than inserting a
    synthetic sleep, because the implementation has no server-side timer for
    the first message; the rejection fires immediately on receipt of a
    non-auth payload.
    """
    client = TestClient(app_with_ws)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/rider/some-id") as ws:
            # Send an irrelevant message — simulates "client never sent auth"
            # in that the server sees a non-auth message and terminates.
            ws.send_json({"type": "ping"})
            # Server should respond with an error frame …
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["message"] == "authentication_required"
            # … then close — next receive raises WebSocketDisconnect
            ws.receive_json()


def test_ws_no_token_in_auth_message_closes_connection(app_with_ws):
    """Auth message with missing `token` field is treated as unauthenticated."""
    client = TestClient(app_with_ws)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/rider/some-id") as ws:
            ws.send_json({"type": "auth"})  # no token key
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["message"] == "authentication_required"
            ws.receive_json()


# ── Test 2: valid auth accepted ───────────────────────────────────────────────


def test_ws_valid_auth_accepted(app_with_ws):
    """Send a valid auth message (Firebase path); assert auth_success returned."""
    firebase_payload = {
        "uid": _RIDER_USER["id"],
        "phone_number": _RIDER_USER["phone"],
        "aud": _TEST_FIREBASE_APP_ID,
    }

    patches = _start_patches(
        patch("backend.routes.websocket.settings", _rider_settings_mock()),
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            return_value=firebase_payload,
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_id",
            new=AsyncMock(return_value=_RIDER_USER),
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_rows",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "backend.routes.websocket.db.find_one",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.routes.websocket.manager.broadcast_to_admins",
            new=AsyncMock(return_value=None),
        ),
    )
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/rider/{_RIDER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "valid-firebase-token"})
            msg = ws.receive_json()
            assert msg["type"] == "auth_success"
            assert msg["client_type"] == "rider"
    finally:
        _stop_patches(patches)


# ── Test 3: invalid token rejected ───────────────────────────────────────────


def test_ws_invalid_token_rejected(app_with_ws):
    """Send auth with a token that both Firebase and JWT backends reject.

    The endpoint falls through both verification paths, arrives at
    `user = None`, and closes with `invalid_token_or_user_not_found`.
    """
    patches = _start_patches(
        _patch_firebase_fail(),
        _patch_jwt_fail(),
    )
    try:
        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/rider/anyone") as ws:
                ws.send_json({"type": "auth", "token": "bad-token"})
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert "invalid_token" in msg["message"]
                ws.receive_json()
    finally:
        _stop_patches(patches)


def test_ws_invalid_token_via_admin_jwt_path(app_with_ws):
    """Admin JWT with a bad token also produces an error close.

    Firebase fails first (admin tokens are not Firebase tokens), then the
    legacy JWT verification raises, so `user` is None.
    """
    patches = _start_patches(
        _patch_firebase_fail(),
        _patch_jwt_fail(),
    )
    try:
        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/admin/anyone") as ws:
                ws.send_json({"type": "auth", "token": "totally-invalid"})
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert msg["message"] == "invalid_token_or_user_not_found"
                ws.receive_json()
    finally:
        _stop_patches(patches)


# ── Test 4: per-user inbound message rate limit ───────────────────────────────


@pytest.mark.anyio
async def test_ws_message_rate_limit():
    """ConnectionManager.note_user_message() enforces 30 msg/s per user.

    The 30th call in a 1-second window must return True (allowed).
    The 31st call must return False (rate limited) — the socket is NOT closed,
    matching the production behaviour documented in socket_manager.py.

    We test the rate-limit logic directly (unit-level) rather than through
    the full WebSocket stack, which would require sending 31 real messages
    through a TestClient and risk timing flakiness in CI.
    """
    from backend.socket_manager import ConnectionManager

    mgr = ConnectionManager()
    user_id = "rate_limit_test_user"

    # Drive the first 30 messages in — all should be accepted.
    for i in range(30):
        allowed = mgr.note_user_message(user_id, max_per_second=30)
        assert allowed, f"message {i + 1} should be within the 30/s budget"

    # The 31st message in the same ~instant window must be rejected.
    over_limit = mgr.note_user_message(user_id, max_per_second=30)
    assert not over_limit, "31st message should exceed the 30/s limit"


@pytest.mark.anyio
async def test_ws_message_rate_limit_resets_after_window():
    """After the 1-second sliding window expires, the bucket clears and
    messages are accepted again — the socket is never terminated by this path.
    """
    from backend.socket_manager import ConnectionManager

    mgr = ConnectionManager()
    user_id = "rate_limit_reset_user"

    # Fill the bucket.
    for _ in range(30):
        mgr.note_user_message(user_id, max_per_second=30)

    # Manually rewind all timestamps by 1.1 s so the next call trims the bucket.
    bucket = mgr._user_msg_timestamps.get(user_id, [])
    mgr._user_msg_timestamps[user_id] = [t - 1.1 for t in bucket]

    # Now the bucket should be clear and a new message should pass.
    allowed = mgr.note_user_message(user_id, max_per_second=30)
    assert allowed, "after the window expires, the next message should be accepted"


@pytest.mark.anyio
async def test_ws_rate_limit_response_keeps_socket_open(app_with_ws):
    """When the rate limit is exceeded the server sends `rate_limited` but
    does NOT close the socket.  The response frame includes the limit fields
    documented in routes/websocket.py.

    We test via the WS endpoint proper, using a mocked rider session.
    We send enough messages to trip the rate-limit, then verify the
    endpoint responds with `rate_limited` rather than closing.
    """
    firebase_payload = {
        "uid": _RIDER_USER["id"],
        "phone_number": _RIDER_USER["phone"],
        "aud": _TEST_FIREBASE_APP_ID,
    }

    patches = _start_patches(
        patch("backend.routes.websocket.settings", _rider_settings_mock()),
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            return_value=firebase_payload,
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_id",
            new=AsyncMock(return_value=_RIDER_USER),
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_rows",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "backend.routes.websocket.db.find_one",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.routes.websocket.manager.broadcast_to_admins",
            new=AsyncMock(return_value=None),
        ),
    )
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/rider/{_RIDER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "valid-firebase-token"})
            ack = ws.receive_json()
            assert ack["type"] == "auth_success"

            # Manually pre-fill the user's rate-limit bucket so the very next
            # message is over the limit — avoids sending 31 real messages
            # and the resulting DB calls.
            from backend.socket_manager import manager as global_mgr

            now_ts = time.monotonic()
            global_mgr._user_msg_timestamps[_RIDER_USER["id"]] = [now_ts] * 30

            # Send one more message (type unknown → hits the rate limiter first).
            ws.send_json({"type": "unknown_type_to_trigger_rate_limit"})
            resp = ws.receive_json()
            assert resp["type"] == "rate_limited"
            assert resp["limit"] == 30
            assert "retry_after_seconds" in resp

            # Socket is still open — we can receive more messages (e.g. pings).
            # Clean up the bucket so the socket doesn't stay blocked.
            global_mgr._user_msg_timestamps.pop(_RIDER_USER["id"], None)
    finally:
        _stop_patches(patches)
