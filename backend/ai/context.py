"""Request-scoped auth context for AI tool execution.

The in-process chat path passes the authenticated user explicitly into
execute_tool(). The /mcp transport cannot — FastMCP owns the request cycle —
so its ASGI auth middleware resolves the bearer token and stashes the user
dict here. Tool wrappers then call require_ai_user().

The user identity is NEVER a tool parameter: the model (or an MCP client)
cannot choose whose data a tool reads.
"""

from contextvars import ContextVar
from typing import Any, Dict, Optional


class AIAuthError(Exception):
    """Raised when a tool executes without an authenticated user in context."""


current_ai_user: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_ai_user", default=None)


def require_ai_user() -> Dict[str, Any]:
    user = current_ai_user.get()
    if not user or not user.get("id"):
        raise AIAuthError("no authenticated user in AI tool context")
    return user
