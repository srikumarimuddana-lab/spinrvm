"""Optional /mcp streamable-HTTP mount — the same tool registry over MCP.

External agent clients (Claude Desktop/Code, future surfaces) get the
READ-ONLY tool subset (ToolSpec.mcp_exposed) with the same user scoping as
the chat path: the ASGI auth middleware resolves the Bearer token through
the regular get_current_user dependency and stashes the user in the
current_ai_user ContextVar — identity is never an MCP parameter.

Defence in depth:
- the ``mcp`` SDK is imported lazily; absent SDK → /mcp simply not mounted
- app_settings.ai_mcp_enabled (default False) is checked per request → 503
- admin tokens are rejected (this surface is for rider/driver accounts)
- booking tools are invisible here (mcp_exposed=False on their specs)
- per-user daily tool-call cap (ai_mcp_daily_tool_cap, falling back to
  ai_daily_message_cap) — the chat path is capped in the orchestrator, this
  surface caps itself

The streamable-HTTP session manager must be running before requests are
served; core/lifespan.py calls start_mcp()/stop_mcp() around the app's
lifetime. SDK surface here matches mcp>=1.9; verify on upgrade.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

try:
    from .context import current_ai_user, require_ai_user
    from .tools import TOOL_REGISTRY, ensure_registry_loaded, execute_tool
except ImportError:
    from ai.context import current_ai_user, require_ai_user
    from ai.tools import TOOL_REGISTRY, ensure_registry_loaded, execute_tool

try:
    from ..dependencies import get_current_user
    from ..settings_loader import get_app_settings
    from ..utils.redis_client import redis_expire, redis_incr
except ImportError:
    from dependencies import get_current_user
    from settings_loader import get_app_settings
    from utils.redis_client import redis_expire, redis_incr

logger = logging.getLogger(__name__)

_state: Dict[str, Any] = {"manager": None, "run_ctx": None}


async def _send_json(send, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _audience_for(user: Dict[str, Any]) -> str:
    return "driver" if user.get("is_driver") else "rider"


async def _over_mcp_daily_cap(user_id: str, cap: int) -> bool:
    """Per-user daily cap on /mcp tool calls via Redis INCR. The chat path is
    capped in the orchestrator, but /mcp called execute_tool directly with no
    ceiling — an unattended agent client could hammer Maps-fee-free reads (or
    any future exposed tool) all day. Fails OPEN with a loud log, mirroring
    the chat-cap policy: ai_mcp_enabled stays the hard stop when Redis is
    down."""
    key = f"ai:mcp:daily:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    try:
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, 86400)
        return count > cap
    except Exception:
        logger.error("mcp daily-cap check failed — failing open", exc_info=True, extra={"user_id": user_id})
        return False


class MCPAuthMiddleware:
    """Raw ASGI middleware: kill switch + Bearer auth + ContextVar scoping.

    Reuses the regular get_current_user dependency by constructing the
    HTTPAuthorizationCredentials it expects — Firebase/JWT verification,
    token_version and session checks all apply unchanged.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = await get_app_settings()
        if not settings.get("ai_mcp_enabled"):
            await _send_json(send, 503, {"detail": "MCP is disabled"})
            return

        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        auth = (headers.get(b"authorization") or b"").decode()
        if not auth.lower().startswith("bearer "):
            await _send_json(send, 401, {"detail": "Bearer token required"})
            return

        try:
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=auth[7:])
            user = await get_current_user(credentials)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Unauthorized"
            await _send_json(send, exc.status_code, {"detail": detail})
            return
        except Exception:
            logger.error("mcp auth failed unexpectedly", exc_info=True)
            await _send_json(send, 401, {"detail": "Unauthorized"})
            return

        if user.get("role") == "admin":
            # Admin tokens carry trusted claims for the dashboard — they are
            # not rider/driver identities and have no business on this surface.
            await _send_json(send, 403, {"detail": "Admin tokens are not accepted on /mcp"})
            return

        token = current_ai_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_ai_user.reset(token)


def build_mcp_asgi_app() -> Optional[MCPAuthMiddleware]:
    """Build the wrapped ASGI app, or None when the SDK is unavailable or
    construction fails (logged loudly; the chat path is unaffected)."""
    try:
        import mcp.types as mcp_types
        from mcp.server.lowlevel import Server
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    except ImportError:
        logger.info("mcp SDK not installed — /mcp not mounted (regenerate the lockfile to enable)")
        return None

    try:
        ensure_registry_loaded()
        server = Server("spinr-ai")

        @server.list_tools()
        async def _list_tools():
            user = require_ai_user()
            audience = _audience_for(user)
            return [
                mcp_types.Tool(name=s.name, description=s.description, inputSchema=s.input_schema)
                for s in sorted(TOOL_REGISTRY.values(), key=lambda s: s.name)
                if s.mcp_exposed and audience in s.audiences
            ]

        @server.call_tool()
        async def _call_tool(name: str, arguments: Dict[str, Any]):
            user = require_ai_user()
            spec = TOOL_REGISTRY.get(name)
            if spec is None or not spec.mcp_exposed:
                payload: Dict[str, Any] = {"error": f"unknown tool: {name}"}
            else:
                settings = await get_app_settings()
                cap = int(settings.get("ai_mcp_daily_tool_cap") or settings.get("ai_daily_message_cap") or 50)
                if await _over_mcp_daily_cap(user["id"], cap):
                    payload = {"error": "daily limit reached — try again tomorrow"}
                else:
                    payload, _ok = await execute_tool(name, arguments or {}, user=user, audience=_audience_for(user))
            return [mcp_types.TextContent(type="text", text=json.dumps(payload, default=str))]

        manager = StreamableHTTPSessionManager(app=server, stateless=True)
        _state["manager"] = manager

        async def _asgi(scope, receive, send):
            await manager.handle_request(scope, receive, send)

        return MCPAuthMiddleware(_asgi)
    except Exception:
        # Version drift in the mcp SDK must never take the API down.
        logger.error("failed to build /mcp app — continuing without it", exc_info=True)
        _state["manager"] = None
        return None


async def start_mcp() -> None:
    """Run the streamable-HTTP session manager (called from core/lifespan)."""
    manager = _state.get("manager")
    if manager is None or _state.get("run_ctx") is not None:
        return
    run_ctx = manager.run()
    await run_ctx.__aenter__()
    _state["run_ctx"] = run_ctx
    logger.info("MCP session manager started (/mcp live, gated by ai_mcp_enabled)")


async def stop_mcp() -> None:
    run_ctx = _state.pop("run_ctx", None)
    if run_ctx is not None:
        try:
            await run_ctx.__aexit__(None, None, None)
        except Exception:
            logger.warning("MCP session manager shutdown raised", exc_info=True)
