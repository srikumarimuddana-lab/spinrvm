"""Spinr backend HTTP client — fetches health, KPIs, and operational data."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_TIMEOUT = 15


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if settings.SPINR_API_KEY:
        h["Authorization"] = f"Bearer {settings.SPINR_API_KEY}"
    return h


async def _get(path: str) -> Any:
    url = f"{settings.SPINR_BACKEND_URL}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def _safe_get(path: str, label: str) -> dict[str, Any]:
    try:
        return {"status": "ok", "data": await _get(path)}
    except httpx.HTTPStatusError as exc:
        logger.warning("%s fetch failed: %s", label, exc.response.status_code)
        return {"status": "error", "code": exc.response.status_code}
    except httpx.RequestError as exc:
        logger.warning("%s fetch failed: %s", label, exc)
        return {"status": "unreachable", "detail": str(exc)}


# ── Infra report ──────────────────────────────────────────────

async def get_infra_health() -> dict[str, Any]:
    health = await _safe_get("/health", "backend-health")
    # /ready is the Railway health-check endpoint
    ready = await _safe_get("/ready", "backend-ready")
    metrics = await _safe_get("/metrics", "backend-metrics")
    return {
        "backend_health": health,
        "backend_ready": ready,
        "metrics_available": metrics.get("status") == "ok",
        "configured_url": settings.SPINR_BACKEND_URL,
    }


# ── Ops report ────────────────────────────────────────────────

async def get_ops_report() -> dict[str, Any]:
    rides = await _safe_get("/admin/rides/stats", "ride-stats")
    drivers = await _safe_get("/admin/drivers/stats", "driver-stats")
    payments = await _safe_get("/admin/payments/stats", "payment-stats")
    dispatch = await _safe_get("/admin/dispatch/stats", "dispatch-stats")
    return {
        "rides": rides,
        "drivers": drivers,
        "payments": payments,
        "dispatch": dispatch,
    }


async def get_ride_list(limit: int = 10) -> dict[str, Any]:
    return await _safe_get(f"/admin/rides?limit={limit}&sort=-created_at", "ride-list")


async def get_driver_list(online_only: bool = False) -> dict[str, Any]:
    path = "/admin/drivers?limit=20"
    if online_only:
        path += "&is_online=true"
    return await _safe_get(path, "driver-list")


async def get_full_report() -> dict[str, Any]:
    infra = await get_infra_health()
    ops = await get_ops_report()
    return {"infra": infra, "ops": ops}


# ── HTTP routes (direct access) ──────────────────────────────

@router.get("/health")
async def spinr_health():
    return await get_infra_health()


@router.get("/ops")
async def spinr_ops():
    return await get_ops_report()


@router.get("/report")
async def spinr_full_report():
    return await get_full_report()


@router.get("/rides")
async def spinr_rides(limit: int = 10):
    return await get_ride_list(limit)


@router.get("/drivers")
async def spinr_drivers(online_only: bool = False):
    return await get_driver_list(online_only)
