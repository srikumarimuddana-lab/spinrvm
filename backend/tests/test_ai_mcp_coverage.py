"""Coverage-focused unit tests for backend/ai/mcp_server.py (A1c Sub-tier C).

tests/test_ai_mcp.py already covers MCPAuthMiddleware end-to-end (kill
switch, bearer auth, admin rejection, context scoping/reset) and the
SDK-exposure rules (booking tools + write-capable tools never mcp_exposed).
This file fills the remaining gaps: the actual registered
list_tools/call_tool handlers built inside build_mcp_asgi_app() (only
reachable once the real `mcp` SDK is importable — skipped otherwise, same
convention as the existing SDK-gated tests), build_mcp_asgi_app()'s
top-level exception-swallow branch, _audience_for, and stop_mcp()'s
shutdown-exception swallow.

Test-only — ai/mcp_server.py is not modified.

Per CLAUDE.md's PIPEDA logging rules: /mcp payloads pass through
execute_tool()'s own business logic (already covered by ai/tools_*.py
tests) — this file only exercises mcp_server.py's routing/cap/audience
layer, which never itself logs PII.
"""

import importlib.util
from unittest.mock import AsyncMock, patch

import pytest

import backend.ai.mcp_server as mcp_server
from backend.ai.context import current_ai_user
from backend.ai.tools import ensure_registry_loaded

RIDER = {"id": "rider-1", "is_driver": False, "role": "user"}
DRIVER = {"id": "driver-1", "is_driver": True, "role": "user"}

_HAS_MCP_SDK = importlib.util.find_spec("mcp") is not None


class TestAudienceFor:
    def test_rider_when_not_is_driver(self):
        assert mcp_server._audience_for({"is_driver": False}) == "rider"
        assert mcp_server._audience_for({}) == "rider"

    def test_driver_when_is_driver_true(self):
        assert mcp_server._audience_for({"is_driver": True}) == "driver"


class TestMiddlewareAdditionalBranches:
    """Two MCPAuthMiddleware branches not covered by test_ai_mcp.py's
    TestMiddleware: non-HTTP ASGI scopes (lifespan/websocket) pass through
    untouched, and an auth failure that raises something other than
    HTTPException still degrades to a clean 401 rather than propagating."""

    @pytest.mark.anyio
    async def test_non_http_scope_passes_through_untouched(self):
        inner_called = {"value": False}

        async def inner(scope, receive, send):
            inner_called["value"] = True

        middleware = mcp_server.MCPAuthMiddleware(inner)
        await middleware({"type": "lifespan"}, AsyncMock(), AsyncMock())
        assert inner_called["value"] is True

    @pytest.mark.anyio
    async def test_unexpected_auth_exception_degrades_to_401(self, caplog):
        import logging

        inner_called = {"value": False}

        async def inner(scope, receive, send):
            inner_called["value"] = True

        middleware = mcp_server.MCPAuthMiddleware(inner)
        sent = []

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "headers": [(b"authorization", b"Bearer sometoken")]}
        with (
            patch.object(mcp_server, "get_app_settings", AsyncMock(return_value={"ai_mcp_enabled": True})),
            patch.object(mcp_server, "get_current_user", AsyncMock(side_effect=RuntimeError("boom"))),
            caplog.at_level(logging.ERROR),
        ):
            await middleware(scope, AsyncMock(), send)

        status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
        assert status == 401
        assert inner_called["value"] is False
        assert any("mcp auth failed unexpectedly" in r.message for r in caplog.records)


class TestBuildMcpAsgiAppExceptionSwallow:
    def test_construction_failure_logs_and_returns_none(self, caplog):
        """Version drift in the mcp SDK must never take the API down —
        any exception during server construction degrades to /mcp not
        being mounted, not a startup crash."""
        import logging

        if not _HAS_MCP_SDK:
            pytest.skip("mcp SDK not installed")

        with patch.object(mcp_server, "ensure_registry_loaded", side_effect=RuntimeError("registry boom")):
            with caplog.at_level(logging.ERROR):
                app = mcp_server.build_mcp_asgi_app()
        assert app is None
        assert mcp_server._state["manager"] is None
        assert any("failed to build /mcp app" in r.message for r in caplog.records)


