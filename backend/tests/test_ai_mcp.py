"""/mcp surface: auth middleware, kill switch, registry exposure rules.

The ``mcp`` SDK is not in the lockfile yet, so build_mcp_asgi_app() returns
None in CI (pinned below) and the SDK-dependent handlers are exercised only
when the SDK is present (skipif). The middleware and the exposure rules are
SDK-independent and fully tested:

- ai_mcp_enabled=False → 503 before any auth work
- missing/invalid bearer → 401 via the regular get_current_user path
- admin tokens → 403 (trusted-claims tokens don't belong on this surface)
- valid token → inner app runs with current_ai_user set, reset afterwards
- booking tools (mcp_exposed=False) are never exposed
"""

import importlib.util
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import backend.ai.mcp_server as mcp_server
from backend.ai.context import current_ai_user
from backend.ai.mcp_server import MCPAuthMiddleware, build_mcp_asgi_app
from backend.ai.tools import TOOL_REGISTRY, ensure_registry_loaded

RIDER = {"id": "rider-1", "is_driver": False, "role": "user"}

_HAS_MCP_SDK = importlib.util.find_spec("mcp") is not None


def _scope(auth: str | None = "Bearer good-token"):
    headers = [(b"host", b"test")]
    if auth is not None:
        headers.append((b"authorization", auth.encode()))
    return {"type": "http", "method": "POST", "path": "/", "headers": headers}


class _Recorder:
    """Inner ASGI app that records whether it ran and what user was in context."""

    def __init__(self):
        self.called = False
        self.user_in_context = None

    async def __call__(self, scope, receive, send):
        self.called = True
        self.user_in_context = current_ai_user.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


async def _run_middleware(scope, *, enabled=True, auth_result=None, auth_raises=None):
    inner = _Recorder()
    middleware = MCPAuthMiddleware(inner)
    sent = []

    async def send(message):
        sent.append(message)

    auth = AsyncMock(return_value=auth_result)
    if auth_raises is not None:
        auth.side_effect = auth_raises

    with (
        patch.object(mcp_server, "get_app_settings", AsyncMock(return_value={"ai_mcp_enabled": enabled})),
        patch.object(mcp_server, "get_current_user", auth),
    ):
        await middleware(scope, AsyncMock(), send)

    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return inner, status, (json.loads(body) if body else {})


class TestMiddleware:
    @pytest.mark.anyio
    async def test_disabled_returns_503_before_auth(self):
        inner, status, body = await _run_middleware(_scope(), enabled=False)
        assert status == 503
        assert inner.called is False

    @pytest.mark.anyio
    async def test_missing_bearer_401(self):
        inner, status, _ = await _run_middleware(_scope(auth=None))
        assert status == 401
        assert inner.called is False

    @pytest.mark.anyio
    async def test_invalid_token_propagates_auth_status(self):
        inner, status, _ = await _run_middleware(
            _scope(), auth_raises=HTTPException(status_code=401, detail="bad token")
        )
        assert status == 401
        assert inner.called is False

    @pytest.mark.anyio
    async def test_admin_token_rejected(self):
        inner, status, body = await _run_middleware(_scope(), auth_result={"id": "admin-1", "role": "admin"})
        assert status == 403
        assert inner.called is False

    @pytest.mark.anyio
    async def test_valid_token_scopes_context_and_resets(self):
        inner, status, _ = await _run_middleware(_scope(), auth_result=dict(RIDER))
        assert status == 200
        assert inner.called is True
        assert inner.user_in_context["id"] == "rider-1"
        # context must not leak past the request
        assert current_ai_user.get() is None


class TestBuild:
    @pytest.mark.skipif(_HAS_MCP_SDK, reason="covers the SDK-absent deployment state")
    def test_returns_none_without_sdk(self):
        assert build_mcp_asgi_app() is None

    @pytest.mark.skipif(not _HAS_MCP_SDK, reason="mcp SDK not installed (lockfile not regenerated)")
    def test_builds_with_sdk(self):
        app = build_mcp_asgi_app()
        assert isinstance(app, MCPAuthMiddleware)
        assert mcp_server._state["manager"] is not None


class TestExposureRules:
    def test_booking_tools_never_mcp_exposed(self):
        ensure_registry_loaded()
        exposed = {n for n, s in TOOL_REGISTRY.items() if s.mcp_exposed}
        assert {"find_place", "get_fare_quote", "propose_ride_booking"}.isdisjoint(exposed)
        # the read-only surface is present
        assert {"get_active_ride", "get_wallet_balance", "search_faqs"} <= exposed

    def test_no_write_capable_tool_mcp_exposed(self):
        """/mcp is documented as READ-ONLY. escalate_to_support can open a
        real Zoho ticket (with the chat transcript attached) when
        ai_escalation_creates_ticket is on — it defaulted to mcp_exposed=True
        and quietly contradicted that contract."""
        ensure_registry_loaded()
        exposed = {n for n, s in TOOL_REGISTRY.items() if s.mcp_exposed}
        assert "escalate_to_support" not in exposed
        assert "request_map_pin" not in exposed


class TestMcpDailyCap:
    @pytest.mark.anyio
    async def test_cap_blocks_after_limit(self):
        counts = {}

        async def fake_incr(key):
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        with (
            patch.object(mcp_server, "redis_incr", fake_incr),
            patch.object(mcp_server, "redis_expire", AsyncMock()),
        ):
            assert await mcp_server._over_mcp_daily_cap("u1", 2) is False
            assert await mcp_server._over_mcp_daily_cap("u1", 2) is False
            assert await mcp_server._over_mcp_daily_cap("u1", 2) is True
            # independent per user
            assert await mcp_server._over_mcp_daily_cap("u2", 2) is False

    @pytest.mark.anyio
    async def test_cap_fails_open_on_redis_error(self):
        with patch.object(mcp_server, "redis_incr", AsyncMock(side_effect=RuntimeError("redis down"))):
            assert await mcp_server._over_mcp_daily_cap("u1", 2) is False

    @pytest.mark.anyio
    async def test_lifecycle_noops_without_manager(self):
        with patch.dict(mcp_server._state, {"manager": None, "run_ctx": None}):
            await mcp_server.start_mcp()
            await mcp_server.stop_mcp()
