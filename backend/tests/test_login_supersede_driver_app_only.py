"""login_supersede_driver_app_only_enabled: per-login sessions, driver-app-only supersede.

Before (and with the flag off): every login overwrote the shared
users.current_session_id, tombstoned the previous session, kicked the user's
sockets and took the driver offline, and every refreshed access token re-read
that shared column. So signing into the rider app signed the driver app out,
and (had that been skipped) a rider logout would still have tombstoned the
driver's session id through the shared column.

With the flag on: only a driver-app login owns current_session_id and signs
other devices out; every login's refresh chain keeps its own session id, and a
device's logout tombstones its own session.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

FLAG = "login_supersede_driver_app_only_enabled"


def _resolve_inner(fn):
    """Unwrap slowapi's @limiter.limit (same helper as test_auth_remaining_endpoints.py)."""
    while True:
        nxt = getattr(fn, "__wrapped__", None)
        if nxt is None:
            for cell in getattr(fn, "__closure__", None) or ():
                val = cell.cell_contents
                if callable(val) and getattr(val, "__code__", None) is not None and val is not fn:
                    return val
            return fn
        fn = nxt


def _request(platform: str | None = None, cookies: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.headers = {"user-agent": "pytest", **({"X-App-Platform": platform} if platform else {})}
    request.cookies = cookies or {}
    return request


def _settings(enabled: bool):
    return patch("backend.routes.auth.get_app_settings", AsyncMock(return_value={FLAG: enabled}))


# ── login policy ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings,platform,driver_session_enabled,expected",
    [
        ({}, "rider", False, (False, True)),  # flag off: unchanged
        ({FLAG: "true"}, "rider", False, (False, True)),  # only a real True
        ({FLAG: True}, "driver", False, (True, True)),
        ({FLAG: True}, "rider", False, (True, False)),
        ({FLAG: True}, None, False, (True, False)),  # portal / old build
        ({FLAG: True}, "rider", True, (True, True)),  # single-session rollout keeps its rule
        # Mutually exclusive: with single-session on, this flag is ignored.
        ({FLAG: True, "driver_single_session_enabled": True}, "rider", False, (False, True)),
    ],
)
async def test_login_session_policy(settings, platform, driver_session_enabled, expected):
    from backend.routes.auth import _login_session_policy

    with patch("backend.routes.auth.get_app_settings", AsyncMock(return_value=settings)):
        assert await _login_session_policy(_request(platform), driver_session_enabled) == expected


