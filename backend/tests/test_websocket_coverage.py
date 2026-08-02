"""routes/websocket.py — targeted coverage-gap tests.

Companion to the existing WS test files (test_websocket_auth.py,
test_websocket_auth_ack.py, test_websocket_per_user_rate_limit.py,
test_websocket_token_revocation.py, test_ws_disconnect_presence_grace.py,
test_ws_health.py, test_websocket_live_location.py,
test_ws_rider_location_statuses.py, test_ws_fanout_metrics.py) — this file
does NOT duplicate any test name from those. It targets the receive-loop
branches those files leave uncovered: the 64 KB message-size guard,
malformed JSON, driver_location happy-path persistence + rider fan-out +
admin broadcast, location_batch handling (non-list points, batch rate
limit, session-revoked ack-with-zero, successful persist), the
disconnect/exception cleanup tail, and `_handle_driver_ws_disconnect` /
`heartbeat_task` edge branches.

Patch target: `backend.routes.websocket.<name>` throughout, matching the
house style in test_websocket_auth.py (module-level bindings, not the
defining module — this file imports its dependencies via the dual-import
`try/except` block at module load time).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_request_deadline():
    """Defend against a pre-existing test-pollution bug (NOT in
    websocket.py): several tests in test_utils_extended.py's TestDeadline*
    class call `set_request_deadline(...)` directly and never reset the
    contextvar afterward, leaking a permanently-past deadline into every
    later test in the same pytest process that calls
    `repositories._base.run_sync`. Same workaround already used in
    test_wallet_repo.py / test_ride_repo_coverage.py — reset the
    contextvar to a known-good `None` state for every test here too.
    """
    try:
        from backend.utils.request_deadline import _request_deadline_ctx
    except ImportError:
        try:
            from utils.request_deadline import _request_deadline_ctx  # type: ignore
        except ImportError:
            _request_deadline_ctx = None
    if _request_deadline_ctx is not None:
        token = _request_deadline_ctx.set(None)
        try:
            yield
        finally:
            _request_deadline_ctx.reset(token)
    else:
        yield


# ── shared fixtures ─────────────────────────────────────────────────────────

_RIDER_USER = {
    "id": "rider_ws_cov_1",
    "phone": "+15550009999",
    "role": "rider",
    "first_name": "Cov",
    "token_version": 0,
}
_DRIVER_USER = {
    "id": "driver_ws_cov_1",
    "phone": "+15550008888",
    "role": "driver",
    "first_name": "Dan",
    "token_version": 0,
}
_DRIVER_PROFILE = {"id": "driver_profile_cov_1", "user_id": _DRIVER_USER["id"], "is_online": True}


@pytest.fixture
def app_with_ws():
    from backend.routes.websocket import router

    app = FastAPI()
    app.include_router(router)
    return app


def _start(*ps):
    started = []
    for p in ps:
        p.start()
        started.append(p)
    return started


def _stop(started):
    for p in started:
        try:
            p.stop()
        except RuntimeError:
            pass


def _driver_auth_patches(extra=None, *, session_id=None):
    """Patches to get a driver socket through the handshake and into the
    receive loop with minimal side effects, mirroring test_websocket_auth's
    `_patch_jwt_return` pattern but for the driver client_type."""
    jwt_payload = {"user_id": _DRIVER_USER["id"], "role": "driver"}
    if session_id:
        jwt_payload["session_id"] = session_id
    ps = [
        patch("backend.routes.websocket.firebase_auth.verify_id_token", side_effect=Exception("no firebase")),
        patch("backend.routes.websocket.verify_jwt_token", return_value=jwt_payload),
        patch("backend.routes.websocket.db_supabase.get_user_by_id", new=AsyncMock(return_value=_DRIVER_USER)),
        patch(
            "backend.routes.websocket.db_supabase.get_rows",
            new=AsyncMock(return_value=[_DRIVER_PROFILE]),
        ),
        patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.mark_present", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.is_session_revoked", new=AsyncMock(return_value=False)),
    ]
    if extra:
        ps.extend(extra)
    return ps


# ── 1. Message-size guard ───────────────────────────────────────────────────


def test_oversized_message_rejected_without_closing_socket(app_with_ws):
    """A raw text frame over WS_MAX_MESSAGE_SIZE gets a `message_too_large`
    error and the loop `continue`s — the socket must stay open for the next
    (valid) message."""
    from backend.routes import websocket as ws_mod

    patches = _start(*_driver_auth_patches())
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()  # auth_success

            oversized_raw = "x" * (ws_mod.WS_MAX_MESSAGE_SIZE + 100)
            ws.send_text(oversized_raw)
            msg = ws.receive_json()
            assert msg == {"type": "error", "message": "message_too_large"}

            # Socket still alive: send a harmless unknown-type message next.
            ws.send_json({"type": "totally_unknown_type"})
            # No response is sent for unknown types (only logged) — prove
            # liveness instead via a pong round-trip.
            ws.send_json({"type": "pong"})
    finally:
        _stop(patches)


# ── 2. Malformed JSON ────────────────────────────────────────────────────────


def test_malformed_json_gets_invalid_json_error(app_with_ws):
    patches = _start(*_driver_auth_patches())
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()

            ws.send_text("{not valid json::")
            msg = ws.receive_json()
            assert msg == {"type": "error", "message": "invalid_json"}
    finally:
        _stop(patches)


# ── 3. driver_location happy path: integrity check, throttled DB write,
#    breadcrumb buffering, rider fan-out, admin broadcast ──────────────────


@pytest.mark.anyio
async def test_driver_location_happy_path_persists_and_fans_out(app_with_ws):
    active_ride = {
        "id": "ride_cov_1",
        "rider_id": "rider_target_1",
        "driver_id": _DRIVER_PROFILE["id"],
        "status": "driver_accepted",
        "pickup_lat": 50.45,
        "pickup_lng": -104.61,
    }

    update_loc = AsyncMock(return_value=None)
    send_personal = AsyncMock(return_value=None)
    broadcast_admin_loc = AsyncMock(return_value=None)
    buffer_crumb = AsyncMock(return_value=None)
    update_driver_loc_db = AsyncMock(return_value=None)

    extra = [
        patch("backend.routes.websocket.check_location_integrity", new=AsyncMock(return_value=(True, "ok"))),
        patch("backend.routes.websocket.db_supabase.update_driver_location", new=update_driver_loc_db),
        patch("backend.routes.websocket.manager.update_driver_location", new=update_loc),
        patch("backend.routes.websocket.resolve_active_rides_cached", new=AsyncMock(return_value=[active_ride])),
        patch("backend.routes.websocket.buffer_ride_breadcrumb", new=buffer_crumb),
        patch("backend.routes.websocket.get_app_settings", new=AsyncMock(return_value={"google_maps_api_key": "k"})),
        patch("backend.routes.websocket.get_cached_ride_eta", new=AsyncMock(return_value=42)),
        patch("backend.routes.websocket.manager.send_personal_message", new=send_personal),
        patch("backend.routes.websocket.manager.broadcast_driver_location_to_admins", new=broadcast_admin_loc),
    ]
    patches = _start(*_driver_auth_patches(extra))
    try:
        from backend.routes.websocket import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()

            ws.send_json({"type": "driver_location", "lat": 50.4452, "lng": -104.6189, "speed": 12, "heading": 90})
            # No direct ack for driver_location — give the loop a beat then
            # send a pong to confirm the socket is still alive and the
            # handler didn't raise.
            ws.send_json({"type": "pong"})
    finally:
        _stop(patches)

    update_driver_loc_db.assert_awaited_once()
    update_loc.assert_awaited_once()
    buffer_crumb.assert_awaited_once()
    send_personal.assert_awaited()
    sent_msg, sent_target = send_personal.await_args.args[0], send_personal.await_args.args[1]
    assert sent_msg["type"] == "driver_location_update"
    assert sent_target == f"rider_{active_ride['rider_id']}"
    assert sent_msg.get("eta_seconds") == 42
    broadcast_admin_loc.assert_awaited_once()


@pytest.mark.anyio
async def test_driver_location_untrusted_integrity_skips_all_writes(app_with_ws):
    """check_location_integrity() returning trusted=False must short-circuit
    before any persistence or fan-out — GPS spoofing guard."""
    update_driver_loc_db = AsyncMock(return_value=None)
    update_loc = AsyncMock(return_value=None)
    send_personal = AsyncMock(return_value=None)

    extra = [
        patch("backend.routes.websocket.check_location_integrity", new=AsyncMock(return_value=(False, "teleport"))),
        patch("backend.routes.websocket.db_supabase.update_driver_location", new=update_driver_loc_db),
        patch("backend.routes.websocket.manager.update_driver_location", new=update_loc),
        patch("backend.routes.websocket.manager.send_personal_message", new=send_personal),
    ]
    patches = _start(*_driver_auth_patches(extra))
    try:
        from backend.routes.websocket import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "driver_location", "lat": 50.4, "lng": -104.6})
            ws.send_json({"type": "pong"})
    finally:
        _stop(patches)

    update_driver_loc_db.assert_not_awaited()
    update_loc.assert_not_awaited()
    send_personal.assert_not_awaited()


@pytest.mark.anyio
async def test_driver_location_skips_breadcrumb_when_session_revoked(app_with_ws):
    """A signed-out session (mid-connection revocation) must not append to
    the durable breadcrumb trail, even though the live-marker fan-out still
    runs (ephemeral, harmless)."""
    buffer_crumb = AsyncMock(return_value=None)

    # JWT payload carries a session_id so the mid-connection
    # `_ws_session_revoked()` check actually consults Redis instead of
    # short-circuiting on `if not ws_session_id: return False`. The first
    # call (handshake) must return False to get past the connect-time
    # tombstone check; the second call (mid-loop, before the breadcrumb
    # write) returns True to exercise the skip-persist branch.
    extra = [
        patch("backend.routes.websocket.check_location_integrity", new=AsyncMock(return_value=(True, "ok"))),
        patch("backend.routes.websocket.db_supabase.update_driver_location", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.manager.update_driver_location", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.resolve_active_rides_cached", new=AsyncMock(return_value=[])),
        patch("backend.routes.websocket.buffer_ride_breadcrumb", new=buffer_crumb),
        patch("backend.routes.websocket.is_session_revoked", new=AsyncMock(side_effect=[False, True])),
        patch("backend.routes.websocket.manager.broadcast_driver_location_to_admins", new=AsyncMock(return_value=None)),
    ]
    patches = _start(*_driver_auth_patches(extra, session_id="sess-cov-1"))
    try:
        from backend.routes.websocket import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "driver_location", "lat": 50.4, "lng": -104.6})
            ws.send_json({"type": "pong"})
    finally:
        _stop(patches)

    buffer_crumb.assert_not_awaited()


# ── 4. location_batch handling ───────────────────────────────────────────────


def test_location_batch_non_list_points_acks_zero(app_with_ws):
    patches = _start(*_driver_auth_patches())
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "location_batch", "points": "not-a-list"})
            msg = ws.receive_json()
            assert msg == {"type": "location_batch_ack", "count": 0}
    finally:
        _stop(patches)


def test_location_batch_empty_points_list_acks_zero(app_with_ws):
    # Regression test for #3175: a well-formed but empty points list
    # previously fell through the `and points` truthiness guard and got no
    # ack at all, unlike the not-a-list case above.
    patches = _start(*_driver_auth_patches())
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "location_batch", "points": []})
            msg = ws.receive_json()
            assert msg == {"type": "location_batch_ack", "count": 0}
    finally:
        _stop(patches)


@pytest.mark.anyio
async def test_location_batch_rate_limit_exceeded(app_with_ws):
    extra = [
        patch("backend.routes.websocket.redis_incr", new=AsyncMock(return_value=2000)),
        patch("backend.routes.websocket.redis_expire", new=AsyncMock(return_value=None)),
    ]
    patches = _start(*_driver_auth_patches(extra))
    try:
        from backend.routes.websocket import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "location_batch", "points": [{"lat": 50.4, "lng": -104.6}]})
            msg = ws.receive_json()
            assert msg["type"] == "rate_limited"
            assert msg["scope"] == "location_batch"
    finally:
        _stop(patches)


@pytest.mark.anyio
async def test_location_batch_session_revoked_acks_zero(app_with_ws):
    extra = [
        patch("backend.routes.websocket.redis_incr", new=AsyncMock(return_value=1)),
        patch("backend.routes.websocket.redis_expire", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.is_session_revoked", new=AsyncMock(side_effect=[False, True])),
    ]
    patches = _start(*_driver_auth_patches(extra, session_id="sess-cov-2"))
    try:
        from backend.routes.websocket import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "location_batch", "points": [{"lat": 50.4, "lng": -104.6}]})
            msg = ws.receive_json()
            assert msg == {"type": "location_batch_ack", "count": 0}
    finally:
        _stop(patches)


@pytest.mark.anyio
async def test_location_batch_successful_persist_fans_out_to_riders(app_with_ws):
    active_ride = {
        "id": "ride_cov_batch_1",
        "rider_id": "rider_batch_target",
        "driver_id": _DRIVER_PROFILE["id"],
        "status": "in_progress",
        "pickup_lat": 50.45,
        "pickup_lng": -104.61,
    }
    send_personal = AsyncMock(return_value=None)
    broadcast_admin_loc = AsyncMock(return_value=None)

    extra = [
        patch("backend.routes.websocket.redis_incr", new=AsyncMock(return_value=1)),
        patch("backend.routes.websocket.redis_expire", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.is_session_revoked", new=AsyncMock(return_value=False)),
        patch("backend.routes.websocket.persist_ride_breadcrumbs", new=AsyncMock(return_value=1)),
        patch("backend.routes.websocket.check_location_integrity", new=AsyncMock(return_value=(True, "ok"))),
        patch("backend.routes.websocket.db_supabase.update_driver_location", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.manager.update_driver_location", new=AsyncMock(return_value=None)),
        patch(
            "backend.routes.websocket.db_supabase.get_rows",
            new=AsyncMock(side_effect=[[_DRIVER_PROFILE], [active_ride]]),
        ),
        patch("backend.routes.websocket.get_cached_ride_eta", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.refresh_ride_eta", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.manager.send_personal_message", new=send_personal),
        patch("backend.routes.websocket.manager.broadcast_driver_location_to_admins", new=broadcast_admin_loc),
    ]
    patches = _start(*_driver_auth_patches(extra))
    try:
        from backend.routes.websocket import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json(
                {
                    "type": "location_batch",
                    "points": [{"latitude": 50.44, "longitude": -104.61, "speed": 5}],
                }
            )
            msg = ws.receive_json()
            assert msg == {"type": "location_batch_ack", "count": 1}
    finally:
        _stop(patches)

    send_personal.assert_awaited()
    broadcast_admin_loc.assert_awaited_once()


# ── 5. Disconnect / exception-handling tail ──────────────────────────────────


def test_clean_disconnect_flushes_breadcrumbs_and_forgets_throttle(app_with_ws):
    flush = AsyncMock(return_value=None)
    forget_throttle = MagicMock()

    extra = [
        patch("backend.routes.websocket.flush_driver_breadcrumbs", new=flush),
        patch("backend.routes.websocket.manager.forget_driver_location_throttle", new=forget_throttle),
        patch("backend.routes.websocket._handle_driver_ws_disconnect", new=AsyncMock(return_value=None)),
    ]
    patches = _start(*_driver_auth_patches(extra))
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            # Closing the client side triggers WebSocketDisconnect server-side.
    finally:
        _stop(patches)

    flush.assert_awaited_once_with(_DRIVER_PROFILE["id"])
    forget_throttle.assert_called_once_with(_DRIVER_PROFILE["id"])


def test_unexpected_exception_in_loop_closes_socket_and_runs_cleanup(app_with_ws):
    """A non-WebSocketDisconnect exception inside the receive loop must still
    run the same connection_key-owned cleanup and close the socket (not raise
    unhandled), per CLAUDE.md's "surface loudly, never silently swallow" —
    here the surfacing is `logger.exception` plus a definite close, not a
    half-open zombie socket."""
    boom = AsyncMock(side_effect=RuntimeError("boom"))
    disconnect_hook = AsyncMock(return_value=None)

    extra = [
        patch("backend.routes.websocket.check_location_integrity", new=boom),
        patch("backend.routes.websocket._handle_driver_ws_disconnect", new=disconnect_hook),
        patch("backend.routes.websocket.flush_driver_breadcrumbs", new=AsyncMock(return_value=None)),
    ]
    patches = _start(*_driver_auth_patches(extra))
    try:
        client = TestClient(app_with_ws)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
                ws.send_json({"type": "auth", "token": "tok"})
                ws.receive_json()
                ws.send_json({"type": "driver_location", "lat": 50.4, "lng": -104.6})
                # Server closes after the exception path; next receive raises.
                ws.receive_json()
                ws.receive_json()
    finally:
        _stop(patches)

    disconnect_hook.assert_awaited()


# ── 6. _handle_driver_ws_disconnect edges ────────────────────────────────────


@pytest.mark.anyio
async def test_handle_driver_ws_disconnect_noop_for_non_driver_key():
    from backend.routes.websocket import _handle_driver_ws_disconnect

    # No-op branch: key missing the "driver_" prefix.
    await _handle_driver_ws_disconnect("rider_abc", {"id": "abc"})
    await _handle_driver_ws_disconnect(None, {"id": "abc"})
    await _handle_driver_ws_disconnect("driver_abc", None)


@pytest.mark.anyio
async def test_handle_driver_ws_disconnect_skips_when_newer_socket_present():
    from backend.routes.websocket import _handle_driver_ws_disconnect, manager

    key = "driver_stale_reconnect_test"
    manager.active_connections[key] = MagicMock()
    try:
        with patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value=None)) as find_one:
            await _handle_driver_ws_disconnect(key, {"id": "u1"})
            find_one.assert_not_called()
    finally:
        manager.active_connections.pop(key, None)


@pytest.mark.anyio
async def test_handle_driver_ws_disconnect_no_profile_returns_early():
    from backend.routes.websocket import _handle_driver_ws_disconnect

    with patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value=None)):
        await _handle_driver_ws_disconnect("driver_no_profile", {"id": "u1"})


@pytest.mark.anyio
async def test_handle_driver_ws_disconnect_idle_driver_skips_broadcast():
    """Only intent-online drivers are operationally interesting; an idle
    (never went online) driver's socket dropping must not log or broadcast."""
    from backend.routes.websocket import _handle_driver_ws_disconnect

    broadcast = AsyncMock(return_value=None)
    with (
        patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value={"id": "d1", "is_online": False})),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=broadcast),
    ):
        await _handle_driver_ws_disconnect("driver_idle", {"id": "u1"})
    broadcast.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_driver_ws_disconnect_online_driver_logs_and_broadcasts():
    from backend.routes.websocket import _handle_driver_ws_disconnect

    broadcast = AsyncMock(return_value=None)
    insert_one = AsyncMock(return_value=None)
    with (
        patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value={"id": "d1", "is_online": True})),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=broadcast),
        patch("backend.routes.websocket.db_supabase.insert_one", new=insert_one),
    ):
        await _handle_driver_ws_disconnect("driver_online", {"id": "u1"})
    broadcast.assert_awaited_once()
    assert broadcast.await_args.args[0]["type"] == "driver_connection_lost"
    insert_one.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_driver_ws_disconnect_activity_log_failure_still_broadcasts():
    """Best-effort audit log insert failing must not block the admin
    broadcast — the operational signal matters more than the audit trail."""
    from backend.routes.websocket import _handle_driver_ws_disconnect

    broadcast = AsyncMock(return_value=None)
    with (
        patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value={"id": "d1", "is_online": True})),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=broadcast),
        patch("backend.routes.websocket.db_supabase.insert_one", new=AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        await _handle_driver_ws_disconnect("driver_log_fail", {"id": "u1"})
    broadcast.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_driver_ws_disconnect_outer_exception_swallowed():
    """The outer try/except is the function's own documented best-effort
    contract (an admin-visibility nicety, not a state-machine or money
    write) — a find_one failure must not propagate and crash the disconnect
    path."""
    from backend.routes.websocket import _handle_driver_ws_disconnect

    with patch("backend.routes.websocket.db.find_one", new=AsyncMock(side_effect=RuntimeError("db down"))):
        await _handle_driver_ws_disconnect("driver_db_down", {"id": "u1"})


# ── 7. heartbeat_task edges ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_heartbeat_send_failure_breaks_loop():
    from backend.routes.websocket import heartbeat_task

    ws = MagicMock()
    ws.send_json = AsyncMock(side_effect=RuntimeError("socket dead"))

    with patch("backend.routes.websocket.asyncio.sleep", new=AsyncMock(return_value=None)):
        await heartbeat_task(ws, "driver_hb_1")
    ws.send_json.assert_awaited_once()


@pytest.mark.anyio
async def test_heartbeat_closes_on_stale_pong(monkeypatch):
    from backend.routes.websocket import heartbeat_task

    ws = MagicMock()
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)
    conn_state = {"last_pong_at": -10_000.0}  # far in the past → always stale

    with patch("backend.routes.websocket.asyncio.sleep", new=AsyncMock(return_value=None)):
        await heartbeat_task(ws, "driver_hb_2", conn_state)
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs.get("code") == 1001


