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
- every tool-call response body is PII-scrubbed under ScrubPolicy.STRICT in
  _serialize_tool_payload (bounded to pii._MAX_SCRUB_DEPTH) -- this surface
  is a third-party egress, so unlike the in-app chat card it gets no
  trip-location exemption (ADR 012). The auth / kill-switch error bodies and
  the tool list carry no user data and do not pass through it.
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
    from .guardrails import fallback_over_cap
    from .pii import scrub_pii_deep
    from .tools import TOOL_REGISTRY, ensure_registry_loaded, execute_tool
except ImportError:
    from ai.context import current_ai_user, require_ai_user
    from ai.guardrails import fallback_over_cap
    from ai.pii import scrub_pii_deep
    from ai.tools import TOOL_REGISTRY, ensure_registry_loaded, execute_tool

try:
    from ..dependencies import ADMIN_STAFF_ROLES, get_current_user, get_token_session_id
    from ..settings_loader import get_app_settings
    from ..utils.redis_client import redis_expire, redis_incr
    from ..utils.session_revocation import is_session_revoked
except ImportError:
    from dependencies import ADMIN_STAFF_ROLES, get_current_user, get_token_session_id
    from settings_loader import get_app_settings
    from utils.redis_client import redis_expire, redis_incr
    from utils.session_revocation import is_session_revoked

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


def _is_customer_principal(user: Dict[str, Any]) -> bool:
    """Whether this authenticated principal is a customer (rider/driver).

    /mcp exposes rider/driver tools scoped to the supplied identity. A staff
    principal is not such an identity and has no business on this surface.

    F07 (AI security assessment (PR #5138)): this used to be
    ``user.get("role") == "admin"``, which rejected exactly one of the six
    roles the verified staff pipeline returns — ``super_admin``,
    ``operations``, ``support``, ``finance`` and ``custom`` all reached the
    MCP app. Offline middleware probes confirmed all five.

    Gated primarily on ``_admin_verified``, the private marker that ONLY
    dependencies._verify_admin_payload sets after the full admin pipeline —
    the same authoritative signal ``get_admin_user`` uses, and for the same
    reason it gives there: ``role`` is a users-table column that ordinary
    tokens also carry, so it proves nothing on its own.

    ``ADMIN_STAFF_ROLES`` is then checked as a fail-closed backstop. On the
    ADMIT side that would be unsound (a rider row whose ``users.role`` column
    says "support" is not staff), but this is a DENY rule, where
    over-rejecting is the safe direction: it keeps the gate closed even if a
    future path stops stripping the marker.
    """
    if user.get("_admin_verified"):
        return False
    if (user.get("role") or "") in ADMIN_STAFF_ROLES:
        return False
    return bool(user.get("id"))


def _serialize_tool_payload(payload: Any) -> str:
    """The /mcp egress point. execute_tool's _cap_result scrubs only the
    MODEL-facing portion of a result (the in-app chat policy: postal codes
    and trip pins kept) and leaves ``_client_action`` untouched because in the
    chat path that card goes back to the rider's own app. An MCP client is a
    third party, so the whole payload -- card included -- is re-scrubbed
    here under the default STRICT policy before it leaves the process. This
    keeps /mcp's posture exactly what it was before the card exemption
    (2026-09-04, ADR 012); the double scrub is idempotent because redaction
    tokens never re-match a pattern."""
    return json.dumps(scrub_pii_deep(payload), default=str)


async def _over_mcp_daily_cap(user_id: str, cap: int) -> bool:
    """Per-user daily cap on /mcp tool calls via Redis INCR. The chat path is
    capped in the orchestrator, but /mcp called execute_tool directly with no
    ceiling — an unattended agent client could hammer Maps-fee-free reads (or
    any future exposed tool) all day. On a Redis error, falls back to a
    bounded, process-local cap (AI1b, GitHub #3742) rather than failing open
    — see ai/guardrails.py; ai_mcp_enabled remains the separate hard stop."""
    key = f"ai:mcp:daily:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    try:
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, 86400)
        return count > cap
    except Exception:
        logger.error(
            "mcp daily-cap check failed — falling back to bounded process-local cap",
            exc_info=True,
            extra={"user_id": user_id},
        )
        return fallback_over_cap(user_id, cap)


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
        # Two switches, both must be on — same invariant as every other AI
        # entry point (orchestrator.py, public_assistant.py, support_assistant.py).
        # Without this, an operator flipping the global ai_assistant_enabled
        # kill switch off (the "stop sending user data to the third-party LLM
        # provider" incident lever) would not actually stop /mcp if
        # ai_mcp_enabled was separately left on (#4641).
        if not settings.get("ai_assistant_enabled") or not settings.get("ai_mcp_enabled"):
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

        if not _is_customer_principal(user):
            await _send_json(send, 403, {"detail": "Staff tokens are not accepted on /mcp"})
            return

        # F02: /mcp calls get_current_user directly rather than through a
        # FastAPI dependency, so it cannot pick up get_current_user_active_session
        # the way the AI routes do — the tombstone check is spelled out here
        # instead. An unattended agent client holding a signed-out token is
        # exactly the zombie-writer case session tombstones exist for.
        # Fail-open on every ambiguous input, same as everywhere else.
        # get_token_session_id is a plain async function (its Depends default is
        # only for FastAPI); calling it with the credentials we already built
        # reuses one decode path instead of duplicating JWT parsing here.
        if await is_session_revoked(await get_token_session_id(credentials)):
            await _send_json(send, 401, {"detail": "ERR_SESSION_REVOKED"})
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
            return [mcp_types.TextContent(type="text", text=_serialize_tool_payload(payload))]

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
