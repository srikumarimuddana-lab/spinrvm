"""
WebSocket authentication tests: auth message validation, token checks,
and per-user inbound rate limiting.

Implementation notes (read backend/routes/websocket.py before modifying):

- The endpoint wraps the first `await websocket.receive_json()` in
  `asyncio.wait_for(..., timeout=30.0)`.  A client that connects but never
  sends anything is closed with code 1008 (RFC 6455 Policy Violation) after
  30 seconds.  The "auth timeout" in the test name refers to both this server
  timer AND the immediate rejection when the wrong message type arrives first.

- The rate-limiter (socket_manager.ConnectionManager.note_user_message) drops
  the *message* and sends a `rate_limited` frame; it does NOT close the socket.
  The socket stays alive so a brief burst doesn't force a re-auth round-trip.

- The heartbeat (30 s interval) runs as a background task AFTER auth succeeds;
  it is not involved in the initial auth handshake.

Test strategy:
  1. test_ws_auth_closes_connection_after_timeout:
       Send a non-auth message type first; the endpoint sends an error and
       closes — exercises the immediate-rejection path.

  1b. test_ws_auth_timeout_no_message:
       Patch asyncio.wait_for to raise TimeoutError; assert close code 1008.
       Exercises the 30-second server timer path without waiting real time.

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

    We exercise the actual rejection code path rather than patching the timer,
    because both paths (timer expiry and wrong-message-type) result in the same
    closed connection — this test pins the immediate-rejection variant.
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


# ── Test 1b: server-side 30s timer path ──────────────────────────────────────


@pytest.mark.anyio
async def test_ws_auth_timeout_no_message(app_with_ws):
    """When asyncio.wait_for raises TimeoutError the endpoint closes with 1008.

    Patches asyncio.wait_for to raise immediately so the test completes in
    microseconds rather than waiting 30 real seconds.  Asserts:
      - websocket.close() is called
      - close code is 1008 (RFC 6455 Policy Violation)

    This is client-misbehavior territory (not a server fault), hence
    logger.warning rather than logger.error.
    """
    import asyncio as _asyncio

    close_calls: list[dict] = []

    mock_ws = MagicMock()
    mock_ws.accept = AsyncMock()
    mock_ws.send_json = AsyncMock()

    async def _close(code=1000, reason=""):
        close_calls.append({"code": code, "reason": reason})

    mock_ws.close = _close

    with patch(
        "backend.routes.websocket.asyncio.wait_for",
        side_effect=_asyncio.TimeoutError,
    ):
        from backend.routes.websocket import websocket_endpoint

        await websocket_endpoint(mock_ws, client_type="rider", client_id="test_rider")

    assert close_calls, "websocket.close() was never called after timeout"
    assert close_calls[0]["code"] == 1008, f"Expected close code 1008 (Policy Violation), got {close_calls[0]['code']}"


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
async def test_ws_message_rate_limit(mock_redis):
    """ConnectionManager.note_user_message() enforces 30 msg/s per user
    via the Redis-backed fleet-wide counter (B4; ``mock_redis`` stands in
    for Redis — see utils/redis_client.py's transparent fallback).

    The 30th call in the current window must return True (allowed).
    The 31st call must return False (rate limited) — the socket is NOT closed,
    matching the production behaviour documented in socket_manager.py.

    We test the rate-limit logic directly (unit-level) rather than through
    the full WebSocket stack, which would require sending 31 real messages
    through a TestClient and risk timing flakiness in CI. Full contract
    coverage (fleet-wide aggregation, per-user isolation, window reset,
    Redis-failure fallback, bucket cleanup) lives in
    test_websocket_per_user_rate_limit.py.
    """
    from backend.socket_manager import ConnectionManager

    mgr = ConnectionManager()
    user_id = "rate_limit_test_user"

    # Drive the first 30 messages in — all should be accepted.
    for i in range(30):
        allowed = await mgr.note_user_message(user_id, max_per_second=30)
        assert allowed, f"message {i + 1} should be within the 30/s budget"

    # The 31st message in the same window must be rejected.
    over_limit = await mgr.note_user_message(user_id, max_per_second=30)
    assert not over_limit, "31st message should exceed the 30/s limit"


@pytest.mark.anyio
async def test_ws_message_rate_limit_resets_after_window(mock_redis):
    """After the 1-second window expires (the Redis key's TTL), the
    counter resets and messages are accepted again — the socket is never
    terminated by this path."""
    from backend.socket_manager import ConnectionManager

    mgr = ConnectionManager()
    user_id = "rate_limit_reset_user"

    # Fill the window.
    for _ in range(30):
        await mgr.note_user_message(user_id, max_per_second=30)
    assert await mgr.note_user_message(user_id, max_per_second=30) is False

    # Wait out the TTL set on the counter's first increment.
    time.sleep(1.05)

    # Now the counter should have expired and a new message should pass.
    allowed = await mgr.note_user_message(user_id, max_per_second=30)
    assert allowed, "after the window expires, the next message should be accepted"


# ── C4: no auto-create on lookup miss; DB outage ≠ invalid token ──────────────


def test_ws_firebase_user_missing_is_not_auto_created(app_with_ws):
    """A valid Firebase token whose users row is missing must NOT mint a new
    user over the socket (C4 — mirrors get_current_user in dependencies/).

    The old path called create_user() on a lookup miss: a transient Supabase
    replica miss forked a phantom account, and a PIPEDA-deleted account's
    Firebase UID could re-provision itself over a socket. The endpoint now
    closes 1013 (try again later) so the client retries.
    """
    firebase_payload = {
        "uid": "ghost-uid-c4",
        "phone_number": "+15550009999",
        "aud": _TEST_FIREBASE_APP_ID,
    }
    create_user = AsyncMock()

    patches = _start_patches(
        patch("backend.routes.websocket.settings", _rider_settings_mock()),
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            return_value=firebase_payload,
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_phone",
            new=AsyncMock(return_value=None),
        ),
        patch("backend.routes.websocket.db_supabase.create_user", new=create_user),
    )
    try:
        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/rider/ghost-uid-c4") as ws:
                ws.send_json({"type": "auth", "token": "valid-token-rowless-user"})
                ws.receive_json()
        assert exc_info.value.code == 1013, f"expected 1013 (try again later), got {exc_info.value.code}"
        create_user.assert_not_awaited()
    finally:
        _stop_patches(patches)


def test_ws_db_outage_closes_1013_not_invalid_token(app_with_ws):
    """A Supabase outage during the user lookup is NOT an auth failure (C4).

    The old blanket except turned a DB blip into invalid_token_or_user_not_found,
    which client backoff treats as terminal. The endpoint must close 1013 so
    the client retries.
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
            new=AsyncMock(side_effect=Exception("supabase down")),
        ),
    )
    try:
        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/rider/{_RIDER_USER['id']}") as ws:
                ws.send_json({"type": "auth", "token": "valid-firebase-token"})
                ws.receive_json()
        assert exc_info.value.code == 1013, f"expected 1013 (try again later), got {exc_info.value.code}"
    finally:
        _stop_patches(patches)