@pytest.mark.anyio
async def test_heartbeat_revokes_on_bumped_token_version():
    from backend.routes.websocket import heartbeat_task

    ws = MagicMock()
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)
    key = "driver_hb_revoke"

    with (
        patch("backend.routes.websocket.asyncio.sleep", new=AsyncMock(return_value=None)),
        patch(
            "backend.routes.websocket._read_token_version",
            new=AsyncMock(return_value=5),
        ),
        patch("backend.routes.websocket.clear_presence", new=AsyncMock(return_value=None)) as clear_pres,
    ):
        await heartbeat_task(ws, key, None, user_id="u1", driver_id="d1", claim_token_version=1)

    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs.get("code") == 1008
    # Presence clear is gated on this socket owning the connection_key; it
    # is not registered in manager.active_connections here, so must skip.
    clear_pres.assert_not_awaited()


@pytest.mark.anyio
async def test_heartbeat_revokes_via_firebase_watermark():
    from backend.routes.websocket import heartbeat_task

    ws = MagicMock()
    ws.send_json = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)

    with (
        patch("backend.routes.websocket.asyncio.sleep", new=AsyncMock(return_value=None)),
        patch(
            "backend.routes.websocket._read_firebase_session_revoked",
            new=AsyncMock(return_value=True),
        ),
    ):
        await heartbeat_task(ws, "driver_hb_fb_revoke", None, user_id="u1", driver_id=None, firebase_auth_time=1234)
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs.get("code") == 1008


