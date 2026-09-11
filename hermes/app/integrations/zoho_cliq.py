"""Zoho Cliq integration — incoming webhook + outgoing bot messages."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

from app.config import settings
from app.integrations import spinr
from app.reports import format_infra, format_ops, format_full

logger = logging.getLogger(__name__)
router = APIRouter()


async def send_cliq_message(channel_or_chat: str, text: str) -> dict[str, Any]:
    """Post a message to a Cliq channel or chat via the bot API."""
    if not settings.ZOHO_CLIQ_BOT_API_URL:
        return {"error": "ZOHO_CLIQ_BOT_API_URL not configured"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            settings.ZOHO_CLIQ_BOT_API_URL,
            json={"text": text, "channel": channel_or_chat},
        )
        return resp.json()


async def send_cliq_card(channel: str, title: str, body: str) -> dict[str, Any]:
    """Post a rich card to a Cliq channel."""
    if not settings.ZOHO_CLIQ_BOT_API_URL:
        return {"error": "ZOHO_CLIQ_BOT_API_URL not configured"}
    card = {
        "text": "",
        "card": {"title": title, "theme": "modern-inline"},
        "slides": [{"type": "text", "data": body}],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(settings.ZOHO_CLIQ_BOT_API_URL, json=card)
        return resp.json()


# ── Command handling (Cliq bot commands or slash commands) ────

async def _handle_cliq_command(cmd: str, params: str) -> str:
    cmd = cmd.lower().strip("/").strip()
    if cmd in ("health", "infra"):
        data = await spinr.get_infra_health()
        return format_infra(data)
    elif cmd in ("ops", "kpi"):
        data = await spinr.get_ops_report()
        return format_ops(data)
    elif cmd in ("report", "full"):
        data = await spinr.get_full_report()
        return format_full(data)
    elif cmd == "rides":
        data = await spinr.get_ride_list()
        return str(data)[:3000]
    elif cmd == "drivers":
        data = await spinr.get_driver_list(online_only=True)
        return str(data)[:3000]
    elif cmd == "help":
        return (
            "Hermes commands:\n"
            "  health — Infra health\n"
            "  ops — Operational KPIs\n"
            "  report — Full report\n"
            "  rides — Recent rides\n"
            "  drivers — Online drivers"
        )
    return f"Unknown command: {cmd}. Try 'help'."


# ── Webhook endpoint ─────────────────────────────────────────

@router.post("/webhook")
async def cliq_webhook(request: Request):
    if settings.ZOHO_CLIQ_WEBHOOK_TOKEN:
        token = request.headers.get("X-Zoho-Cliq-Token", "")
        if token != settings.ZOHO_CLIQ_WEBHOOK_TOKEN:
            return Response(status_code=403)

    body: dict[str, Any] = await request.json()
    event_type = body.get("type", "")

    if event_type == "bot_message":
        text = body.get("message", {}).get("text", "")
        parts = text.strip().split(None, 1)
        cmd = parts[0] if parts else ""
        params = parts[1] if len(parts) > 1 else ""

        # Known commands go through the command handler; free text goes to LLM
        known = {"health", "infra", "ops", "kpi", "report", "full", "rides", "drivers", "help"}
        if cmd.lower().strip("/") in known:
            try:
                reply = await _handle_cliq_command(cmd, params)
            except Exception:
                logger.exception("Cliq command error: %s", cmd)
                reply = f"Error processing '{cmd}'"
        elif settings.LLM_PROVIDER:
            try:
                from app.llm.providers import chat
                reply = await chat([{"role": "user", "content": text}])
            except Exception:
                logger.exception("Cliq LLM error")
                reply = "Sorry, I couldn't process that. Try a known command: help"
        else:
            reply = await _handle_cliq_command(cmd or "help", params)
        return {"text": reply}

    if event_type == "slash_command":
        cmd = body.get("name", "help")
        params = body.get("params", {}).get("arguments", "")
        try:
            reply = await _handle_cliq_command(cmd, params)
        except Exception:
            logger.exception("Cliq slash command error: %s", cmd)
            reply = f"Error processing '/{cmd}'"
        return {"text": reply}

    return {"text": "Event received"}


@router.post("/send")
async def cliq_send(request: Request):
    body = await request.json()
    channel = body.get("channel", "")
    text = body.get("text", "")
    if not channel or not text:
        return {"error": "channel and text required"}
    return await send_cliq_message(channel, text)