@pytest.mark.asyncio
async def test_unreadable_flag_is_off():
    from backend.routes.auth import _login_session_policy

    with patch("backend.routes.auth.get_app_settings", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await _login_session_policy(_request("rider"), False) == (False, True)


# ── refresh chains ───────────────────────────────────────────────────────────


def test_chain_session_id_for_rotation():
    from backend.routes.auth import _chain_session_id_for_rotation

    user = {"current_session_id": "driver-session"}
    # A chain that already has its id keeps it, whatever the shared column says.
    assert _chain_session_id_for_rotation({"session_id": "own"}, user, _request("rider")) == "own"
    # Legacy chain on a driver (or unidentified) device keeps the id its tokens
    # already carry, so the availability controller binding survives.
    assert _chain_session_id_for_rotation({}, user, _request("driver")) == "driver-session"
    assert _chain_session_id_for_rotation({}, user, _request(None)) == "driver-session"
    # Legacy chain on a rider device gets its own id: its logout must not
    # tombstone the driver's session.
    rider_sid = _chain_session_id_for_rotation({}, user, _request("rider"))
    assert rider_sid != "driver-session" and uuid.UUID(rider_sid)
    assert uuid.UUID(_chain_session_id_for_rotation({}, {"current_session_id": None}, _request("driver")))


def _refresh_request(platform: str | None) -> StarletteRequest:
    headers = [(b"user-agent", b"pytest"), (b"cf-connecting-ip", b"203.0.113.9")]
    if platform:
        headers.append((b"x-app-platform", platform.encode()))
    return StarletteRequest(
        {"type": "http", "method": "POST", "path": "/auth/refresh", "query_string": b"", "headers": headers}
    )


@pytest.mark.anyio
@pytest.mark.parametrize("enabled,expected_session", [(True, "chain-session"), (False, "shared-session")])
async def test_refresh_mints_the_chains_own_session_id(enabled, expected_session):
    from backend.routes import auth as auth_mod

    row = {"id": "rtk-1", "user_id": "u1", "audience": "rider", "session_id": "chain-session"}
    user = {
        "id": "u1",
        "phone": "+15551234567",
        "role": "rider",
        "token_version": 0,
        "current_session_id": "shared-session",
    }
    issue = AsyncMock(return_value=("new-refresh", "rtk-2", datetime.now(timezone.utc) + timedelta(days=30)))
    jwt_spy = MagicMock(return_value="new-access")
    with (
        _settings(enabled),
        patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=row)),
        patch.object(auth_mod.db, "find_one", AsyncMock(return_value=user)),
        patch.object(auth_mod, "issue_refresh_token", issue),
        patch.object(auth_mod, "create_jwt_token", jwt_spy),
    ):

        class _Body:
            refresh_token = "old-refresh"
            proposed_refresh_token = None

        await auth_mod.refresh_access_token(request=_refresh_request("rider"), response=MagicMock(), body=_Body())

    assert jwt_spy.call_args.kwargs["session_id"] == expected_session
    assert issue.await_args.kwargs.get("session_id") == ("chain-session" if enabled else None)


# ── logins ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,platform,owns_current",
    [(True, "rider", False), (True, "driver", True), (False, "rider", True)],
)
async def test_reactivation_login_session_ownership(enabled, platform, owns_current):
    from backend.routes.auth import ReactivateRequest, reactivate_account

    user = {
        "id": "u-react-1",
        "phone": "+13065551111",
        "role": "driver",
        "is_driver": True,
        "current_session_id": "old-session",
        "status": "pending_deletion",
        "token_version": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    tombstone = AsyncMock()
    kick = AsyncMock()
    offline = AsyncMock()
    update_mock = AsyncMock(return_value=True)
    issue = AsyncMock(return_value=("raw-refresh", "hash", datetime.now(timezone.utc) + timedelta(days=30)))
    with (
        _settings(enabled),
        patch("backend.routes.auth.verify_reactivation_token", return_value="u-react-1"),
        patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user))),
        patch("backend.routes.auth.db_supabase.update_one", update_mock),
        patch("backend.routes.auth.db_supabase.rpc", AsyncMock(return_value=[{"enabled": False}])),
        patch("backend.routes.auth.revoke_session", tombstone),
        patch("backend.socket_manager.manager.kick_user", kick),
        patch("backend.routes.auth._offline_driver_for_logout_all", offline),
        patch("backend.routes.auth.redis_set", AsyncMock()),
        patch("backend.routes.auth.issue_refresh_token", issue),
        patch("backend.routes.auth._audit_log_user", AsyncMock()),
        patch("backend.routes.auth._alert_if_new_device", AsyncMock()),
    ):
        inner = _resolve_inner(reactivate_account)
        result = await inner(_request(platform), MagicMock(), ReactivateRequest(reactivation_token="valid-token"))

    assert result.token
    wrote_current = any(c.args[0] == "users" and "current_session_id" in c.args[2] for c in update_mock.await_args_list)
    assert wrote_current is owns_current
    new_session = issue.await_args.kwargs.get("session_id")
    assert (new_session is not None) is enabled
    if owns_current:
        tombstone.assert_awaited_once_with("old-session")
        kick.assert_awaited_once_with("u-react-1", client_types=["driver", "rider"], reason="session_superseded")
        offline.assert_awaited_once_with("u-react-1", cause="superseded", ended_session_id="old-session")
    else:
        tombstone.assert_not_awaited()
        kick.assert_not_awaited()
        offline.assert_not_awaited()