@pytest.mark.anyio
async def test_read_token_version_db_failure_returns_none():
    from backend.routes.websocket import _read_token_version

    with patch("backend.routes.websocket.db_supabase.get_user_by_id", new=AsyncMock(side_effect=RuntimeError("down"))):
        result = await _read_token_version("rider_x", "u1")
    assert result is None


@pytest.mark.anyio
async def test_read_token_version_admin_key_reads_admin_staff():
    from backend.routes.websocket import _read_token_version

    with patch(
        "backend.routes.websocket.db.find_one",
        new=AsyncMock(return_value={"token_version": 7}),
    ) as find_one:
        result = await _read_token_version("admin_x", "u1")
    assert result == 7
    find_one.assert_awaited_once_with("admin_staff", {"id": "u1"})


@pytest.mark.anyio
async def test_read_token_version_missing_row_returns_none():
    from backend.routes.websocket import _read_token_version

    with patch("backend.routes.websocket.db_supabase.get_user_by_id", new=AsyncMock(return_value=None)):
        result = await _read_token_version("rider_x", "u1")
    assert result is None


@pytest.mark.anyio
async def test_read_firebase_session_revoked_db_failure_fails_open():
    from backend.routes.websocket import _read_firebase_session_revoked

    with patch("backend.routes.websocket.db_supabase.get_user_by_id", new=AsyncMock(side_effect=RuntimeError("down"))):
        result = await _read_firebase_session_revoked("rider_x", "u1", 123)
    assert result is False


