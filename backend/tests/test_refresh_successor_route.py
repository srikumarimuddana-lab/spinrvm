"""T11-5b2 (X8, default off): /auth/refresh successor-commitment integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

from backend.utils.error_handling import TokenExpiredException

PARENT = "parent-raw-token"
PROPOSED = "Q" * 64
USER = {"id": "u1", "phone": "+13065550100", "token_version": 2, "current_session_id": "s1"}

pytestmark = pytest.mark.asyncio


def _request(cookie: str | None = None) -> StarletteRequest:
    headers = [(b"user-agent", b"DriverApp/1.0")]
    if cookie is not None:
        headers.append((b"cookie", f"refresh_token={cookie}".encode()))
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "query_string": b"",
            "headers": headers,
        }
    )


def _body(proposed=PROPOSED, refresh_token=PARENT):
    from backend.routes.auth import RefreshRequest

    return RefreshRequest(refresh_token=refresh_token, proposed_refresh_token=proposed)


def _patches(auth, *, flag=True, verdict=("no_match", None), settings_exc=None):
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    settings_mock = AsyncMock(side_effect=settings_exc, return_value={"refresh_successor_commitment_enabled": flag})
    return {
        "settings": patch.object(auth, "get_app_settings", settings_mock),
        "classify": patch.object(auth, "classify_committed_replay", AsyncMock(return_value=verdict)),
        "lookup": patch.object(
            auth,
            "lookup_refresh_token",
            AsyncMock(return_value={"id": "row-1", "user_id": "u1", "audience": "driver", "token_version": 2}),
        ),
        "find": patch.object(auth.db, "find_one", AsyncMock(return_value=dict(USER))),
        "issue": patch.object(auth, "issue_refresh_token", AsyncMock(return_value=("rotated", "row-2", expires))),
        "jwt": patch.object(auth, "create_jwt_token", return_value="access"),
        "metric": patch.object(auth, "_metric_inc", MagicMock()),
        # login_supersede_driver_app_only_enabled has its own reader; stub it so
        # "settings" above counts only the X8 flag these tests are about.
        "sessions": patch.object(auth, "_driver_app_only_sessions_enabled", AsyncMock(return_value=False)),
    }


async def _call(auth, patches, body, cookie=None):
    started = {name: p.start() for name, p in patches.items()}
    try:
        result = await auth.refresh_access_token(request=_request(cookie), response=MagicMock(), body=body)
    finally:
        for p in patches.values():
            p.stop()
    return result, started


async def test_flag_off_ignores_proposal_and_keeps_todays_call():
    from backend.routes import auth

    result, m = await _call(auth, _patches(auth, flag=False), _body())
    m["classify"].assert_not_awaited()
    assert "raw" not in m["issue"].await_args.kwargs
    assert result.refresh_token == "rotated"


async def test_unreadable_flag_counts_as_off():
    from backend.routes import auth

    _result, m = await _call(auth, _patches(auth, settings_exc=RuntimeError("down")), _body())
    m["classify"].assert_not_awaited()
    assert "raw" not in m["issue"].await_args.kwargs


@pytest.mark.parametrize("proposed", [None, "short", "Q" * 63 + "="])
async def test_malformed_proposal_is_ignored_without_reading_flag(proposed):
    from backend.routes import auth

    _result, m = await _call(auth, _patches(auth), _body(proposed))
    m["settings"].assert_not_awaited()
    m["classify"].assert_not_awaited()


async def test_no_match_with_cookie_parent_never_uses_the_proposal():
    """A "no_match" verdict is the normal case for every first-time refresh.
    When the parent comes from the HttpOnly cookie, the client's proposed bytes
    must NOT reach issue_refresh_token's `raw` kwarg: a script that can shape
    the refresh request body (but not read the cookie) would otherwise plant a
    known plaintext as the next refresh token's secret."""
    from backend.routes import auth

    _result, m = await _call(auth, _patches(auth), _body(refresh_token=""), cookie=PARENT)
    m["classify"].assert_awaited_once_with(PARENT, PROPOSED)
    m["lookup"].assert_awaited_once_with(PARENT)
    assert "raw" not in m["issue"].await_args.kwargs


async def test_no_match_with_body_parent_commits_the_proposal():
    """A mobile client sends the parent in the body with its proposal. It
    already holds a live credential, so the proposal becomes the successor's
    secret; a lost response can then be recovered by replaying both."""
    from backend.routes import auth

    _result, m = await _call(auth, _patches(auth), _body())
    m["classify"].assert_awaited_once_with(PARENT, PROPOSED)
    m["lookup"].assert_awaited_once_with(PARENT)
    assert m["issue"].await_args.kwargs["raw"] == PROPOSED


async def test_body_parent_is_preferred_over_a_stale_cookie():
    """Native HTTP stacks keep the cookie from the last foreground response,
    which is stale after the driver app's background task rotated the token.
    With a valid proposal, the body token is the one the client holds."""
    from backend.routes import auth

    _result, m = await _call(auth, _patches(auth), _body(), cookie="stale-cookie-token")
    m["classify"].assert_awaited_once_with(PARENT, PROPOSED)
    m["lookup"].assert_awaited_once_with(PARENT)
    assert m["issue"].await_args.kwargs["raw"] == PROPOSED


async def test_flag_off_keeps_cookie_precedence_and_server_random_successor():
    from backend.routes import auth

    _result, m = await _call(auth, _patches(auth, flag=False), _body(), cookie="cookie-token")
    m["lookup"].assert_awaited_once_with("cookie-token")
    assert "raw" not in m["issue"].await_args.kwargs


async def test_recover_returns_proposed_without_new_row_or_lookup():
    from backend.routes import auth

    expires = datetime.now(timezone.utc) + timedelta(days=12)
    successor = {"id": "succ", "user_id": "u1", "audience": "driver", "expires_at": expires.isoformat()}
    result, m = await _call(auth, _patches(auth, verdict=("recover", successor)), _body())
    assert result.refresh_token == PROPOSED
    assert result.refresh_expires_at == expires
    m["lookup"].assert_not_awaited()
    m["issue"].assert_not_awaited()
    m["metric"].assert_called_once_with("spinr_auth_refresh_recovered_total", {"audience": "driver"})


async def test_recover_enforces_account_active():
    from backend.routes import auth

    successor = {"id": "succ", "user_id": "u1", "audience": "driver", "expires_at": "2099-01-01T00:00:00+00:00"}
    patches = _patches(auth, verdict=("recover", successor))
    patches["active"] = patch.object(auth, "_enforce_account_active", MagicMock(side_effect=PermissionError("gone")))
    with pytest.raises(PermissionError):
        await _call(auth, patches, _body())


async def test_dead_is_401_without_lookup_or_cascade():
    from backend.routes import auth

    patches = _patches(auth, verdict=("dead", None))
    with pytest.raises(TokenExpiredException) as caught:
        await _call(auth, patches, _body())
    assert caught.value.status_code == 401


async def test_admin_twin_is_untouched():
    from pathlib import Path

    admin = (Path(__file__).resolve().parents[1] / "routes" / "admin" / "auth.py").read_text(encoding="utf-8")
    assert "proposed_refresh_token" not in admin
    assert "classify_committed_replay" not in admin
