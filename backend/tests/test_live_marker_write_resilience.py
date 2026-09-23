"""Marker-write DB failures must not close the driver WebSocket (2026-09-22 incident).

A 42883 from update_live_driver_marker (migration 445's uuid signature) raised
DatabaseError out of _write_ws_marker into websocket_endpoint's generic
``except Exception``, which closed the driver socket as ``handler_error``
(109 server-side disconnects in one hour, drivers missed offers).

These tests pin the fix:
  * a DatabaseError from the marker write keeps the socket alive, is logged at
    ERROR with the driver id, sent to Sentry, and counted in metrics;
  * live fan-out still runs for the fresh, integrity-checked sample;
  * the catch is narrow -- a non-DB exception still takes the handler_error path;
  * the REST v2 background task logs a stable message (Sentry via logging) and
    the shared REST helper counts + re-raises without changing responses.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

_METRIC = "spinr_live_marker_write_failures_total"

# Handshake helpers mirror tests/test_websocket_coverage.py's driver fixtures.
_DRIVER_USER = {
    "id": "driver_ws_marker_1",
    "phone": "+155****7777",
    "role": "driver",
    "first_name": "Dan",
    "token_version": 0,
}
_DRIVER_PROFILE = {"id": "driver_profile_marker_1", "user_id": _DRIVER_USER["id"], "is_online": True}


@pytest.fixture
def app_with_ws():
    from backend.routes.websocket import router

    app = FastAPI()
    app.include_router(router)
    return app


def _start(*ps):
    for p in ps:
        p.start()
    return list(ps)


def _stop(started):
    for p in started:
        try:
            p.stop()
        except RuntimeError:
            pass


def _driver_auth_patches(extra):
    jwt_payload = {"user_id": _DRIVER_USER["id"], "role": "driver"}
    return [
        patch("backend.routes.websocket.firebase_auth.verify_id_token", side_effect=Exception("no firebase")),
        patch("backend.routes.websocket.verify_jwt_token", return_value=jwt_payload),
        patch("backend.routes.websocket.db_supabase.get_user_by_id", new=AsyncMock(return_value=_DRIVER_USER)),
        patch("backend.routes.websocket.db_supabase.get_rows", new=AsyncMock(return_value=[_DRIVER_PROFILE])),
        patch("backend.routes.websocket.db.find_one", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.manager.broadcast_to_admins", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.mark_present", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.is_session_revoked", new=AsyncMock(return_value=False)),
        *extra,
    ]


def _metric_value(metrics_mod, path: str) -> int:
    return int(metrics_mod.snapshot()["counters"].get(_METRIC, {}).get((("path", path),), 0))


@pytest.fixture(autouse=True)
def _clear_request_deadline():
    # Same pollution guard as test_websocket_coverage.py (leaked contextvar).
    try:
        from backend.utils.request_deadline import _request_deadline_ctx
    except ImportError:  # pragma: no cover
        _request_deadline_ctx = None
    if _request_deadline_ctx is None:
        yield
        return
    token = _request_deadline_ctx.set(None)
    try:
        yield
    finally:
        _request_deadline_ctx.reset(token)


def _ws_patches(update_side_effect, *, send_personal, broadcast_admin, sentry_capture):
    active_ride = {
        "id": "ride_marker_1",
        "rider_id": "rider_marker_1",
        "driver_id": _DRIVER_PROFILE["id"],
        "status": "in_progress",
    }
    return [
        patch("backend.routes.websocket.should_write_marker", new=AsyncMock(return_value=True)),
        patch("backend.routes.websocket.check_location_integrity", new=AsyncMock(return_value=(True, "ok"))),
        patch(
            "backend.routes.websocket.db_supabase.update_driver_location",
            new=AsyncMock(side_effect=update_side_effect),
        ),
        patch("backend.routes.websocket.resolve_active_rides_cached", new=AsyncMock(return_value=[active_ride])),
        patch("backend.routes.websocket.buffer_ride_breadcrumb", new=AsyncMock(return_value=None)),
        patch("backend.routes.websocket.get_app_settings", new=AsyncMock(return_value={})),
        patch("backend.routes.websocket.manager.send_personal_message", new=send_personal),
        patch("backend.routes.websocket.manager.broadcast_driver_location_to_admins", new=broadcast_admin),
        patch("sentry_sdk.capture_exception", new=sentry_capture),
    ]


def _send_ping(ws):
    ws.send_json(
        {
            "type": "driver_location",
            "lat": 50.4452,
            "lng": -104.6189,
            "heading": 90,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def test_ws_marker_db_error_keeps_socket_alive_and_is_observable(app_with_ws):
    from backend.routes import websocket as ws_mod
    from backend.utils import metrics

    db_error = ws_mod.DatabaseError(details={"original": "operator does not exist: text = uuid"})
    send_personal = AsyncMock(return_value=None)
    broadcast_admin = AsyncMock(return_value=None)
    sentry_capture = MagicMock()
    disconnect_handler = AsyncMock(return_value=None)
    before = _metric_value(metrics, "ws_single")

    extra = _ws_patches(
        db_error, send_personal=send_personal, broadcast_admin=broadcast_admin, sentry_capture=sentry_capture
    )
    extra.append(patch("backend.routes.websocket._handle_driver_ws_disconnect", new=disconnect_handler))
    # Patch the module logger (not a loguru sink): other suites reconfigure
    # loguru handlers, which made a sink-based assertion order-dependent.
    ws_logger = MagicMock()
    extra.append(patch("backend.routes.websocket.logger", new=ws_logger))
    patches = _start(*_driver_auth_patches(extra))
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            # Two failing pings: the first used to close the socket.
            _send_ping(ws)
            _send_ping(ws)
            # Socket is still serving requests after both failures.
            ws.send_json({"type": "location_batch", "points": []})
            assert ws.receive_json() == {"type": "location_batch_ack", "count": 0}
    finally:
        _stop(patches)

    # Not closed as handler_error: no server-side disconnect handling ran
    # while the socket was open (only the client's own close afterwards).
    for call in disconnect_handler.await_args_list:
        assert call.kwargs.get("conn_state", {}).get("server_close_reason") != "handler_error"
    # Observable: ERROR log with driver id, Sentry capture, metric.
    error_logs = [str(c.args[0]) for c in ws_logger.error.call_args_list]
    marker_errors = [m for m in error_logs if "live marker write failed" in m]
    assert len(marker_errors) == 2
    assert all(f"driver_id={_DRIVER_PROFILE['id']}" in m for m in marker_errors)
    # The outer handler's handler_error branch (logger.exception) never ran.
    assert not any("WS branch=Exception" in str(c.args[0]) for c in ws_logger.exception.call_args_list)
    assert sentry_capture.call_count == 2
    assert sentry_capture.call_args.args[0] is db_error
    assert _metric_value(metrics, "ws_single") - before == 2
    # Live delivery continues for the fresh sample.
    assert broadcast_admin.await_count == 2
    assert any(c.args[0].get("type") == "driver_location_update" for c in send_personal.await_args_list)


def test_ws_non_database_error_still_closes_socket_as_handler_error(app_with_ws):
    """The catch is narrow: an unexpected bug must still surface as handler_error."""
    disconnect_handler = AsyncMock(return_value=None)
    extra = _ws_patches(
        RuntimeError("unexpected bug"),
        send_personal=AsyncMock(return_value=None),
        broadcast_admin=AsyncMock(return_value=None),
        sentry_capture=MagicMock(),
    )
    extra.append(patch("backend.routes.websocket._handle_driver_ws_disconnect", new=disconnect_handler))
    patches = _start(*_driver_auth_patches(extra))
    try:
        client = TestClient(app_with_ws)
        with client.websocket_connect(f"/ws/driver/{_DRIVER_USER['id']}") as ws:
            ws.send_json({"type": "auth", "token": "tok"})
            ws.receive_json()
            _send_ping(ws)
            with pytest.raises(Exception):
                ws.receive_json()
    finally:
        _stop(patches)

    disconnect_handler.assert_awaited()
    assert disconnect_handler.await_args.kwargs["conn_state"]["server_close_reason"] == "handler_error"


def test_write_ws_marker_returns_true_on_db_error_and_false_on_rejection():
    from backend.routes import websocket as ws_mod

    now = datetime.now(timezone.utc)
    with (
        patch.object(ws_mod, "should_write_marker", new=AsyncMock(return_value=True)),
        patch.object(ws_mod, "_report_ws_marker_write_failure") as report,
    ):
        with patch.object(
            ws_mod.db_supabase, "update_driver_location", new=AsyncMock(side_effect=ws_mod.DatabaseError())
        ):
            assert asyncio.run(ws_mod._write_ws_marker("d-1", 50.4, -104.6, None, now, "ws_batch")) is True
        report.assert_called_once()
        assert report.call_args.args[:2] == ("d-1", "ws_batch")
        # An ordering rejection from Postgres (False) is unchanged behaviour.
        with patch.object(ws_mod.db_supabase, "update_driver_location", new=AsyncMock(return_value=False)):
            assert asyncio.run(ws_mod._write_ws_marker("d-1", 50.4, -104.6, None, now, "ws_batch")) is False


# ── REST: /location-live and /location-batch (shared helpers) ───────────────


def test_rest_write_marker_if_due_counts_and_reraises_db_error():
    from backend.routes.drivers import location
    from backend.utils import metrics

    before = _metric_value(metrics, "rest_v2_trip")
    with (
        patch.object(location, "should_write_marker", new=AsyncMock(return_value=True)),
        patch.object(
            location.db_supabase,
            "update_driver_location",
            new=AsyncMock(side_effect=location.DatabaseError()),
        ),
    ):
        with pytest.raises(location.DatabaseError):
            asyncio.run(
                location._write_marker_if_due(
                    {"id": "d-1"},
                    {"lat": 50.4, "lng": -104.6, "location_captured_at": datetime.now(timezone.utc)},
                    "d-1",
                    "rest_v2_trip",
                )
            )
    assert _metric_value(metrics, "rest_v2_trip") - before == 1


def test_rest_v2_marker_failure_logs_stable_error_with_exc_info(caplog):
    from backend.routes.drivers import location

    with (
        patch.object(location, "_write_marker_if_due", new=AsyncMock(side_effect=location.DatabaseError())),
        patch(
            "backend.utils.location_integrity.check_location_integrity",
            new=AsyncMock(return_value=(True, "ok")),
        ),
        patch("backend.settings_loader.get_app_settings", new=AsyncMock(return_value={})),
        caplog.at_level(logging.ERROR),
    ):
        # Must not raise: the HTTP response was already returned.
        asyncio.run(
            location._apply_v2_live_marker_update(
                "d-1", "ride-1", 50.45, -104.6, 90, 12, 8, False, True, datetime.now(timezone.utc)
            )
        )

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    matching = [r for r in errors if r.getMessage().startswith("live marker write failed driver_id=d-1")]
    assert matching, [r.getMessage() for r in errors]
    # exc_info attached => Sentry's LoggingIntegration captures the exception event.
    assert matching[0].exc_info is not None
    assert isinstance(matching[0].exc_info[1], location.DatabaseError)