@pytest.mark.anyio
async def test_read_firebase_session_revoked_missing_row_returns_false():
    from backend.routes.websocket import _read_firebase_session_revoked

    with patch("backend.routes.websocket.db_supabase.get_user_by_id", new=AsyncMock(return_value=None)):
        result = await _read_firebase_session_revoked("rider_x", "u1", 123)
    assert result is False


# ── 8. Small pure-function edges ─────────────────────────────────────────────


def test_parse_live_coordinate_edge_cases():
    from backend.routes.websocket import _parse_live_coordinate

    assert _parse_live_coordinate(None) is None
    assert _parse_live_coordinate("") is None
    assert _parse_live_coordinate("not-a-number") is None
    assert _parse_live_coordinate("nan") is None  # float("nan") parses but isn't finite
    assert _parse_live_coordinate("50.4452") == 50.4452


def test_valid_live_coordinates_edge_cases():
    from backend.routes.websocket import _valid_live_coordinates

    assert _valid_live_coordinates(0.0, 0.0) is False  # registration-default guard
    assert _valid_live_coordinates(91.0, 0.0) is False
    assert _valid_live_coordinates(0.0, 181.0) is False
    assert _valid_live_coordinates(50.4452, -104.6189) is True


# ── 9. get_nearby_drivers / chat_message / ride_status_update: reachability ──