def test_ws_revoked_admin_jwt_is_invalid_token_not_1013(app_with_ws):
    """A JWT that decodes but fails admin verification (revoked JTI, disabled
    staff, stale token_version, idle timeout) is a genuine auth failure and
    must stay on the invalid_token close — NOT become a 1013 "try again
    later" (C4 boundary pin: only DB outages get 1013)."""
    from fastapi import HTTPException

    patches = _start_patches(
        _patch_firebase_fail(),
        _patch_jwt_return({"user_id": "admin_revoked_c4", "role": "admin"}),
        patch(
            "backend.routes.websocket._verify_admin_payload",
            new=AsyncMock(side_effect=HTTPException(status_code=401, detail="ERR_TOKEN_REVOKED")),
        ),
    )
    try:
        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/admin/anyone") as ws:
                ws.send_json({"type": "auth", "token": "decodes-but-revoked"})
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert msg["message"] == "invalid_token_or_user_not_found"
                ws.receive_json()
    finally:
        _stop_patches(patches)


# ── C5: ride_status_update requires participation & echoes DB status ──────────

_C5_RIDER = {
    "id": "rider_ws_c5_1",
    "phone": "+15550005555",
    "role": "rider",
    "first_name": "Carol",
    "token_version": 0,
}


def _c5_patches(ride: dict, send_personal: AsyncMock, broadcast_admins: AsyncMock):
    firebase_payload = {
        "uid": _C5_RIDER["id"],
        "phone_number": _C5_RIDER["phone"],
        "aud": _TEST_FIREBASE_APP_ID,
    }
    return _start_patches(
        patch("backend.routes.websocket.settings", _rider_settings_mock()),
        patch(
            "backend.routes.websocket.firebase_auth.verify_id_token",
            return_value=firebase_payload,
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_user_by_id",
            new=AsyncMock(return_value=_C5_RIDER),
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_ride",
            new=AsyncMock(return_value=ride),
        ),
        patch(
            "backend.routes.websocket.db_supabase.get_rows",
            new=AsyncMock(return_value=[]),
        ),
        patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.manager.send_personal_message", new=send_personal),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=broadcast_admins),
    )


def _status_relays(mock: AsyncMock) -> list[dict]:
    return [
        c.args[0]
        for c in mock.await_args_list
        if c.args and isinstance(c.args[0], dict) and c.args[0].get("type") == "ride_status_changed"
    ]


