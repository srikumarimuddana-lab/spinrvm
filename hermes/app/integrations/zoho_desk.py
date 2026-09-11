"""Zoho Desk integration — ticket search, create, update."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request

from app.config import settings
from app.zoho_auth import get_access_token

logger = logging.getLogger(__name__)
router = APIRouter()

_TIMEOUT = 15


async def _desk_headers() -> dict[str, str]:
    token = await get_access_token()
    return {
        "Authorization": f"Zoho-oauthtoken {token}",
        "orgId": settings.ZOHO_DESK_ORG_ID,
        "Content-Type": "application/json",
    }


async def search_tickets(
    query: str = "",
    status: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": min(limit, 100)}
    if query:
        params["searchStr"] = query
    if status:
        params["status"] = status

    url = f"{settings.ZOHO_DESK_API_URL}/tickets/search" if query else f"{settings.ZOHO_DESK_API_URL}/tickets"
    headers = await _desk_headers()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()


async def get_ticket(ticket_id: str) -> dict[str, Any]:
    headers = await _desk_headers()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{settings.ZOHO_DESK_API_URL}/tickets/{ticket_id}",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


async def create_ticket(
    subject: str,
    description: str,
    contact_email: str = "",
    priority: str = "Medium",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subject": subject,
        "description": description,
        "priority": priority,
    }
    if contact_email:
        payload["email"] = contact_email

    headers = await _desk_headers()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.ZOHO_DESK_API_URL}/tickets",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def add_ticket_comment(ticket_id: str, comment: str, is_public: bool = False) -> dict[str, Any]:
    headers = await _desk_headers()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.ZOHO_DESK_API_URL}/tickets/{ticket_id}/comments",
            headers=headers,
            json={"content": comment, "isPublic": is_public},
        )
        resp.raise_for_status()
        return resp.json()


# ── HTTP routes ──────────────────────────────────────────────

@router.get("/tickets")
async def list_tickets(query: str = "", status: str = "", limit: int = 10):
    return await search_tickets(query, status, limit)


@router.get("/tickets/{ticket_id}")
async def read_ticket(ticket_id: str):
    return await get_ticket(ticket_id)


@router.post("/tickets")
async def create_ticket_route(request: Request):
    body = await request.json()
    return await create_ticket(
        subject=body["subject"],
        description=body.get("description", ""),
        contact_email=body.get("email", ""),
        priority=body.get("priority", "Medium"),
    )


@router.post("/tickets/{ticket_id}/comment")
async def comment_ticket(ticket_id: str, request: Request):
    body = await request.json()
    return await add_ticket_comment(
        ticket_id,
        body["comment"],
        is_public=body.get("is_public", False),
    )