def test_get_nearby_drivers_kill_switch_returns_empty(app_with_ws):
    extra = [
        patch("backend.routes.websocket.settings_loader_get_app_settings_placeholder", create=True),
    ]
    # map_settings / prematch_driver_list / dispatch_geo_bounds are imported
    # inline inside the handler via nested try/except — patch the modules
    # they resolve to directly.
    with (
        patch("backend.utils.driver_map_visibility.map_settings", return_value=(False, 500, 10.0)),
        patch("backend.settings_loader.get_app_settings", new=AsyncMock(return_value={})),
    ):
        patches = _start(*_driver_auth_patches())
        try:
            client = TestClient(app_with_ws)
            with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
                ws.send_json({"type": "auth", "token": "tok"})
                ws.receive_json()
                ws.send_json({"type": "get_nearby_drivers", "lat": 50.4, "lng": -104.6})
                msg = ws.receive_json()
                assert msg == {"type": "nearby_drivers", "drivers": []}
        finally:
            _stop(patches)


def test_ride_status_update_unknown_ride_id_is_silent(app_with_ws):
    """`ride_id` truthy but the ride doesn't exist: no error frame, no
    fan-out — the handler simply falls through."""
    extra = [
        patch("backend.routes.websocket.db_supabase.get_ride", new=AsyncMock(return_value=None)),
    ]
    patches = _start(*_driver_auth_patches(extra))
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "ride_status_update", "ride_id": "does-not-exist"})
            # Prove liveness / no crash via a subsequent round-trip. An empty
            # `points` list takes a different (silent) branch than a
            # non-list value, so use the latter, which always acks.
            ws.send_json({"type": "location_batch", "points": "not-a-list"})
            msg = ws.receive_json()
            assert msg == {"type": "location_batch_ack", "count": 0}
    finally:
        _stop(patches)


