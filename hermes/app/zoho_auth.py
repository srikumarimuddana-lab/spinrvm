"""Shared Zoho OAuth2 token manager — refreshes automatically."""
from __future__ import annotations

import asyncio
import time
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_token: str = ""
_expires_at: float = 0.0
_lock = asyncio.Lock()


async def get_access_token() -> str:
    global _token, _expires_at
    if _token and time.time() < _expires_at - 60:
        return _token
    async with _lock:
        if _token and time.time() < _expires_at - 60:
            return _token
        return await _refresh()


async def _refresh() -> str:
    global _token, _expires_at
    if not settings.ZOHO_CLIENT_ID or not settings.ZOHO_REFRESH_TOKEN:
        raise RuntimeError("Zoho OAuth credentials not configured")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.ZOHO_ACCOUNTS_URL}/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.ZOHO_CLIENT_ID,
                "client_secret": settings.ZOHO_CLIENT_SECRET,
                "refresh_token": settings.ZOHO_REFRESH_TOKEN,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    _token = data["access_token"]
    _expires_at = time.time() + data.get("expires_in", 3600)
    logger.info("Zoho access token refreshed, expires in %ds", data.get("expires_in", 3600))
    return _token
