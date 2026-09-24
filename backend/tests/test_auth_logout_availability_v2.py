"""T11-B2: auth session-end wiring under availability v2 (design A1/X5, C9)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio


def _inner(fn):
    while True:
        nxt = getattr(fn, "__wrapped__", None)
        if nxt is None:
            for cell in getattr(fn, "__closure__", None) or ():
                val = cell.cell_contents
                if callable(val) and getattr(val, "__code__", None) is not None and val is not fn:
                    return val
            return fn
        fn = nxt


@pytest.mark.parametrize("outcome", ["stopped", "skipped", "failed"])
async def test_v2_outcomes_never_run_raw_offline_or_declines(outcome):
    from backend.routes import auth

    stop = AsyncMock(return_value=outcome)
    with (
        patch.object(auth.driver_session_end_service, "stop_requests_for_session_end", stop),
        patch.object(auth.db, "get_rows", AsyncMock()) as get_rows,
        patch.object(auth.db, "update_one", AsyncMock()) as update_one,
        patch.object(auth, "record_period_transition", AsyncMock()) as period,
        patch.object(auth, "clear_presence", AsyncMock()) as clear,
    ):
        await auth._offline_driver_for_logout_all("u1")

    stop.assert_awaited_once_with("u1", cause="logout-all", ended_session_id=None)
    get_rows.assert_not_awaited()
    update_one.assert_not_awaited()
    period.assert_not_awaited()
    clear.assert_not_awaited()


async def test_service_exception_never_falls_back_to_raw_writes():
    from backend.routes import auth

    with (
        patch.object(
            auth.driver_session_end_service,
            "stop_requests_for_session_end",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch.object(auth.db, "update_one", AsyncMock()) as update_one,
        patch.object(auth, "record_period_transition", AsyncMock()) as period,
    ):
        await auth._offline_driver_for_logout_all("u1")

    update_one.assert_not_awaited()
    period.assert_not_awaited()


async def test_legacy_outcome_keeps_flag_off_offline_and_period_zero():
    from backend.routes import auth

    get_rows = AsyncMock(side_effect=[[{"id": "d1", "is_online": True}], [], [], []])
    with (
        patch.object(
            auth.driver_session_end_service,
            "stop_requests_for_session_end",
            AsyncMock(return_value="legacy"),
        ),
        patch.object(auth.db, "get_rows", get_rows),
        patch.object(auth.db, "update_one", AsyncMock(return_value={"id": "d1"})) as update_one,
        patch.object(auth, "record_period_transition", AsyncMock()) as period,
        patch.object(auth, "clear_presence", AsyncMock()) as clear,
    ):
        await auth._offline_driver_for_logout_all("u1")

    assert update_one.await_args.args[0] == "drivers"
    assert update_one.await_args.args[2]["is_online"] is False
    period.assert_awaited_once_with("d1", 0)
    clear.assert_awaited_once_with("d1")


async def test_superseded_cleanup_passes_previous_session():
    from backend.routes import auth

    offline = AsyncMock()
    with (
        patch.object(auth, "revoke_session", AsyncMock()),
        patch("backend.socket_manager.manager.kick_user", AsyncMock()),
        patch.object(auth, "_offline_driver_for_logout_all", offline),
    ):
        await auth._cleanup_superseded_session("u1", "old-sess", "new-sess")

    offline.assert_awaited_once_with("u1", cause="superseded", ended_session_id="old-sess")


def _logout_request():
    request = MagicMock()
    request.cookies = {"refresh_token": "raw-refresh"}
    return request


async def test_logout_stops_driver_before_revoking_refresh_token():
    from backend.routes import auth

    order: list[str] = []
    stop = AsyncMock(side_effect=lambda *a, **k: order.append("stop") or "stopped")
    revoke = AsyncMock(side_effect=lambda *a, **k: order.append("revoke"))
    user = {"id": "u1", "is_driver": True, "current_session_id": "sess-1"}
    with (
        patch.object(auth.driver_session_end_service, "stop_requests_for_session_end", stop),
        patch.object(auth, "revoke_refresh_token", revoke),
        patch.object(auth, "redis_delete", AsyncMock()),
        patch.object(auth, "_clear_push_token_on_logout", AsyncMock()),
        patch.object(auth, "revoke_session", AsyncMock()),
        patch.object(auth, "_spawn", MagicMock()),
        patch.object(auth, "_audit_log_user", MagicMock()),
    ):
        result = await _inner(auth.logout)(_logout_request(), MagicMock(), None, user, "sess-1")

    assert result == {"success": True}
    stop.assert_awaited_once_with("u1", cause="logout", ended_session_id="sess-1")
    assert order == ["stop", "revoke"]


async def test_logout_never_fails_when_stop_raises():
    from backend.routes import auth

    user = {"id": "u1", "role": "driver", "current_session_id": "sess-1"}
    revoke = AsyncMock()
    with (
        patch.object(
            auth.driver_session_end_service,
            "stop_requests_for_session_end",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch.object(auth, "revoke_refresh_token", revoke),
        patch.object(auth, "redis_delete", AsyncMock()),
        patch.object(auth, "_clear_push_token_on_logout", AsyncMock()),
        patch.object(auth, "revoke_session", AsyncMock()),
        patch.object(auth, "_spawn", MagicMock()),
        patch.object(auth, "_audit_log_user", MagicMock()),
    ):
        result = await _inner(auth.logout)(_logout_request(), MagicMock(), None, user, "sess-1")

    assert result == {"success": True}
    revoke.assert_awaited_once()


@pytest.mark.parametrize(
    ("user", "session"),
    [({"id": "u1", "role": "rider"}, "sess-1"), ({"id": "u1", "is_driver": True}, None)],
)
async def test_logout_skips_stop_for_riders_or_sessionless_tokens(user, session):
    from backend.routes import auth

    stop = AsyncMock()
    with (
        patch.object(auth.driver_session_end_service, "stop_requests_for_session_end", stop),
        patch.object(auth, "revoke_refresh_token", AsyncMock()),
        patch.object(auth, "redis_delete", AsyncMock()),
        patch.object(auth, "_clear_push_token_on_logout", AsyncMock()),
        patch.object(auth, "revoke_session", AsyncMock()),
        patch.object(auth, "_spawn", MagicMock()),
        patch.object(auth, "_audit_log_user", MagicMock()),
    ):
        await _inner(auth.logout)(_logout_request(), MagicMock(), None, user, session)

    stop.assert_not_awaited()


def test_auth_keeps_exactly_five_client_ip_calls():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "routes" / "auth.py").read_text(encoding="utf-8")
    assert source.count("get_real_client_ip(request)") == 5