def test_chat_message_rejects_unassigned_driver(app_with_ws):
    ride = {"id": "ride_chat_1", "rider_id": "rider_x", "driver_id": "some_other_driver"}
    extra = [
        patch("backend.routes.websocket.db_supabase.get_ride", new=AsyncMock(return_value=ride)),
        patch("backend.routes.websocket.db_supabase.get_rows", new=AsyncMock(return_value=[_DRIVER_PROFILE])),
    ]
    patches = _start(*_driver_auth_patches(extra))
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "chat_message", "ride_id": ride["id"], "text": "hi"})
            msg = ws.receive_json()
            assert msg == {"type": "error", "message": "not_ride_participant"}
    finally:
        _stop(patches)


def test_chat_message_blank_text_is_ignored(app_with_ws):
    patches = _start(*_driver_auth_patches())
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "chat_message", "ride_id": "r1", "text": "   "})
            ws.send_json({"type": "location_batch", "points": "not-a-list"})
            msg = ws.receive_json()
            assert msg == {"type": "location_batch_ack", "count": 0}
    finally:
        _stop(patches)


def test_admin_snapshot_message_types(app_with_ws):
    admin_user = {"id": "admin_snap_1", "email": "a@spinr.test", "role": "admin", "token_version": 0}
    ps = [
        patch("backend.routes.websocket.firebase_auth.verify_id_token", side_effect=Exception("no firebase")),
        patch(
            "backend.routes.websocket.verify_jwt_token",
            return_value={"user_id": admin_user["id"], "role": "admin"},
        ),
        patch("backend.routes.websocket._verify_admin_payload", new=AsyncMock(return_value=admin_user)),
        patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.is_session_revoked", new=AsyncMock(return_value=False)),
        patch(
            "backend.routes.admin.monitoring.fetch_monitoring_drivers",
            new=AsyncMock(return_value=[{"id": "d1"}]),
        ),
        patch(
            "backend.routes.admin.monitoring.fetch_monitoring_rides",
            new=AsyncMock(return_value=[{"id": "r1"}]),
        ),
    ]
    patches = _start(*ps)
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/admin/{admin_user['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "get_drivers_snapshot"})
            msg = ws.receive_json()
            assert msg == {"type": "drivers_snapshot", "drivers": [{"id": "d1"}]}
            ws.send_json({"type": "get_rides_snapshot"})
            msg = ws.receive_json()
            assert msg == {"type": "rides_snapshot", "rides": [{"id": "r1"}]}
    finally:
        _stop(patches)


def test_admin_snapshot_fetch_failure_returns_error(app_with_ws):
    admin_user = {"id": "admin_snap_2", "email": "a2@spinr.test", "role": "admin", "token_version": 0}
    ps = [
        patch("backend.routes.websocket.firebase_auth.verify_id_token", side_effect=Exception("no firebase")),
        patch(
            "backend.routes.websocket.verify_jwt_token",
            return_value={"user_id": admin_user["id"], "role": "admin"},
        ),
        patch("backend.routes.websocket._verify_admin_payload", new=AsyncMock(return_value=admin_user)),
        patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.is_session_revoked", new=AsyncMock(return_value=False)),
        patch(
            "backend.routes.admin.monitoring.fetch_monitoring_drivers",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ]
    patches = _start(*ps)
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/admin/{admin_user['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            ws.send_json({"type": "get_drivers_snapshot"})
            msg = ws.receive_json()
            assert msg == {"type": "error", "message": "snapshot_failed"}
    finally:
        _stop(patches)
