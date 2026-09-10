"""LLM provider abstraction — Claude (Anthropic) and OpenAI with tool-use."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── Tool schema (shared across providers) ─────────────────────

HERMES_TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "get_infra_health",
            "description": "Get Spinr backend infrastructure health status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ops_report",
            "description": "Get Spinr operational KPIs — rides, drivers, payments, dispatch.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_full_report",
            "description": "Get combined infra + ops report.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rides",
            "description": "Get recent rides.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 10}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_drivers",
            "description": "Get drivers (optionally only online ones).",
            "parameters": {
                "type": "object",
                "properties": {"online_only": {"type": "boolean", "default": False}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_desk_tickets",
            "description": "Search Zoho Desk support tickets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_desk_ticket",
            "description": "Create a Zoho Desk support ticket.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string", "enum": ["Low", "Medium", "High", "Urgent"]},
                },
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_telegram_message",
            "description": "Send a message to a Telegram chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["chat_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_cliq_message",
            "description": "Send a message to a Zoho Cliq channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["channel", "text"],
            },
        },
    },
]

HERMES_TOOLS_ANTHROPIC = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in HERMES_TOOLS_OPENAI
]


# ── Tool executor (reused from MCP server) ───────────────────

async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    from app.mcp.server import _execute_tool
    try:
        result = await _execute_tool(name, arguments)
        return json.dumps(result, default=str, indent=2)
    except Exception as exc:
        return f"Tool error: {exc}"


# ── Provider interface ────────────────────────────────────────

async def chat(messages: list[dict[str, Any]], max_rounds: int = 5) -> str:
    """Run a multi-turn conversation with tool use. Returns the final text answer."""
    provider = settings.LLM_PROVIDER.lower()
    if provider == "anthropic":
        return await _chat_anthropic(messages, max_rounds)
    elif provider == "openai":
        return await _chat_openai(messages, max_rounds)
    else:
        return "LLM not configured. Set LLM_PROVIDER to 'anthropic' or 'openai'."


# ── Anthropic (Claude) ───────────────────────────────────────

async def _chat_anthropic(messages: list[dict[str, Any]], max_rounds: int) -> str:
    if not settings.ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY not set."

    api_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    for _ in range(max_rounds):
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.ANTHROPIC_MODEL,
                    "max_tokens": settings.LLM_MAX_TOKENS,
                    "system": settings.LLM_SYSTEM_PROMPT,
                    "tools": HERMES_TOOLS_ANTHROPIC,
                    "messages": api_messages,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        stop_reason = data.get("stop_reason", "")
        content_blocks = data.get("content", [])

        if stop_reason != "tool_use":
            # Final text response
            text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
            return "\n".join(text_parts) or "(no response)"

        # Process tool calls
        api_messages.append({"role": "assistant", "content": content_blocks})
        tool_results = []
        for block in content_blocks:
            if block.get("type") == "tool_use":
                result_text = await execute_tool(block["name"], block.get("input", {}))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result_text,
                })
        api_messages.append({"role": "user", "content": tool_results})

    return "(max tool rounds reached)"


# ── OpenAI ───────────────────────────────────────────────────

async def _chat_openai(messages: list[dict[str, Any]], max_rounds: int) -> str:
    if not settings.OPENAI_API_KEY:
        return "OPENAI_API_KEY not set."

    api_messages = [{"role": "system", "content": settings.LLM_SYSTEM_PROMPT}]
    api_messages.extend({"role": m["role"], "content": m["content"]} for m in messages)

    for _ in range(max_rounds):
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "max_tokens": settings.LLM_MAX_TOKENS,
                    "tools": HERMES_TOOLS_OPENAI,
                    "messages": api_messages,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason", "")

        if finish_reason != "tool_calls" or not message.get("tool_calls"):
            return message.get("content", "(no response)")

        # Process tool calls
        api_messages.append(message)
        for tc in message["tool_calls"]:
            fn = tc["function"]
            args = json.loads(fn.get("arguments", "{}"))
            result_text = await execute_tool(fn["name"], args)
            api_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_text,
            })

    return "(max tool rounds reached)"