@pytest.mark.skipif(not _HAS_MCP_SDK, reason="mcp SDK not installed (lockfile not regenerated)")
class TestListAndCallToolHandlers:
    """Exercises the real _list_tools/_call_tool closures registered on the
    lowlevel Server via build_mcp_asgi_app(), by driving them through the
    Server's own request_handlers dict (the same path the streamable-HTTP
    transport uses) — the closures aren't reachable any other way since the
    mcp SDK's list_tools()/call_tool() decorators return the *original*
    undecorated function to the caller, not the wrapped handler.
    """

    def setup_method(self):
        ensure_registry_loaded()

    def _build(self):
        app = mcp_server.build_mcp_asgi_app()
        assert app is not None, "mcp SDK present but app failed to build"
        server = mcp_server._state["manager"].app
        return server

    @pytest.mark.anyio
    async def test_list_tools_scopes_by_audience_and_excludes_unexposed(self):
        import mcp.types as mcp_types

        server = self._build()
        token = current_ai_user.set(dict(RIDER))
        try:
            handler = server.request_handlers[mcp_types.ListToolsRequest]
            result = await handler(mcp_types.ListToolsRequest(method="tools/list"))
            names = {t.name for t in result.root.tools}
        finally:
            current_ai_user.reset(token)

        # read-only rider-facing tools are present
        assert "get_wallet_balance" in names
        # booking tools are never exposed on /mcp regardless of audience
        assert "propose_ride_booking" not in names
        assert "find_place" not in names
        assert "get_fare_quote" not in names

    @pytest.mark.anyio
    async def test_call_tool_unknown_name_returns_error_payload(self):
        import json

        import mcp.types as mcp_types

        server = self._build()
        token = current_ai_user.set(dict(RIDER))
        try:
            with patch.object(mcp_server, "get_app_settings", AsyncMock(return_value={"ai_mcp_daily_tool_cap": 50})):
                handler = server.request_handlers[mcp_types.CallToolRequest]
                req = mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(name="not_a_real_tool", arguments={}),
                )
                result = await handler(req)
        finally:
            current_ai_user.reset(token)

        text = result.root.content[0].text
        payload = json.loads(text)
        assert "unknown tool" in payload["error"]

    @pytest.mark.anyio
    async def test_call_tool_over_daily_cap_returns_limit_error_without_executing(self):
        import json

        import mcp.types as mcp_types

        server = self._build()
        token = current_ai_user.set(dict(RIDER))
        try:
            with (
                patch.object(mcp_server, "get_app_settings", AsyncMock(return_value={"ai_mcp_daily_tool_cap": 1})),
                patch.object(mcp_server, "_over_mcp_daily_cap", AsyncMock(return_value=True)),
                patch.object(mcp_server, "execute_tool", AsyncMock()) as mock_execute,
            ):
                handler = server.request_handlers[mcp_types.CallToolRequest]
                req = mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(name="get_wallet_balance", arguments={}),
                )
                result = await handler(req)
        finally:
            current_ai_user.reset(token)

        text = result.root.content[0].text
        payload = json.loads(text)
        assert "daily limit reached" in payload["error"]
        mock_execute.assert_not_awaited()

    @pytest.mark.anyio
    async def test_call_tool_success_executes_and_returns_payload(self):
        import json

        import mcp.types as mcp_types

        server = self._build()
        token = current_ai_user.set(dict(RIDER))
        try:
            with (
                patch.object(mcp_server, "get_app_settings", AsyncMock(return_value={"ai_mcp_daily_tool_cap": 50})),
                patch.object(mcp_server, "_over_mcp_daily_cap", AsyncMock(return_value=False)),
                patch.object(
                    mcp_server, "execute_tool", AsyncMock(return_value=({"balance": "12.50"}, True))
                ) as mock_execute,
            ):
                handler = server.request_handlers[mcp_types.CallToolRequest]
                req = mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(name="get_wallet_balance", arguments={}),
                )
                result = await handler(req)
        finally:
            current_ai_user.reset(token)

        text = result.root.content[0].text
        payload = json.loads(text)
        assert payload == {"balance": "12.50"}
        mock_execute.assert_awaited_once()
        call_kwargs = mock_execute.call_args.kwargs
        assert call_kwargs["user"]["id"] == "rider-1"
        assert call_kwargs["audience"] == "rider"

    @pytest.mark.anyio
    async def test_call_tool_uses_fallback_cap_when_mcp_cap_unset(self):
        """cap falls back to ai_daily_message_cap, then to 50, when
        ai_mcp_daily_tool_cap is not configured."""
        import mcp.types as mcp_types

        server = self._build()
        token = current_ai_user.set(dict(RIDER))
        try:
            with (
                patch.object(mcp_server, "get_app_settings", AsyncMock(return_value={})),
                patch.object(mcp_server, "_over_mcp_daily_cap", AsyncMock(return_value=False)) as mock_cap,
                patch.object(mcp_server, "execute_tool", AsyncMock(return_value=({}, True))),
            ):
                handler = server.request_handlers[mcp_types.CallToolRequest]
                req = mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(name="get_wallet_balance", arguments={}),
                )
                await handler(req)
        finally:
            current_ai_user.reset(token)

        # cap arg (50 default) passed through to the daily-cap check
        assert mock_cap.await_args.args[1] == 50


class TestStopMcpSwallowsShutdownException:
    @pytest.mark.anyio
    async def test_stop_mcp_logs_and_swallows_aexit_error(self, caplog):
        import logging

        class _BadRunCtx:
            async def __aexit__(self, *exc):
                raise RuntimeError("shutdown boom")

        with patch.dict(mcp_server._state, {"manager": object(), "run_ctx": _BadRunCtx()}):
            with caplog.at_level(logging.WARNING):
                await mcp_server.stop_mcp()

        assert mcp_server._state.get("run_ctx") is None
        assert any("MCP session manager shutdown raised" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_stop_mcp_clean_shutdown(self):
        calls = {"count": 0}

        class _GoodRunCtx:
            async def __aexit__(self, *exc):
                calls["count"] += 1

        with patch.dict(mcp_server._state, {"manager": object(), "run_ctx": _GoodRunCtx()}):
            await mcp_server.stop_mcp()
        assert calls["count"] == 1
