"""login_supersede_driver_app_only_enabled: only a driver-app login signs other devices out.

Before, any login (rider app included) tombstoned the previous session, kicked the
user's sockets and took the driver offline, and the driver app signs itself out on
that kick. With the flag on, a rider-app / portal / header-less login leaves the
driver's session alone; a driver-app login still ends the old driver device.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


def _request(platform: str | None) -> MagicMock:
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.headers = {"user-agent": "pytest", **({"X-App-Platform": platform} if platform else {})}
    request.cookies = {}
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings,platform,driver_session_enabled,expected",
    [
        ({}, "rider", False, True),  # flag off: unchanged, every login supersedes
        ({FLAG: False}, "rider", False, True),
        ({FLAG: "true"}, "rider", False, True),  # only a real True turns it on
        ({FLAG: True}, "driver", False, True),
        ({FLAG: True}, "rider", False, False),
        ({FLAG: True}, None, False, False),  # old build / portal: no header
        ({FLAG: True}, "rider", True, True),  # single-session rollout keeps its rule
    ],
)
async def test_login_supersedes_other_devices(settings, platform, driver_session_enabled, expected):
    from backend.routes.auth import _login_supersedes_other_devices

    with patch("backend.routes.auth.get_app_settings", AsyncMock(return_value=settings)):
        assert await _login_supersedes_other_devices(_request(platform), driver_session_enabled) is expected


@pytest.mark.asyncio
async def test_unreadable_flag_keeps_signing_other_devices_out():
    from backend.routes.auth import _login_supersedes_other_devices

    with patch("backend.routes.auth.get_app_settings", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await _login_supersedes_other_devices(_request("rider"), False) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,supersedes", [("rider", False), ("driver", True)])
async def test_reactivation_login_only_ends_other_devices_from_the_driver_app(platform, supersedes):
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
    with (
        patch("backend.routes.auth.get_app_settings", AsyncMock(return_value={FLAG: True})),
        patch("backend.routes.auth.verify_reactivation_token", return_value="u-react-1"),
        patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user))),
        patch("backend.routes.auth.db_supabase.update_one", update_mock),
        patch("backend.routes.auth.db_supabase.rpc", AsyncMock(return_value=[{"enabled": False}])),
        patch("backend.routes.auth.revoke_session", tombstone),
        patch("backend.socket_manager.manager.kick_user", kick),
        patch("backend.routes.auth._offline_driver_for_logout_all", offline),
        patch("backend.routes.auth.redis_set", AsyncMock()),
        patch(
            "backend.routes.auth.issue_refresh_token",
            AsyncMock(return_value=("raw-refresh", "hash", datetime.now(timezone.utc) + timedelta(days=30))),
        ),
        patch("backend.routes.auth._audit_log_user", AsyncMock()),
        patch("backend.routes.auth._alert_if_new_device", AsyncMock()),
    ):
        inner = _resolve_inner(reactivate_account)
        result = await inner(_request(platform), MagicMock(), ReactivateRequest(reactivation_token="valid-token"))

    assert result.token
    # The new session is recorded either way; only the other devices' fate differs.
    assert any(c.args[0] == "users" and c.args[2].get("current_session_id") for c in update_mock.await_args_list)
    if supersedes:
        tombstone.assert_awaited_once_with("old-session")
        kick.assert_awaited_once_with("u-react-1", client_types=["driver", "rider"], reason="session_superseded")
        offline.assert_awaited_once_with("u-react-1", cause="superseded", ended_session_id="old-session")
    else:
        tombstone.assert_not_awaited()
        kick.assert_not_awaited()
        offline.assert_not_awaited()
