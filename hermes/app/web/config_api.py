"""Config + chat API endpoints for the dashboard."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request

from app.config import settings
from app.llm.providers import chat

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Connection tests ─────────────────────────────────────────

async def _test_spinr() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{settings.SPINR_BACKEND_URL}/health",
                headers={"Authorization": f"Bearer {settings.SPINR_API_KEY}"} if settings.SPINR_API_KEY else {},
            )
            return {"connected": r.status_code == 200, "status_code": r.status_code}
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


async def _test_telegram() -> dict[str, Any]:
    if not settings.TELEGRAM_BOT_TOKEN:
        return {"connected": False, "error": "Token not set"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getMe")
            data = r.json()
            if data.get("ok"):
                return {"connected": True, "bot": data["result"].get("username", "?")}
            return {"connected": False, "error": data.get("description", "unknown")}
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


async def _test_zoho() -> dict[str, Any]:
    if not settings.ZOHO_CLIENT_ID:
        return {"connected": False, "error": "OAuth credentials not set"}
    try:
        from app.zoho_auth import get_access_token
        token = await get_access_token()
        return {"connected": True, "token_preview": f"{token[:8]}..."}
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


async def _test_llm() -> dict[str, Any]:
    provider = settings.LLM_PROVIDER.lower()
    if not provider:
        return {"connected": False, "provider": "none", "error": "LLM_PROVIDER not set"}
    if provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            return {"connected": False, "provider": "anthropic", "error": "ANTHROPIC_API_KEY not set"}
        try:
            reply = await chat([{"role": "user", "content": "Reply with just 'ok'."}], max_rounds=1)
            return {"connected": True, "provider": "anthropic", "model": settings.ANTHROPIC_MODEL, "test": reply.strip()}
        except Exception as exc:
            return {"connected": False, "provider": "anthropic", "error": str(exc)}
    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            return {"connected": False, "provider": "openai", "error": "OPENAI_API_KEY not set"}
        try:
            reply = await chat([{"role": "user", "content": "Reply with just 'ok'."}], max_rounds=1)
            return {"connected": True, "provider": "openai", "model": settings.OPENAI_MODEL, "test": reply.strip()}
        except Exception as exc:
            return {"connected": False, "provider": "openai", "error": str(exc)}
    return {"connected": False, "provider": provider, "error": "Unknown provider"}


# ── API routes ───────────────────────────────────────────────

@router.get("/config/status")
async def config_status():
    """Current configuration status (no secrets exposed)."""
    return {
        "env": settings.ENV,
        "integrations": {
            "spinr": {
                "configured": bool(settings.SPINR_API_KEY),
                "url": settings.SPINR_BACKEND_URL,
            },
            "telegram": {
                "configured": bool(settings.TELEGRAM_BOT_TOKEN),
                "webhook_url": f"{settings.HERMES_PUBLIC_URL}/telegram/webhook" if settings.HERMES_PUBLIC_URL else "",
                "allowed_users": len(settings.allowed_telegram_users),
            },
            "zoho_cliq": {
                "configured": bool(settings.ZOHO_CLIQ_BOT_API_URL),
            },
            "zoho_desk": {
                "configured": bool(settings.ZOHO_DESK_ORG_ID),
                "api_url": settings.ZOHO_DESK_API_URL,
            },
            "zoho_oauth": {
                "configured": bool(settings.ZOHO_CLIENT_ID and settings.ZOHO_REFRESH_TOKEN),
            },
        },
        "llm": {
            "provider": settings.LLM_PROVIDER or "disabled",
            "model": (
                settings.ANTHROPIC_MODEL if settings.LLM_PROVIDER == "anthropic"
                else settings.OPENAI_MODEL if settings.LLM_PROVIDER == "openai"
                else ""
            ),
            "configured": bool(
                (settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY)
                or (settings.LLM_PROVIDER == "openai" and settings.OPENAI_API_KEY)
            ),
        },
        "mcp": {
            "sse_endpoint": f"{settings.HERMES_PUBLIC_URL}/mcp/sse" if settings.HERMES_PUBLIC_URL else "/mcp/sse",
            "http_endpoint": f"{settings.HERMES_PUBLIC_URL}/mcp" if settings.HERMES_PUBLIC_URL else "/mcp",
        },
    }


@router.post("/config/test/{integration}")
async def test_connection(integration: str):
    """Test a specific integration connection."""
    testers = {
        "spinr": _test_spinr,
        "telegram": _test_telegram,
        "zoho": _test_zoho,
        "llm": _test_llm,
    }
    tester = testers.get(integration)
    if not tester:
        return {"error": f"Unknown integration: {integration}"}
    return await tester()


@router.post("/chat")
async def chat_endpoint(request: Request):
    """Chat with Hermes via LLM. Supports tool-use loop."""
    body = await request.json()
    user_message = body.get("message", "")
    history = body.get("history", [])
    if not user_message:
        return {"error": "message required"}

    messages = list(history)
    messages.append({"role": "user", "content": user_message})

    reply = await chat(messages)
    return {"reply": reply}