def test_ws_ride_status_update_rejected_for_non_participant(app_with_ws):
    """Any authenticated socket used to be able to relay an arbitrary status
    string for ANY ride to the victim rider and every admin console (C5).
    A non-participant now gets not_ride_participant and nothing is relayed.
    """
    victim_ride = {
        "id": "ride_c5_victim",
        "rider_id": "someone_else",
        "driver_id": "driver_x",
        "status": "in_progress",
    }
    send_personal = AsyncMock()
    broadcast_admins = AsyncMock()
    patches = _c5_patches(victim_ride, send_personal, broadcast_admins)
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/rider/{_C5_RIDER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "valid-firebase-token"})
            assert ws.receive_json()["type"] == "auth_success"

            ws.send_json({"type": "ride_status_update", "ride_id": "ride_c5_victim", "status": "cancelled"})
            resp = ws.receive_json()
            assert resp == {"type": "error", "message": "not_ride_participant"}

        assert _status_relays(send_personal) == [], "spoofed status must not reach the rider"
        assert _status_relays(broadcast_admins) == [], "spoofed status must not reach admins"
    finally:
        _stop_patches(patches)


def test_ws_ride_status_update_echoes_db_status_not_client_string(app_with_ws):
    """A participant's ride_status_update relays the ride's canonical DB
    status — the client-supplied string must never reach other parties (C5).
    """
    own_ride = {
        "id": "ride_c5_own",
        "rider_id": _C5_RIDER["id"],
        "driver_id": "driver_x",
        "status": "driver_arrived",
    }
    send_personal = AsyncMock()
    broadcast_admins = AsyncMock()
    patches = _c5_patches(own_ride, send_personal, broadcast_admins)
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/rider/{_C5_RIDER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "valid-firebase-token"})
            assert ws.receive_json()["type"] == "auth_success"

            # Client lies about the status; the relay must use the DB value.
            ws.send_json({"type": "ride_status_update", "ride_id": "ride_c5_own", "status": "completed"})

            # Synchronization: get_nearby_drivers produces a response frame,
            # and the message loop is sequential — once we get nearby_drivers
            # back, the ride_status_update above has been fully processed.
            ws.send_json({"type": "get_nearby_drivers", "lat": 52.13, "lng": -106.67})
            assert ws.receive_json()["type"] == "nearby_drivers"

        relayed = _status_relays(send_personal)
        assert relayed, "participant rebroadcast should have been relayed to the rider"
        assert all(m["status"] == "driver_arrived" for m in relayed), (
            "relay must echo the DB status, not the client-supplied string"
        )
        admin_relayed = _status_relays(broadcast_admins)
        assert admin_relayed and all(m["status"] == "driver_arrived" for m in admin_relayed)
    finally:
        _stop_patches(patches)


def test_ws_ride_status_update_burst_is_cooled_down(app_with_ws):
    """Each ride_status_update echo costs a get_ride read plus a rider send
    and an all-admin broadcast; the 30 msg/s socket cap alone would let a
    participant amplify that against the DB and every admin console. Frames
    inside the per-connection RIDE_STATUS_ECHO_COOLDOWN_S window are dropped
    silently (real transitions are broadcast by the HTTP handlers anyway)."""
    own_ride = {
        "id": "ride_c5_burst",
        "rider_id": _C5_RIDER["id"],
        "driver_id": "driver_x",
        "status": "driver_arrived",
    }
    send_personal = AsyncMock()
    broadcast_admins = AsyncMock()
    patches = _c5_patches(own_ride, send_personal, broadcast_admins)
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/rider/{_C5_RIDER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "valid-firebase-token"})
            assert ws.receive_json()["type"] == "auth_success"

            for _ in range(3):
                ws.send_json({"type": "ride_status_update", "ride_id": "ride_c5_burst", "status": "completed"})

            # Sequential message loop: a nearby_drivers response means every
            # frame above has been fully processed.
            ws.send_json({"type": "get_nearby_drivers", "lat": 52.13, "lng": -106.67})
            assert ws.receive_json()["type"] == "nearby_drivers"

        relayed = _status_relays(send_personal)
        assert len(relayed) == 1, f"burst must collapse to one echo, got {len(relayed)}"
        admin_relayed = _status_relays(broadcast_admins)
        assert len(admin_relayed) == 1, "admin fan-out must be cooled down too"
    finally:
        _stop_patches(patches)


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
        # B4: note_user_message's primary path is Redis-backed (fleet-wide);
        # force the per-machine fallback so pre-filling _user_msg_timestamps
        # below actually takes effect, same as before B4.
        patch(
            "backend.socket_manager.redis_incr",
            new=AsyncMock(side_effect=ConnectionError("redis down")),
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
