"""Telegram bot — webhook-based, receives commands and returns reports."""
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

_API_BASE = "https://api.telegram.org/bot"


def _url(method: str) -> str:
    return f"{_API_BASE}{settings.TELEGRAM_BOT_TOKEN}/{method}"


def _is_allowed(user_id: int) -> bool:
    allowed = settings.allowed_telegram_users
    return not allowed or user_id in allowed


async def _send(chat_id: int, text: str, parse_mode: str = "Markdown") -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            _url("sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        )


async def register_webhook() -> None:
    webhook_url = f"{settings.HERMES_PUBLIC_URL}/telegram/webhook"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _url("setWebhook"),
            json={
                "url": webhook_url,
                "allowed_updates": ["message"],
                "secret_token": settings.HERMES_SECRET,
            },
        )
        data = resp.json()
    if data.get("ok"):
        logger.info("Telegram webhook registered: %s", webhook_url)
    else:
        logger.error("Telegram webhook registration failed: %s", data)


# ── Command handlers ─────────────────────────────────────────

COMMANDS: dict[str, str] = {
    "/start": "Welcome to Hermes — Spinr ops bot.\nCommands: /health /ops /report /rides /drivers /help",
    "/help": (
        "*Hermes Commands*\n"
        "/health — Infrastructure health\n"
        "/ops — Operational KPIs\n"
        "/report — Full infra + ops report\n"
        "/rides — Recent rides\n"
        "/drivers — Online drivers\n"
        "/ticket <subject> — Create a Zoho Desk ticket"
    ),
}


async def _handle_command(cmd: str, args: str, chat_id: int) -> None:
    if cmd in ("/start", "/help"):
        await _send(chat_id, COMMANDS[cmd])
        return

    if cmd == "/health":
        data = await spinr.get_infra_health()
        await _send(chat_id, format_infra(data))
    elif cmd == "/ops":
        data = await spinr.get_ops_report()
        await _send(chat_id, format_ops(data))
    elif cmd == "/report":
        data = await spinr.get_full_report()
        await _send(chat_id, format_full(data))
    elif cmd == "/rides":
        data = await spinr.get_ride_list()
        await _send(chat_id, f"```\n{_json_summary(data)}\n```")
    elif cmd == "/drivers":
        data = await spinr.get_driver_list(online_only=True)
        await _send(chat_id, f"```\n{_json_summary(data)}\n```")
    elif cmd == "/ticket":
        if not args.strip():
            await _send(chat_id, "Usage: /ticket <subject description>")
            return
        from app.integrations.zoho_desk import create_ticket
        result = await create_ticket(subject=args.strip(), description=f"Created via Telegram by chat {chat_id}")
        await _send(chat_id, f"Ticket created: {result.get('id', '?')}")
    else:
        await _send(chat_id, f"Unknown command: {cmd}\nTry /help")


def _json_summary(data: dict[str, Any], max_lines: int = 30) -> str:
    if data.get("status") != "ok":
        return f"Error: {data.get('status', 'unknown')} — {data.get('detail', data.get('code', ''))}"
    items = data.get("data", data)
    if isinstance(items, list):
        lines = []
        for item in items[:max_lines]:
            if isinstance(item, dict):
                line = " | ".join(f"{k}={v}" for k, v in list(item.items())[:5])
                lines.append(line)
            else:
                lines.append(str(item))
        return "\n".join(lines) or "No data"
    return str(items)[:2000]


# ── Webhook endpoint ─────────────────────────────────────────

@router.post("/webhook")
async def telegram_webhook(request: Request):
    if settings.HERMES_SECRET:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if token != settings.HERMES_SECRET:
            return Response(status_code=403)

    body: dict[str, Any] = await request.json()
    message = body.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")

    if not chat_id or not text:
        return {"ok": True}

    if not _is_allowed(user_id):
        await _send(chat_id, "Unauthorized. Your user ID is not in the allow-list.")
        return {"ok": True}

    parts = text.strip().split(None, 1)
    cmd = parts[0].lower().split("@")[0]  # strip @botname suffix
    args = parts[1] if len(parts) > 1 else ""

    if cmd.startswith("/"):
        try:
            await _handle_command(cmd, args, chat_id)
        except Exception:
            logger.exception("Error handling command %s", cmd)
            await _send(chat_id, f"Error processing {cmd}. Check logs.")
    elif settings.LLM_PROVIDER:
        try:
            from app.llm.providers import chat
            reply = await chat([{"role": "user", "content": text}])
            await _send(chat_id, reply, parse_mode="Markdown")
        except Exception:
            logger.exception("LLM chat error for Telegram")
            await _send(chat_id, "Sorry, I couldn't process that. Try a /command instead.")

    return {"ok": True}
