"""Session-id integrity on the auth paths that mint access tokens.

Two pre-existing bugs, found by `/code-review` on PR #5654 and fixed together
because one creates the state the other mishandles:

  1. ``verify_otp`` logged a failed ``users.current_session_id`` write and
     carried on, minting a token whose session_id was never persisted.
  2. ``refresh_access_token`` then fell back to ``row.get("user_agent")`` for
     the JWT's session_id -- ``refresh_tokens`` has no session-id column, so
     that put a client-supplied User-Agent string into the token.

Why that mattered: ``should_tombstone(payload_session_id, current_session_id)``
(``utils/session_revocation.py``) returns False unless the two match, so a
UA-derived session_id never matched, logout silently failed to tombstone, and
the access token stayed honoured for its full TTL. Every user on the same
client build also shared one "session id".

Run:
    pytest backend/tests/test_auth_session_id_integrity.py -v
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

USER_ID = "user_session_integrity"
OLD_REFRESH_ROW_ID = "rtk-row-session-001"
SENTINEL_UA = "SpinrRider/9.9.9 (okhttp/4.12.0)"


def _make_request(user_agent: str = SENTINEL_UA) -> StarletteRequest:
    """A real Starlette Request so the rate-limit decorator accepts it.

    Carries ``cf-connecting-ip`` deliberately: that lets the route's client-IP
    helper resolve without being patched, so this file does not depend on
    whether ``routes/auth.py`` currently uses ``get_real_client_ip`` (PR #5654)
    or slowapi's ``get_remote_address``. The scope has no "client" key, which
    both helpers tolerate.
    """
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "query_string": b"",
            "headers": [
                (b"user-agent", user_agent.encode()),
                (b"cf-connecting-ip", b"203.0.113.9"),
            ],
        }
    )


def _refresh_row() -> dict:
    return {
        "id": OLD_REFRESH_ROW_ID,
        "user_id": USER_ID,
        "audience": "rider",
        "user_agent": SENTINEL_UA,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _user_row_without_session() -> dict:
    """A legacy/failed-write user: no ``current_session_id`` on record."""
    return {
        "id": USER_ID,
        "phone": "+15551234567",
        "role": "rider",
        "profile_complete": True,
        "token_version": 0,
        "current_session_id": None,
    }


@pytest.mark.anyio
async def test_refresh_never_uses_user_agent_as_session_id() -> None:
    """A null ``current_session_id`` must yield session_id=None, not the UA.

    The old fallback chain was ``current_session_id or row.get("user_agent")
    or ""``. With no persisted session, that minted a token carrying the
    User-Agent -- a value ``should_tombstone`` can never match, and one shared
    by every user on the same client build.
    """
    from backend.routes import auth as auth_mod

    refresh_expires = datetime.now(timezone.utc) + timedelta(days=30)
    jwt_spy = MagicMock(return_value="new-access-token")

    with (
        patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
        patch.object(auth_mod.db, "find_one", AsyncMock(return_value=_user_row_without_session())),
        patch.object(
            auth_mod,
            "issue_refresh_token",
            AsyncMock(return_value=("new-refresh-raw", "row-id", refresh_expires)),
        ),
        patch.object(auth_mod, "create_jwt_token", jwt_spy),
    ):

        class _Body:
            refresh_token = "old-refresh-raw"

        await auth_mod.refresh_access_token(request=_make_request(), response=MagicMock(), body=_Body())

    assert jwt_spy.called, "refresh_access_token did not mint an access token"
    minted_session_id = jwt_spy.call_args.kwargs.get("session_id")

    assert minted_session_id != SENTINEL_UA, (
        "the User-Agent leaked into the JWT session_id -- should_tombstone() can "
        "never match it, so logout would not tombstone this session"
    )
    assert minted_session_id is None, (
        "with no persisted current_session_id the token must carry session_id=None "
        f"(should_tombstone treats it as 'nothing to key on'), got {minted_session_id!r}"
    )


def test_every_session_id_write_failure_raises_rather_than_continuing() -> None:
    """No auth path may log a failed session_id write and carry on.

    Continuing hands back a token whose session_id is not recorded server-side,
    so single-device enforcement and logout tombstoning both silently stop
    working for that session. CLAUDE.md: never log a DB/auth error and continue
    -- return a clean HTTPException so the client retries.

    Source-level because the two call sites live in long handlers with heavy
    setup; this pins the control flow cheaply and catches a regression in either
    one. Today there are exactly two such handlers (``verify_otp`` and
    ``firebase_auth_login``); the other ``current_session_id`` writes either ride
    along in a larger create/update payload or have no try/except at all, so an
    error there already propagates.
    """
    source = (pathlib.Path(__file__).parents[1] / "routes" / "auth.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders: list[int] = []
    handlers = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        segment = ast.get_source_segment(source, node) or ""
        if "update session_id" not in segment.lower():
            continue
        handlers += 1
        if not any(isinstance(inner, ast.Raise) for inner in ast.walk(node)):
            offenders.append(node.lineno)

    assert handlers >= 2, (
        f"expected at least 2 session_id-write handlers in routes/auth.py, found {handlers} -- "
        "if a path was renamed or removed, update this guard deliberately"
    )
    assert not offenders, (
        "these except handlers log a failed session_id write and continue instead of "
        f"raising (routes/auth.py lines {offenders})"
    )
