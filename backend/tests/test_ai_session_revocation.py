"""F02 — session revocation on protected AI requests.

Source finding: docs/security/2026-09-08-ai-security-assessment.md, F02.
Ordinary /auth/logout deliberately leaves the access token valid until its
exp (15 min) and only writes a tombstone; get_current_user does not consult
that tombstone. So a signed-out session could keep driving AI turns — sending
the customer's data to a third-party provider and spending their quota — for
the rest of the token's life.

The fix is a separate dependency rather than a check inside get_current_user,
because utils/session_revocation documents the deliberate decision to keep a
Redis round-trip off the hot path (auth-refresh <200 ms, dispatch <2 s P95).
These tests pin both halves of that decision: the AI routes DO consult the
tombstone, and the fail-open posture on ambiguous input is preserved.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

_USER = {"id": "rider-1", "is_driver": False}


async def _call(*, revoked: bool, session_id="sess-1", user=None):
    from backend.dependencies import get_current_user_active_session

    with patch("backend.dependencies.is_session_revoked", AsyncMock(return_value=revoked)) as m:
        result = await get_current_user_active_session(current_user=dict(user or _USER), token_session_id=session_id)
    return result, m


class TestActiveSessionDependency:
    @pytest.mark.asyncio
    async def test_revoked_session_is_rejected(self):
        with pytest.raises(HTTPException) as exc:
            await _call(revoked=True)
        assert exc.value.status_code == 401
        assert exc.value.detail == "ERR_SESSION_REVOKED"

    @pytest.mark.asyncio
    async def test_live_session_passes_the_user_through_unchanged(self):
        result, _ = await _call(revoked=False)
        assert result["id"] == "rider-1"

    @pytest.mark.asyncio
    async def test_the_tombstone_is_keyed_on_the_token_session_id(self):
        _, m = await _call(revoked=False, session_id="sess-abc")
        m.assert_awaited_once_with("sess-abc")

    @pytest.mark.asyncio
    async def test_absent_session_id_is_allowed(self):
        """A Firebase-authenticated session carries no session_id claim.
        Rejecting it here would lock out every Firebase rider; logout-all /
        sessions_invalid_before is that path's revocation mechanism. The
        fail-open posture is inherited from is_session_revoked, which returns
        False for None — asserted here so the dependency cannot start
        second-guessing it."""
        from backend.dependencies import get_current_user_active_session

        # No patch: the real is_session_revoked short-circuits on None.
        result = await get_current_user_active_session(current_user=dict(_USER), token_session_id=None)
        assert result["id"] == "rider-1"


def _dependency_of(fn, param="current_user"):
    """Resolve a route's declared dependency for ``param``.

    Unwraps decorators explicitly rather than trusting inspect.signature to
    follow ``__wrapped__``: /ai/chat and /support/chat are wrapped by slowapi's
    rate limiter, and a decorator that skipped functools.wraps would make a
    signature-only check silently inspect the WRAPPER's parameters and pass
    for the wrong reason.
    """
    import inspect

    for _ in range(10):
        params = inspect.signature(fn).parameters
        if param in params:
            return params[param].default
        fn = getattr(fn, "__wrapped__", None)
        if fn is None:
            break
    raise AssertionError(f"no {param!r} parameter found to inspect")


class TestAiRoutesUseTheGate:
    """The dependency only helps if the routes actually declare it. Pinned by
    inspecting the resolved signature rather than by calling through FastAPI,
    so a route silently reverting to get_current_user fails here."""

    @pytest.mark.parametrize(
        "route_name",
        ["ai_chat", "list_ai_conversations", "get_ai_conversation_messages", "delete_ai_conversation"],
    )
    def test_customer_ai_routes_require_an_active_session(self, route_name):
        from backend.dependencies import get_current_user_active_session
        from backend.routes import ai as ai_routes

        dep = _dependency_of(getattr(ai_routes, route_name))
        assert dep.dependency is get_current_user_active_session, f"{route_name} does not consult the session tombstone"

    def test_legacy_support_chat_requires_an_active_session(self):
        from backend.dependencies import get_current_user_active_session
        from backend.routes.support import support_chat

        assert _dependency_of(support_chat).dependency is get_current_user_active_session

    def test_ai_config_deliberately_does_not(self):
        """Documented exception: /ai/config returns global feature flags and
        disclaimer copy — no customer data, no provider egress — and the apps
        poll it on launch. If someone later adds user data to this response,
        this test should be changed to require the gate, not deleted."""
        from backend.dependencies import get_current_user
        from backend.routes.ai import ai_config

        assert _dependency_of(ai_config).dependency is get_current_user

    def test_mcp_middleware_checks_the_tombstone(self):
        """/mcp calls get_current_user directly rather than via Depends, so it
        cannot pick the dependency up — the check is spelled out inline and
        must stay there."""
        import inspect

        from backend.ai import mcp_server

        src = inspect.getsource(mcp_server.MCPAuthMiddleware)
        assert "is_session_revoked" in src
        assert "ERR_SESSION_REVOKED" in src
