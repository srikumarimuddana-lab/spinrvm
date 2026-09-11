"""MCP server over SSE transport — lets Claude Code desktop connect to Hermes.

Implements the MCP (Model Context Protocol) JSON-RPC layer with SSE streaming,
exposing Hermes tools: infra reports, ops reports, Zoho Desk tickets,
Telegram/Cliq messaging.

Connect from Claude Code desktop by adding to .claude/settings.local.json:
{
  "mcpServers": {
    "hermes": {
      "type": "http",
      "url": "https://hermes-spinr.fly.dev/mcp/sse"
    }
  }
}
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from sse_starlette.sse import EventSourceResponse

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Tool definitions ─────────────────────────────────────────

TOOLS = [
    {
        "name": "get_infra_health",
        "description": "Get Spinr backend infrastructure health — backend status, readiness, metrics availability.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_ops_report",
        "description": "Get Spinr operational KPIs — ride stats, driver stats, payment stats, dispatch stats.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_full_report",
        "description": "Get combined infra + ops report for Spinr.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_rides",
        "description": "Get recent rides from Spinr backend.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10, "description": "Max rides to return"}},
        },
    },
    {
        "name": "get_drivers",
        "description": "Get drivers from Spinr backend.",
        "inputSchema": {
            "type": "object",
            "properties": {"online_only": {"type": "boolean", "default": False, "description": "Only online drivers"}},
        },
    },
    {
        "name": "search_desk_tickets",
        "description": "Search Zoho Desk tickets by query string or status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "status": {"type": "string", "description": "Filter by status (Open, Closed, etc.)"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "get_desk_ticket",
        "description": "Get a specific Zoho Desk ticket by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string", "description": "Zoho Desk ticket ID"}},
            "required": ["ticket_id"],
        },
    },
    {
        "name": "create_desk_ticket",
        "description": "Create a new Zoho Desk support ticket.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["Low", "Medium", "High", "Urgent"], "default": "Medium"},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "send_telegram_message",
        "description": "Send a message to a Telegram chat via the Hermes bot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "integer", "description": "Telegram chat ID"},
                "text": {"type": "string", "description": "Message text (Markdown)"},
            },
            "required": ["chat_id", "text"],
        },
    },
    {
        "name": "send_cliq_message",
        "description": "Send a message to a Zoho Cliq channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Cliq channel name"},
                "text": {"type": "string", "description": "Message text"},
            },
            "required": ["channel", "text"],
        },
    },
]


# ── Tool execution ───────────────────────────────────────────

async def _execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    from app.integrations import spinr
    from app.integrations.zoho_desk import search_tickets, get_ticket, create_ticket
    from app.integrations.zoho_cliq import send_cliq_message
    from app.integrations.telegram import _send as telegram_send

    if name == "get_infra_health":
        return await spinr.get_infra_health()
    elif name == "get_ops_report":
        return await spinr.get_ops_report()
    elif name == "get_full_report":
        return await spinr.get_full_report()
    elif name == "get_rides":
        return await spinr.get_ride_list(arguments.get("limit", 10))
    elif name == "get_drivers":
        return await spinr.get_driver_list(arguments.get("online_only", False))
    elif name == "search_desk_tickets":
        return await search_tickets(
            query=arguments.get("query", ""),
            status=arguments.get("status", ""),
            limit=arguments.get("limit", 10),
        )
    elif name == "get_desk_ticket":
        return await get_ticket(arguments["ticket_id"])
    elif name == "create_desk_ticket":
        return await create_ticket(
            subject=arguments["subject"],
            description=arguments.get("description", ""),
            priority=arguments.get("priority", "Medium"),
        )
    elif name == "send_telegram_message":
        await telegram_send(arguments["chat_id"], arguments["text"])
        return {"sent": True}
    elif name == "send_cliq_message":
        return await send_cliq_message(arguments["channel"], arguments["text"])
    else:
        raise ValueError(f"Unknown tool: {name}")


# ── JSON-RPC handler ─────────────────────────────────────────

def _jsonrpc_response(id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


async def _handle_jsonrpc(body: dict[str, Any]) -> dict[str, Any]:
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        return _jsonrpc_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "hermes-spinr", "version": "0.1.0"},
        })

    elif method == "notifications/initialized":
        return _jsonrpc_response(req_id, {})

    elif method == "tools/list":
        return _jsonrpc_response(req_id, {"tools": TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = await _execute_tool(tool_name, arguments)
            return _jsonrpc_response(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, default=str, indent=2)}],
            })
        except Exception as exc:
            logger.exception("Tool call failed: %s", tool_name)
            return _jsonrpc_response(req_id, {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "isError": True,
            })

    elif method == "ping":
        return _jsonrpc_response(req_id, {})

    else:
        return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")


# ── SSE transport endpoint ───────────────────────────────────

@router.get("/sse")
async def mcp_sse(request: Request):
    """SSE endpoint — client connects here, then POSTs JSON-RPC to /mcp/message."""
    session_id = str(uuid.uuid4())
    logger.info("MCP SSE session started: %s", session_id)

    async def event_generator():
        yield {
            "event": "endpoint",
            "data": f"/mcp/message?session_id={session_id}",
        }
        # Keep alive until client disconnects
        import asyncio
        try:
            while True:
                if await request.is_disconnected():
                    break
                yield {"event": "ping", "data": ""}
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass
        logger.info("MCP SSE session ended: %s", session_id)

    return EventSourceResponse(event_generator())


@router.post("/message")
async def mcp_message(request: Request):
    """JSON-RPC message endpoint for MCP tools/call etc."""
    body = await request.json()
    result = await _handle_jsonrpc(body)
    return result


# ── Streamable HTTP (single-endpoint, newer MCP transport) ───

@router.post("")
async def mcp_streamable(request: Request):
    """Streamable HTTP transport — single POST endpoint for both init and tool calls."""
    body = await request.json()
    result = await _handle_jsonrpc(body)
    return result