# ── logout ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,chain_session,tombstoned",
    [
        (True, "rider-session", True),  # proves ownership of its own chain
        (True, "someone-else", False),  # chain does not match the token
        (True, None, False),  # legacy chain: current-session rule only
        (False, "rider-session", False),  # flag off: current-session rule only
    ],
)
async def test_logout_tombstones_its_own_session(enabled, chain_session, tombstoned):
    from backend.routes.auth import logout

    tombstone = AsyncMock()
    with (
        _settings(enabled),
        patch("backend.routes.auth.revoke_refresh_token", AsyncMock(return_value=True)),
        patch("backend.routes.auth.refresh_token_session_id", AsyncMock(return_value=chain_session)),
        patch("backend.routes.auth.revoke_session", tombstone),
        patch("backend.routes.auth.redis_delete", AsyncMock()),
        patch("backend.routes.auth._audit_log_user", AsyncMock()),
        patch("backend.routes.auth.clear_csrf_cookie"),
    ):
        inner = _resolve_inner(logout)
        await inner(
            _request("rider", cookies={"refresh_token": "raw-refresh"}),
            MagicMock(),
            body=None,
            # The driver's login owns current_session_id; this is the rider app.
            current_user={"id": "u1", "current_session_id": "driver-session"},
            token_session_id="rider-session",
        )

    if tombstoned:
        tombstone.assert_awaited_once_with("rider-session")
    else:
        tombstone.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_of_the_owning_session_still_tombstones_with_flag_on():
    from backend.routes.auth import logout

    tombstone = AsyncMock()
    with (
        _settings(True),
        patch("backend.routes.auth.revoke_refresh_token", AsyncMock(return_value=True)),
        patch("backend.routes.auth.refresh_token_session_id", AsyncMock(return_value=None)),
        patch("backend.routes.auth.revoke_session", tombstone),
        patch("backend.routes.auth.redis_delete", AsyncMock()),
        patch("backend.routes.auth._audit_log_user", AsyncMock()),
        patch("backend.routes.auth.clear_csrf_cookie"),
    ):
        inner = _resolve_inner(logout)
        await inner(
            _request("driver", cookies={"refresh_token": "raw-refresh"}),
            MagicMock(),
            body=None,
            current_user={"id": "u1", "current_session_id": "driver-session"},
            token_session_id="driver-session",
        )

    tombstone.assert_awaited_once_with("driver-session")


# ── storage ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", ["chain-session", None])
async def test_issue_refresh_token_stores_session_id_only_when_given(session_id):
    from backend.utils import refresh_tokens

    insert = AsyncMock(return_value={"id": "rtk-new"})
    with patch.object(refresh_tokens.db, "insert_one", insert):
        await refresh_tokens.issue_refresh_token("u1", audience="rider", session_id=session_id)

    row = insert.await_args.args[1]
    if session_id:
        assert row["session_id"] == session_id
    else:
        assert "session_id" not in row


@pytest.mark.asyncio
async def test_refresh_token_session_id_lookup():
    from backend.utils import refresh_tokens

    with patch.object(refresh_tokens.db, "find_one", AsyncMock(return_value={"session_id": "s1", "user_id": "u1"})):
        assert await refresh_tokens.refresh_token_session_id("raw") == "s1"
        assert await refresh_tokens.refresh_token_session_id("raw", user_id="u1") == "s1"
        # Someone else's refresh token never vouches for this user's session.
        assert await refresh_tokens.refresh_token_session_id("raw", user_id="u2") is None
    with patch.object(refresh_tokens.db, "find_one", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await refresh_tokens.refresh_token_session_id("raw") is None
    assert await refresh_tokens.refresh_token_session_id("") is None
